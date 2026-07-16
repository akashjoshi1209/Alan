"""Application state management for Alan."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from alan.logging import get_logger

logger = get_logger(__name__)


class StateScope(Enum):
    """State scope levels."""

    GLOBAL = "global"
    USER = "user"
    SESSION = "session"
    CONVERSATION = "conversation"
    AGENT = "agent"
    TOOL = "tool"


@dataclass
class StateEntry:
    """A single state entry with metadata."""

    key: str
    value: Any
    scope: StateScope = StateScope.GLOBAL
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def touch(self) -> None:
        """Update the last accessed timestamp."""
        self.updated_at = datetime.utcnow()


class ApplicationState:
    """Centralized application state management."""

    def __init__(self):
        self._state: Dict[str, StateEntry] = {}
        self._scopes: Dict[StateScope, Set[str]] = {
            scope: set() for scope in StateScope
        }
        self._lock = asyncio.Lock()
        self._listeners: Dict[str, List[callable]] = {}

    async def set(
        self,
        key: str,
        value: Any,
        scope: StateScope = StateScope.GLOBAL,
        ttl: Optional[float] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set a state value."""
        async with self._lock:
            expires_at = None
            if ttl is not None:
                from datetime import timedelta
                expires_at = datetime.utcnow() + timedelta(seconds=ttl)

            entry = StateEntry(
                key=key,
                value=value,
                scope=scope,
                expires_at=expires_at,
                tags=tags or [],
                metadata=metadata or {},
            )

            old_value = self._state.get(key)
            self._state[key] = entry
            self._scopes[scope].add(key)

            # Notify listeners
            await self._notify_listeners(key, value, old_value.value if old_value else None)

            logger.debug("State set", key=key, scope=scope.value)

    async def get(
        self,
        key: str,
        default: Any = None,
        scope: Optional[StateScope] = None,
    ) -> Any:
        """Get a state value."""
        async with self._lock:
            entry = self._state.get(key)
            if entry is None:
                return default

            # Check expiration
            if entry.is_expired():
                await self._remove_entry(key)
                return default

            # Check scope filter
            if scope is not None and entry.scope != scope:
                return default

            entry.touch()
            return entry.value

    async def get_all(self, scope: Optional[StateScope] = None) -> Dict[str, Any]:
        """Get all state values, optionally filtered by scope."""
        async with self._lock:
            result = {}
            for key, entry in self._state.items():
                if entry.is_expired():
                    continue
                if scope is not None and entry.scope != scope:
                    continue
                result[key] = entry.value
            return result

    async def delete(self, key: str) -> bool:
        """Delete a state value."""
        async with self._lock:
            return await self._remove_entry(key)

    async def _remove_entry(self, key: str) -> bool:
        """Internal method to remove an entry."""
        entry = self._state.pop(key, None)
        if entry:
            self._scopes[entry.scope].discard(key)
            logger.debug("State deleted", key=key)
            return True
        return False

    async def clear(self, scope: Optional[StateScope] = None) -> int:
        """Clear state, optionally filtered by scope."""
        async with self._lock:
            if scope is None:
                count = len(self._state)
                self._state.clear()
                for s in self._scopes:
                    self._scopes[s].clear()
                logger.debug("All state cleared", count=count)
                return count

            keys_to_remove = list(self._scopes[scope])
            for key in keys_to_remove:
                await self._remove_entry(key)
            logger.debug("Scope cleared", scope=scope.value, count=len(keys_to_remove))
            return len(keys_to_remove)

    async def exists(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        async with self._lock:
            entry = self._state.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                await self._remove_entry(key)
                return False
            return True

    async def get_entry(self, key: str) -> Optional[StateEntry]:
        """Get full state entry with metadata."""
        async with self._lock:
            entry = self._state.get(key)
            if entry and entry.is_expired():
                await self._remove_entry(key)
                return None
            return entry

    async def get_keys_by_tag(self, tag: str) -> List[str]:
        """Get all keys with a specific tag."""
        async with self._lock:
            return [
                key for key, entry in self._state.items()
                if tag in entry.tags and not entry.is_expired()
            ]

    async def get_keys_by_scope(self, scope: StateScope) -> List[str]:
        """Get all keys in a scope."""
        async with self._lock:
            return [
                key for key in self._scopes[scope]
                if key in self._state and not self._state[key].is_expired()
            ]

    # Listener support
    def add_listener(self, key: str, callback: callable) -> None:
        """Add a listener for state changes."""
        if key not in self._listeners:
            self._listeners[key] = []
        self._listeners[key].append(callback)

    def remove_listener(self, key: str, callback: callable) -> bool:
        """Remove a listener."""
        if key in self._listeners:
            try:
                self._listeners[key].remove(callback)
                return True
            except ValueError:
                pass
        return False

    async def _notify_listeners(
        self, key: str, new_value: Any, old_value: Any
    ) -> None:
        """Notify listeners of state change."""
        if key in self._listeners:
            for callback in self._listeners[key]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(key, new_value, old_value)
                    else:
                        callback(key, new_value, old_value)
                except Exception as e:
                    logger.error("Listener error", key=key, error=str(e))

    # Convenience methods for common patterns
    async def increment(self, key: str, amount: int = 1, default: int = 0) -> int:
        """Atomically increment a numeric value."""
        async with self._lock:
            current = await self.get(key, default)
            if not isinstance(current, (int, float)):
                current = default
            new_value = current + amount
            await self.set(key, new_value)
            return new_value

    async def append(self, key: str, value: Any) -> List[Any]:
        """Append to a list value."""
        async with self._lock:
            current = await self.get(key, [])
            if not isinstance(current, list):
                current = [current] if current is not None else []
            current.append(value)
            await self.set(key, current)
            return current

    async def setdefault(self, key: str, default: Any) -> Any:
        """Get value or set default if not exists."""
        async with self._lock:
            value = await self.get(key)
            if value is None:
                await self.set(key, default)
                return default
            return value


# Global application state
_app_state: Optional[ApplicationState] = None


def get_app_state() -> ApplicationState:
    """Get the global application state."""
    global _app_state
    if _app_state is None:
        _app_state = ApplicationState()
    return _app_state


def set_app_state(state: ApplicationState) -> None:
    """Set the global application state (mainly for testing)."""
    global _app_state
    _app_state = state


class ConversationState:
    """State management for a conversation session."""

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.state = ApplicationState()
        self._created_at = datetime.utcnow()

    async def get(self, key: str, default: Any = None) -> Any:
        return await self.state.get(key, default)

    async def set(self, key: str, value: Any, **kwargs) -> None:
        await self.state.set(key, value, scope=StateScope.CONVERSATION, **kwargs)

    async def delete(self, key: str) -> bool:
        return await self.state.delete(key)

    async def clear(self) -> int:
        return await self.state.clear(StateScope.CONVERSATION)


class SessionState:
    """State management for a user session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = ApplicationState()
        self._created_at = datetime.utcnow()

    async def get(self, key: str, default: Any = None) -> Any:
        return await self.state.get(key, default)

    async def set(self, key: str, value: Any, **kwargs) -> None:
        await self.state.set(key, value, scope=StateScope.SESSION, **kwargs)

    async def delete(self, key: str) -> bool:
        return await self.state.delete(key)

    async def clear(self) -> int:
        return await self.state.clear(StateScope.SESSION)