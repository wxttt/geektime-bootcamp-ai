# 问题 3：响应/模型缺陷及测试覆盖实施方案

## 问题概述

当前 pg-mcp 在响应模型和测试覆盖方面存在以下问题：

| 问题 | 位置 | 严重程度 |
|------|------|----------|
| `QueryResponse.to_dict()` 重复定义 | `query.py:160, 214` | 高（功能） |
| `allow_write_operations` 未使用 | `settings.py:79` | 高（安全） |
| 缺少集成测试验证实际请求流 | `tests/` | 中 |
| 组件测试存在但不验证集成 | `tests/unit/` | 中 |

## 现状分析

### 3.1 重复的 to_dict() 方法

**文件：** `src/pg_mcp/models/query.py`

```python
# 第 160-173 行 - 第一个版本
class QueryResponse(BaseModel):
    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary for MCP tool return."""
        result = self.model_dump(exclude_none=False)
        if result.get("tokens_used") is None:
            result["tokens_used"] = 0
        return result

# 第 214-220 行 - 第二个版本（覆盖了第一个！）
class QueryResponse(BaseModel):
    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)  # 不同的行为！
```

**问题分析：**
- Python 类定义中后面的方法会覆盖前面的方法
- 第一个版本：`exclude_none=False`，确保 `tokens_used=0`
- 第二个版本：`exclude_none=True`，可能排除必要字段
- 实际使用的是第二个版本，可能导致响应格式不一致

### 3.2 未使用的配置字段

**搜索 `allow_write_operations` 的使用：**

```bash
$ grep -r "allow_write_operations" src/pg_mcp/
# 仅在 settings.py 中定义，无其他使用
```

### 3.3 测试覆盖情况

**现有测试统计：**

| 测试文件 | 行数 | 覆盖模块 |
|----------|------|----------|
| `test_config.py` | 422 | 配置验证 |
| `test_models.py` | 445 | 数据模型 |
| `test_orchestrator.py` | 769 | 编排器 |
| `test_resilience.py` | 563 | 断路器/速率限制 |
| `test_schema_cache.py` | 358 | Schema 缓存 |
| `test_sql_executor.py` | 616 | SQL 执行 |
| `test_sql_generator.py` | 547 | SQL 生成 |
| `test_sql_validator.py` | 661 | SQL 验证 |

**缺失的测试：**
- 端到端集成测试
- Rate Limiter 在实际请求中的集成测试
- Metrics 收集集成测试
- Tracing 集成测试
- 多数据库场景测试

---

## 实施方案

### Phase 1: 修复模型缺陷

#### 1.1 修复重复的 to_dict() 方法

**文件：** `src/pg_mcp/models/query.py`

首先，让我们读取当前文件并修复：

```python
# 修复后的 QueryResponse 类

class QueryResponse(BaseModel):
    """Response from query execution."""

    success: bool = Field(..., description="Whether the query succeeded")
    generated_sql: str | None = Field(
        default=None, description="Generated SQL query"
    )
    validation: ValidationResult | None = Field(
        default=None, description="SQL validation result"
    )
    data: QueryResult | None = Field(
        default=None, description="Query result data"
    )
    error: ErrorDetail | None = Field(
        default=None, description="Error details if query failed"
    )
    confidence: int = Field(
        default=0, ge=0, le=100, description="Confidence score 0-100"
    )
    tokens_used: int = Field(
        default=0, ge=0, description="Number of LLM tokens used"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary for MCP tool return.

        This method ensures consistent response format:
        - All fields are included (exclude_none=False)
        - tokens_used defaults to 0 if not set
        - Nested models are converted to dicts
        """
        result = self.model_dump(exclude_none=False)

        # 确保 tokens_used 总是存在且为整数
        if result.get("tokens_used") is None:
            result["tokens_used"] = 0

        return result

    # 删除第二个 to_dict() 定义！
```

#### 1.2 清理其他冗余代码

检查其他模型是否有类似问题：

**文件：** `src/pg_mcp/models/query.py`

```python
# QueryResult.to_dict() - 保留，简化实现
class QueryResult(BaseModel):
    """Query execution result."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0)
    execution_time_ms: float = Field(default=0.0)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary."""
        return self.model_dump()

    # 保留 Pydantic 的 model_dump，to_dict 只是别名
```

#### 1.3 添加模型一致性检查

**新增文件：** `src/pg_mcp/models/validators.py`

