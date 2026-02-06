# 问题 1 扩展：多数据库配置与智能选择方案

## 背景

问题 1 的 Phase 1 已完成 Orchestrator 层的多数据库执行器支持，但存在两个待解决问题：

1. **配置方式不友好**：当前只能配置单数据库，`DATABASE_2_HOST` 这种命名方式不够优雅
2. **数据库选择问题**：用户用自然语言提问时，系统如何知道选择哪个数据库？

## 方案设计

### 1. 配置方式改进

#### 1.1 JSON 格式的多数据库配置

**环境变量：**

```bash
# .env 文件
PG_DATABASES='{
  "blog_small": {
    "host": "/tmp",
    "port": 5432,
    "user": "danny",
    "password": "",
    "description": "博客系统数据库，包含文章(posts)、评论(comments)、用户(users)、标签(tags)表"
  },
  "ecommerce_medium": {
    "host": "/tmp",
    "port": 5432,
    "user": "danny",
    "password": "",
    "description": "电商平台数据库，包含商品(products)、订单(orders)、客户(customers)、库存(inventory)表"
  },
  "saas_crm_large": {
    "host": "/tmp",
    "port": 5432,
    "user": "danny",
    "password": "",
    "description": "CRM系统数据库，包含联系人(contacts)、公司(companies)、交易(deals)、活动(activities)表"
  }
}'

# 默认数据库（可选，不指定则需要智能选择或用户指定）
PG_DEFAULT_DATABASE=blog_small

# OpenAI 配置
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

**.mcp.json 配置示例：**

```json
{
  "mcpServers": {
    "pg-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/pg-mcp",
        "run",
        "-m",
        "pg_mcp"
      ],
      "env": {
        "OPENAI_API_KEY": "sk-xxx",
        "OPENAI_BASE_URL": "https://cfcus02.opapi.win/v1",
        "OPENAI_MODEL": "gpt-4o-mini",
        "PG_DATABASES": "{\"blog_small\":{\"host\":\"/tmp\",\"user\":\"danny\",\"description\":\"博客系统，包含文章、评论、用户表\"},\"ecommerce_medium\":{\"host\":\"/tmp\",\"user\":\"danny\",\"description\":\"电商系统，包含商品、订单、客户表\"}}",
        "PG_DEFAULT_DATABASE": "blog_small"
      }
    }
  }
}
```

#### 1.2 数据模型定义

**文件：** `src/pg_mcp/config/settings.py`

```python
from typing import Annotated
import json

class DatabaseConfigItem(BaseModel):
    """单个数据库配置项"""
    host: str = Field(default="localhost")
    port: int = Field(default=5432, ge=1, le=65535)
    name: str | None = Field(default=None, description="数据库名，默认使用 key 作为数据库名")
    user: str = Field(default="postgres")
    password: str = Field(default="")
    description: str = Field(default="", description="数据库描述，用于智能选择")

    # 连接池配置
    min_pool_size: int = Field(default=2, ge=1, le=50)
    max_pool_size: int = Field(default=10, ge=1, le=100)

    def get_dsn(self, db_name: str) -> str:
        """生成 DSN 连接字符串"""
        actual_name = self.name or db_name
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{actual_name}"


class MultiDatabaseConfig(BaseSettings):
    """多数据库配置"""

    model_config = SettingsConfigDict(env_prefix="PG_", env_file=".env", extra="ignore")

    databases_json: str = Field(
        default="{}",
        alias="databases",
        description="JSON 格式的多数据库配置"
    )
    default_database: str | None = Field(
        default=None,
        description="默认数据库名称"
    )
    auto_select_enabled: bool = Field(
        default=True,
        description="启用基于问题的智能数据库选择"
    )

    @property
    def databases(self) -> dict[str, DatabaseConfigItem]:
        """解析 JSON 配置为数据库字典"""
        try:
            raw = json.loads(self.databases_json)
            return {
                name: DatabaseConfigItem(**config)
                for name, config in raw.items()
            }
        except json.JSONDecodeError:
            return {}

    @property
    def database_descriptions(self) -> dict[str, str]:
        """获取所有数据库的描述，用于智能选择"""
        return {
            name: config.description
            for name, config in self.databases.items()
            if config.description
        }
