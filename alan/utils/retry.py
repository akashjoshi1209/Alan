"""Retry utilities with exponential backoff."""

import asyncio
import functools
import random
import time
from collections.abc import Callable
from typing import Any, Optional, Type, TypeVar, Union

from tenacity import (
    AsyncRetrying,
    Retrying,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
    wait_random,
    retry_if_exception_type,
    retry_if_result,
    before_sleep_log,
    after_log,
)
import logging


logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryPolicy:
    """Configurable retry policy."""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retry_exceptions: tuple = (Exception,),
        timeout: Optional[float] = None,
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retry_exceptions = retry_exceptions
        self.timeout = timeout

    def _get_wait(self):
        """Get wait strategy."""
        wait = wait_exponential(
            multiplier=self.base_delay,
            max=self.max_delay,
            exp_base=self.exponential_base,
        )
        if self.jitter:
            wait = wait_random(0, self.base_delay) + wait
        return wait

    def get_sync(self) -> Retrying:
        """Get synchronous retrying."""
        return Retrying(
            stop=stop_after_attempt(self.max_attempts) |
                 (stop_after_delay(self.timeout) if self.timeout else stop_after_attempt(1)),
            wait=self._get_wait(),
            retry=retry_if_exception_type(self.retry_exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            after=after_log(logger, logging.INFO),
            reraise=True,
        )

    def get_async(self) -> AsyncRetrying:
        """Get asynchronous retrying."""
        return AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts) |
                 (stop_after_delay(self.timeout) if self.timeout else stop_after_attempt(1)),
            wait=self._get_wait(),
            retry=retry_if_exception_type(self.retry_exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            after=after_log(logger, logging.INFO),
            reraise=True,
        )


# Default policies
DEFAULT_POLICY = RetryPolicy()
AGGRESSIVE_POLICY = RetryPolicy(max_attempts=5, base_delay=0.5, max_delay=30.0)
CONSERVATIVE_POLICY = RetryPolicy(max_attempts=3, base_delay=2.0, max_delay=120.0)
NETWORK_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay=1.0,
    max_delay=60.0,
    retry_exceptions=(ConnectionError, TimeoutError, IOError, OSError),
)


def with_retry(
    policy: Optional[RetryPolicy] = None,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Decorator for adding retry logic to functions."""
    if policy is None:
        policy = RetryPolicy(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            retry_exceptions=exceptions,
        )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> T:
                retrying = policy.get_async()
                return await retrying(func, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> T:
                retrying = policy.get_sync()
                return retrying(func, *args, **kwargs)
            return sync_wrapper

    return decorator


async def retry_async(
    func: Callable[..., T],
    *args,
    policy: Optional[RetryPolicy] = None,
    **kwargs,
) -> T:
    """Execute async function with retry."""
    policy = policy or DEFAULT_POLICY
    retrying = policy.get_async()
    return await retrying(func, *args, **kwargs)


def retry_sync(
    func: Callable[..., T],
    *args,
    policy: Optional[RetryPolicy] = None,
    **kwargs,
) -> T:
    """Execute sync function with retry."""
    policy = policy or DEFAULT_POLICY
    retrying = policy.get_sync()
    return retrying(func, *args, **kwargs)


class CircuitBreaker:
    """Circuit breaker pattern implementation."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._failures = 0
        self._last_failure_time: Optional[float] = None
        self._state = "closed"  # closed, open, half-open
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        if self._state == "open" and self._last_failure_time:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                return "half-open"
        return self._state

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute function with circuit breaker."""
        async with self._lock:
            if self.state == "open":
                raise CircuitBreakerOpenError("Circuit breaker is open")

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            async with self._lock:
                self._failures = 0
                self._state = "closed"
            return result

        except self.expected_exception as e:
            async with self._lock:
                self._failures += 1
                self._last_failure_time = time.time()
                if self._failures >= self.failure_threshold:
                    self._state = "open"
            raise

    def reset(self) -> None:
        """Reset circuit breaker."""
        self._failures = 0
        self._last_failure_time = None
        self._state = "closed"


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


def exponential_backoff(
    attempt: int,
    base: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
) -> float:
    """Calculate exponential backoff delay."""
    delay = min(base * (2 ** attempt), max_delay)
    if jitter:
        delay *= random.uniform(0.5, 1.5)
    return delay


async def with_timeout(
    coro: Any,
    timeout: float,
    default: Any = None,
) -> Any:
    """Execute coroutine with timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return default


def timeout_decorator(timeout: float, default: Any = None):
    """Decorator for async timeout."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await with_timeout(func(*args, **kwargs), timeout, default)
        return wrapper
    return decorator


class TaskGroup:
    """Manage a group of async tasks with retry support."""

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self.policy = policy or DEFAULT_POLICY
        self._tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Any, name: Optional[str] = None) -> asyncio.Task:
        """Create and track a task."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_all(self) -> list[Any]:
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
async def task_group(policy: Optional[RetryPolicy] = None) -> TaskGroup:
    """Context manager for task group."""
    group = TaskGroup(policy)
    try:
        yield group
    finally:
        await group.cancel_all_and_wait()