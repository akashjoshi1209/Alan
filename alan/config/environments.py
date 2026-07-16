"""Environment-specific settings for Alan."""

from typing import Optional

from alan.config.schemas import Settings


def get_environment_settings(
    environment: str,
    base_settings: Optional[Settings] = None,
) -> Settings:
    """Get settings for a specific environment."""
    from alan.config.schemas import get_settings

    settings = base_settings or get_settings()

    # Environment-specific overrides
    overrides = {
        "development": _development_overrides(),
        "staging": _staging_overrides(),
        "production": _production_overrides(),
        "testing": _testing_overrides(),
    }

    env_overrides = overrides.get(environment, {})
    if env_overrides:
        settings_dict = settings.model_dump()
        merged = _deep_merge(settings_dict, env_overrides)
        return Settings(**merged)

    return settings


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _development_overrides() -> dict:
    """Development environment overrides."""
    return {
        "debug": True,
        "log_level": "DEBUG",
        "api": {
            "reload": True,
            "workers": 1,
        },
        "database": {
            "echo": True,
        },
        "ai_providers": {
            "default_provider": "ollama",
        },
        "features": {
            "enable_telemetry": False,
            "enable_debug_endpoints": True,
        },
    }


def _staging_overrides() -> dict:
    """Staging environment overrides."""
    return {
        "debug": False,
        "log_level": "INFO",
        "api": {
            "workers": 2,
        },
        "database": {
            "pool_size": 10,
            "max_overflow": 5,
        },
        "features": {
            "enable_telemetry": True,
        },
    }


def _production_overrides() -> dict:
    """Production environment overrides."""
    return {
        "debug": False,
        "log_level": "WARNING",
        "api": {
            "workers": 4,
            "reload": False,
        },
        "database": {
            "pool_size": 20,
            "max_overflow": 10,
            "echo": False,
        },
        "security": {
            "api_key_enabled": True,
        },
        "features": {
            "enable_telemetry": True,
            "enable_debug_endpoints": False,
        },
    }


def _testing_overrides() -> dict:
    """Testing environment overrides."""
    return {
        "debug": True,
        "log_level": "DEBUG",
        "database": {
            "url": "sqlite+aiosqlite:///:memory:",
            "pool_size": 1,
            "max_overflow": 0,
        },
        "redis": {
            "url": "redis://localhost:6379/1",
        },
        "vector_db": {
            "collection": "alan_test",
        },
        "ai_providers": {
            "default_provider": "mock",
        },
        "features": {
            "enable_voice": False,
            "enable_mcp": False,
            "enable_telemetry": False,
        },
        "security": {
            "api_key_enabled": False,
        },
    }