```

---

### 2. 智能数据库选择

#### 2.1 选择流程

```
用户问题: "显示所有已发布的文章"
                ↓
    ┌──────────────────────────────────┐
    │  Step 1: 检查是否指定数据库      │
    │  - 用户指定 database 参数？      │
    │  - 问题中提到数据库名？          │
    └──────────────────────────────────┘
                ↓ 未指定
    ┌──────────────────────────────────┐
    │  Step 2: 检查默认数据库          │
    │  - 只有一个数据库？→ 直接使用    │
    │  - 配置了 default_database？     │
    │  - auto_select_enabled=false？   │
    └──────────────────────────────────┘
                ↓ 需要智能选择
    ┌──────────────────────────────────┐
    │  Step 3: LLM 智能选择            │
    │  输入: 问题 + 各数据库 description│
    │  输出: 最匹配的数据库名          │
    └──────────────────────────────────┘
                ↓
    ┌──────────────────────────────────┐
    │  Step 4: 生成并执行 SQL          │
    │  使用选中数据库的 schema         │
    └──────────────────────────────────┘
```

#### 2.2 数据库选择器实现

**新增文件：** `src/pg_mcp/services/database_selector.py`

```python
"""Database selector for intelligent database routing.

This module provides LLM-based database selection based on user questions
and database descriptions.
"""

import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from pg_mcp.config.settings import OpenAIConfig

logger = logging.getLogger(__name__)


@dataclass
class SelectionResult:
    """Result of database selection."""
    database: str
    confidence: float
    reason: str


