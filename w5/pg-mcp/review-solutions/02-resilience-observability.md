# 问题 2：弹性与可观测性实施方案

## 问题概述

当前 pg-mcp 的弹性和可观测性模块存在以下问题：

| 组件 | 初始化 | 实际使用 | 配置驱动 |
|------|--------|----------|----------|
| CircuitBreaker | ✓ | ✓ | ✓ |
| RateLimiter | ✓ | ✗ **从未使用** | ✗ 硬编码 |
| MetricsCollector | ✓ | ✗ **从未调用** | ✓ |
| Tracing | ✓ | ✗ **从未使用** | ✓ |

## 现状分析

### 2.1 速率限制器 (RateLimiter)

**初始化代码 (`server.py:186-190`)：**

```python
_rate_limiter = MultiRateLimiter(
    query_limit=10,  # 硬编码！
    llm_limit=5,     # 硬编码！
)
# 创建后从未使用！
```

**问题：**
- 速率限制值硬编码，无法通过配置调整
- `_rate_limiter` 变量从未在请求处理中使用

### 2.2 指标收集器 (MetricsCollector)

**初始化代码 (`server.py:135-144`)：**

```python
_metrics = MetricsCollector()
if _settings.observability.metrics_enabled:
    start_http_server(_settings.observability.metrics_port)
```

**MetricsCollector 提供的方法 (`metrics.py`)：**

```python
class MetricsCollector:
    def record_query_request(self, database: str, success: bool): ...
    def record_query_duration(self, database: str, duration: float): ...
    def record_llm_call(self, model: str, operation: str, success: bool): ...
    def record_llm_latency(self, model: str, latency: float): ...
    def record_llm_tokens(self, model: str, tokens: int): ...
    def record_sql_rejected(self, reason: str): ...
    def update_db_connections(self, database: str, count: int): ...
    def record_db_query_duration(self, database: str, duration: float): ...
    def update_schema_cache_age(self, database: str, age: float): ...
```

**问题：以上方法从未在请求处理流程中被调用！**

### 2.3 追踪 (Tracing)

**提供的基础设施 (`tracing.py`)：**

```python
class TraceContext(BaseModel): ...
def generate_request_id() -> str: ...

@asynccontextmanager
async def request_context(operation: str) -> AsyncGenerator[TraceContext, None]: ...

def trace_async(operation: str):
    """Decorator for tracing async functions."""
    ...
```

**问题：在 services 中完全未使用追踪装饰器或上下文管理器！**

---

## 实施方案

### Phase 1: 速率限制器集成

#### 1.1 配置化速率限制

**文件：** `src/pg_mcp/config/settings.py`

```python
class ResilienceConfig(BaseSettings):
    """Resilience and fault tolerance configuration."""

    model_config = SettingsConfigDict(env_prefix="RESILIENCE_", env_file=".env", extra="ignore")

    # 现有字段...
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay: float = Field(default=1.0, ge=0.1, le=10.0)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    circuit_breaker_threshold: int = Field(default=5, ge=1, le=100)
    circuit_breaker_timeout: float = Field(default=60.0, ge=10.0, le=300.0)

    # 新增：速率限制配置
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable rate limiting"
    )
    rate_limit_queries_per_second: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximum queries per second"
    )
    rate_limit_llm_calls_per_second: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum LLM calls per second"
    )
```

#### 1.2 修改 Server 初始化

**文件：** `src/pg_mcp/server.py`

```python
# 使用配置初始化速率限制器
if _settings.resilience.rate_limit_enabled:
    _rate_limiter = MultiRateLimiter(
        query_limit=_settings.resilience.rate_limit_queries_per_second,
        llm_limit=_settings.resilience.rate_limit_llm_calls_per_second,
    )
else:
    _rate_limiter = None
```

#### 1.3 在请求处理中使用速率限制器

**文件：** `src/pg_mcp/server.py`

```python
@mcp.tool()
async def query(
    question: str,
    database: str | None = None,
    return_type: str = "result",
) -> dict[str, Any]:
    """Execute a natural language query against PostgreSQL database."""

    # 速率限制检查
    if _rate_limiter is not None:
        try:
            await _rate_limiter.acquire_query()
        except RateLimitExceeded as e:
            return {
                "success": False,
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": str(e),
                },
                "confidence": 0,
                "tokens_used": 0,
            }

    # ... 继续处理请求
```