```python
"""Model validators and consistency checks."""

from typing import Any

from pg_mcp.models.query import QueryResponse


def validate_response_format(response: QueryResponse) -> tuple[bool, list[str]]:
    """Validate response format consistency.

    Returns:
        Tuple of (is_valid, list of errors)
    """
    errors = []

    result = response.to_dict()

    # 必须字段检查
    required_fields = ["success", "confidence", "tokens_used"]
    for field in required_fields:
        if field not in result:
            errors.append(f"Missing required field: {field}")

    # 类型检查
    if not isinstance(result.get("success"), bool):
        errors.append("Field 'success' must be boolean")

    if not isinstance(result.get("confidence"), int):
        errors.append("Field 'confidence' must be integer")

    if not isinstance(result.get("tokens_used"), int):
        errors.append("Field 'tokens_used' must be integer")

    # 成功/失败一致性
    if result.get("success") and result.get("error"):
        errors.append("Cannot have both success=True and error set")

    if not result.get("success") and not result.get("error"):
        errors.append("Failed response must include error details")

    return len(errors) == 0, errors
```

---

### Phase 2: 启用未使用的配置

#### 2.1 使用 allow_write_operations

（此部分已在问题 1 文档中详细说明，这里提供快速参考）

**文件：** `src/pg_mcp/services/sql_validator.py`

```python
class SQLValidator:
    def __init__(
        self,
        security_config: SecurityConfig,
        allow_explain: bool = False,
    ):
        self._allow_write = security_config.allow_write_operations

    def validate(self, sql: str) -> ValidationResult:
        # 检查写操作
        if self._is_data_modification(sql) and not self._allow_write:
            return ValidationResult(
                is_valid=False,
                is_select=False,
                allows_data_modification=True,
                error_message="Write operations are disabled by configuration",
                uses_blocked_functions=[],
            )

        # ... 其他验证
```

#### 2.2 审计所有配置字段使用情况

**创建配置审计脚本：** `scripts/audit_config_usage.py`

```python
#!/usr/bin/env python3
"""Audit configuration field usage across the codebase."""

import ast
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ConfigField:
    name: str
    config_class: str
    file: str
    line: int
    used_in: list[str]


def find_config_fields(settings_file: Path) -> list[ConfigField]:
    """Extract all config fields from settings.py."""
    with open(settings_file) as f:
        tree = ast.parse(f.read())

    fields = []
    current_class = None

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            current_class = node.name
        elif isinstance(node, ast.AnnAssign) and current_class:
            if isinstance(node.target, ast.Name):
                fields.append(ConfigField(
                    name=node.target.id,
                    config_class=current_class,
                    file=str(settings_file),
                    line=node.lineno,
                    used_in=[],
                ))

    return fields


def find_field_usage(field: ConfigField, src_dir: Path) -> list[str]:
    """Find where a config field is used."""
    usages = []

    for py_file in src_dir.rglob("*.py"):
        if "settings.py" in str(py_file):
            continue

        with open(py_file) as f:
            content = f.read()

        if field.name in content:
            usages.append(str(py_file))

    return usages


def main():
    src_dir = Path("src/pg_mcp")
    settings_file = src_dir / "config" / "settings.py"

    fields = find_config_fields(settings_file)

    print("Configuration Field Usage Audit")
    print("=" * 60)

    unused = []
    for field in fields:
        usages = find_field_usage(field, src_dir)
        field.used_in = usages

        if not usages:
            unused.append(field)
            status = "❌ UNUSED"
        else:
            status = f"✓ Used in {len(usages)} files"

        print(f"{field.config_class}.{field.name}: {status}")

    if unused:
        print("\n⚠️  Unused Configuration Fields:")
        for field in unused:
            print(f"  - {field.config_class}.{field.name} (line {field.line})")


if __name__ == "__main__":
    main()
```

---

### Phase 3: 测试覆盖增强

#### 3.1 添加模型测试

**文件：** `tests/unit/test_query_response.py`