class DatabaseSelector:
    """Selects appropriate database based on user question and database descriptions.

    This class uses LLM to analyze the user's question and match it against
    database descriptions to select the most appropriate database.

    Example:
        >>> selector = DatabaseSelector(openai_config)
        >>> result = await selector.select(
        ...     question="显示所有已发布的文章",
        ...     database_descriptions={
        ...         "blog_small": "博客系统，包含文章、评论、用户表",
        ...         "ecommerce_medium": "电商系统，包含商品、订单、客户表"
        ...     }
        ... )
        >>> print(result.database)  # "blog_small"
    """

    SYSTEM_PROMPT = """你是一个数据库路由助手。根据用户的问题和可用数据库的描述，选择最合适的数据库。

可用数据库:
{databases}

请分析用户问题，选择最匹配的数据库。返回 JSON 格式：
{{
    "database": "选中的数据库名",
    "confidence": 0.0-1.0 之间的置信度,
    "reason": "选择原因的简短说明"
}}

注意：
- 如果问题明确提到某个数据库或其相关内容，选择该数据库
- 如果问题模糊，选择最可能包含相关数据的数据库
- 如果完全无法确定，confidence 应该很低"""

    def __init__(self, config: OpenAIConfig) -> None:
        """Initialize database selector.

        Args:
            config: OpenAI configuration for LLM calls.
        """
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            timeout=config.timeout,
        )

    async def select(
        self,
        question: str,
        database_descriptions: dict[str, str],
    ) -> SelectionResult:
        """Select the most appropriate database for a question.

        Args:
            question: User's natural language question.
            database_descriptions: Dict mapping database names to descriptions.

        Returns:
            SelectionResult: The selected database with confidence and reason.

        Raises:
            ValueError: If no databases are provided.
            LLMError: If LLM call fails.
        """
        if not database_descriptions:
            raise ValueError("No databases available for selection")

        # 如果只有一个数据库，直接返回
        if len(database_descriptions) == 1:
            db_name = next(iter(database_descriptions.keys()))
            return SelectionResult(
                database=db_name,
                confidence=1.0,
                reason="Only one database available"
            )

        # 构建数据库描述文本
        db_text = "\n".join(
            f"- {name}: {desc}"
            for name, desc in database_descriptions.items()
        )

        system_prompt = self.SYSTEM_PROMPT.format(databases=db_text)

        try:
            response = await self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"用户问题: {question}"},
                ],
                temperature=0.0,
                max_tokens=200,
            )

            content = response.choices[0].message.content
            result = self._parse_response(content, database_descriptions)

            logger.info(
                "Database selected",
                extra={
                    "question": question[:100],
                    "selected_database": result.database,
                    "confidence": result.confidence,
                    "reason": result.reason,
                },
            )

            return result

        except Exception as e:
            logger.warning(f"Database selection failed: {e}, using first database")
            # 回退：使用第一个数据库
            db_name = next(iter(database_descriptions.keys()))
            return SelectionResult(
                database=db_name,
                confidence=0.5,
                reason=f"Selection failed, fallback to first database: {e}"
            )

    def _parse_response(
        self,
        content: str,
        available_databases: dict[str, str],
    ) -> SelectionResult:
        """Parse LLM response to SelectionResult.

        Args:
            content: Raw LLM response content.
            available_databases: Available database names for validation.

        Returns:
            SelectionResult: Parsed selection result.
        """
        try:
            # 尝试提取 JSON
            import re
            json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())

                database = data.get("database", "")
                # 验证数据库名是否有效
                if database not in available_databases:
                    # 尝试模糊匹配
                    for db_name in available_databases:
                        if db_name.lower() in database.lower() or database.lower() in db_name.lower():
                            database = db_name
                            break
                    else:
                        database = next(iter(available_databases.keys()))

                return SelectionResult(
                    database=database,
                    confidence=float(data.get("confidence", 0.8)),
                    reason=data.get("reason", "Selected by LLM"),
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        # 解析失败，使用第一个数据库
        return SelectionResult(
            database=next(iter(available_databases.keys())),
            confidence=0.5,
            reason="Failed to parse LLM response",
        )
```

#### 2.3 集成到 Orchestrator

**修改：** `src/pg_mcp/services/orchestrator.py`

```python
from pg_mcp.services.database_selector import DatabaseSelector, SelectionResult


class QueryOrchestrator:
    def __init__(
        self,
        # ... 现有参数
        database_selector: DatabaseSelector | None = None,
        database_descriptions: dict[str, str] | None = None,
    ) -> None:
        # ... 现有初始化
        self.database_selector = database_selector
        self.database_descriptions = database_descriptions or {}

    async def _resolve_database(self, database: str | None, question: str) -> str:
        """Resolve database name with intelligent selection support.

        Args:
            database: Explicitly specified database (optional).
            question: User's question for intelligent selection.

        Returns:
            str: Resolved database name.
        """
        # 1. 如果明确指定了数据库
        if database is not None:
            if database not in self.pools:
                raise DatabaseError(
                    message=f"Database '{database}' not found",
                    details={"available_databases": list(self.pools.keys())},
                )
            return database

        # 2. 只有一个数据库，直接使用
        available_dbs = list(self.pools.keys())
        if len(available_dbs) == 1:
            return available_dbs[0]

        # 3. 有默认数据库且有效
        if self.default_database and self.default_database in self.pools:
            # 如果没有启用智能选择，使用默认数据库
            if self.database_selector is None:
                return self.default_database

        # 4. 智能选择
        if self.database_selector and self.database_descriptions:
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

        # 5. 回退到默认数据库
        if self.default_database and self.default_database in self.pools:
            return self.default_database

        # 6. 无法确定，报错
        raise DatabaseError(
            message="Multiple databases available, please specify which to query",
            details={
                "available_databases": available_dbs,
                "hint": "Add 'database' parameter or configure PG_DEFAULT_DATABASE",
            },
        )
```

---

### 3. Server 初始化修改

**修改：** `src/pg_mcp/server.py`

```python
from pg_mcp.config.settings import MultiDatabaseConfig
from pg_mcp.services.database_selector import DatabaseSelector


@asynccontextmanager
async def lifespan(_app: FastMCP) -> AsyncIterator[None]:
    global _settings, _pools, _schema_cache, _orchestrator, _metrics
    global _circuit_breaker, _rate_limiter

    # ... 现有初始化代码 ...

    # 加载多数据库配置
    multi_db_config = MultiDatabaseConfig()

    # 创建数据库连接池
    _pools = {}

    if multi_db_config.databases:
        # 多数据库模式
        for db_name, db_config in multi_db_config.databases.items():
            pool = await create_pool_from_config(db_name, db_config)
            _pools[db_name] = pool
            logger.info(f"Created connection pool for database '{db_name}'")
    else:
        # 单数据库模式（向后兼容）
        pool = await create_pool(_settings.database)
        _pools[_settings.database.name] = pool

    # 创建数据库选择器（如果有多个数据库且启用智能选择）
    database_selector = None
    if len(_pools) > 1 and multi_db_config.auto_select_enabled:
        database_selector = DatabaseSelector(_settings.openai)
        logger.info("Database selector initialized for intelligent routing")

    # 创建 SQL 执行器
    sql_executors: dict[str, SQLExecutor] = {}
    for db_name, pool in _pools.items():
        executor = SQLExecutor(
            pool=pool,
            security_config=_settings.security,
            db_config=_settings.database,  # TODO: 使用对应的 db_config
        )
        sql_executors[db_name] = executor

    # 创建 Orchestrator
    _orchestrator = QueryOrchestrator(
        sql_generator=sql_generator,
        sql_validator=sql_validator,
        sql_executors=sql_executors,
        result_validator=result_validator,
        schema_cache=_schema_cache,
        pools=_pools,
        resilience_config=_settings.resilience,
        validation_config=_settings.validation,
        default_database=multi_db_config.default_database,
        rate_limiter=_rate_limiter,
        metrics=_metrics,
        # 新增参数
        database_selector=database_selector,
        database_descriptions=multi_db_config.database_descriptions,
    )
```

---

## 验证方案

### 单元测试

**新增文件：** `tests/unit/test_database_selector.py`

```python
"""Unit tests for DatabaseSelector."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pg_mcp.services.database_selector import DatabaseSelector, SelectionResult
from pg_mcp.config.settings import OpenAIConfig


class TestDatabaseSelector:
    """Tests for DatabaseSelector."""

    @pytest.fixture
    def openai_config(self) -> OpenAIConfig:
        return OpenAIConfig(api_key="sk-test")

    @pytest.fixture
    def database_descriptions(self) -> dict[str, str]:
        return {
            "blog_small": "博客系统数据库，包含文章(posts)、评论(comments)、用户(users)表",
            "ecommerce_medium": "电商平台数据库，包含商品(products)、订单(orders)、客户(customers)表",
        }

    @pytest.mark.asyncio
    async def test_single_database_returns_directly(self, openai_config: OpenAIConfig) -> None:
        """Test that single database is returned without LLM call."""
        selector = DatabaseSelector(openai_config)

        result = await selector.select(
            question="显示所有文章",
            database_descriptions={"blog_small": "博客系统"},
        )

        assert result.database == "blog_small"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_select_blog_for_article_question(
        self,
        openai_config: OpenAIConfig,
        database_descriptions: dict[str, str],
    ) -> None:
        """Test selecting blog database for article-related question."""
        selector = DatabaseSelector(openai_config)

        # Mock OpenAI response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '''
        {"database": "blog_small", "confidence": 0.95, "reason": "问题涉及文章，blog_small 包含 posts 表"}
        '''

        with patch.object(selector.client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_response

            result = await selector.select(
                question="显示所有已发布的文章",
                database_descriptions=database_descriptions,
            )

        assert result.database == "blog_small"
        assert result.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_select_ecommerce_for_order_question(
        self,
        openai_config: OpenAIConfig,
        database_descriptions: dict[str, str],
    ) -> None:
        """Test selecting ecommerce database for order-related question."""
        selector = DatabaseSelector(openai_config)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '''
        {"database": "ecommerce_medium", "confidence": 0.92, "reason": "问题涉及订单，ecommerce_medium 包含 orders 表"}
        '''

        with patch.object(selector.client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_response

            result = await selector.select(
                question="查询最近一周的订单总额",
                database_descriptions=database_descriptions,
            )

        assert result.database == "ecommerce_medium"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(
        self,
        openai_config: OpenAIConfig,
        database_descriptions: dict[str, str],
    ) -> None:
        """Test fallback to first database on LLM error."""
        selector = DatabaseSelector(openai_config)

        with patch.object(selector.client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = Exception("API Error")

            result = await selector.select(
                question="显示数据",
                database_descriptions=database_descriptions,
            )

        assert result.database in database_descriptions
        assert result.confidence == 0.5
```

### 集成测试

**新增文件：** `tests/integration/test_multi_database.py`

```python
"""Integration tests for multi-database support."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from pg_mcp.services.orchestrator import QueryOrchestrator
from pg_mcp.services.database_selector import DatabaseSelector
from pg_mcp.config.settings import ResilienceConfig, ValidationConfig, OpenAIConfig
from pg_mcp.models.query import QueryRequest, ReturnType


class TestMultiDatabaseIntegration:
    """Integration tests for multi-database query routing."""

    @pytest.fixture
    def mock_pools(self) -> dict[str, MagicMock]:
        return {
            "blog_small": MagicMock(),
            "ecommerce_medium": MagicMock(),
        }

    @pytest.fixture
    def database_descriptions(self) -> dict[str, str]:
        return {
            "blog_small": "博客系统，包含文章(posts)、评论、用户表",
            "ecommerce_medium": "电商系统，包含商品(products)、订单(orders)、客户表",
        }

    @pytest.mark.asyncio
    async def test_explicit_database_selection(self, mock_pools: dict) -> None:
        """Test that explicit database parameter is respected."""
        orchestrator = QueryOrchestrator(
            sql_generator=AsyncMock(),
            sql_validator=MagicMock(),
            sql_executors={name: MagicMock() for name in mock_pools},
            result_validator=MagicMock(),
            schema_cache=MagicMock(),
            pools=mock_pools,
            resilience_config=ResilienceConfig(),
            validation_config=ValidationConfig(),
        )

        # 显式指定数据库应该被尊重
        resolved = await orchestrator._resolve_database(
            database="ecommerce_medium",
            question="显示所有文章"  # 问题与 blog 相关，但指定了 ecommerce
        )

        assert resolved == "ecommerce_medium"

    @pytest.mark.asyncio
    async def test_smart_selection_for_article_question(
        self,
        mock_pools: dict,
        database_descriptions: dict,
    ) -> None:
        """Test smart selection routes article questions to blog database."""
        mock_selector = AsyncMock(spec=DatabaseSelector)
        mock_selector.select.return_value = MagicMock(
            database="blog_small",
            confidence=0.95,
            reason="文章相关"
        )

        orchestrator = QueryOrchestrator(
            sql_generator=AsyncMock(),
            sql_validator=MagicMock(),
            sql_executors={name: MagicMock() for name in mock_pools},
            result_validator=MagicMock(),
            schema_cache=MagicMock(),
            pools=mock_pools,
            resilience_config=ResilienceConfig(),
            validation_config=ValidationConfig(),
            database_selector=mock_selector,
            database_descriptions=database_descriptions,
        )

        resolved = await orchestrator._resolve_database(
            database=None,
            question="显示所有已发布的文章"
        )

        assert resolved == "blog_small"
        mock_selector.select.assert_called_once()
```

### 手动验证步骤

```bash
# 1. 配置多数据库环境变量
export PG_DATABASES='{
  "blog_small": {"host": "/tmp", "user": "danny", "description": "博客系统，包含文章、评论、用户表"},
  "ecommerce_medium": {"host": "/tmp", "user": "danny", "description": "电商系统，包含商品、订单、客户表"}
}'

