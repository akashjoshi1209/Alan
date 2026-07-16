"""Cache utilities."""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable, Generic, Optional, TypeVar

from alan.utils.json import dumps_str, loads

T = TypeVar("T")


class CacheEntry:
    """Cache entry with expiration."""

    __slots__ = ("value", "expiry", "created")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expiry = time.time() + ttl
        self.created = time.time()

    def is_expired(self) -> bool:
        return time.time() > self.expiry


class MemoryCache(Generic[T]):
    """In-memory cache with TTL and LRU eviction."""

    def __init__(self, max_size: int = 1000, default_ttl: float = 300):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    def _make_key(self, key: Any) -> str:
        """Convert key to string."""
        if isinstance(key, str):
            return key
        return hashlib.md5(json.dumps(key, sort_keys=True).encode()).hexdigest()

    async def get(self, key: Any, default: Optional[T] = None) -> Optional[T]:
        """Get value from cache."""
        key = self._make_key(key)
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return default
            if entry.is_expired():
                del self._cache[key]
                return default
            # Move to end (LRU)
            self._cache.move_to_end(key)
            return entry.value

    async def set(self, key: Any, value: T, ttl: Optional[float] = None) -> None:
        """Set value in cache."""
        key = self._make_key(key)
        ttl = ttl or self.default_ttl
        async with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.max_size and key not in self._cache:
                self._cache.popitem(last=False)
            self._cache[key] = CacheEntry(value, ttl)
            self._cache.move_to_end(key)

    async def delete(self, key: Any) -> bool:
        """Delete key from cache."""
        key = self._make_key(key)
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """Clear all cache."""
        async with self._lock:
            self._cache.clear()

    async def cleanup_expired(self) -> int:
        """Remove expired entries."""
        async with self._lock:
            expired = [k for k, v in self._cache.items() if v.is_expired()]
            for k in expired:
                del self._cache[k]
            return len(expired)

    async def size(self) -> int:
        """Get cache size."""
        async with self._lock:
            return len(self._cache)

    def __contains__(self, key: Any) -> bool:
        key = self._make_key(key)
        return key in self._cache


class AsyncCache(MemoryCache):
    """Async-friendly memory cache with additional features."""

    async def get_or_set(
        self,
        key: Any,
        factory: Callable[[], Any],
        ttl: Optional[float] = None,
    ) -> Any:
        """Get value or compute and cache."""
        value = await self.get(key)
        if value is not None:
            return value

        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()

        await self.set(key, value, ttl)
        return value


def cached(
    cache: Optional[MemoryCache] = None,
    ttl: float = 300,
    key_prefix: str = "",
) -> Callable:
    """Decorator for caching function results."""
    _cache = cache or MemoryCache(default_ttl=ttl)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Generate cache key
            key_parts = [key_prefix, func.__module__, func.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = ":".join(key_parts)

            return await _cache.get_or_set(key, lambda: func(*args, **kwargs), ttl)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            key_parts = [key_prefix, func.__module__, func.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = ":".join(key_parts)

            value = _cache.get(key)
            if value is not None:
                return value

            result = func(*args, **kwargs)
            _cache.set(key, result, ttl)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


class RedisCache:
    """Redis cache client (requires redis package)."""

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        default_ttl: int = 300,
        key_prefix: str = "alan:",
    ):
        self.url = url
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
        self._client = None

    async def _get_client(self):
        if self._client is None:
            import redis.asyncio as redis
            self._client = redis.from_url(self.url, decode_responses=True)
        return self._client

    def _make_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    async def get(self, key: str, default: Any = None) -> Any:
        client = await self._get_client()
        value = await client.get(self._make_key(key))
        if value is None:
            return default
        return loads(value)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        client = await self._get_client()
        return await client.setex(
            self._make_key(key),
            ttl or self.default_ttl,
            dumps_str(value),
        )

    async def delete(self, key: str) -> bool:
        client = await self._get_client()
        return await client.delete(self._make_key(key)) > 0

    async def clear_pattern(self, pattern: str) -> int:
        client = await self._get_client()
        keys = await client.keys(self._make_key(pattern))
        if keys:
            return await client.delete(*keys)
        return 0

    async def close(self) -> None:
        if self._client:
            await self._client.close()


def memoize(ttl: float = 300) -> Callable:
    """Simple memoization decorator."""
    cache = MemoryCache(default_ttl=ttl)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            key = f"{func.__name__}:{args}:{frozenset(kwargs.items())}"
            return await cache.get_or_set(key, lambda: func(*args, **kwargs), ttl)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            key = f"{func.__name__}:{args}:{frozenset(kwargs.items())}"
            result = cache.get(key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            cache.set(key, result, ttl)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator