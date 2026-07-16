"""Settings management for Alan."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import ValidationError

from alan.config.schemas import Settings, get_settings as get_global_settings

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False


class SettingsManager:
    """Manages application settings with multiple sources."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.cwd() / "config"
        self._settings: Optional[Settings] = None
        self._overrides: Dict[str, Any] = {}

    @lru_cache(maxsize=1)
    def load(self) -> Settings:
        """Load settings from all sources."""
        # Start with defaults from environment
        settings = get_global_settings()

        # Apply YAML config file if exists
        config_file = self.config_dir / f"{settings.app_env}.yaml"
        if config_file.exists():
            with open(config_file) as f:
                yaml_config = yaml.safe_load(f) or {}
            settings = self._merge_settings(settings, yaml_config)

        # Apply local overrides
        if self._overrides:
            settings = self._merge_settings(settings, self._overrides)

        self._settings = settings
        return settings

    def _merge_settings(
        self, base: Settings, overrides: Dict[str, Any]
    ) -> Settings:
        """Merge overrides into base settings."""
        base_dict = base.model_dump()
        merged = self._deep_merge(base_dict, overrides)
        return Settings(**merged)

    def _deep_merge(self, base: Dict, overrides: Dict) -> Dict:
        """Deep merge two dictionaries."""
        result = base.copy()
        for key, value in overrides.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get(self) -> Settings:
        """Get current settings, loading if necessary."""
        if self._settings is None:
            return self.load()
        return self._settings

    def reload(self) -> Settings:
        """Force reload settings."""
        self.load.cache_clear()
        return self.load()

    def override(self, **kwargs: Any) -> None:
        """Set temporary overrides."""
        self._overrides.update(kwargs)
        self._settings = None

    def clear_overrides(self) -> None:
        """Clear all overrides."""
        self._overrides.clear()
        self._settings = None


class SecretsManager:
    """Manages secrets using keyring or environment variables."""

    SERVICE_NAME = "alan"

    @staticmethod
    def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a secret from keyring or environment."""
        # Try keyring first
        if KEYRING_AVAILABLE:
            try:
                value = keyring.get_password(SecretsManager.SERVICE_NAME, key)
                if value is not None:
                    return value
            except Exception:
                pass

        # Fall back to environment
        import os
        env_key = key.upper().replace("-", "_")
        return os.environ.get(env_key, default)

    @staticmethod
    def set_secret(key: str, value: str) -> bool:
        """Store a secret in keyring."""
        if not KEYRING_AVAILABLE:
            return False

        try:
            keyring.set_password(SecretsManager.SERVICE_NAME, key, value)
            return True
        except Exception:
            return False

    @staticmethod
    def delete_secret(key: str) -> bool:
        """Delete a secret from keyring."""
        if not KEYRING_AVAILABLE:
            return False

        try:
            keyring.delete_password(SecretsManager.SERVICE_NAME, key)
            return True
        except Exception:
            return False

    @staticmethod
    def list_secrets() -> list[str]:
        """List all stored secret keys."""
        if not KEYRING_AVAILABLE:
            return []

        try:
            # Keyring doesn't have a standard list method
            # This is a best-effort implementation
            return []
        except Exception:
            return []


# Global instances
_settings_manager: Optional[SettingsManager] = None
_secrets_manager: Optional[SecretsManager] = None


def get_settings_manager(config_dir: Optional[Path] = None) -> SettingsManager:
    """Get the global settings manager."""
    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager(config_dir)
    return _settings_manager


def get_secrets_manager() -> SecretsManager:
    """Get the global secrets manager."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager()
    return _secrets_manager


def get_settings(config_dir: Optional[Path] = None) -> Settings:
    """Get the current settings."""
    return get_settings_manager(config_dir).get()


def reload_settings(config_dir: Optional[Path] = None) -> Settings:
    """Reload settings from all sources."""
    return get_settings_manager(config_dir).reload()