# 2. 启动 MCP 服务
cd pg-mcp && uv run -m pg_mcp

# 3. 使用 Claude Code 测试
# 问题 1: "显示所有已发布的文章" → 应路由到 blog_small
# 问题 2: "查询最近一周的订单" → 应路由到 ecommerce_medium
# 问题 3: "显示所有文章", database="ecommerce_medium" → 应使用指定的 ecommerce_medium

# 4. 检查日志确认数据库选择
# 应该看到类似：
# INFO: Smart database selection: blog_small (confidence: 0.95, reason: 问题涉及文章)
```

---

## 实施步骤

### Phase 1: 配置层 (0.5 天)

1. 新增 `MultiDatabaseConfig` 类
2. 添加 `DatabaseConfigItem` 数据模型
3. 编写配置解析测试

### Phase 2: 数据库选择器 (1 天)

1. 实现 `DatabaseSelector` 类
2. 设计并测试 LLM prompt
3. 实现错误回退逻辑
4. 编写单元测试

### Phase 3: 集成 (1 天)

1. 修改 `server.py` 支持多数据库初始化
2. 修改 `orchestrator._resolve_database()` 集成选择器
3. 编写集成测试
4. 更新文档

### Phase 4: 验证 (0.5 天)

1. 手动测试各种场景
2. 性能测试（多数据库场景）
3. 向后兼容性测试（单数据库配置）

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| LLM 选择错误数据库 | 中 | 中 | 返回 confidence 分数，低于阈值时要求用户确认 |
| LLM 调用增加延迟 | 高 | 低 | 使用快速模型(gpt-4o-mini)，设置短超时 |
| description 描述不准确 | 中 | 高 | 提供配置示例，建议包含关键表名 |
| 向后兼容性问题 | 低 | 高 | 单数据库配置完全兼容现有方式 |

---

## 配置最佳实践

### description 编写建议

**好的 description:**
```
"博客系统数据库，包含文章(posts)、评论(comments)、用户(users)、标签(tags)表"
```

**不好的 description:**
```
"数据库1"  # 没有描述内容
"blog"     # 太简单，无法区分
```

### 关键点

1. **包含主要表名**：在描述中提及关键表名，如 `posts`、`orders`
2. **使用业务领域词汇**：如"博客"、"电商"、"CRM"
3. **中英文兼顾**：同时包含中文描述和英文表名
4. **简洁明了**：一句话概括数据库用途

---

*文档生成时间：2026-02-06*
*基于 pg-mcp 多数据库支持讨论*