```python
"""Tests for QueryResponse model."""

import pytest

from pg_mcp.models.query import (
    QueryResponse,
    QueryResult,
    ValidationResult,
    ErrorDetail,
)
from pg_mcp.models.validators import validate_response_format


class TestQueryResponseToDict:
    def test_to_dict_includes_all_fields(self):
        """Test to_dict includes all required fields."""
        response = QueryResponse(
            success=True,
            generated_sql="SELECT 1",
            confidence=90,
            tokens_used=100,
        )

        result = response.to_dict()

        assert "success" in result
        assert "generated_sql" in result
        assert "confidence" in result
        assert "tokens_used" in result
        assert "validation" in result
        assert "data" in result
        assert "error" in result

    def test_to_dict_tokens_used_default(self):
        """Test tokens_used defaults to 0."""
        response = QueryResponse(success=True)

        result = response.to_dict()

        assert result["tokens_used"] == 0

    def test_to_dict_none_fields_included(self):
        """Test None fields are included (not excluded)."""
        response = QueryResponse(success=True)

        result = response.to_dict()

        # None 字段应该存在
        assert "generated_sql" in result
        assert result["generated_sql"] is None

    def test_to_dict_nested_models(self):
        """Test nested models are converted to dicts."""
        response = QueryResponse(
            success=True,
            data=QueryResult(
                columns=["id", "name"],
                rows=[{"id": 1, "name": "test"}],
                row_count=1,
                execution_time_ms=10.5,
            ),
            validation=ValidationResult(
                is_valid=True,
                is_select=True,
                allows_data_modification=False,
                uses_blocked_functions=[],
            ),
        )

        result = response.to_dict()

        assert isinstance(result["data"], dict)
        assert result["data"]["columns"] == ["id", "name"]
        assert isinstance(result["validation"], dict)
        assert result["validation"]["is_valid"] is True


class TestResponseFormatValidation:
    def test_valid_success_response(self):
        """Test valid success response passes validation."""
        response = QueryResponse(
            success=True,
            generated_sql="SELECT 1",
            confidence=90,
            tokens_used=50,
        )

        is_valid, errors = validate_response_format(response)

        assert is_valid
        assert len(errors) == 0

    def test_valid_error_response(self):
        """Test valid error response passes validation."""
        response = QueryResponse(
            success=False,
            error=ErrorDetail(
                code="sql_error",
                message="Invalid SQL",
            ),
            confidence=0,
            tokens_used=0,
        )

        is_valid, errors = validate_response_format(response)

        assert is_valid
        assert len(errors) == 0

    def test_invalid_success_with_error(self):
        """Test success=True with error is invalid."""
        response = QueryResponse(
            success=True,
            error=ErrorDetail(code="error", message="oops"),
            confidence=90,
        )

        is_valid, errors = validate_response_format(response)

        assert not is_valid
        assert "Cannot have both success=True and error set" in errors

    def test_invalid_failure_without_error(self):
        """Test success=False without error is invalid."""
        response = QueryResponse(
            success=False,
            confidence=0,
        )

        is_valid, errors = validate_response_format(response)

        assert not is_valid
        assert "Failed response must include error details" in errors
```

#### 3.2 添加集成测试

**文件：** `tests/integration/test_end_to_end.py`

