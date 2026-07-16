"""Unit tests for configuration."""

import pytest
from pathlib import Path

from alan.config import Settings, get_settings, load_config


class TestSettings:
    """Test settings configuration."""

    def test_default_settings(self):
        """Test default settings values."""
        settings = Settings()
        assert settings.app_name == "Alan"
        assert settings.app_env == "development"
        assert settings.debug is True
        assert settings.log_level == "DEBUG"

    def test_database_settings(self):
        """Test database settings."""
        settings = Settings()
        assert settings.database is not None
        assert settings.database.url is not None

    def test_ai_provider_settings(self):
        """Test AI provider settings."""
        settings = Settings()
        assert settings.ai_providers is not None
        assert settings.ai_providers.default_provider == "ollama"

    def test_environment_overrides(self, monkeypatch):
        """Test environment variable overrides."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("LOG_LEVEL", "INFO")

        settings = Settings()
        assert settings.app_env == "production"
        assert settings.debug is False
        assert settings.log_level == "INFO"

    def test_secret_key_generation(self, monkeypatch):
        """Test secret key is generated if not provided."""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        settings = Settings()
        assert len(settings.secret_key) > 32


class TestConfigLoading:
    """Test configuration loading."""

    def test_load_from_env(self, monkeypatch):
        """Test loading from environment variables."""
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        settings = Settings()
        assert settings.database.url == "sqlite+aiosqlite:///test.db"
        assert settings.redis.url == "redis://localhost:6379/0"

    def test_get_settings_singleton(self):
        """Test get_settings returns singleton."""
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_environment_specific_config(self):
        """Test environment-specific configurations."""
        from alan.config.environments import get_environment_settings

        dev_settings = get_environment_settings("development")
        assert dev_settings.debug is True
        assert dev_settings.api.reload is True

        prod_settings = get_environment_settings("production")
        assert prod_settings.debug is False
        assert prod_settings.api.reload is False

        test_settings = get_environment_settings("testing")
        assert test_settings.database.url == "sqlite+aiosqlite:///:memory:"
        assert test_settings.features.enable_voice is False


class TestConfigLoader:
    """Test ConfigLoader class."""

    def test_create_config_files(self, tmp_path):
        """Test creating config template files."""
        loader = load_config()
        loader.create_config_files(tmp_path)

        assert (tmp_path / "development.yaml").exists()
        assert (tmp_path / "production.yaml").exists()
        assert (tmp_path / "testing.yaml").exists()
        assert (tmp_path / "base.yaml").exists()

    def test_load_yaml_config(self, tmp_path):
        """Test loading YAML config."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
app_name: "TestApp"
debug: true
api:
  port: 9000
""")

        loader = load_config(config_dir=tmp_path)
        settings = loader.load()

        assert settings.app_name == "TestApp"
        assert settings.debug is True
        assert settings.api.port == 9000