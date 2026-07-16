"""Conversation repository."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from alan.database.models.conversation import Conversation, Message
from alan.database.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    """Repository for conversations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Conversation)

    async def get_by_user(
        self,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Conversation]:
        """Get conversations for a user."""
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_with_messages(self, id: UUID) -> Optional[Conversation]:
        """Get conversation with messages loaded."""
        from sqlalchemy.orm import selectinload
        result = await self.session.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == id)
        )
        return result.scalar_one_or_none()


class MessageRepository(BaseRepository[Message]):
    """Repository for messages."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Message)

    async def get_by_conversation(
        self,
        conversation_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Message]:
        """Get messages for a conversation."""
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_last_messages(
        self,
        conversation_id: UUID,
        count: int = 10,
    ) -> List[Message]:
        """Get last N messages for a conversation."""
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(desc(Message.created_at))
            .limit(count)
        )
        return list(reversed(result.scalars().all()))