"""Memory repository."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alan.database.models.memory import MemoryVector, MemoryEntity, MemoryRelation
from alan.database.repositories.base import BaseRepository


class MemoryVectorRepository(BaseRepository[MemoryVector]):
    """Repository for vector memory."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, MemoryVector)

    async def get_by_user_and_collection(
        self,
        user_id: UUID,
        collection_name: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryVector]:
        """Get vectors by user and collection."""
        result = await self.session.execute(
            select(MemoryVector)
            .where(
                MemoryVector.user_id == user_id,
                MemoryVector.collection_name == collection_name,
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_vector_id(self, vector_id: str) -> Optional[MemoryVector]:
        """Get vector by external vector ID."""
        result = await self.session.execute(
            select(MemoryVector).where(MemoryVector.vector_id == vector_id)
        )
        return result.scalar_one_or_none()


class MemoryEntityRepository(BaseRepository[MemoryEntity]):
    """Repository for graph memory entities."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, MemoryEntity)

    async def get_by_user(
        self,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryEntity]:
        """Get entities for a user."""
        result = await self.session.execute(
            select(MemoryEntity)
            .where(MemoryEntity.user_id == user_id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_name_and_type(
        self, user_id: UUID, name: str, type: str
    ) -> Optional[MemoryEntity]:
        """Get entity by name and type."""
        result = await self.session.execute(
            select(MemoryEntity).where(
                MemoryEntity.user_id == user_id,
                MemoryEntity.name == name,
                MemoryEntity.type == type,
            )
        )
        return result.scalar_one_or_none()


class MemoryRelationRepository(BaseRepository[MemoryRelation]):
    """Repository for graph memory relations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, MemoryRelation)

    async def get_by_user(
        self,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRelation]:
        """Get relations for a user."""
        result = await self.session.execute(
            select(MemoryRelation)
            .where(MemoryRelation.user_id == user_id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_source(self, source_id: UUID) -> List[MemoryRelation]:
        """Get relations from a source entity."""
        result = await self.session.execute(
            select(MemoryRelation).where(MemoryRelation.source_id == source_id)
        )
        return list(result.scalars().all())

    async def get_by_target(self, target_id: UUID) -> List[MemoryRelation]:
        """Get relations to a target entity."""
        result = await self.session.execute(
            select(MemoryRelation).where(MemoryRelation.target_id == target_id)
        )
        return list(result.scalars().all())