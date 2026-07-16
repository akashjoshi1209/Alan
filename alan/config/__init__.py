"""Configuration management for Alan."""

from alan.config.loader import ConfigLoader, create_config_files, load_config
from alan.config.schemas import (
    AIProviderConfig,
    AIProvidersConfig,
    AgentConfig,
    DatabaseConfig,
    FeatureFlagsConfig,
    LoggingConfig,
    MCPConfig,
    MemoryConfig,
    RedisConfig,
    SecurityConfig,
    Settings,
    TelemetryConfig,
    ToolConfig,
    VectorDBConfig,
    VoiceConfig,
    get_settings,
    set_settings,
)
from alan.config.secrets import (
    SecretReference,
    SecretsManager,
    delete_secret,
    get_secret,
    get_secrets_manager,
    resolve_secret_refs,
    set_secret,
)

__all__ = [
    # Schemas
    "Settings",
    "DatabaseConfig",
    "RedisConfig",
    "VectorDBConfig",
    "AIProviderConfig",
    "AIProvidersConfig",
    "MemoryConfig",
    "AgentConfig",
    "ToolConfig",
    "MCPConfig",
    "VoiceConfig",
    "LoggingConfig",
    "SecurityConfig",
    "TelemetryConfig",
    "FeatureFlagsConfig",
    "get_settings",
    "set_settings",
    # Loader
    "ConfigLoader",
    "load_config",
    "create_config_files",
    # Secrets
    "SecretsManager",
    "SecretReference",
    "get_secrets_manager",
    "set_secret",
    "get_secret",
    "delete_secret",
    "resolve_secret_refs",
    # Environments
    "get_environment_settings",
]