"""Unit of Work pattern for database transactions."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from alan.database.base import DatabaseManager, get_db_manager
from alan.database.repositories.conversation import ConversationRepository, MessageRepository
from alan.database.repositories.agent import AgentRepository
from alan.database.repositories.memory import (
    MemoryVectorRepository,
    MemoryEntityRepository,
    MemoryRelationRepository,
)


class UnitOfWork:
    """Unit of Work for managing database transactions."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self._manager = manager or get_db_manager()
        self._session: Optional[AsyncSession] = None

        # Repositories
        self.conversations: Optional[ConversationRepository] = None
        self.messages: Optional[MessageRepository] = None
        self.agents: Optional[AgentRepository] = None
        self.memory_vectors: Optional[MemoryVectorRepository] = None
        self.memory_entities: Optional[MemoryEntityRepository] = None
        self.memory_relations: Optional[MemoryRelationRepository] = None

    async def __aenter__(self) -> "UnitOfWork":
        self._session = self._manager.session_factory()
        self._init_repositories()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        await self.close()

    def _init_repositories(self) -> None:
        """Initialize repositories with current session."""
        if self._session is None:
            raise RuntimeError("Session not initialized")
        self.conversations = ConversationRepository(self._session)
        self.messages = MessageRepository(self._session)
        self.agents = AgentRepository(self._session)
        self.memory_vectors = MemoryVectorRepository(self._session)
        self.memory_entities = MemoryEntityRepository(self._session)
        self.memory_relations = MemoryRelationRepository(self._session)

    async def commit(self) -> None:
        """Commit the transaction."""
        if self._session:
            await self._session.commit()

    async def rollback(self) -> None:
        """Rollback the transaction."""
        if self._session:
            await self._session.rollback()

    async def close(self) -> None:
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> AsyncSession:
        """Get the current session."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context.")
        return self._session


@asynccontextmanager
async def unit_of_work() -> AsyncGenerator[UnitOfWork, None]:
    """Context manager for unit of work."""
    uow = UnitOfWork()
    try:
        await uow.__aenter__()
        yield uow
    except Exception:
        await uow.rollback()
        raise
    else:
        await uow.commit()
    finally:
        await uow.close()