#### 1.4 在 LLM 调用中使用速率限制

**文件：** `src/pg_mcp/services/sql_generator.py`

```python
class SQLGenerator:
    def __init__(
        self,
        config: OpenAIConfig,
        rate_limiter: MultiRateLimiter | None = None,
    ):
        self.config = config
        self._rate_limiter = rate_limiter

    async def generate(self, question: str, schema: "DatabaseSchema", ...) -> str:
        # LLM 速率限制
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire_llm()

        # ... 调用 OpenAI API
```

---

### Phase 2: 指标收集集成

#### 2.1 创建指标装饰器

**新增文件：** `src/pg_mcp/observability/decorators.py`

```python
"""Observability decorators for automatic metrics collection."""

import time
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from pg_mcp.observability.metrics import MetricsCollector

P = ParamSpec("P")
T = TypeVar("T")


def record_query_metrics(
    metrics: MetricsCollector,
    database_param: str = "database",
):
    """Decorator to record query metrics."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            database = kwargs.get(database_param, "default")
            start_time = time.perf_counter()

            try:
                result = await func(*args, **kwargs)
                duration = time.perf_counter() - start_time

                # 记录成功指标
                metrics.record_query_request(database, success=True)
                metrics.record_query_duration(database, duration)

                return result

            except Exception as e:
                duration = time.perf_counter() - start_time

                # 记录失败指标
                metrics.record_query_request(database, success=False)
                metrics.record_query_duration(database, duration)

                raise

        return wrapper
    return decorator


def record_llm_metrics(
    metrics: MetricsCollector,
    model: str,
    operation: str,
):
    """Decorator to record LLM call metrics."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            start_time = time.perf_counter()

            try:
                result = await func(*args, **kwargs)
                latency = time.perf_counter() - start_time

                # 记录成功指标
                metrics.record_llm_call(model, operation, success=True)
                metrics.record_llm_latency(model, latency)

                # 如果结果包含 token 使用信息
                if hasattr(result, "usage") and result.usage:
                    metrics.record_llm_tokens(
                        model,
                        result.usage.total_tokens
                    )

                return result

            except Exception as e:
                latency = time.perf_counter() - start_time

                # 记录失败指标
                metrics.record_llm_call(model, operation, success=False)
                metrics.record_llm_latency(model, latency)

                raise

        return wrapper
    return decorator


def record_db_metrics(
    metrics: MetricsCollector,
    database_param: str = "database",
):
    """Decorator to record database query metrics."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            database = kwargs.get(database_param, "default")
            start_time = time.perf_counter()

            try:
                result = await func(*args, **kwargs)
                duration = time.perf_counter() - start_time

                metrics.record_db_query_duration(database, duration)

                return result

            except Exception:
                duration = time.perf_counter() - start_time
                metrics.record_db_query_duration(database, duration)
                raise

        return wrapper
    return decorator
```

#### 2.2 在服务中集成指标

**文件：** `src/pg_mcp/services/orchestrator.py`

```python
import time
from pg_mcp.observability.metrics import MetricsCollector


class QueryOrchestrator:
    def __init__(
        self,
        # ... 其他参数
        metrics: MetricsCollector | None = None,
    ):
        self._metrics = metrics

    async def execute_query(
        self,
        question: str,
        database: str | None = None,
        return_type: str = "result",
    ) -> QueryResponse:
        start_time = time.perf_counter()
        db_name = database or self._default_database

        try:
            # ... 执行查询逻辑

            # 记录成功指标
            if self._metrics:
                duration = time.perf_counter() - start_time
                self._metrics.record_query_request(db_name, success=True)
                self._metrics.record_query_duration(db_name, duration)

            return response

        except Exception as e:
            # 记录失败指标
            if self._metrics:
                duration = time.perf_counter() - start_time
                self._metrics.record_query_request(db_name, success=False)
                self._metrics.record_query_duration(db_name, duration)

            raise
```

**文件：** `src/pg_mcp/services/sql_generator.py`