```python
"""End-to-end integration tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pg_mcp.config.settings import Settings, get_settings
from pg_mcp.services.orchestrator import QueryOrchestrator
from pg_mcp.models.query import QueryResponse


@pytest.fixture
def mock_openai_response():
    """Mock OpenAI API response."""
    return MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content="```sql\nSELECT COUNT(*) FROM users;\n```"
                )
            )
        ],
        usage=MagicMock(total_tokens=50),
    )


@pytest.fixture
def mock_db_result():
    """Mock database query result."""
    return [{"count": 42}]


@pytest.mark.asyncio
class TestEndToEndQuery:
    async def test_successful_query_flow(
        self,
        orchestrator: QueryOrchestrator,
        mock_openai_response,
        mock_db_result,
    ):
        """Test complete successful query flow."""
        with patch.object(
            orchestrator._sql_generator.client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            with patch.object(
                orchestrator._sql_executor,
                "execute",
                new_callable=AsyncMock,
                return_value=mock_db_result,
            ):
                response = await orchestrator.execute_query(
                    question="How many users are there?",
                    database="test_db",
                    return_type="result",
                )

        assert response.success is True
        assert response.generated_sql is not None
        assert "SELECT" in response.generated_sql
        assert response.data is not None
        assert response.data.row_count == 1
        assert response.confidence > 0
        assert response.tokens_used >= 0

    async def test_query_with_sql_validation_failure(
        self,
        orchestrator: QueryOrchestrator,
    ):
        """Test query flow when SQL validation fails."""
        with patch.object(
            orchestrator._sql_generator,
            "generate",
            new_callable=AsyncMock,
            return_value="DROP TABLE users;",  # 危险 SQL
        ):
            response = await orchestrator.execute_query(
                question="Delete all users",
                database="test_db",
            )

        assert response.success is False
        assert response.error is not None
        assert "not allowed" in response.error.message.lower()

    async def test_query_with_llm_failure(
        self,
        orchestrator: QueryOrchestrator,
    ):
        """Test query flow when LLM call fails."""
        with patch.object(
            orchestrator._sql_generator.client.chat.completions,
            "create",
            new_callable=AsyncMock,
            side_effect=Exception("API Error"),
        ):
            response = await orchestrator.execute_query(
                question="Count users",
                database="test_db",
            )

        assert response.success is False
        assert response.error is not None

    async def test_query_with_db_execution_failure(
        self,
        orchestrator: QueryOrchestrator,
        mock_openai_response,
    ):
        """Test query flow when database execution fails."""
        with patch.object(
            orchestrator._sql_generator.client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            with patch.object(
                orchestrator._sql_executor,
                "execute",
                new_callable=AsyncMock,
                side_effect=Exception("Database connection failed"),
            ):
                response = await orchestrator.execute_query(
                    question="Count users",
                    database="test_db",
                )

        assert response.success is False
        assert response.error is not None


@pytest.mark.asyncio
class TestRateLimitingIntegration:
    async def test_rate_limiting_applied(
        self,
        orchestrator_with_rate_limiter: QueryOrchestrator,
    ):
        """Test rate limiting is applied to queries."""
        # 发送多个请求直到触发限制
        responses = []
        for i in range(15):
            try:
                response = await orchestrator_with_rate_limiter.execute_query(
                    question=f"Query {i}",
                    database="test_db",
                )
                responses.append(response)
            except Exception as e:
                responses.append(e)

        # 至少有一些请求应该被限制
        rate_limited = [
            r for r in responses
            if isinstance(r, QueryResponse) and r.error
            and r.error.code == "rate_limit_exceeded"
        ]
        assert len(rate_limited) > 0


@pytest.mark.asyncio
class TestMetricsIntegration:
    async def test_metrics_recorded_on_success(
        self,
        orchestrator_with_metrics: QueryOrchestrator,
        metrics_collector,
        mock_openai_response,
        mock_db_result,
    ):
        """Test metrics are recorded on successful query."""
        with patch.object(
            orchestrator_with_metrics._sql_generator.client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_openai_response,
        ):
            with patch.object(
                orchestrator_with_metrics._sql_executor,
                "execute",
                new_callable=AsyncMock,
                return_value=mock_db_result,
            ):
                await orchestrator_with_metrics.execute_query(
                    question="Count users",
                    database="test_db",
                )

        # 验证指标被记录
        # 这需要检查 Prometheus registry 或 mock metrics collector
        assert metrics_collector.query_requests._value.get(
            ("test_db", "true")
        ) == 1

    async def test_metrics_recorded_on_failure(
        self,
        orchestrator_with_metrics: QueryOrchestrator,
        metrics_collector,
    ):
        """Test metrics are recorded on failed query."""
        with patch.object(
            orchestrator_with_metrics._sql_generator,
            "generate",
            new_callable=AsyncMock,
            side_effect=Exception("LLM Error"),
        ):
            await orchestrator_with_metrics.execute_query(
                question="Count users",
                database="test_db",
            )

        assert metrics_collector.query_requests._value.get(
            ("test_db", "false")
        ) == 1
```

#### 3.3 添加安全测试

**文件：** `tests/security/test_sql_injection.py`

