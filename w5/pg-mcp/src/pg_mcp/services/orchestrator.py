"""Query orchestrator for coordinating the complete query flow.

This module provides the QueryOrchestrator class that coordinates all components
of the query processing pipeline: SQL generation, validation, execution, and result
validation. It implements retry logic, error handling, and request tracking.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from asyncpg import Pool

from pg_mcp.cache.schema_cache import SchemaCache
from pg_mcp.config.settings import ResilienceConfig, ValidationConfig
from pg_mcp.models.errors import (
    DatabaseError,
    ErrorCode,
    LLMError,
    PgMcpError,
    SchemaLoadError,
    SecurityViolationError,
    SQLParseError,
)
from pg_mcp.models.query import (
    ErrorDetail,
    QueryRequest,
    QueryResponse,
    QueryResult,
    ReturnType,
    ValidationResult,
)
from pg_mcp.observability.metrics import MetricsCollector
from pg_mcp.resilience.circuit_breaker import CircuitBreaker
from pg_mcp.resilience.rate_limiter import MultiRateLimiter
from pg_mcp.services.result_validator import ResultValidator
from pg_mcp.services.sql_executor import SQLExecutor
from pg_mcp.services.sql_generator import SQLGenerator
from pg_mcp.services.sql_validator import SQLValidator

if TYPE_CHECKING:
    from pg_mcp.services.database_selector import DatabaseSelector

logger = logging.getLogger(__name__)


class QueryOrchestrator:
    """Orchestrates the complete query processing pipeline.

    This class coordinates SQL generation, validation, execution, and result
    validation. It implements retry logic with error feedback, circuit breaker
    pattern for fault tolerance, and comprehensive error handling.

    Example:
        >>> orchestrator = QueryOrchestrator(
        ...     sql_generator=generator,
        ...     sql_validator=validator,
        ...     sql_executor=executor,
        ...     result_validator=result_validator,
        ...     schema_cache=cache,
        ...     pools={"mydb": pool},
        ...     resilience_config=resilience_config,
        ...     validation_config=validation_config,
        ... )
        >>> response = await orchestrator.execute_query(QueryRequest(
        ...     question="How many users?",
        ...     database="mydb"
        ... ))
    """

    def __init__(
        self,
        sql_generator: SQLGenerator,
        sql_validator: SQLValidator,
        sql_executors: dict[str, SQLExecutor],
        result_validator: ResultValidator,
        schema_cache: SchemaCache,
        pools: dict[str, Pool],
        resilience_config: ResilienceConfig,
        validation_config: ValidationConfig,
        default_database: str | None = None,
        rate_limiter: MultiRateLimiter | None = None,
        metrics: MetricsCollector | None = None,
        database_selector: DatabaseSelector | None = None,
        database_descriptions: dict[str, str] | None = None,
    ) -> None:
        """Initialize query orchestrator.

        Args:
            sql_generator: SQL generation service.
            sql_validator: SQL validation service.
            sql_executors: Dictionary mapping database names to SQL executors.
            result_validator: Result validation service.
            schema_cache: Schema cache instance.
            pools: Dictionary mapping database names to connection pools.
            resilience_config: Resilience configuration for retries and circuit breaker.
            validation_config: Validation configuration including thresholds.
            default_database: Default database name when not specified in request.
            rate_limiter: Optional rate limiter for concurrency control.
            metrics: Optional metrics collector for observability.
            database_selector: Optional database selector for intelligent routing.
            database_descriptions: Optional mapping of database names to descriptions.
        """
        self.sql_generator = sql_generator
        self.sql_validator = sql_validator
        self.sql_executors = sql_executors
        self.result_validator = result_validator
        self.schema_cache = schema_cache
        self.pools = pools
        self.resilience_config = resilience_config
        self.validation_config = validation_config
        self.default_database = default_database or (
            next(iter(sql_executors.keys())) if sql_executors else None
        )
        self.rate_limiter = rate_limiter
        self.metrics = metrics
        self.database_selector = database_selector
        self.database_descriptions = database_descriptions or {}

        # Create circuit breaker for LLM calls
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=resilience_config.circuit_breaker_threshold,
            recovery_timeout=resilience_config.circuit_breaker_timeout,
        )

    async def execute_query(self, request: QueryRequest) -> QueryResponse:
        """Execute complete query flow from question to results.

        This method orchestrates the entire pipeline:
        1. Generate request_id for tracking
        2. Resolve and validate database name
        3. Load schema from cache
        4. Generate and validate SQL with retry logic
        5. Execute SQL (if return_type == RESULT)
        6. Validate results (optional)
        7. Return structured response

        Args:
            request: Query request containing question and parameters.

        Returns:
            QueryResponse: Complete response with SQL, results, or error information.

        Example:
            >>> response = await orchestrator.execute_query(
            ...     QueryRequest(question="Count all users", return_type="result")
            ... )
            >>> if response.success:
            ...     print(f"Found {response.data.row_count} rows")
        """
        # Generate request_id for full-chain tracing
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()

        logger.info(
            "Starting query execution",
            extra={"request_id": request_id, "question": request.question[:100]},
        )

        # Resolve database name early for metrics
        try:
            database_name = await self._resolve_database(request.database, request.question)
        except DatabaseError as e:
            if self.metrics:
                self.metrics.increment_query_request("error", request.database or "unknown")
            return QueryResponse(
                success=False,
                generated_sql=None,
                validation=None,
                data=None,
                error=ErrorDetail(
                    code=e.code.value,
                    message=e.message,
                    details=e.details,
                ),
                confidence=0,
                tokens_used=None,
            )

        try:
            # Apply rate limiting if enabled
            if self.rate_limiter is not None:
                try:
                    timeout = self.resilience_config.rate_limit_timeout
                    async with self.rate_limiter.for_queries(timeout=timeout):
                        return await self._execute_query_internal(
                            request=request,
                            database_name=database_name,
                            request_id=request_id,
                            start_time=start_time,
                        )
                except TimeoutError:
                    logger.warning(
                        "Rate limit exceeded",
                        extra={"request_id": request_id, "database": database_name},
                    )
                    if self.metrics:
                        self.metrics.increment_query_request("rate_limited", database_name)
                    return QueryResponse(
                        success=False,
                        generated_sql=None,
                        validation=None,
                        data=None,
                        error=ErrorDetail(
                            code=ErrorCode.RATE_LIMIT_EXCEEDED.value,
                            message="Rate limit exceeded. Too many concurrent requests.",
                            details={"timeout": self.resilience_config.rate_limit_timeout},
                        ),
                        confidence=0,
                        tokens_used=None,
                    )
            else:
                # No rate limiting, execute directly
                return await self._execute_query_internal(
                    request=request,
                    database_name=database_name,
                    request_id=request_id,
                    start_time=start_time,
                )

        except PgMcpError as e:
            # Handle known application errors
            duration = time.perf_counter() - start_time
            logger.warning(
                "Query execution failed with known error",
                extra={
                    "request_id": request_id,
                    "error_code": e.code,
                    "error_message": str(e),
                    "duration_seconds": duration,
                },
            )
            if self.metrics:
                self.metrics.increment_query_request("error", database_name)
                self.metrics.query_duration.observe(duration)
            return QueryResponse(
                success=False,
                generated_sql=None,
                validation=None,
                data=None,
                error=ErrorDetail(
                    code=e.code.value,
                    message=e.message,
                    details=e.details,
                ),
                confidence=0,
                tokens_used=None,
            )
        except Exception as e:
            # Handle unexpected errors
            duration = time.perf_counter() - start_time
            logger.exception(
                "Query execution failed with unexpected error",
                extra={"request_id": request_id, "duration_seconds": duration},
            )
            if self.metrics:
                self.metrics.increment_query_request("error", database_name)
                self.metrics.query_duration.observe(duration)
            return QueryResponse(
                success=False,
                generated_sql=None,
                validation=None,
                data=None,
                error=ErrorDetail(
                    code=ErrorCode.INTERNAL_ERROR.value,
                    message=f"Internal server error: {e!s}",
                    details={"error_type": type(e).__name__},
                ),
                confidence=0,
                tokens_used=None,
            )

    async def _execute_query_internal(
        self,
        request: QueryRequest,
        database_name: str,
        request_id: str,
        start_time: float,
    ) -> QueryResponse:
        """Internal method to execute query after rate limiting."""
        logger.debug(
            "Resolved database",
            extra={"request_id": request_id, "database": database_name},
        )

        # Step 2: Get schema from cache
        schema = self.schema_cache.get(database_name)
        if schema is None:
            # Schema not in cache, load it
            pool = self.pools.get(database_name)
            if pool is None:
                raise DatabaseError(
                    message=f"No connection pool available for database '{database_name}'",
                    details={"database": database_name},
                )
            try:
                schema = await self.schema_cache.load(database_name, pool)
            except Exception as e:
                raise SchemaLoadError(
                    message=f"Failed to load schema for database '{database_name}': {e!s}",
                    details={"database": database_name, "error": str(e)},
                ) from e

        logger.debug(
            "Schema loaded",
            extra={
                "request_id": request_id,
                "database": database_name,
                "tables": len(schema.tables),
            },
        )

        # Step 3: Generate and validate SQL with retry logic
        generated_sql, validation_result, tokens_used = await self._generate_sql_with_retry(
            question=request.question,
            schema=schema,
            request_id=request_id,
        )

        # Record LLM tokens if available
        if self.metrics and tokens_used:
            self.metrics.increment_llm_tokens("sql_generation", tokens_used)

        # Step 4: If return_type is SQL, return early
        if request.return_type == ReturnType.SQL:
            duration = time.perf_counter() - start_time
            logger.info(
                "Returning SQL only",
                extra={
                    "request_id": request_id,
                    "sql_length": len(generated_sql),
                    "duration_seconds": duration,
                },
            )
            if self.metrics:
                self.metrics.increment_query_request("success", database_name)
                self.metrics.query_duration.observe(duration)
            return QueryResponse(
                success=True,
                generated_sql=generated_sql,
                validation=validation_result,
                data=None,
                error=None,
                confidence=100,
                tokens_used=tokens_used,
            )

        # Step 5: Execute SQL
        logger.debug("Executing SQL", extra={"request_id": request_id})
        sql_start_time = time.perf_counter()

        # Get the executor for this database
        executor = self._get_executor(database_name)
        results, total_count = await executor.execute(generated_sql)

        sql_duration = time.perf_counter() - sql_start_time
        if self.metrics:
            self.metrics.observe_db_query_duration(sql_duration)

        execution_time_ms = sql_duration * 1000
        logger.info(
            "SQL executed successfully",
            extra={
                "request_id": request_id,
                "row_count": total_count,
                "execution_time_ms": execution_time_ms,
            },
        )

        # Step 6: Validate results (non-blocking, failures don't fail the request)
        result_confidence = await self._validate_results_safely(
            question=request.question,
            sql=generated_sql,
            results=results,
            row_count=total_count,
            request_id=request_id,
        )

        # Step 7: Build successful response
        query_result = QueryResult(
            columns=list(results[0].keys()) if results else [],
            rows=results,
            row_count=len(results),  # Limited row count (after max_rows applied)
            execution_time_ms=execution_time_ms,
        )

        duration = time.perf_counter() - start_time
        if self.metrics:
            self.metrics.increment_query_request("success", database_name)
            self.metrics.query_duration.observe(duration)

        return QueryResponse(
            success=True,
            generated_sql=generated_sql,
            validation=validation_result,
            data=query_result,
            error=None,
            confidence=result_confidence,
            tokens_used=tokens_used,
        )

    async def _resolve_database(self, database: str | None, question: str) -> str:
        """Resolve database name from request, default, or intelligent selection.

        Resolution order:
        1. If database is explicitly specified, validate and use it
        2. If only one database available, use it
        3. If default_database is configured, use it (unless smart selection enabled)
        4. If database_selector is available, use intelligent selection
        5. Fall back to default_database if available
        6. Raise error if cannot determine

        Args:
            database: Database name from request (optional).
            question: User's question for intelligent selection.

        Returns:
            str: Resolved database name.

        Raises:
            DatabaseError: If database is invalid or cannot be determined.

        Example:
            >>> name = await orchestrator._resolve_database("mydb", "query")
            >>> name = await orchestrator._resolve_database(None, "显示所有文章")
        """
        # 1. If database explicitly specified, validate and use it
        if database is not None:
            if database not in self.pools:
                raise DatabaseError(
                    message=f"Database '{database}' not found",
                    details={
                        "requested_database": database,
                        "available_databases": list(self.pools.keys()),
                    },
                )
            return database

        available_dbs = list(self.pools.keys())

        # 2. If no databases configured
        if len(available_dbs) == 0:
            raise DatabaseError(
                message="No databases configured",
                details={},
            )

        # 3. If only one database available, use it
        if len(available_dbs) == 1:
            return available_dbs[0]

        # 4. Try intelligent selection if selector and descriptions available
        if self.database_selector and self.database_descriptions:
            try:
                result = await self.database_selector.select(
                    question=question,
                    database_descriptions=self.database_descriptions,
                )
                if result.database in self.pools:
                    logger.info(
                        f"Smart database selection: {result.database} "
                        f"(confidence: {result.confidence:.2f}, reason: {result.reason})"
                    )
                    return result.database
                else:
                    logger.warning(
                        f"Database selector returned '{result.database}' but it's not in pools"
                    )
            except Exception as e:
                logger.warning(f"Database selection failed: {e}")

        # 5. Fall back to default database if configured
        if self.default_database is not None and self.default_database in self.pools:
            return self.default_database

        # 6. Cannot determine, raise error
        raise DatabaseError(
            message="Multiple databases available, please specify which to query",
            details={
                "available_databases": available_dbs,
                "hint": "Add 'database' parameter or configure PG_DEFAULT_DATABASE",
            },
        )

    def _get_executor(self, database: str) -> SQLExecutor:
        """Get SQL executor for the specified database.

        Args:
            database: Database name to get executor for.

        Returns:
            SQLExecutor: The executor for the specified database.

        Raises:
            DatabaseError: If no executor exists for the database.
        """
        executor = self.sql_executors.get(database)
        if executor is None:
            raise DatabaseError(
                message=f"No SQL executor configured for database '{database}'",
                details={
                    "requested_database": database,
                    "available_databases": list(self.sql_executors.keys()),
                },
            )
        return executor

    async def _generate_sql_with_retry(
        self,
        question: str,
        schema: Any,
        request_id: str,
    ) -> tuple[str, ValidationResult, int | None]:
        """Generate and validate SQL with retry logic on validation failures.

        This method implements a retry loop that:
        1. Checks circuit breaker state
        2. Applies LLM rate limiting
        3. Generates SQL using LLM
        4. Validates the generated SQL
        5. On validation failure, retries with error feedback
        6. Records success/failure to circuit breaker and metrics

        Args:
            question: User's natural language question.
            schema: Database schema for context.
            request_id: Request ID for tracking.

        Returns:
            tuple: (generated_sql, validation_result, tokens_used)

        Raises:
            LLMError: If circuit breaker is open or generation fails.
            SecurityViolationError: If SQL fails validation after all retries.
            SQLParseError: If SQL cannot be parsed.

        Example:
            >>> sql, validation, tokens = await orchestrator._generate_sql_with_retry(
            ...     question="Count users",
            ...     schema=db_schema,
            ...     request_id="123",
            ... )
        """
        # Check circuit breaker
        if not self.circuit_breaker.allow_request():
            if self.metrics:
                self.metrics.increment_llm_call("sql_generation_circuit_open")
            raise LLMError(
                message="SQL generation service is temporarily unavailable (circuit breaker open)",
                details={
                    "circuit_state": self.circuit_breaker.state,
                    "failure_count": self.circuit_breaker.failure_count,
                },
            )

        previous_sql: str | None = None
        error_feedback: str | None = None
        max_retries = self.resilience_config.max_retries
        tokens_used: int | None = None

        for attempt in range(max_retries + 1):
            try:
                logger.debug(
                    "Generating SQL",
                    extra={
                        "request_id": request_id,
                        "attempt": attempt + 1,
                        "max_retries": max_retries + 1,
                    },
                )

                # Apply LLM rate limiting if enabled
                llm_start_time = time.perf_counter()
                if self.rate_limiter is not None:
                    try:
                        timeout = self.resilience_config.rate_limit_timeout
                        async with self.rate_limiter.for_llm(timeout=timeout):
                            generated_sql = await self.sql_generator.generate(
                                question=question,
                                schema=schema,
                                previous_attempt=previous_sql,
                                error_feedback=error_feedback,
                            )
                    except TimeoutError:
                        if self.metrics:
                            self.metrics.increment_llm_call("sql_generation_rate_limited")
                        raise LLMError(
                            message="LLM rate limit exceeded. Too many concurrent LLM calls.",
                            details={"timeout": self.resilience_config.rate_limit_timeout},
                        )
                else:
                    generated_sql = await self.sql_generator.generate(
                        question=question,
                        schema=schema,
                        previous_attempt=previous_sql,
                        error_feedback=error_feedback,
                    )

                llm_duration = time.perf_counter() - llm_start_time
                if self.metrics:
                    self.metrics.increment_llm_call("sql_generation")
                    self.metrics.observe_llm_latency("sql_generation", llm_duration)

                logger.debug(
                    "SQL generated",
                    extra={
                        "request_id": request_id,
                        "sql_length": len(generated_sql),
                        "llm_duration_seconds": llm_duration,
                    },
                )

                # Validate SQL
                try:
                    self.sql_validator.validate_or_raise(generated_sql)
                except (SecurityViolationError, SQLParseError) as validation_error:
                    if self.metrics:
                        self.metrics.increment_sql_rejected(type(validation_error).__name__)
                    if attempt < max_retries:
                        # Record as failure and retry with feedback
                        logger.warning(
                            "SQL validation failed, retrying with feedback",
                            extra={
                                "request_id": request_id,
                                "attempt": attempt + 1,
                                "error": str(validation_error),
                            },
                        )
                        previous_sql = generated_sql
                        error_feedback = str(validation_error)
                        continue
                    else:
                        # Out of retries, record failure and raise
                        self.circuit_breaker.record_failure()
                        logger.error(
                            "SQL validation failed after all retries",
                            extra={
                                "request_id": request_id,
                                "attempts": attempt + 1,
                                "error": str(validation_error),
                            },
                        )
                        raise

                # Validation successful
                self.circuit_breaker.record_success()
                logger.info(
                    "SQL generated and validated successfully",
                    extra={
                        "request_id": request_id,
                        "attempts": attempt + 1,
                    },
                )

                # Build validation result
                validation_result = ValidationResult(
                    is_valid=True,
                    is_select=True,
                    allows_data_modification=False,
                    uses_blocked_functions=[],
                    error_message=None,
                )

                return generated_sql, validation_result, tokens_used

            except (LLMError, SecurityViolationError, SQLParseError):
                # Re-raise known errors
                raise
            except Exception as e:
                # Unexpected error during generation
                self.circuit_breaker.record_failure()
                if self.metrics:
                    self.metrics.increment_llm_call("sql_generation_error")
                logger.exception(
                    "Unexpected error during SQL generation",
                    extra={"request_id": request_id},
                )
                raise LLMError(
                    message=f"SQL generation failed unexpectedly: {e!s}",
                    details={"error_type": type(e).__name__},
                ) from e

        # Should not reach here, but just in case
        self.circuit_breaker.record_failure()
        raise LLMError(
            message="SQL generation failed after all retry attempts",
            details={"max_retries": max_retries},
        )

    async def _validate_results_safely(
        self,
        question: str,
        sql: str,
        results: list[dict[str, Any]],
        row_count: int,
        request_id: str,
    ) -> int:
        """Validate query results with error handling (non-blocking).

        This method attempts to validate results using LLM, but failures
        don't cause the overall query to fail. Returns a confidence score.

        Args:
            question: User's original question.
            sql: Generated SQL query.
            results: Query results.
            row_count: Total row count.
            request_id: Request ID for tracking.

        Returns:
            int: Confidence score (0-100). Returns 100 if validation disabled/fails.

        Example:
            >>> confidence = await orchestrator._validate_results_safely(
            ...     question="Count users",
            ...     sql="SELECT COUNT(*) FROM users",
            ...     results=[{"count": 42}],
            ...     row_count=1,
            ...     request_id="123",
            ... )
        """
        if not self.validation_config.enabled:
            return 100

        try:
            logger.debug(
                "Validating results",
                extra={"request_id": request_id},
            )

            validation_result = await self.result_validator.validate(
                question=question,
                sql=sql,
                results=results,
                row_count=row_count,
            )

            logger.info(
                "Result validation completed",
                extra={
                    "request_id": request_id,
                    "confidence": validation_result.confidence,
                    "is_acceptable": validation_result.is_acceptable,
                },
            )

            return validation_result.confidence

        except Exception as e:
            # Log but don't fail the query
            logger.warning(
                "Result validation failed, continuing with default confidence",
                extra={
                    "request_id": request_id,
                    "error": str(e),
                },
            )
            return 100  # Default to high confidence if validation fails
