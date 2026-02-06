# 问题 1：多数据库与安全控制实施方案

## 问题概述

当前 pg-mcp 在多数据库支持和安全控制方面存在以下问题：

| 问题 | 位置 | 严重程度 |
|------|------|----------|
| 仅使用单一数据库执行器 | `server.py:197` | 高 |
| `allow_write_operations` 配置未使用 | `settings.py:79` | 高（安全） |
| `allow_explain` 硬编码为 False | `sql_validator.py` | 中 |
| 无法配置表/列访问限制 | 全局 | 中 |

## 现状分析

### 1.1 多数据库支持

**当前代码流程：**

```python
# pool.py - 支持多数据库的基础设施存在
async def create_pools(configs: list[DatabaseConfig]) -> dict[str, Pool]: ...

# server.py:99-102 - 但只创建单个数据库连接
pool = await create_pool(_settings.database)  # 只有一个！
_pools[_settings.database.name] = pool

# server.py:197 - 只使用主数据库的执行器
sql_executor=sql_executors[_settings.database.name],
```

### 1.2 安全控制配置

**未使用的配置：**

```python
# settings.py:79-81
allow_write_operations: bool = Field(
    default=False,
    description="Allow write operations (INSERT, UPDATE, DELETE)"
)
# 在整个项目中从未被检查！
```

**硬编码的 EXPLAIN 限制：**

```python
# server.py:157
sql_validator = SQLValidator(
    allow_explain=False,  # 硬编码，无法通过配置启用
    ...
)
```

---

## 实施方案

### Phase 1: 多数据库配置支持

#### 1.1 修改配置结构

**文件：** `src/pg_mcp/config/settings.py`

```python
# 新增：多数据库配置支持
class DatabasesConfig(BaseSettings):
    """Multiple database configurations."""

    model_config = SettingsConfigDict(env_prefix="DATABASES_", env_file=".env", extra="ignore")

    # 支持多个数据库配置，格式：db_name:host:port:dbname:user:password
    configs_str: str = Field(
        default="",
        alias="configs",
        description="Comma-separated database configurations"
    )

    # 默认数据库
    default_database: str = Field(
        default="",
        description="Default database name when not specified in query"
    )

    @property
    def databases(self) -> list[DatabaseConfig]:
        """Parse database configurations."""
        if not self.configs_str:
            return []

        configs = []
        for config_str in self.configs_str.split(";"):
            parts = config_str.strip().split(":")
            if len(parts) >= 5:
                configs.append(DatabaseConfig(
                    name=parts[0],
                    host=parts[1],
                    port=int(parts[2]),
                    name=parts[3],
                    user=parts[4],
                    password=parts[5] if len(parts) > 5 else ""
                ))
        return configs


class Settings(BaseSettings):
    # 保留单数据库配置（向后兼容）
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # 新增：多数据库配置
    databases: DatabasesConfig = Field(default_factory=DatabasesConfig)

    def get_all_database_configs(self) -> list[DatabaseConfig]:
        """Get all database configurations."""
        configs = self.databases.databases.copy()
        # 如果多数据库配置为空，使用单数据库配置
        if not configs:
            configs.append(self.database)
        return configs
```

#### 1.2 修改 Server 初始化

**文件：** `src/pg_mcp/server.py`

```python
# 修改 lifespan 函数
@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncGenerator[None, None]:
    global _pools, _orchestrator, _metrics, _rate_limiter

    try:
        _settings = get_settings()

        # 获取所有数据库配置
        db_configs = _settings.get_all_database_configs()

        # 为每个数据库创建连接池
        _pools = {}
        for config in db_configs:
            pool = await create_pool(config)
            _pools[config.name] = pool
            logger.info(f"Created pool for database: {config.name}")

        # 为每个数据库创建执行器
        sql_executors: dict[str, SQLExecutor] = {}
        for db_name, pool in _pools.items():
            executor = SQLExecutor(
                pool=pool,
                security_config=_settings.security,
            )
            sql_executors[db_name] = executor

        # 创建 Orchestrator，传入所有执行器
        _orchestrator = QueryOrchestrator(
            sql_generator=sql_generator,
            sql_validator=sql_validator,
            sql_executors=sql_executors,  # 传入所有执行器
            result_validator=result_validator,
            circuit_breaker=circuit_breaker,
            settings=_settings,
        )

        yield
    finally:
        # 关闭所有连接池
        for pool in _pools.values():
            await pool.close()
```

