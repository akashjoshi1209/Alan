"""Database package for Alan."""

from alan.database.base import (
    Base,
    DatabaseManager,
    get_db_manager,
    set_db_manager,
    get_session,
    get_db_session,
)
from alan.database.session import get_session, get_db_session
from alan.database.models import (
    User,
    APIKey,
    Conversation,
    Message,
    Agent,
    Tool,
    MemoryVector,
    MemoryEntity,
    MemoryRelation,
    MCPServer,
    AuditLog,
)
from alan.database.repositories import (
    BaseRepository,
    ConversationRepository,
    MessageRepository,
    AgentRepository,
    MemoryVectorRepository,
    MemoryEntityRepository,
    MemoryRelationRepository,
)
from alan.database.unit_of_work import UnitOfWork, unit_of_work

__all__ = [
    # Base
    "Base",
    "DatabaseManager",
    "get_db_manager",
    "set_db_manager",
    "get_session",
    "get_db_session",
    # Models
    "User",
    "APIKey",
    "Conversation",
    "Message",
    "Agent",
    "Tool",
    "MemoryVector",
    "MemoryEntity",
    "MemoryRelation",
    "MCPServer",
    "AuditLog",
    # Repositories
    "BaseRepository",
    "ConversationRepository",
    "MessageRepository",
    "AgentRepository",
    "MemoryVectorRepository",
    "MemoryEntityRepository",
    "MemoryRelationRepository",
    # Unit of Work
    "UnitOfWork",
    "unit_of_work",
]