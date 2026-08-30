"""Configuration schemas for Alan."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """API server settings."""

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    reload: bool = False
    timeout: int = 30


class DatabaseSettings(BaseSettings):
    """Database settings."""

    url: str = "sqlite+aiosqlite:///./alan.db"
    pool_size: int = 10
    max_overflow: int = 5
    pool_timeout: int = 30
    pool_recycle: int = 3600
    echo: bool = False


class RedisSettings(BaseSettings):
    """Redis settings."""

    url: str = "redis://localhost:6379/0"
    max_connections: int = 50
    socket_timeout: int = 5
    socket_connect_timeout: int = 5


class VectorDBSettings(BaseSettings):
    """Vector database settings."""

    provider: str = "chroma"
    host: str = "localhost"
    port: int = 8000
    collection: str = "alan_memory"
    api_key: Optional[str] = None
    environment: Optional[str] = None
    index_name: Optional[str] = None


class AIProviderConfig(BaseSettings):
    """Individual AI provider configuration."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    org_id: Optional[str] = None
    default_model: Optional[str] = None
    embedding_model: Optional[str] = None
    timeout: int = 120
    max_retries: int = 3


class AIProvidersSettings(BaseSettings):
    """AI provider settings."""

    default_provider: str = "ollama"
    default_embedding_provider: str = "ollama"
    default_chat_model: str = "llama3"
    default_embedding_model: str = "nomic-embed-text"
    default_temperature: float = 0.7
    default_max_tokens: int = 4096
    default_top_p: float = 1.0

    routing_strategy: str = "local_first"
    cost_tracking: bool = True
    monthly_budget_usd: float = 50.0
    fallback_enabled: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 60

    providers: Dict[str, AIProviderConfig] = Field(default_factory=dict)

    openai: AIProviderConfig = Field(default_factory=AIProviderConfig)
    anthropic: AIProviderConfig = Field(default_factory=AIProviderConfig)
    gemini: AIProviderConfig = Field(default_factory=AIProviderConfig)
    ollama: AIProviderConfig = Field(default_factory=AIProviderConfig)
    local: AIProviderConfig = Field(default_factory=AIProviderConfig)
    cohere: AIProviderConfig = Field(default_factory=AIProviderConfig)
    mistral: AIProviderConfig = Field(default_factory=AIProviderConfig)
    groq: AIProviderConfig = Field(default_factory=AIProviderConfig)
    together: AIProviderConfig = Field(default_factory=AIProviderConfig)
    replicate: AIProviderConfig = Field(default_factory=AIProviderConfig)
    huggingface: AIProviderConfig = Field(default_factory=AIProviderConfig)


class MemorySettings(BaseSettings):
    """Memory system settings."""

    embedding_dimension: int = 768
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 10
    similarity_threshold: float = 0.75
    max_context_tokens: int = 8000
    consolidation_interval: int = 3600
    pii_redaction: bool = True
    retention_days: int = 365
    vector_db: str = "chroma"


class AgentSettings(BaseSettings):
    """Agent system settings."""

    default_agent: str = "assistant"
    max_iterations: int = 10
    max_tool_calls: int = 20
    planning_enabled: bool = True
    handoff_enabled: bool = True
    default_timeout: int = 300
    max_concurrent: int = 5


class ToolSettings(BaseSettings):
    """Tool system settings."""

    timeout: int = 60
    max_memory_mb: int = 512
    max_cpu_percent: int = 50
    network_enabled: bool = False
    sandbox_type: str = "docker"
    sandbox_image: str = "python:3.11-slim"
    enable_code_execution: bool = True
    code_execution_timeout: int = 30
    allowlist: List[str] = Field(default_factory=list)


class MCPSettings(BaseSettings):
    """MCP settings."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 3000
    transport: str = "stdio"
    servers_config: str = "config/mcp_servers.yaml"
    connection_timeout: int = 30
    request_timeout: int = 120


class VoiceSettings(BaseSettings):
    """Voice system settings."""

    enabled: bool = False
    wake_word: str = "hey alan"
    wake_word_sensitivity: float = 0.5
    stt_provider: str = "faster-whisper"
    stt_model: str = "base.en"
    tts_provider: str = "pyttsx3"
    tts_voice: Optional[str] = None
    vad_provider: str = "webrtc"
    sample_rate: int = 16000
    channels: int = 1
    input_device: Optional[int] = None
    output_device: Optional[int] = None
    vad_threshold: float = 0.5
    silence_duration: float = 1.5


class LoggingSettings(BaseSettings):
    """Logging settings."""

    format: str = "json"
    level: str = "INFO"
    console_level: str = "INFO"
    file_level: str = "DEBUG"
    file: str = "logs/alan.log"
    max_size: str = "100MB"
    backup_count: int = 10
    include_trace: bool = True


class SecuritySettings(BaseSettings):
    """Security settings."""

    api_key_enabled: bool = True
    api_key_prefix: str = "alan_"
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    cors_allow_credentials: bool = True
    rate_limit_requests: int = 100
    rate_limit_window: int = 60


class TelemetrySettings(BaseSettings):
    """Telemetry settings."""

    enabled: bool = False
    sentry_dsn: Optional[str] = None
    otel_endpoint: Optional[str] = None
    otel_service_name: str = "alan"
    prometheus_enabled: bool = True
    prometheus_port: int = 9090


class FeatureFlags(BaseSettings):
    """Feature flags."""

    enable_voice: bool = True
    enable_mcp: bool = True
    enable_agents: bool = True
    enable_memory_graph: bool = True
    enable_api: bool = True
    enable_cli: bool = True
    enable_telemetry: bool = False


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Alan"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = ""

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # API
    api: APISettings = Field(default_factory=APISettings)

    # Database
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    # Redis
    redis: RedisSettings = Field(default_factory=RedisSettings)

    # Vector DB
    vector_db: VectorDBSettings = Field(default_factory=VectorDBSettings)

    # AI Providers
    ai_providers: AIProvidersSettings = Field(default_factory=AIProvidersSettings)

    # Memory
    memory: MemorySettings = Field(default_factory=MemorySettings)

    # Agents
    agents: AgentSettings = Field(default_factory=AgentSettings)

    # Tools
    tools: ToolSettings = Field(default_factory=ToolSettings)

    # MCP
    mcp: MCPSettings = Field(default_factory=MCPSettings)

    # Voice
    voice: VoiceSettings = Field(default_factory=VoiceSettings)

    # Logging
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    # Security
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    # Telemetry
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)

    # Features
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    @field_validator("secret_key", mode="before")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if not v or v == "your-secret-key-change-in-production":
            import secrets
            return secrets.token_urlsafe(32)
        return v

    @field_validator("app_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        valid = {"development", "staging", "production", "testing"}
        if v not in valid:
            raise ValueError(f"app_env must be one of {valid}")
        return v

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_testing(self) -> bool:
        return self.app_env == "testing"