#### 1.3 修改 QueryOrchestrator

**文件：** `src/pg_mcp/services/orchestrator.py`

```python
class QueryOrchestrator:
    def __init__(
        self,
        sql_generator: SQLGenerator,
        sql_validator: SQLValidator,
        sql_executors: dict[str, SQLExecutor],  # 改为字典
        result_validator: ResultValidator,
        circuit_breaker: CircuitBreaker,
        settings: Settings,
    ):
        self._sql_executors = sql_executors
        self._default_database = settings.databases.default_database or next(iter(sql_executors))

    def _get_executor(self, database: str | None) -> SQLExecutor:
        """Get executor for specified database."""
        db_name = database or self._default_database
        if db_name not in self._sql_executors:
            raise DatabaseNotFoundError(f"Database '{db_name}' not configured")
        return self._sql_executors[db_name]

    async def execute_query(
        self,
        question: str,
        database: str | None = None,
        return_type: str = "result",
    ) -> QueryResponse:
        # 获取对应数据库的执行器
        executor = self._get_executor(database)

        # 使用该执行器执行查询
        ...
```

---

### Phase 2: 安全控制启用

#### 2.1 启用 allow_write_operations 检查

**文件：** `src/pg_mcp/services/sql_validator.py`

```python
class SQLValidator:
    def __init__(
        self,
        security_config: SecurityConfig,
        allow_explain: bool = False,
    ):
        self._security_config = security_config
        self._allow_explain = allow_explain
        # 新增：从配置读取是否允许写操作
        self._allow_write = security_config.allow_write_operations

    def validate(self, sql: str) -> ValidationResult:
        """Validate SQL statement."""
        # 检查是否是写操作
        if self._is_write_operation(sql) and not self._allow_write:
            return ValidationResult(
                is_valid=False,
                is_select=False,
                allows_data_modification=True,
                error_message="Write operations are not allowed",
                uses_blocked_functions=[],
            )

        # ... 其他验证逻辑

    def _is_write_operation(self, sql: str) -> bool:
        """Check if SQL is a write operation."""
        write_keywords = {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "MERGE"}
        try:
            parsed = sqlglot.parse_one(sql, dialect="postgres")
            return parsed.__class__.__name__.upper() in write_keywords
        except Exception:
            return False
```

#### 2.2 配置化 EXPLAIN 支持

**文件：** `src/pg_mcp/config/settings.py`

```python
class SecurityConfig(BaseSettings):
    # 新增：EXPLAIN 控制
    allow_explain: bool = Field(
        default=False,
        description="Allow EXPLAIN statements for query plan analysis"
    )
```

**文件：** `src/pg_mcp/server.py`

```python
# 修改 SQLValidator 初始化
sql_validator = SQLValidator(
    security_config=_settings.security,
    allow_explain=_settings.security.allow_explain,  # 从配置读取
)
```

#### 2.3 表/列访问控制

**新增文件：** `src/pg_mcp/security/access_control.py`