```python
"""SQL injection security tests."""

import pytest

from pg_mcp.services.sql_validator import SQLValidator
from pg_mcp.config.settings import SecurityConfig


class TestSQLInjectionPrevention:
    @pytest.fixture
    def validator(self):
        return SQLValidator(security_config=SecurityConfig())

    @pytest.mark.parametrize("malicious_sql", [
        "SELECT * FROM users; DROP TABLE users;--",
        "SELECT * FROM users WHERE id = 1 OR 1=1",
        "SELECT * FROM users UNION SELECT * FROM passwords",
        "SELECT * FROM users; DELETE FROM users WHERE 1=1;",
        "SELECT * FROM users'; DROP TABLE users;--",
        "SELECT * FROM users WHERE name = '' OR ''='",
        "SELECT * FROM pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(100)",
        "SELECT * FROM users; COPY users TO '/tmp/data';",
    ])
    def test_blocks_sql_injection_attempts(self, validator, malicious_sql):
        """Test various SQL injection attempts are blocked."""
        result = validator.validate(malicious_sql)

        # 至少一个检查应该失败
        assert (
            not result.is_valid
            or result.allows_data_modification
            or len(result.uses_blocked_functions) > 0
        ), f"SQL injection not blocked: {malicious_sql}"


class TestWriteOperationBlocking:
    @pytest.fixture
    def readonly_validator(self):
        return SQLValidator(
            security_config=SecurityConfig(allow_write_operations=False)
        )

    @pytest.fixture
    def write_validator(self):
        return SQLValidator(
            security_config=SecurityConfig(allow_write_operations=True)
        )

    @pytest.mark.parametrize("write_sql", [
        "INSERT INTO users VALUES (1, 'test')",
        "UPDATE users SET name = 'hacked' WHERE 1=1",
        "DELETE FROM users WHERE id = 1",
        "TRUNCATE TABLE users",
        "DROP TABLE users",
        "CREATE TABLE evil (id INT)",
        "ALTER TABLE users ADD COLUMN pwned BOOLEAN",
    ])
    def test_blocks_write_operations_when_disabled(
        self, readonly_validator, write_sql
    ):
        """Test write operations are blocked when disabled."""
        result = readonly_validator.validate(write_sql)

        assert not result.is_valid or result.allows_data_modification

    @pytest.mark.parametrize("read_sql", [
        "SELECT * FROM users",
        "SELECT COUNT(*) FROM users WHERE status = 'active'",
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "SELECT u.name, COUNT(o.id) FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY u.id",
    ])
    def test_allows_read_operations(self, readonly_validator, read_sql):
        """Test read operations are allowed."""
        result = readonly_validator.validate(read_sql)

        assert result.is_valid
        assert not result.allows_data_modification


class TestBlockedFunctions:
    @pytest.fixture
    def validator(self):
        return SQLValidator(
            security_config=SecurityConfig(
                blocked_functions_str="pg_sleep,pg_read_file,pg_write_file,lo_import,lo_export"
            )
        )

    @pytest.mark.parametrize("sql,blocked_func", [
        ("SELECT pg_sleep(100)", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT pg_write_file('/tmp/evil', 'data')", "pg_write_file"),
        ("SELECT lo_import('/etc/passwd')", "lo_import"),
        ("SELECT lo_export(12345, '/tmp/data')", "lo_export"),
    ])
    def test_blocks_dangerous_functions(self, validator, sql, blocked_func):
        """Test dangerous PostgreSQL functions are blocked."""
        result = validator.validate(sql)

        assert not result.is_valid or blocked_func in result.uses_blocked_functions
```

#### 3.4 测试配置 Fixtures

**文件：** `tests/conftest.py`

