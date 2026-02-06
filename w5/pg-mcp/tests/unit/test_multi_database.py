"""Unit tests for multi-database configuration and database selector."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pg_mcp.config.settings import (
    DatabaseConfigItem,
    MultiDatabaseConfig,
)
from pg_mcp.services.database_selector import DatabaseSelector, SelectionResult


class TestDatabaseConfigItem:
    """Tests for DatabaseConfigItem."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = DatabaseConfigItem()
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.user == "postgres"
        assert config.name is None
        assert config.description == ""

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = DatabaseConfigItem(
            host="/tmp",
            port=5433,
            user="danny",
            name="mydb",
            description="Test database",
        )
        assert config.host == "/tmp"
        assert config.port == 5433
        assert config.user == "danny"
        assert config.name == "mydb"
        assert config.description == "Test database"

    def test_get_dsn_with_name(self) -> None:
        """Test DSN generation when name is specified."""
        config = DatabaseConfigItem(
            host="localhost",
            port=5432,
            user="testuser",
            password="testpass",
            name="mydb",
        )
        dsn = config.get_dsn("ignored_key")
        assert dsn == "postgresql://testuser:testpass@localhost:5432/mydb"

    def test_get_dsn_without_name(self) -> None:
        """Test DSN generation using key when name is not specified."""
        config = DatabaseConfigItem(
            host="localhost",
            port=5432,
            user="testuser",
            password="testpass",
        )
        dsn = config.get_dsn("blog_small")
        assert dsn == "postgresql://testuser:testpass@localhost:5432/blog_small"

    def test_get_safe_dsn(self) -> None:
        """Test safe DSN masks password."""
        config = DatabaseConfigItem(
            host="localhost",
            user="testuser",
            password="secret123",
        )
        safe_dsn = config.get_safe_dsn("mydb")
        assert "secret123" not in safe_dsn
        assert "***" in safe_dsn


class TestMultiDatabaseConfig:
    """Tests for MultiDatabaseConfig."""

    def test_empty_databases(self) -> None:
        """Test default empty databases."""
        config = MultiDatabaseConfig()
        assert config.databases == {}
        assert config.database_descriptions == {}
        assert config.has_multiple_databases is False

    def test_parse_json_databases(self) -> None:
        """Test parsing databases from JSON string."""
        json_str = '{"blog": {"host": "/tmp", "user": "danny", "description": "Blog DB"}}'

        config = MultiDatabaseConfig(databases_json=json_str)
        assert "blog" in config.databases
        assert config.databases["blog"].host == "/tmp"
        assert config.databases["blog"].user == "danny"
        assert config.databases["blog"].description == "Blog DB"

    def test_database_descriptions(self) -> None:
        """Test extracting database descriptions."""
        json_str = '''{
            "blog": {"host": "/tmp", "description": "Blog system"},
            "shop": {"host": "/tmp", "description": "Shop system"},
            "empty": {"host": "/tmp"}
        }'''

        config = MultiDatabaseConfig(databases_json=json_str)
        descriptions = config.database_descriptions

        assert "blog" in descriptions
        assert descriptions["blog"] == "Blog system"
        assert "shop" in descriptions
        assert descriptions["shop"] == "Shop system"
        # Empty description should be excluded
        assert "empty" not in descriptions

    def test_has_multiple_databases(self) -> None:
        """Test has_multiple_databases property."""
        json_str = '{"db1": {"host": "/tmp"}, "db2": {"host": "/tmp"}}'

        config = MultiDatabaseConfig(databases_json=json_str)
        assert config.has_multiple_databases is True

    def test_get_database_config(self) -> None:
        """Test get_database_config method."""
        json_str = '{"blog": {"host": "/tmp", "user": "danny"}}'

        config = MultiDatabaseConfig(databases_json=json_str)
        db_config = config.get_database_config("blog")

        assert db_config is not None
        assert db_config.host == "/tmp"
        assert db_config.user == "danny"

        # Non-existent database
        assert config.get_database_config("nonexistent") is None

    def test_default_database(self) -> None:
        """Test default_database configuration."""
        config = MultiDatabaseConfig(default_database="blog")
        assert config.default_database == "blog"

    def test_auto_select_enabled(self) -> None:
        """Test auto_select_enabled configuration."""
        # Default is True
        config = MultiDatabaseConfig()
        assert config.auto_select_enabled is True

        # Can be disabled
        config = MultiDatabaseConfig(auto_select_enabled=False)
        assert config.auto_select_enabled is False

    def test_invalid_json(self) -> None:
        """Test handling of invalid JSON."""
        config = MultiDatabaseConfig(databases_json="not valid json")
        assert config.databases == {}


