"""Test fixtures and configuration."""

import asyncio
import os
import sys
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from alan.config import Settings, get_settings
from alan.core import AlanKernel
from alan.database import DatabaseManager, get_db_manager
from alan.logging import setup_logging


# Test settings
@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Get test settings."""
    os.environ["APP_ENV"] = "testing"
    os.environ["DEBUG"] = "true"
    os.environ["LOG_LEVEL"] = "DEBUG"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    os.environ["REDIS_URL"] = "redis://localhost:6379/1"
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["DEFAULT_LLM_PROVIDER"] = "mock"

    settings = Settings()
    return settings


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_logging():
    """Setup logging for tests."""
    setup_logging(
        level="DEBUG",
        format="console",
    )


@pytest.fixture
def mock_settings(test_settings: Settings) -> Settings:
    """Provide test settings."""
    return test_settings


@pytest_asyncio.fixture
async def test_kernel(test_settings: Settings) -> AsyncGenerator[AlanKernel, None]:
    """Create test kernel."""
    kernel = AlanKernel(environment="testing")
    await kernel.start()
    yield kernel
    await kernel.stop()


@pytest_asyncio.fixture
async def test_db_manager(test_settings: Settings) -> AsyncGenerator[DatabaseManager, None]:
    """Create test database manager."""
    # Override database URL for testing
    test_settings.database.url = "sqlite+aiosqlite:///:memory:"

    manager = DatabaseManager(test_settings)
    await manager.create_tables()
    yield manager
    await manager.close()


@pytest_asyncio.fixture
async def db_session(test_db_manager: DatabaseManager) -> AsyncGenerator:
    """Get database session."""
    async with test_db_manager.session() as session:
        yield session


# Mock providers
@pytest.fixture
def mock_chat_provider():
    """Provide mock chat provider."""
    from tests.mocks.ai_provider import MockChatProvider
    return MockChatProvider()


@pytest.fixture
def mock_embedding_provider():
    """Provide mock embedding provider."""
    from tests.mocks.ai_provider import MockEmbeddingProvider
    return MockEmbeddingProvider()


@pytest.fixture
def mock_vector_store():
    """Provide mock vector store."""
    from tests.mocks.vector_store import MockVectorStore
    return MockVectorStore()


@pytest.fixture
def mock_database():
    """Provide mock database."""
    from tests.mocks.database import MockDatabaseManager
    return MockDatabaseManager()


# Test data fixtures
@pytest.fixture
def sample_user_data():
    """Sample user data."""
    return {
        "email": "test@example.com",
        "full_name": "Test User",
        "hashed_password": "hashed_password",
        "is_active": True,
        "is_superuser": False,
    }


@pytest.fixture
def sample_conversation_data():
    """Sample conversation data."""
    return {
        "title": "Test Conversation",
        "agent_id": "assistant",
    }


@pytest.fixture
def sample_message_data():
    """Sample message data."""
    return {
        "role": "user",
        "content": "Hello, this is a test message.",
    }


@pytest.fixture
def sample_agent_data():
    """Sample agent data."""
    return {
        "name": "Test Agent",
        "type": "assistant",
        "config": {},
        "tools": [],
    }


# Async utilities
@pytest.fixture
def async_mock():
    """Create async mock."""
    from unittest.mock import AsyncMock
    return AsyncMock()


# Pytest markers
def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "unit: Unit tests"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests"
    )
    config.addinivalue_line(
        "markers", "slow: Slow running tests"
    )
    config.addinivalue_line(
        "markers", "requires_db: Tests requiring database"
    )
    config.addinivalue_line(
        "markers", "requires_redis: Tests requiring Redis"
    )
    config.addinivalue_line(
        "markers", "requires_ai: Tests requiring AI provider API keys"
    )
    config.addinivalue_line(
        "markers", "requires_voice: Tests requiring voice hardware"
    )


# Test collection hooks
def pytest_collection_modifyitems(config, items):
    """Modify test collection."""
    for item in items:
        # Add markers based on test location
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)

        # Add slow marker for tests with "slow" in name
        if "slow" in item.name.lower():
            item.add_marker(pytest.mark.slow)