```python
"""Shared test fixtures."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from pg_mcp.config.settings import (
    Settings,
    DatabaseConfig,
    OpenAIConfig,
    SecurityConfig,
    ResilienceConfig,
    ObservabilityConfig,
)
from pg_mcp.services.orchestrator import QueryOrchestrator
from pg_mcp.services.sql_generator import SQLGenerator
from pg_mcp.services.sql_validator import SQLValidator
from pg_mcp.services.sql_executor import SQLExecutor
from pg_mcp.services.result_validator import ResultValidator
from pg_mcp.resilience.circuit_breaker import CircuitBreaker
from pg_mcp.resilience.rate_limiter import MultiRateLimiter
from pg_mcp.observability.metrics import MetricsCollector


@pytest.fixture
def test_settings():
    """Create test settings."""
    return Settings(
        database=DatabaseConfig(
            host="localhost",
            port=5432,
            name="test_db",
            user="test_user",
            password="test_pass",
        ),
        openai=OpenAIConfig(
            api_key="sk-test-key",
            model="gpt-4o-mini",
            max_tokens=1000,
        ),
        security=SecurityConfig(
            allow_write_operations=False,
            allow_explain=False,
        ),
        resilience=ResilienceConfig(
            rate_limit_enabled=True,
            rate_limit_queries_per_second=10,
            rate_limit_llm_calls_per_second=5,
        ),
    )


@pytest.fixture
def mock_pool():
    """Create mock database pool."""
    pool = MagicMock()
    pool.acquire = AsyncMock()
    pool.release = AsyncMock()
    return pool


@pytest.fixture
def sql_generator(test_settings):
    """Create SQL generator with mocked client."""
    generator = SQLGenerator(config=test_settings.openai)
    generator.client = MagicMock()
    generator.client.chat.completions.create = AsyncMock()
    return generator


@pytest.fixture
def sql_validator(test_settings):
    """Create SQL validator."""
    return SQLValidator(security_config=test_settings.security)


@pytest.fixture
def sql_executor(mock_pool, test_settings):
    """Create SQL executor with mock pool."""
    return SQLExecutor(
        pool=mock_pool,
        security_config=test_settings.security,
    )


@pytest.fixture
def result_validator(test_settings):
    """Create result validator."""
    validator = ResultValidator(
        openai_config=test_settings.openai,
        validation_config=test_settings.validation,
    )
    validator.client = MagicMock()
    validator.client.chat.completions.create = AsyncMock()
    return validator


@pytest.fixture
def circuit_breaker():
    """Create circuit breaker."""
    return CircuitBreaker(failure_threshold=5, recovery_timeout=60)


@pytest.fixture
def rate_limiter():
    """Create rate limiter."""
    return MultiRateLimiter(query_limit=10, llm_limit=5)


@pytest.fixture
def metrics_collector():
    """Create metrics collector."""
    return MetricsCollector()


@pytest.fixture
def orchestrator(
    sql_generator,
    sql_validator,
    sql_executor,
    result_validator,
    circuit_breaker,
    test_settings,
):
    """Create orchestrator with all dependencies."""
    return QueryOrchestrator(
        sql_generator=sql_generator,
        sql_validator=sql_validator,
        sql_executor=sql_executor,
        result_validator=result_validator,
        circuit_breaker=circuit_breaker,
        settings=test_settings,
    )


@pytest.fixture
def orchestrator_with_rate_limiter(
    orchestrator,
    rate_limiter,
):
    """Create orchestrator with rate limiter."""
    orchestrator._rate_limiter = rate_limiter
    return orchestrator


@pytest.fixture
def orchestrator_with_metrics(
    orchestrator,
    metrics_collector,
):
    """Create orchestrator with metrics."""
    orchestrator._metrics = metrics_collector
    return orchestrator
```

---

## 验证方案

### 运行测试

```bash
# 运行所有测试
uv run pytest

# 运行带覆盖率
uv run pytest --cov=src/pg_mcp --cov-report=html --cov-fail-under=80

# 只运行模型测试
uv run pytest tests/unit/test_query_response.py -v

# 只运行安全测试
uv run pytest tests/security/ -v

# 只运行集成测试
uv run pytest tests/integration/ -v
```

### 覆盖率目标

| 模块 | 目标覆盖率 | 优先级 |
|------|-----------|--------|
| `models/query.py` | 95%+ | 高 |
| `services/sql_validator.py` | 95%+ | 高（安全） |
| `services/orchestrator.py` | 90%+ | 高 |
| `services/sql_generator.py` | 85%+ | 中 |
| `services/sql_executor.py` | 85%+ | 中 |
| `resilience/` | 80%+ | 中 |
| `observability/` | 75%+ | 低 |

---

## 实施步骤

### Step 1: 修复模型缺陷 (0.5 天)

1. 删除 `query.py` 中重复的 `to_dict()` 方法
2. 统一 `to_dict()` 行为
3. 添加模型验证器
4. 编写模型单元测试

### Step 2: 配置字段审计 (0.5 天)

1. 运行配置审计脚本
2. 在 `sql_validator.py` 中使用 `allow_write_operations`
3. 移除或记录真正未使用的配置字段

### Step 3: 集成测试 (1.5 天)

1. 创建集成测试 fixtures
2. 编写端到端查询流程测试
3. 编写速率限制集成测试
4. 编写指标收集集成测试

### Step 4: 安全测试 (1 天)

1. 编写 SQL 注入测试
2. 编写写操作阻止测试
3. 编写危险函数阻止测试

### Step 5: 覆盖率提升 (1 天)

1. 分析当前覆盖率报告
2. 补充缺失的测试用例
3. 确保达到目标覆盖率

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 修复 to_dict 导致 API 变化 | 低 | 中 | 确保返回格式向后兼容 |
| 新测试发现更多 bug | 中 | 低 | 这是好事，按优先级修复 |
| 覆盖率目标过高 | 低 | 低 | 可适当调整非关键模块目标 |
