"""Configuration management for PostgreSQL MCP Server.

This module defines all configuration settings using Pydantic for validation
and type safety. Configuration is loaded from environment variables with
sensible defaults.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """PostgreSQL database connection configuration."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_", env_file=".env", extra="ignore")

    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, ge=1, le=65535, description="Database port")
    name: str = Field(default="postgres", description="Database name")
    user: str = Field(default="postgres", description="Database user")
    password: str = Field(default="", description="Database password")

    # Connection pool settings
    min_pool_size: int = Field(default=5, ge=1, le=100, description="Minimum pool size")
    max_pool_size: int = Field(default=20, ge=1, le=100, description="Maximum pool size")
    pool_timeout: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Pool acquire timeout in seconds"
    )
    command_timeout: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Command execution timeout in seconds"
    )

    @property
    def dsn(self) -> str:
        """Build PostgreSQL DSN connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def safe_dsn(self) -> str:
        """Build DSN with masked password for logging."""
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.name}"


class OpenAIConfig(BaseSettings):
    """OpenAI API configuration."""

    model_config = SettingsConfigDict(env_prefix="OPENAI_", env_file=".env", extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), description="OpenAI API key")
    base_url: str | None = Field(
        default=None, description="Custom API base URL for third-party OpenAI-compatible services"
    )
    model: str = Field(default="gpt-4o-mini", description="Model to use for SQL generation")
    max_tokens: int = Field(default=2000, ge=100, le=128000, description="Maximum tokens in response")
    temperature: float = Field(
        default=0.0, ge=0.0, le=2.0, description="Temperature for response randomness"
    )
    timeout: float = Field(
        default=30.0, ge=5.0, le=120.0, description="API request timeout in seconds"
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: SecretStr) -> SecretStr:
        """Validate API key is not empty."""
        api_key_str = v.get_secret_value()
        if not api_key_str or not api_key_str.strip():
            raise ValueError("OpenAI API key must not be empty")
        return v


class SecurityConfig(BaseSettings):
    """Security and access control configuration."""

    model_config = SettingsConfigDict(env_prefix="SECURITY_", env_file=".env", extra="ignore")

    allow_write_operations: bool = Field(
        default=False, description="Allow write operations (INSERT, UPDATE, DELETE)"
    )
    allow_explain: bool = Field(
        default=False, description="Allow EXPLAIN statements for query plan analysis"
    )
    blocked_functions_str: str = Field(
        default="pg_sleep,pg_read_file,pg_write_file,lo_import,lo_export",
        alias="blocked_functions",
        description="Comma-separated list of blocked PostgreSQL functions",
    )
    max_rows: int = Field(default=10000, ge=1, le=100000, description="Maximum rows to return")
    max_execution_time: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Maximum query execution time in seconds"
    )
    readonly_role: str | None = Field(
        default=None, description="PostgreSQL role to switch to for read-only access"
    )
    safe_search_path: str = Field(
        default="public", description="Safe search_path to set during query execution"
    )

    @property
    def blocked_functions(self) -> list[str]:
        """Return blocked functions as a list."""
        return [f.strip() for f in self.blocked_functions_str.split(",") if f.strip()]


class ValidationConfig(BaseSettings):
    """Query validation configuration."""

    model_config = SettingsConfigDict(env_prefix="VALIDATION_", env_file=".env", extra="ignore")

    max_question_length: int = Field(
        default=10000, ge=1, le=50000, description="Maximum question length in characters"
    )
    min_confidence_score: int = Field(
        default=70, ge=0, le=100, description="Minimum confidence score (0-100)"
    )

    # Result validation settings
    enabled: bool = Field(default=True, description="Enable result validation using LLM")
    sample_rows: int = Field(
        default=5, ge=1, le=100, description="Number of sample rows to include in validation"
    )
    timeout_seconds: float = Field(
        default=10.0, ge=1.0, le=60.0, description="Result validation timeout in seconds"
    )
    confidence_threshold: int = Field(
        default=70, ge=0, le=100, description="Minimum confidence for acceptable results"
    )


class CacheConfig(BaseSettings):
    """Schema cache configuration."""

    model_config = SettingsConfigDict(env_prefix="CACHE_", env_file=".env", extra="ignore")

    schema_ttl: int = Field(
        default=3600, ge=60, le=86400, description="Schema cache TTL in seconds"
    )
    max_size: int = Field(default=100, ge=1, le=1000, description="Maximum cache entries")
    enabled: bool = Field(default=True, description="Enable schema caching")


class ResilienceConfig(BaseSettings):
    """Resilience and fault tolerance configuration."""

    model_config = SettingsConfigDict(env_prefix="RESILIENCE_", env_file=".env", extra="ignore")

    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum retry attempts")
    retry_delay: float = Field(
        default=1.0, ge=0.1, le=10.0, description="Initial retry delay in seconds"
    )
    backoff_factor: float = Field(
        default=2.0, ge=1.0, le=10.0, description="Exponential backoff factor"
    )
    circuit_breaker_threshold: int = Field(
        default=5, ge=1, le=100, description="Failures before circuit opens"
    )
    circuit_breaker_timeout: float = Field(
        default=60.0, ge=10.0, le=300.0, description="Circuit breaker timeout in seconds"
    )

    # Rate limiting configuration
    rate_limit_enabled: bool = Field(
        default=True, description="Enable rate limiting for queries and LLM calls"
    )
    rate_limit_max_concurrent_queries: int = Field(
        default=10, ge=1, le=1000, description="Maximum concurrent database queries"
    )
    rate_limit_max_concurrent_llm: int = Field(
        default=5, ge=1, le=100, description="Maximum concurrent LLM API calls"
    )
    rate_limit_timeout: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Rate limiter acquire timeout in seconds"
    )


class ObservabilityConfig(BaseSettings):
    """Observability and monitoring configuration."""

    model_config = SettingsConfigDict(env_prefix="OBSERVABILITY_", env_file=".env", extra="ignore")

    metrics_enabled: bool = Field(default=True, description="Enable Prometheus metrics")
    metrics_port: int = Field(
        default=9090, ge=1024, le=65535, description="Metrics HTTP server port"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )
    log_format: Literal["json", "text"] = Field(default="text", description="Log format")


class Settings(BaseSettings):
    """Main application settings aggregating all config sections."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "staging", "production"] = Field(
        default="development", description="Application environment"
    )

    # Nested configurations
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    resilience: ResilienceConfig = Field(default_factory=ResilienceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == "development"


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create global settings instance.

    Returns:
        Settings: The global settings instance.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset global settings instance. Useful for testing."""
    global _settings
    _settings = None


# =============================================================================
# Multi-Database Configuration
# =============================================================================


class DatabaseConfigItem(BaseModel):
    """Configuration for a single database in multi-database setup.

    This model represents one database entry in the PG_DATABASES JSON configuration.
    The description field is crucial for intelligent database selection.

    Example:
        >>> config = DatabaseConfigItem(
        ...     host="/tmp",
        ...     user="danny",
        ...     description="博客系统，包含文章(posts)、评论(comments)表"
        ... )
    """

    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, ge=1, le=65535, description="Database port")
    name: str | None = Field(
        default=None,
        description="Database name. If not specified, uses the key from PG_DATABASES",
    )
    user: str = Field(default="postgres", description="Database user")
    password: str = Field(default="", description="Database password")
    description: str = Field(
        default="",
        description="Database description for intelligent selection. "
        "Should include table names and business domain.",
    )

    # Connection pool settings
    min_pool_size: int = Field(default=2, ge=1, le=50, description="Minimum pool size")
    max_pool_size: int = Field(default=10, ge=1, le=100, description="Maximum pool size")

    def get_dsn(self, db_key: str) -> str:
        """Build PostgreSQL DSN connection string.

        Args:
            db_key: The database key from PG_DATABASES, used if name is not set.

        Returns:
            str: PostgreSQL DSN connection string.
        """
        actual_name = self.name or db_key
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{actual_name}"

    def get_safe_dsn(self, db_key: str) -> str:
        """Build DSN with masked password for logging.

        Args:
            db_key: The database key from PG_DATABASES.

        Returns:
            str: DSN with password masked.
        """
        actual_name = self.name or db_key
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{actual_name}"


class MultiDatabaseConfig(BaseSettings):
    """Configuration for multi-database support.

    This configuration allows defining multiple databases via a JSON string
    in the PG_DATABASES environment variable.

    Example:
        >>> # Set environment variable:
        >>> # PG_DATABASES='{"blog": {"host": "/tmp", "user": "danny", "description": "博客系统"}}'
        >>> config = MultiDatabaseConfig()
        >>> print(config.databases)  # {"blog": DatabaseConfigItem(...)}

    Environment Variables:
        PG_DATABASES: JSON string mapping database names to their configurations.
        PG_DEFAULT_DATABASE: Name of the default database to use.
        PG_AUTO_SELECT_ENABLED: Whether to enable LLM-based database selection.
    """

    model_config = SettingsConfigDict(
        env_prefix="PG_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,  # Allow both field name and alias
    )

    databases_json: str = Field(
        default="{}",
        validation_alias="PG_DATABASES",  # Env var name (without prefix since we specify full name)
        description="JSON string mapping database names to configurations",
    )
    default_database: str | None = Field(
        default=None,
        description="Default database name when not specified in query",
    )
    auto_select_enabled: bool = Field(
        default=True,
        description="Enable LLM-based intelligent database selection",
    )

    @property
    def databases(self) -> dict[str, DatabaseConfigItem]:
        """Parse JSON configuration into database config dictionary.

        Returns:
            dict: Mapping of database names to DatabaseConfigItem instances.
        """
        try:
            raw = json.loads(self.databases_json)
            return {name: DatabaseConfigItem(**config) for name, config in raw.items()}
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def database_descriptions(self) -> dict[str, str]:
        """Get mapping of database names to their descriptions.

        This is used for intelligent database selection.

        Returns:
            dict: Mapping of database names to descriptions.
        """
        return {
            name: config.description
            for name, config in self.databases.items()
            if config.description
        }

    @property
    def has_multiple_databases(self) -> bool:
        """Check if multiple databases are configured.

        Returns:
            bool: True if more than one database is configured.
        """
        return len(self.databases) > 1

    def get_database_config(self, name: str) -> DatabaseConfigItem | None:
        """Get configuration for a specific database.

        Args:
            name: Database name.

        Returns:
            DatabaseConfigItem or None if not found.
        """
        return self.databases.get(name)
