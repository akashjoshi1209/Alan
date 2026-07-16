"""Configuration loader for Alan."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import ValidationError

from alan.config.schemas import Settings, get_settings as get_global_settings
from alan.config.environments import get_environment_settings
from alan.config.secrets import SecretsManager, resolve_secret_refs


class ConfigLoader:
    """Loads configuration from multiple sources with priority."""

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        environment: Optional[str] = None,
    ):
        self.config_dir = config_dir or Path.cwd() / "config"
        self.environment = environment
        self.secrets_manager = SecretsManager(self.config_dir / "secrets")

    def load(self) -> Settings:
        """Load configuration with full precedence chain."""
        # 1. Start with environment-specific defaults
        env = self.environment or self._detect_environment()
        base_settings = get_environment_settings(env)

        # 2. Apply YAML config file
        config_file = self.config_dir / f"{env}.yaml"
        if config_file.exists():
            with open(config_file) as f:
                yaml_config = yaml.safe_load(f) or {}
            base_settings = self._merge_settings(base_settings, yaml_config)

        # 3. Apply local overrides (not committed to git)
        local_file = self.config_dir / "local.yaml"
        if local_file.exists():
            with open(local_file) as f:
                local_config = yaml.safe_load(f) or {}
            base_settings = self._merge_settings(base_settings, local_config)

        # 4. Resolve secret references
        settings_dict = base_settings.model_dump()
        settings_dict = resolve_secret_refs(settings_dict, self.secrets_manager)
        base_settings = Settings(**settings_dict)

        # 5. Apply environment variables (highest priority)
        # This is handled by pydantic-settings automatically

        return base_settings

    def _detect_environment(self) -> str:
        """Detect current environment."""
        import os
        return os.environ.get("APP_ENV", "development")

    def _merge_settings(self, base: Settings, overrides: Dict[str, Any]) -> Settings:
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

    def validate(self, settings: Settings) -> bool:
        """Validate settings."""
        try:
            # Check required secrets
            if settings.ai_providers.default_provider != "ollama":
                if not settings.ai_providers.providers.get(
                    settings.ai_providers.default_provider
                ):
                    return False
            return True
        except ValidationError:
            return False

    def create_example_config(self, env: str = "development") -> None:
        """Create example configuration file."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

        example_settings = get_environment_settings(env)
        example_dict = example_settings.model_dump()

        # Convert SecretReference back to placeholder
        example_dict = self._strip_secrets(example_dict)

        config_file = self.config_dir / f"{env}.yaml.example"
        with open(config_file, "w") as f:
            yaml.dump(example_dict, f, default_flow_style=False, sort_keys=False)

        print(f"Created example config: {config_file}")

    def _strip_secrets(self, obj: Any) -> Any:
        """Replace secret values with placeholders."""
        if isinstance(obj, dict):
            return {
                k: self._strip_secrets(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [self._strip_secrets(item) for item in obj]
        elif isinstance(obj, str) and (
            "api_key" in str(obj).lower()
            or "secret" in str(obj).lower()
            or "password" in str(obj).lower()
        ):
            return "${SECRET}"
        return obj


def load_config(
    config_dir: Optional[Path] = None,
    environment: Optional[str] = None,
) -> Settings:
    """Load configuration (convenience function)."""
    loader = ConfigLoader(config_dir, environment)
    return loader.load()


def create_config_files(config_dir: Optional[Path] = None) -> None:
    """Create example configuration files for all environments."""
    loader = ConfigLoader(config_dir)
    for env in ["development", "staging", "production", "testing"]:
        loader.create_example_config(env)