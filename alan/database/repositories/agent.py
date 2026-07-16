"""Agent repository."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alan.database.models.agent import Agent
from alan.database.repositories.base import BaseRepository


class AgentRepository(BaseRepository[Agent]):
    """Repository for agents."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Agent)

    async def get_by_user(
        self,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Agent]:
        """Get agents for a user."""
        result = await self.session.execute(
            select(Agent)
            .where(Agent.user_id == user_id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_active(self, user_id: UUID) -> List[Agent]:
        """Get active agents for a user."""
        result = await self.session.execute(
            select(Agent)
            .where(Agent.user_id == user_id, Agent.is_active == True)
        )
        return list(result.scalars().all())

    async def get_by_type(self, user_id: UUID, type: str) -> List[Agent]:
        """Get agents by type."""
        result = await self.session.execute(
            select(Agent)
            .where(Agent.user_id == user_id, Agent.type == type)
        )
        return list(result.scalars().all())