```python
"""Fine-grained access control for tables and columns."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class TableAccessRule(BaseModel):
    """Access rule for a table."""

    table_name: str
    schema_name: str = "public"
    access_type: Literal["allow", "deny"] = "allow"
    allowed_columns: list[str] | None = None  # None = all columns
    denied_columns: list[str] | None = None


class AccessControlConfig(BaseModel):
    """Access control configuration."""

    # 默认策略：allow 或 deny
    default_policy: Literal["allow", "deny"] = "allow"

    # 表规则
    table_rules: list[TableAccessRule] = Field(default_factory=list)

    def is_table_accessible(self, schema: str, table: str) -> bool:
        """Check if table is accessible."""
        for rule in self.table_rules:
            if rule.schema_name == schema and rule.table_name == table:
                return rule.access_type == "allow"
        return self.default_policy == "allow"

    def get_allowed_columns(self, schema: str, table: str) -> list[str] | None:
        """Get allowed columns for a table."""
        for rule in self.table_rules:
            if rule.schema_name == schema and rule.table_name == table:
                if rule.denied_columns:
                    # 返回所有列排除 denied_columns
                    return None  # 需要结合 schema 信息计算
                return rule.allowed_columns
        return None  # None = all columns allowed


class AccessControlValidator:
    """Validate SQL against access control rules."""

    def __init__(self, config: AccessControlConfig):
        self._config = config

    def validate_query(self, sql: str, schema_info: dict) -> tuple[bool, str | None]:
        """Validate query against access control rules.

        Returns:
            Tuple of (is_valid, error_message)
        """
        import sqlglot

        try:
            parsed = sqlglot.parse_one(sql, dialect="postgres")

            # 提取所有引用的表
            tables = self._extract_tables(parsed)

            for schema, table in tables:
                if not self._config.is_table_accessible(schema, table):
                    return False, f"Access denied to table: {schema}.{table}"

            # 提取所有引用的列
            columns = self._extract_columns(parsed, tables)

            for schema, table, column in columns:
                allowed = self._config.get_allowed_columns(schema, table)
                if allowed is not None and column not in allowed:
                    return False, f"Access denied to column: {schema}.{table}.{column}"

            return True, None

        except Exception as e:
            return False, f"Failed to parse query: {e}"

    def _extract_tables(self, parsed) -> list[tuple[str, str]]:
        """Extract all tables from parsed SQL."""
        tables = []
        for table in parsed.find_all(sqlglot.exp.Table):
            schema = table.db or "public"
            name = table.name
            tables.append((schema, name))
        return tables

    def _extract_columns(
        self, parsed, tables: list[tuple[str, str]]
    ) -> list[tuple[str, str, str]]:
        """Extract all columns from parsed SQL."""
        columns = []
        for col in parsed.find_all(sqlglot.exp.Column):
            # 简化处理：假设列属于第一个表
            if tables:
                schema, table = tables[0]
                columns.append((schema, table, col.name))
        return columns
```

---

## 验证方案

### 单元测试

**文件：** `tests/unit/test_multi_database.py`

```python
import pytest
from pg_mcp.config.settings import Settings, DatabaseConfig


class TestMultiDatabaseConfig:
    def test_single_database_backward_compat(self):
        """Test backward compatibility with single database config."""
        settings = Settings(
            database=DatabaseConfig(
                host="localhost",
                port=5432,
                name="testdb",
                user="testuser",
            )
        )
        configs = settings.get_all_database_configs()
        assert len(configs) == 1
        assert configs[0].name == "testdb"

    def test_multiple_databases_from_env(self, monkeypatch):
        """Test multiple database configuration from environment."""
        monkeypatch.setenv(
            "DATABASES_CONFIGS",
            "db1:host1:5432:db1:user1:pass1;db2:host2:5432:db2:user2:pass2"
        )
        settings = Settings()
        configs = settings.get_all_database_configs()
        assert len(configs) == 2
        assert configs[0].name == "db1"
        assert configs[1].name == "db2"


class TestAccessControl:
    def test_table_access_denied(self):
        """Test table access denial."""
        from pg_mcp.security.access_control import (
            AccessControlConfig, TableAccessRule, AccessControlValidator
        )

        config = AccessControlConfig(
            default_policy="allow",
            table_rules=[
                TableAccessRule(table_name="users", access_type="deny")
            ]
        )
        validator = AccessControlValidator(config)

        is_valid, error = validator.validate_query(
            "SELECT * FROM users", {}
        )
        assert not is_valid
        assert "Access denied" in error

    def test_column_access_denied(self):
        """Test column access denial."""
        config = AccessControlConfig(
            table_rules=[
                TableAccessRule(
                    table_name="users",
                    denied_columns=["password", "ssn"]
                )
            ]
        )
        validator = AccessControlValidator(config)

        is_valid, error = validator.validate_query(
            "SELECT password FROM users", {}
        )
        assert not is_valid
```