class TestDatabaseSelector:
    """Tests for DatabaseSelector."""

    @pytest.fixture
    def mock_openai_config(self) -> MagicMock:
        """Create mock OpenAI config."""
        config = MagicMock()
        config.api_key.get_secret_value.return_value = "sk-test"
        config.base_url = None
        config.timeout = 30.0
        config.model = "gpt-4o-mini"
        return config

    @pytest.fixture
    def database_descriptions(self) -> dict[str, str]:
        """Create sample database descriptions."""
        return {
            "blog_small": "博客系统数据库，包含文章(posts)、评论(comments)、用户(users)表",
            "ecommerce_medium": "电商平台数据库，包含商品(products)、订单(orders)、客户(customers)表",
        }

    @pytest.mark.asyncio
    async def test_single_database_returns_directly(
        self, mock_openai_config: MagicMock
    ) -> None:
        """Test that single database is returned without LLM call."""
        selector = DatabaseSelector(mock_openai_config)

        result = await selector.select(
            question="显示所有文章",
            database_descriptions={"blog_small": "博客系统"},
        )

        assert result.database == "blog_small"
        assert result.confidence == 1.0
        assert "only one" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_select_blog_for_article_question(
        self,
        mock_openai_config: MagicMock,
        database_descriptions: dict[str, str],
    ) -> None:
        """Test selecting blog database for article-related question."""
        selector = DatabaseSelector(mock_openai_config)

        # Mock OpenAI response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '''
        {"database": "blog_small", "confidence": 0.95, "reason": "问题涉及文章，blog_small 包含 posts 表"}
        '''

        with patch.object(
            selector.client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
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
        mock_openai_config: MagicMock,
        database_descriptions: dict[str, str],
    ) -> None:
        """Test selecting ecommerce database for order-related question."""
        selector = DatabaseSelector(mock_openai_config)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '''
        {"database": "ecommerce_medium", "confidence": 0.92, "reason": "问题涉及订单"}
        '''

        with patch.object(
            selector.client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await selector.select(
                question="查询最近一周的订单总额",
                database_descriptions=database_descriptions,
            )

        assert result.database == "ecommerce_medium"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(
        self,
        mock_openai_config: MagicMock,
        database_descriptions: dict[str, str],
    ) -> None:
        """Test fallback to first database on LLM error."""
        selector = DatabaseSelector(mock_openai_config)

        with patch.object(
            selector.client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = Exception("API Error")

            result = await selector.select(
                question="显示数据",
                database_descriptions=database_descriptions,
            )

        assert result.database in database_descriptions
        assert result.confidence == 0.5
        assert "fallback" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_empty_databases_raises_error(
        self, mock_openai_config: MagicMock
    ) -> None:
        """Test that empty database descriptions raises ValueError."""
        selector = DatabaseSelector(mock_openai_config)

        with pytest.raises(ValueError) as exc_info:
            await selector.select(
                question="test",
                database_descriptions={},
            )

        assert "no databases" in str(exc_info.value).lower()

    def test_parse_response_valid_json(self, mock_openai_config: MagicMock) -> None:
        """Test parsing valid JSON response."""
        selector = DatabaseSelector(mock_openai_config)

        content = '{"database": "blog", "confidence": 0.85, "reason": "matches"}'
        result = selector._parse_response(content, {"blog": "desc", "shop": "desc"})

        assert result.database == "blog"
        assert result.confidence == 0.85
        assert result.reason == "matches"

    def test_parse_response_invalid_json(self, mock_openai_config: MagicMock) -> None:
        """Test parsing invalid JSON falls back to first database."""
        selector = DatabaseSelector(mock_openai_config)

        content = "not valid json"
        result = selector._parse_response(content, {"blog": "desc", "shop": "desc"})

        assert result.database == "blog"  # First database
        assert result.confidence == 0.5
        assert "failed" in result.reason.lower()

    def test_parse_response_unknown_database(
        self, mock_openai_config: MagicMock
    ) -> None:
        """Test parsing response with unknown database name."""
        selector = DatabaseSelector(mock_openai_config)

        content = '{"database": "unknown_db", "confidence": 0.9, "reason": "test"}'
        result = selector._parse_response(content, {"blog": "desc", "shop": "desc"})

        # Should fall back to first available database
        assert result.database == "blog"

    def test_parse_response_fuzzy_match(self, mock_openai_config: MagicMock) -> None:
        """Test fuzzy matching of database names."""
        selector = DatabaseSelector(mock_openai_config)

        # LLM returns partial match
        content = '{"database": "blog", "confidence": 0.9, "reason": "test"}'
        result = selector._parse_response(
            content, {"blog_small": "desc", "ecommerce_medium": "desc"}
        )

        # Should fuzzy match to blog_small
        assert result.database == "blog_small"
