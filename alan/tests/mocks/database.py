"""Mock database for testing."""

from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession


class MockAsyncSession:
    """Mock async database session."""

    def __init__(self):
        self.data: Dict[str, List[Dict[str, Any]]] = {}
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._added: List[Any] = []
        self._deleted: List[Any] = []

    def add(self, obj: Any) -> None:
        """Add object to session."""
        self._added.append(obj)

    def delete(self, obj: Any) -> None:
        """Delete object from session."""
        self._deleted.append(obj)

    async def commit(self) -> None:
        """Commit session."""
        self.commits += 1
        self._added.clear()
        self._deleted.clear()

    async def rollback(self) -> None:
        """Rollback session."""
        self.rollbacks += 1
        self._added.clear()
        self._deleted.clear()

    async def close(self) -> None:
        """Close session."""
        self.closed = True

    async def execute(self, query: Any) -> Any:
        """Execute query."""
        return MockResult()

    async def flush(self) -> None:
        """Flush pending changes."""
        pass

    async def refresh(self, obj: Any) -> None:
        """Refresh object."""
        pass


class MockResult:
    """Mock query result."""

    def __init__(self, rows: Optional[List] = None):
        self.rows = rows or []
        self._row = None

    def scalar(self) -> Any:
        """Get scalar result."""
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> Any:
        """Get exactly one scalar result."""
        if not self.rows:
            raise Exception("No result found")
        return self.rows[0]

    def scalar_one_or_none(self) -> Any:
        """Get one scalar or None."""
        return self.rows[0] if self.rows else None

    def scalars(self) -> Any:
        """Get scalar results."""
        return self.rows

    def all(self) -> List:
        """Get all results."""
        return self.rows

    def first(self) -> Any:
        """Get first result."""
        return self.rows[0] if self.rows else None


class MockDatabaseManager:
    """Mock database manager."""

    def __init__(self):
        self.session_factory = MagicMock(return_value=MockAsyncSession())
        self.engine = MagicMock()
        self.tables_created = False

    async def create_tables(self) -> None:
        self.tables_created = True

    async def drop_tables(self) -> None:
        self.tables_created = False

    async def close(self) -> None:
        pass

    def get_session(self) -> MockAsyncSession:
        """Get a mock session."""
        return self.session_factory()


def create_mock_session() -> MockAsyncSession:
    """Create a mock async session."""
    return MockAsyncSession()


def create_mock_database_manager() -> MockDatabaseManager:
    """Create a mock database manager."""
    return MockDatabaseManager()


# Test data generators
def generate_test_user(**overrides) -> Dict[str, Any]:
    """Generate test user data."""
    return {
        "id": str(uuid4()),
        "email": overrides.get("email", "test@example.com"),
        "hashed_password": overrides.get("hashed_password", "hashed_password"),
        "full_name": overrides.get("full_name", "Test User"),
        "is_active": overrides.get("is_active", True),
        "is_superuser": overrides.get("is_superuser", False),
    }


def generate_test_conversation(**overrides) -> Dict[str, Any]:
    """Generate test conversation data."""
    return {
        "id": str(uuid4()),
        "user_id": overrides.get("user_id", str(uuid4())),
        "title": overrides.get("title", "Test Conversation"),
        "agent_id": overrides.get("agent_id"),
        "metadata": overrides.get("metadata", {}),
    }


def generate_test_message(**overrides) -> Dict[str, Any]:
    """Generate test message data."""
    return {
        "id": str(uuid4()),
        "conversation_id": overrides.get("conversation_id", str(uuid4())),
        "role": overrides.get("role", "user"),
        "content": overrides.get("content", "Test message"),
        "tool_calls": overrides.get("tool_calls"),
        "tool_call_id": overrides.get("tool_call_id"),
        "metadata": overrides.get("metadata", {}),
    }


def generate_test_agent(**overrides) -> Dict[str, Any]:
    """Generate test agent data."""
    return {
        "id": str(uuid4()),
        "user_id": overrides.get("user_id", str(uuid4())),
        "name": overrides.get("name", "Test Agent"),
        "type": overrides.get("type", "assistant"),
        "config": overrides.get("config", {}),
        "system_prompt": overrides.get("system_prompt"),
        "tools": overrides.get("tools", []),
        "is_active": overrides.get("is_active", True),
    }