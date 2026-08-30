"""Secrets management for Alan."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class SecretsManager:
    """Manages secrets using keyring, environment variables, or encrypted files."""

    SERVICE_NAME = "alan"
    KEYRING_AVAILABLE = KEYRING_AVAILABLE
    CRYPTO_AVAILABLE = CRYPTO_AVAILABLE

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.home() / ".config" / "alan"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._secrets_file = self.config_dir / "secrets.enc"
        self._key_file = self.config_dir / ".secrets_key"
        self._cache: Dict[str, str] = {}

    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key."""
        if self._key_file.exists():
            with open(self._key_file, "rb") as f:
                return f.read()

        # Generate new key
        import secrets
        key = secrets.token_bytes(32)
        with open(self._key_file, "wb") as f:
            f.write(key)
        # Make key file readable only by owner
        os.chmod(self._key_file, 0o600)
        return key

    def _encrypt(self, data: str) -> bytes:
        """Encrypt data."""
        if not self.CRYPTO_AVAILABLE:
            raise RuntimeError("Cryptography library not available")

        key = self._get_encryption_key()
        f = Fernet(key)
        return f.encrypt(data.encode())

    def _decrypt(self, data: bytes) -> str:
        """Decrypt data."""
        if not self.CRYPTO_AVAILABLE:
            raise RuntimeError("Cryptography library not available")

        key = self._get_encryption_key()
        f = Fernet(key)
        return f.decrypt(data).decode()

    def set_secret(self, key: str, value: str, use_keyring: bool = True) -> None:
        """Store a secret."""
        self._cache[key] = value

        # Try keyring first
        if use_keyring and self.KEYRING_AVAILABLE:
            try:
                keyring.set_password(self.SERVICE_NAME, key, value)
                return
            except Exception:
                pass

        # Fall back to encrypted file
        self._save_to_file()

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a secret."""
        # Check cache first
        if key in self._cache:
            return self._cache[key]

        # Try keyring
        if self.KEYRING_AVAILABLE:
            try:
                value = keyring.get_password(self.SERVICE_NAME, key)
                if value is not None:
                    self._cache[key] = value
                    return value
            except Exception:
                pass

        # Try environment variable
        env_key = f"ALAN_{key.upper()}"
        value = os.environ.get(env_key)
        if value is not None:
            self._cache[key] = value
            return value

        # Try encrypted file
        if self._secrets_file.exists():
            self._load_from_file()
            if key in self._cache:
                return self._cache[key]

        return default

    def delete_secret(self, key: str) -> bool:
        """Delete a secret."""
        deleted = False

        # Delete from keyring
        if self.KEYRING_AVAILABLE:
            try:
                keyring.delete_password(self.SERVICE_NAME, key)
                deleted = True
            except Exception:
                pass

        # Delete from cache
        if key in self._cache:
            del self._cache[key]
            deleted = True

        # Update file
        if self._secrets_file.exists():
            self._save_to_file()

        return deleted

    def _load_from_file(self) -> None:
        """Load secrets from encrypted file."""
        if not self._secrets_file.exists():
            return

        try:
            with open(self._secrets_file, "rb") as f:
                encrypted_data = f.read()

            if not encrypted_data:
                return

            decrypted = self._decrypt(encrypted_data)
            import json
            self._cache = json.loads(decrypted)
        except Exception:
            # If decryption fails, clear cache and file
            self._cache = {}
            if self._secrets_file.exists():
                self._secrets_file.unlink()

    def _save_to_file(self) -> None:
        """Save secrets to encrypted file."""
        if not self.CRYPTO_AVAILABLE:
            return

        import json
        data = json.dumps(self._cache).encode()
        encrypted = self._encrypt(data.decode())

        with open(self._secrets_file, "wb") as f:
            f.write(encrypted)
        os.chmod(self._secrets_file, 0o600)

    def list_secrets(self) -> Dict[str, str]:
        """List all secrets (without values)."""
        secrets = {}

        # From cache
        for key in self._cache:
            secrets[key] = "***"

        # From keyring
        if self.KEYRING_AVAILABLE:
            try:
                # Note: keyring doesn't support listing all keys
                pass
            except Exception:
                pass

        # From environment
        for key, value in os.environ.items():
            if key.startswith("ALAN_"):
                secret_key = key[5:].lower()
                if secret_key not in secrets:
                    secrets[secret_key] = "*** (env)"

        return secrets

    def migrate_from_env(self, prefix: str = "ALAN_") -> int:
        """Migrate secrets from environment variables."""
        count = 0
        for key, value in os.environ.items():
            if key.startswith(prefix):
                secret_key = key[len(prefix):].lower()
                self.set_secret(secret_key, value, use_keyring=True)
                count += 1
        return count


class SecretReference:
    """Reference to a secret that can be resolved at runtime."""

    def __init__(self, key: str, default: Optional[str] = None):
        self.key = key
        self.default = default

    def resolve(self, manager: Optional[SecretsManager] = None) -> Optional[str]:
        """Resolve the secret."""
        mgr = manager or get_secrets_manager()
        return mgr.get_secret(self.key, self.default)

    def __str__(self) -> str:
        return f"SecretRef({self.key})"

    def __repr__(self) -> str:
        return f"SecretReference(key={self.key!r}, default={self.default!r})"


# Global secrets manager instance
_secrets_manager: Optional[SecretsManager] = None


def get_secrets_manager(config_dir: Optional[Path] = None) -> SecretsManager:
    """Get the global secrets manager instance."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager(config_dir)
    return _secrets_manager


def set_secret(key: str, value: str, use_keyring: bool = True) -> None:
    """Convenience function to set a secret."""
    get_secrets_manager().set_secret(key, value, use_keyring)


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Convenience function to get a secret."""
    return get_secrets_manager().get_secret(key, default)


def delete_secret(key: str) -> bool:
    """Convenience function to delete a secret."""
    return get_secrets_manager().delete_secret(key)


def resolve_secret_refs(obj: Any, manager: Optional[SecretsManager] = None) -> Any:
    """Recursively resolve SecretReference objects in a data structure."""
    if isinstance(obj, SecretReference):
        return obj.resolve(manager)
    elif isinstance(obj, dict):
        return {k: resolve_secret_refs(v, manager) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_secret_refs(item, manager) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(resolve_secret_refs(item, manager) for item in obj)
    return obj