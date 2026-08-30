"""Database session management."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from alan.database.base import DatabaseManager, get_db_manager

# Re-export for convenience
__all__ = [
    "get_session",
    "get_db_manager",
    "DatabaseManager",
]


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for getting a database session."""
    manager = get_db_manager()
    async with manager.session() as session:
        yield session


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for getting a database session."""
    manager = get_db_manager()
    async with manager.session() as session:
        yield session