```python
class SQLGenerator:
    def __init__(
        self,
        config: OpenAIConfig,
        metrics: MetricsCollector | None = None,
    ):
        self._metrics = metrics

    async def generate(self, question: str, schema: "DatabaseSchema", ...) -> str:
        start_time = time.perf_counter()

        try:
            response = await self.client.chat.completions.create(...)

            # 记录 LLM 指标
            if self._metrics:
                latency = time.perf_counter() - start_time
                self._metrics.record_llm_call(
                    self.config.model, "sql_generation", success=True
                )
                self._metrics.record_llm_latency(self.config.model, latency)
                if response.usage:
                    self._metrics.record_llm_tokens(
                        self.config.model, response.usage.total_tokens
                    )

            return self._extract_sql(response.choices[0].message.content)

        except Exception as e:
            if self._metrics:
                latency = time.perf_counter() - start_time
                self._metrics.record_llm_call(
                    self.config.model, "sql_generation", success=False
                )
                self._metrics.record_llm_latency(self.config.model, latency)
            raise
```

**文件：** `src/pg_mcp/services/sql_validator.py`

```python
class SQLValidator:
    def __init__(
        self,
        security_config: SecurityConfig,
        metrics: MetricsCollector | None = None,
    ):
        self._metrics = metrics

    def validate(self, sql: str) -> ValidationResult:
        result = self._do_validation(sql)

        # 记录拒绝指标
        if self._metrics and not result.is_valid:
            reason = result.error_message or "unknown"
            self._metrics.record_sql_rejected(reason)

        return result
```

#### 2.3 修改 Server 注入 Metrics

**文件：** `src/pg_mcp/server.py`

```python
@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncGenerator[None, None]:
    global _metrics, _orchestrator

    # 初始化 Metrics
    _metrics = MetricsCollector()
    if _settings.observability.metrics_enabled:
        start_http_server(_settings.observability.metrics_port)

    # 创建 SQLGenerator 并注入 metrics
    sql_generator = SQLGenerator(
        config=_settings.openai,
        metrics=_metrics,  # 注入 metrics
    )

    # 创建 SQLValidator 并注入 metrics
    sql_validator = SQLValidator(
        security_config=_settings.security,
        metrics=_metrics,  # 注入 metrics
    )

    # 创建 Orchestrator 并注入 metrics
    _orchestrator = QueryOrchestrator(
        sql_generator=sql_generator,
        sql_validator=sql_validator,
        sql_executor=sql_executor,
        result_validator=result_validator,
        circuit_breaker=circuit_breaker,
        metrics=_metrics,  # 注入 metrics
        settings=_settings,
    )
```

---

### Phase 3: 追踪集成

#### 3.1 创建追踪上下文管理

**文件：** `src/pg_mcp/observability/tracing.py` (增强)

```python
"""Request tracing and context propagation."""

import contextvars
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator

import structlog

# 上下文变量
_trace_context: contextvars.ContextVar["TraceContext | None"] = (
    contextvars.ContextVar("trace_context", default=None)
)

logger = structlog.get_logger()


@dataclass
class TraceSpan:
    """A span within a trace."""

    operation: str
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    status: str = "started"
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class TraceContext:
    """Context for distributed tracing."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_span_id: str | None = None
    spans: list[TraceSpan] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def start_span(self, operation: str, **attributes) -> TraceSpan:
        """Start a new span."""
        span = TraceSpan(operation=operation, attributes=attributes)
        self.spans.append(span)
        return span

    def end_span(self, span: TraceSpan, status: str = "ok", error: str | None = None):
        """End a span."""
        span.end_time = datetime.utcnow()
        span.status = status
        span.error = error


def get_current_trace() -> TraceContext | None:
    """Get current trace context."""
    return _trace_context.get()


@asynccontextmanager
async def trace_request(
    operation: str,
    **attributes,
) -> AsyncGenerator[TraceContext, None]:
    """Create a new trace context for a request."""
    ctx = TraceContext(attributes=attributes)
    token = _trace_context.set(ctx)

    span = ctx.start_span(operation, **attributes)

    try:
        logger.info(
            "trace_started",
            trace_id=ctx.trace_id,
            operation=operation,
            **attributes,
        )
        yield ctx
        ctx.end_span(span, status="ok")
        logger.info(
            "trace_completed",
            trace_id=ctx.trace_id,
            operation=operation,
            duration_ms=(span.end_time - span.start_time).total_seconds() * 1000,
        )

    except Exception as e:
        ctx.end_span(span, status="error", error=str(e))
        logger.error(
            "trace_failed",
            trace_id=ctx.trace_id,
            operation=operation,
            error=str(e),
        )
        raise

    finally:
        _trace_context.reset(token)


@asynccontextmanager
async def trace_span(
    operation: str,
    **attributes,
) -> AsyncGenerator[TraceSpan, None]:
    """Create a span within current trace."""
    ctx = get_current_trace()

    if ctx is None:
        # 如果没有上下文，创建一个临时的
        async with trace_request(operation, **attributes) as new_ctx:
            yield new_ctx.spans[-1]
        return

    span = ctx.start_span(operation, **attributes)

    try:
        yield span
        ctx.end_span(span, status="ok")

    except Exception as e:
        ctx.end_span(span, status="error", error=str(e))
        raise
```