**文件：** `tests/unit/test_security_config.py`

```python
import pytest
from pg_mcp.services.sql_validator import SQLValidator
from pg_mcp.config.settings import SecurityConfig


class TestAllowWriteOperations:
    def test_write_blocked_by_default(self):
        """Test write operations are blocked by default."""
        config = SecurityConfig(allow_write_operations=False)
        validator = SQLValidator(security_config=config)

        result = validator.validate("INSERT INTO users VALUES (1, 'test')")
        assert not result.is_valid
        assert result.allows_data_modification

    def test_write_allowed_when_configured(self):
        """Test write operations allowed when configured."""
        config = SecurityConfig(allow_write_operations=True)
        validator = SQLValidator(security_config=config)

        result = validator.validate("INSERT INTO users VALUES (1, 'test')")
        assert result.is_valid  # 语法有效
        assert result.allows_data_modification  # 标记为写操作


class TestExplainConfig:
    def test_explain_blocked_by_default(self):
        """Test EXPLAIN blocked by default."""
        config = SecurityConfig(allow_explain=False)
        validator = SQLValidator(security_config=config, allow_explain=False)

        result = validator.validate("EXPLAIN SELECT * FROM users")
        assert not result.is_valid

    def test_explain_allowed_when_configured(self):
        """Test EXPLAIN allowed when configured."""
        config = SecurityConfig(allow_explain=True)
        validator = SQLValidator(security_config=config, allow_explain=True)

        result = validator.validate("EXPLAIN SELECT * FROM users")
        assert result.is_valid
```

### 集成测试

**文件：** `tests/integration/test_multi_database.py`

```python
import pytest
from pg_mcp.services.orchestrator import QueryOrchestrator


@pytest.mark.asyncio
class TestMultiDatabaseIntegration:
    async def test_query_specific_database(
        self, orchestrator: QueryOrchestrator
    ):
        """Test querying a specific database."""
        response = await orchestrator.execute_query(
            question="How many users?",
            database="blog_small",
        )
        assert response.success

    async def test_query_nonexistent_database(
        self, orchestrator: QueryOrchestrator
    ):
        """Test error when database not found."""
        with pytest.raises(DatabaseNotFoundError):
            await orchestrator.execute_query(
                question="How many users?",
                database="nonexistent_db",
            )

    async def test_default_database_used(
        self, orchestrator: QueryOrchestrator
    ):
        """Test default database used when not specified."""
        response = await orchestrator.execute_query(
            question="How many users?",
            database=None,  # Should use default
        )
        assert response.success
```

---

## 实施步骤

### Step 1: 配置层修改 (1-2 天)

1. 修改 `settings.py` 添加多数据库配置支持
2. 添加 `allow_explain` 配置字段
3. 编写配置层单元测试

### Step 2: 安全控制启用 (1-2 天)

1. 修改 `sql_validator.py` 使用 `allow_write_operations`
2. 修改 `sql_validator.py` 使用配置的 `allow_explain`
3. 创建 `access_control.py` 实现表/列访问控制
4. 编写安全控制单元测试

### Step 3: 服务层修改 (2-3 天)

1. 修改 `server.py` 支持多数据库初始化
2. 修改 `orchestrator.py` 支持多执行器
3. 添加数据库路由逻辑
4. 编写集成测试

### Step 4: 文档与验证 (1 天)

1. 更新 README 和配置文档
2. 运行完整测试套件
3. 人工验证多数据库场景

---

## 回滚方案

如果出现问题，可以通过以下方式回滚：

1. **配置回滚**：使用单数据库配置时，系统自动降级到单数据库模式
2. **代码回滚**：保留旧的代码路径，通过配置开关控制
3. **Git 回滚**：每个 Phase 单独提交，便于精确回滚

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 多数据库连接池资源耗尽 | 低 | 高 | 配置合理的 max_pool_size |
| 访问控制规则错误导致服务不可用 | 中 | 高 | 提供 default_policy=allow 作为安全网 |
| 配置解析错误 | 低 | 中 | 详细的配置验证和错误提示 |
