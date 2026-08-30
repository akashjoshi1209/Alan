"""Structured logging context for Alan."""

import contextvars
from typing import Any, Dict, Optional, ContextManager
from contextlib import contextmanager

import structlog

# Context variables for structured logging
request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("user_id", default=None)
conversation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("conversation_id", default=None)
agent_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("agent_id", default=None)
trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
span_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("span_id", default=None)


def bind_context(**kwargs: Any) -> ContextManager[None]:
    """Bind context variables for the current context."""
    tokens = []
    try:
        for key, value in kwargs.items():
            var = _get_var(key)
            if var:
                tokens.append(var.set(value))
        yield
    finally:
        for token in tokens:
            token.var.reset(token)


@contextmanager
def request_context(
    request_id: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> ContextManager[None]:
    """Create a request-scoped logging context."""
    with bind_context(
        request_id=request_id,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
    ):
        yield


@contextmanager
def trace_context(
    trace_id: str,
    span_id: str,
) -> ContextManager[None]:
    """Create a trace context."""
    with bind_context(trace_id=trace_id, span_id=span_id):
        yield


def _get_var(key: str) -> Optional[contextvars.ContextVar]:
    """Get context variable by key."""
    var_map = {
        "request_id": request_id_var,
        "user_id": user_id_var,
        "conversation_id": conversation_id_var,
        "agent_id": agent_id_var,
        "trace_id": trace_id_var,
        "span_id": span_id_var,
    }
    return var_map.get(key)


def get_context() -> Dict[str, Any]:
    """Get all current context values."""
    return {
        "request_id": request_id_var.get(),
        "user_id": user_id_var.get(),
        "conversation_id": conversation_id_var.get(),
        "agent_id": agent_id_var.get(),
        "trace_id": trace_id_var.get(),
        "span_id": span_id_var.get(),
    }


def clear_context() -> None:
    """Clear all context variables."""
    for var in [
        request_id_var, user_id_var, conversation_id_var,
        agent_id_var, trace_id_var, span_id_var
    ]:
        var.set(None)


def add_app_context(logger: Any, method_name: str, event_dict: Dict) -> Dict:
    """Add application context to log entries."""
    context = get_context()
    for key, value in context.items():
        if value is not None:
            event_dict[key] = value
    return event_dict


def with_context(func):
    """Decorator to pass context to async functions."""
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Context is automatically inherited via contextvars
        return await func(*args, **kwargs)
    return wrapper