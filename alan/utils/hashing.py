"""Hashing utilities."""

import hashlib
import hmac
import secrets
import xxhash
from typing import Union, Optional


def md5(data: Union[str, bytes]) -> str:
    """Compute MD5 hash."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.md5(data).hexdigest()


def sha1(data: Union[str, bytes]) -> str:
    """Compute SHA1 hash."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha1(data).hexdigest()


def sha256(data: Union[str, bytes]) -> str:
    """Compute SHA256 hash."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def sha512(data: Union[str, bytes]) -> str:
    """Compute SHA512 hash."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha512(data).hexdigest()


def blake2b(data: Union[str, bytes], size: int = 64) -> str:
    """Compute BLAKE2b hash."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.blake2b(data, digest_size=size).hexdigest()


def blake2s(data: Union[str, bytes], size: int = 32) -> str:
    """Compute BLAKE2s hash."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.blake2s(data, digest_size=size).hexdigest()


def xxh64(data: Union[str, bytes], seed: int = 0) -> int:
    """Compute XXH64 hash (fast, non-cryptographic)."""
    if isinstance(data, str):
        data = data.encode()
    return xxhash.xxh64(data, seed=seed).intdigest()


def xxh3_64(data: Union[str, bytes], seed: int = 0) -> int:
    """Compute XXH3 64-bit hash."""
    if isinstance(data, str):
        data = data.encode()
    return xxhash.xxh3_64(data, seed=seed).intdigest()


def xxh3_128(data: Union[str, bytes], seed: int = 0) -> str:
    """Compute XXH3 128-bit hash."""
    if isinstance(data, str):
        data = data.encode()
    return xxhash.xxh3_128(data, seed=seed).hexdigest()


def hmac_sha256(key: Union[str, bytes], data: Union[str, bytes]) -> str:
    """Compute HMAC-SHA256."""
    if isinstance(key, str):
        key = key.encode()
    if isinstance(data, str):
        data = data.encode()
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def hmac_sha512(key: Union[str, bytes], data: Union[str, bytes]) -> str:
    """Compute HMAC-SHA512."""
    if isinstance(key, str):
        key = key.encode()
    if isinstance(data, str):
        data = data.encode()
    return hmac.new(key, data, hashlib.sha512).hexdigest()


def pbkdf2(
    password: Union[str, bytes],
    salt: Union[str, bytes],
    iterations: int = 100000,
    dklen: int = 32,
    algorithm: str = "sha256",
) -> str:
    """Derive key using PBKDF2."""
    if isinstance(password, str):
        password = password.encode()
    if isinstance(salt, str):
        salt = salt.encode()
    return hashlib.pbkdf2_hmac(algorithm, password, salt, iterations, dklen=dklen).hex()


def generate_salt(length: int = 16) -> str:
    """Generate cryptographically secure random salt."""
    return secrets.token_hex(length)


def generate_token(length: int = 32) -> str:
    """Generate secure random token."""
    return secrets.token_urlsafe(length)


def generate_api_key(prefix: str = "ak", length: int = 32) -> str:
    """Generate API key with prefix."""
    return f"{prefix}_{secrets.token_urlsafe(length)}"


def generate_uuid() -> str:
    """Generate UUID4."""
    import uuid
    return str(uuid.uuid4())


def hash_file(
    path: Union[str, Path],
    algorithm: str = "sha256",
    chunk_size: int = 8192,
) -> str:
    """Compute hash of file."""
    hasher = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_hash(data: Union[str, bytes], expected: str, algorithm: str = "sha256") -> bool:
    """Verify hash matches expected value."""
    actual = hashlib.new(algorithm)
    if isinstance(data, str):
        data = data.encode()
    actual.update(data)
    return hmac.compare_digest(actual.hexdigest(), expected)


def constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    return hmac.compare_digest(a, b)


class HashChain:
    """Chain multiple hashes together."""

    def __init__(self, algorithm: str = "sha256"):
        self.algorithm = algorithm
        self._hasher = hashlib.new(algorithm)

    def update(self, data: Union[str, bytes]) -> "HashChain":
        if isinstance(data, str):
            data = data.encode()
        self._hasher.update(data)
        return self

    def update_file(self, path: Union[str, Path], chunk_size: int = 8192) -> "HashChain":
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                self._hasher.update(chunk)
        return self

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()

    def digest(self) -> bytes:
        return self._hasher.digest()

    def copy(self) -> "HashChain":
        new = HashChain(self.algorithm)
        new._hasher = self._hasher.copy()
        return new

    def reset(self) -> "HashChain":
        self._hasher = hashlib.new(self.algorithm)
        return self


def hash_combine(*values: Union[str, bytes], separator: bytes = b"|") -> str:
    """Combine multiple values into single hash."""
    hasher = hashlib.sha256()
    for i, value in enumerate(values):
        if i > 0:
            hasher.update(separator)
        if isinstance(value, str):
            value = value.encode()
        hasher.update(value)
    return hasher.hexdigest()


def merkle_root(hashes: list[str]) -> str:
    """Compute Merkle root from list of hashes."""
    if not hashes:
        return hashlib.sha256(b"").hexdigest()

    current = [bytes.fromhex(h) for h in hashes]
    while len(current) > 1:
        next_level = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else left
            combined = left + right
            next_level.append(hashlib.sha256(combined).digest())
        current = next_level
    return current[0].hex()


def verify_merkle_proof(
    leaf: str,
    proof: list[tuple[str, bool]],  # (hash, is_left)
    root: str,
) -> bool:
    """Verify Merkle proof."""
    current = bytes.fromhex(leaf)
    for sibling_hex, is_left in proof:
        sibling = bytes.fromhex(sibling_hex)
        if is_left:
            combined = sibling + current
        else:
            combined = current + sibling
        current = hashlib.sha256(combined).digest()
    return current.hex() == root