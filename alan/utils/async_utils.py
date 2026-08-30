"""Async utilities for Alan."""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Generic, List, Optional, TypeVar, Union

T = TypeVar("T")
R = TypeVar("R")


async def run_in_executor(
    func: Callable[..., T],
    *args,
    executor: Optional[Any] = None,
    **kwargs,
) -> T:
    """Run a synchronous function in an executor."""
    loop = asyncio.get_running_loop()
    if kwargs:
        func = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(executor, func)
    return await loop.run_in_executor(executor, func, *args)


async def gather_with_concurrency(
    n: int,
    *tasks: Awaitable[T],
    return_exceptions: bool = False,
) -> List[T]:
    """Run tasks with limited concurrency."""
    semaphore = asyncio.Semaphore(n)

    async def sem_task(task: Awaitable[T]) -> T:
        async with semaphore:
            return await task

    return await asyncio.gather(*(sem_task(t) for t in tasks), return_exceptions=return_exceptions)


async def wait_for_first(
    *tasks: Awaitable[T],
    timeout: Optional[float] = None,
) -> T:
    """Wait for the first task to complete."""
    done, pending = await asyncio.wait(
        tasks,
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel pending tasks
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if not done:
        raise asyncio.TimeoutError("No tasks completed within timeout")

    # Return first result
    return done.pop().result()


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retry_on: Optional[tuple] = None,
    **kwargs,
) -> T:
    """Retry an async function with exponential backoff."""
    import random

    last_exception = None
    delay = base_delay

    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if retry_on and not isinstance(e, retry_on):
                raise

            if attempt < max_attempts - 1:
                if jitter:
                    delay = delay * exponential_base * (0.5 + random.random())
                else:
                    delay = delay * exponential_base
                delay = min(delay, max_delay)
                await asyncio.sleep(delay)
            else:
                raise

    raise last_exception


class AsyncCache(Generic[T]):
    """Simple async cache with TTL."""

    def __init__(self, ttl: float = 300):
        self.ttl = ttl
        self._cache: dict[str, tuple[T, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, default: Optional[T] = None) -> Optional[T]:
        """Get value from cache."""
        async with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if asyncio.get_event_loop().time() < expiry:
                    return value
                else:
                    del self._cache[key]
        return default

    async def set(self, key: str, value: T) -> None:
        """Set value in cache."""
        async with self._lock:
            expiry = asyncio.get_event_loop().time() + self.ttl
            self._cache[key] = (value, expiry)

    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """Clear all cache."""
        async with self._lock:
            self._cache.clear()


class AsyncRateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, rate: float, burst: int = 1):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = asyncio.get_event_loop().time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return

            # Wait for tokens
            wait_time = (tokens - self.tokens) / self.rate
            self.tokens = 0
        await asyncio.sleep(wait_time)


async def timeout_after(delay: float, coro: Awaitable[T]) -> T:
    """Run coroutine with timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=delay)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Operation timed out after {delay}s")


async def run_with_timeout(
    func: Callable[..., Awaitable[T]],
    *args,
    timeout: float = 30.0,
    **kwargs,
) -> T:
    """Run async function with timeout."""
    return await timeout_after(timeout, func(*args, **kwargs))


class TaskGroup:
    """Manage a group of async tasks."""

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Awaitable[T], name: Optional[str] = None) -> asyncio.Task[T]:
        """Create and track a task."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_all(self) -> List[Any]:
        """Wait for all tasks to complete."""
        if not self._tasks:
            return []
        return await asyncio.gather(*self._tasks, return_exceptions=True)

    async def wait_any(self) -> Any:
        """Wait for any task to complete."""
        if not self._tasks:
            return None
        done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        return done.pop().result()

    def cancel_all(self) -> None:
        """Cancel all tasks."""
        for task in self._tasks:
            task.cancel()

    async def cancel_all_and_wait(self) -> None:
        """Cancel all tasks and wait."""
        self.cancel_all()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


@asynccontextmanager
async def task_group() -> AsyncGenerator[TaskGroup, None]:
    """Context manager for task group."""
    group = TaskGroup()
    try:
        yield group
    finally:
        await group.cancel_all_and_wait()