#### 3.2 在服务中使用追踪

**文件：** `src/pg_mcp/services/orchestrator.py`

```python
from pg_mcp.observability.tracing import trace_request, trace_span


class QueryOrchestrator:
    async def execute_query(
        self,
        question: str,
        database: str | None = None,
        return_type: str = "result",
    ) -> QueryResponse:
        async with trace_request(
            "execute_query",
            question=question[:100],  # 截断以避免过长
            database=database,
            return_type=return_type,
        ) as trace:
            # 生成 SQL
            async with trace_span("generate_sql"):
                sql = await self._sql_generator.generate(question, schema)

            # 验证 SQL
            async with trace_span("validate_sql"):
                validation = self._sql_validator.validate(sql)

            if not validation.is_valid:
                return self._create_error_response(validation.error_message)

            # 执行 SQL
            if return_type == "result":
                async with trace_span("execute_sql", sql=sql[:200]):
                    result = await executor.execute(sql)

            # 验证结果
            async with trace_span("validate_result"):
                validation_result = await self._result_validator.validate(...)

            return response
```

**文件：** `src/pg_mcp/server.py`

```python
from pg_mcp.observability.tracing import trace_request, get_current_trace


@mcp.tool()
async def query(
    question: str,
    database: str | None = None,
    return_type: str = "result",
) -> dict[str, Any]:
    """Execute a natural language query against PostgreSQL database."""

    async with trace_request(
        "mcp_query",
        question_length=len(question),
        database=database,
    ) as trace:
        try:
            response = await _orchestrator.execute_query(
                question=question,
                database=database,
                return_type=return_type,
            )

            result = response.to_dict()
            result["trace_id"] = trace.trace_id  # 可选：返回 trace_id
            return result

        except Exception as e:
            return {
                "success": False,
                "error": {"code": "internal_error", "message": str(e)},
                "trace_id": trace.trace_id,
            }
```

---

## 验证方案

### 单元测试

**文件：** `tests/unit/test_rate_limiter_integration.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from pg_mcp.resilience.rate_limiter import MultiRateLimiter, RateLimitExceeded


class TestRateLimiterIntegration:
    @pytest.fixture
    def rate_limiter(self):
        return MultiRateLimiter(query_limit=2, llm_limit=1)

    @pytest.mark.asyncio
    async def test_query_rate_limit_enforced(self, rate_limiter):
        """Test query rate limiting is enforced."""
        # 第一次请求应该成功
        await rate_limiter.acquire_query()

        # 第二次请求应该成功
        await rate_limiter.acquire_query()

        # 第三次请求应该被拒绝（超过限制）
        with pytest.raises(RateLimitExceeded):
            await rate_limiter.acquire_query()

    @pytest.mark.asyncio
    async def test_llm_rate_limit_enforced(self, rate_limiter):
        """Test LLM rate limiting is enforced."""
        await rate_limiter.acquire_llm()

        with pytest.raises(RateLimitExceeded):
            await rate_limiter.acquire_llm()

    @pytest.mark.asyncio
    async def test_rate_limit_in_query_handler(self, rate_limiter):
        """Test rate limiting in actual query handler."""
        from pg_mcp.server import query

        # Mock orchestrator
        mock_orchestrator = AsyncMock()

        # 模拟超过限制
        for _ in range(3):
            await rate_limiter.acquire_query()

        # 下一次应该返回错误
        # (这需要注入 rate_limiter 到 query 函数)
```

**文件：** `tests/unit/test_metrics_integration.py`

```python
import pytest
from unittest.mock import MagicMock, patch

from pg_mcp.observability.metrics import MetricsCollector
from pg_mcp.services.orchestrator import QueryOrchestrator


class TestMetricsIntegration:
    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    @pytest.mark.asyncio
    async def test_query_success_metrics_recorded(self, metrics):
        """Test metrics are recorded on successful query."""
        with patch.object(metrics, "record_query_request") as mock_record:
            with patch.object(metrics, "record_query_duration") as mock_duration:
                # 模拟成功查询
                # ... 调用 orchestrator.execute_query

                mock_record.assert_called_once_with("test_db", success=True)
                mock_duration.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_failure_metrics_recorded(self, metrics):
        """Test metrics are recorded on failed query."""
        with patch.object(metrics, "record_query_request") as mock_record:
            # 模拟失败查询
            # ... 调用 orchestrator.execute_query 并触发异常

            mock_record.assert_called_once_with("test_db", success=False)

    @pytest.mark.asyncio
    async def test_llm_metrics_recorded(self, metrics):
        """Test LLM metrics are recorded."""
        with patch.object(metrics, "record_llm_call") as mock_llm:
            with patch.object(metrics, "record_llm_tokens") as mock_tokens:
                # 模拟 SQL 生成
                # ... 调用 sql_generator.generate

                mock_llm.assert_called_once()
                mock_tokens.assert_called_once()

    def test_sql_rejected_metrics_recorded(self, metrics):
        """Test SQL rejection metrics are recorded."""
        from pg_mcp.services.sql_validator import SQLValidator
        from pg_mcp.config.settings import SecurityConfig

        validator = SQLValidator(
            security_config=SecurityConfig(),
            metrics=metrics,
        )

        with patch.object(metrics, "record_sql_rejected") as mock_rejected:
            validator.validate("DROP TABLE users")

            mock_rejected.assert_called_once()
```

**文件：** `tests/unit/test_tracing_integration.py`

```python
import pytest

from pg_mcp.observability.tracing import (
    TraceContext,
    trace_request,
    trace_span,
    get_current_trace,
)


class TestTracingIntegration:
    @pytest.mark.asyncio
    async def test_trace_context_created(self):
        """Test trace context is created for request."""
        async with trace_request("test_operation") as trace:
            assert trace.trace_id is not None
            assert len(trace.spans) == 1
            assert trace.spans[0].operation == "test_operation"

    @pytest.mark.asyncio
    async def test_nested_spans(self):
        """Test nested spans are tracked."""
        async with trace_request("parent") as trace:
            async with trace_span("child1"):
                pass
            async with trace_span("child2"):
                pass

            assert len(trace.spans) == 3  # parent + 2 children

    @pytest.mark.asyncio
    async def test_span_error_recorded(self):
        """Test errors are recorded in spans."""
        with pytest.raises(ValueError):
            async with trace_request("test") as trace:
                async with trace_span("failing_span"):
                    raise ValueError("test error")

        assert trace.spans[-1].status == "error"
        assert "test error" in trace.spans[-1].error

    @pytest.mark.asyncio
    async def test_current_trace_accessible(self):
        """Test current trace is accessible."""
        async with trace_request("test"):
            ctx = get_current_trace()
            assert ctx is not None
            assert ctx.trace_id is not None

        # 退出后应该为 None
        assert get_current_trace() is None
```

### 集成测试

**文件：** `tests/integration/test_observability_e2e.py`

```python
import pytest
import httpx
from prometheus_client import REGISTRY


class TestObservabilityEndToEnd:
    @pytest.mark.asyncio
    async def test_metrics_endpoint_available(self, running_server):
        """Test Prometheus metrics endpoint is available."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:{running_server.metrics_port}/metrics"
            )
            assert response.status_code == 200
            assert "pg_mcp_query_requests_total" in response.text

    @pytest.mark.asyncio
    async def test_metrics_updated_after_query(self, running_server, mcp_client):
        """Test metrics are updated after query execution."""
        # 执行查询
        await mcp_client.call_tool("query", {
            "question": "How many users?",
            "database": "test_db",
        })

        # 检查指标
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:{running_server.metrics_port}/metrics"
            )
            assert 'pg_mcp_query_requests_total{database="test_db"' in response.text

    @pytest.mark.asyncio
    async def test_rate_limiting_works(self, running_server, mcp_client):
        """Test rate limiting prevents excessive requests."""
        # 快速发送多个请求
        results = []
        for _ in range(20):
            result = await mcp_client.call_tool("query", {
                "question": "How many users?",
            })
            results.append(result)

        # 至少有一些请求应该被限制
        rate_limited = [r for r in results if r.get("error", {}).get("code") == "rate_limit_exceeded"]
        assert len(rate_limited) > 0

    @pytest.mark.asyncio
    async def test_trace_id_in_response(self, running_server, mcp_client):
        """Test trace ID is included in response."""
        result = await mcp_client.call_tool("query", {
            "question": "How many users?",
        })

        # 如果配置为返回 trace_id
        if "trace_id" in result:
            assert len(result["trace_id"]) == 36  # UUID 格式
```

---

## 实施步骤

### Step 1: 配置层修改 (0.5 天)

1. 在 `ResilienceConfig` 中添加速率限制配置字段
2. 编写配置单元测试

### Step 2: 速率限制器集成 (1 天)

1. 修改 `server.py` 使用配置初始化速率限制器
2. 在 `query` 工具函数中添加速率限制检查
3. 在 `SQLGenerator` 中添加 LLM 速率限制
4. 编写速率限制集成测试

### Step 3: 指标收集集成 (1.5 天)

1. 创建指标装饰器 (`decorators.py`)
2. 修改 `Orchestrator` 添加指标记录
3. 修改 `SQLGenerator` 添加 LLM 指标
4. 修改 `SQLValidator` 添加拒绝指标
5. 修改 `server.py` 注入 metrics 到各服务
6. 编写指标集成测试

### Step 4: 追踪集成 (1 天)

1. 增强 `tracing.py` 添加 span 管理
2. 在 `Orchestrator` 中添加追踪 span
3. 在 `server.py` 中添加请求级追踪
4. 编写追踪集成测试

### Step 5: 文档与验证 (0.5 天)

1. 更新 README 说明新增的可观测性功能
2. 添加 Grafana dashboard 配置示例
3. 运行完整测试套件

---

## 监控 Dashboard 示例

### Prometheus 查询示例

```promql
# 查询 QPS
rate(pg_mcp_query_requests_total[5m])

# 查询延迟 P99
histogram_quantile(0.99, rate(pg_mcp_query_duration_seconds_bucket[5m]))

# LLM 调用成功率
sum(rate(pg_mcp_llm_calls_total{success="true"}[5m])) /
sum(rate(pg_mcp_llm_calls_total[5m]))

# SQL 拒绝率
rate(pg_mcp_sql_rejected_total[5m])
```

### Grafana Dashboard JSON

```json
{
  "title": "pg-mcp Observability",
  "panels": [
    {
      "title": "Query Rate",
      "type": "graph",
      "targets": [
        {"expr": "rate(pg_mcp_query_requests_total[5m])"}
      ]
    },
    {
      "title": "Query Latency",
      "type": "graph",
      "targets": [
        {"expr": "histogram_quantile(0.99, rate(pg_mcp_query_duration_seconds_bucket[5m]))"}
      ]
    },
    {
      "title": "LLM Token Usage",
      "type": "graph",
      "targets": [
        {"expr": "rate(pg_mcp_llm_tokens_used_total[5m])"}
      ]
    }
  ]
}
```

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 速率限制过严导致服务不可用 | 中 | 高 | 提供配置关闭开关 |
| 指标收集影响性能 | 低 | 中 | 使用异步指标记录 |
| 追踪 overhead | 低 | 低 | 可配置采样率 |
| 内存泄漏（追踪上下文） | 低 | 中 | 使用 contextvars 确保清理 |
