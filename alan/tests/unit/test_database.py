"""Unit tests for database layer."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from alan.database import (
    DatabaseManager,
    get_db_manager,
    set_db_manager,
    Base,
    get_session,
)
from alan.database.models import (
    User,
    Conversation,
    Message,
    Agent,
    Tool,
    APIKey,
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


class TestDatabaseManager:
    """Test DatabaseManager."""

    @pytest.fixture
    def db_manager(self):
        """Create database manager."""
        from alan.config import Settings
        settings = Settings(
            database=DatabaseSettings(url="sqlite+aiosqlite:///:memory:")
        )
        return DatabaseManager(settings)

    @pytest.mark.asyncio
    async def test_create_engine(self, db_manager):
        """Test engine creation."""
        engine = db_manager.engine
        assert engine is not None

    @pytest.mark.asyncio
    async def test_session_factory(self, db_manager):
        """Test session factory."""
        factory = db_manager.session_factory
        assert factory is not None

        async with factory() as session:
            assert session is not None

    @pytest.mark.asyncio
    async def test_create_tables(self, db_manager):
        """Test table creation."""
        await db_manager.create_tables()
        # Tables should be created without error

    @pytest.mark.asyncio
    async def test_drop_tables(self, db_manager):
        """Test table dropping."""
        await db_manager.create_tables()
        await db_manager.drop_tables()

    @pytest.mark.asyncio
    async def test_health_check(self, db_manager):
        """Test database health check."""
        result = await db_manager.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_close(self, db_manager):
        """Test closing engine."""
        await db_manager.close()
        assert db_manager._engine is None


class TestDatabaseModels:
    """Test database models."""

    def test_user_model(self):
        """Test User model creation."""
        user = User(
            id=uuid4(),
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
        )
        assert user.email == "test@example.com"
        assert user.full_name == "Test User"
        assert user.is_active is True
        assert user.is_superuser is False

    def test_conversation_model(self):
        """Test Conversation model creation."""
        user_id = uuid4()
        conv = Conversation(
            id=uuid4(),
            user_id=user_id,
            title="Test Conversation",
            agent_id="assistant",
        )
        assert conv.user_id == user_id
        assert conv.title == "Test Conversation"

    def test_message_model(self):
        """Test Message model creation."""
        conv_id = uuid4()
        msg = Message(
            id=uuid4(),
            conversation_id=conv_id,
            role="user",
            content="Test message",
        )
        assert msg.conversation_id == conv_id
        assert msg.role == "user"
        assert msg.content == "Test message"

    def test_agent_model(self):
        """Test Agent model creation."""
        user_id = uuid4()
        agent = Agent(
            id=uuid4(),
            user_id=user_id,
            name="Test Agent",
            type="assistant",
        )
        assert agent.name == "Test Agent"
        assert agent.type == "assistant"

    def test_tool_model(self):
        """Test Tool model creation."""
        tool = Tool(
            id=uuid4(),
            name="test_tool",
            description="A test tool",
            schema={"type": "object", "properties": {}},
        )
        assert tool.name == "test_tool"
        assert tool.is_builtin is False

    def test_memory_vector_model(self):
        """Test MemoryVector model creation."""
        user_id = uuid4()
        mv = MemoryVector(
            id=uuid4(),
            user_id=user_id,
            collection_name="test",
            vector_id="vec_123",
            content="Test content",
        )
        assert mv.collection_name == "test"
        assert mv.vector_id == "vec_123"

    def test_memory_entity_model(self):
        """Test MemoryEntity model creation."""
        user_id = uuid4()
        me = MemoryEntity(
            id=uuid4(),
            user_id=user_id,
            name="Test Entity",
            type="concept",
        )
        assert me.name == "Test Entity"
        assert me.type == "concept"

    def test_mcp_server_model(self):
        """Test MCPServer model creation."""
        user_id = uuid4()
        server = MCPServer(
            id=uuid4(),
            user_id=user_id,
            name="Test Server",
            transport="stdio",
            command="python -m test_server",
        )
        assert server.name == "Test Server"
        assert server.transport == "stdio"

    def test_audit_log_model(self):
        """Test AuditLog model creation."""
        log = AuditLog(
            id=uuid4(),
            user_id=uuid4(),
            action="test_action",
            resource_type="conversation",
            resource_id=uuid4(),
        )
        assert log.action == "test_action"
        assert log.resource_type == "conversation"


class TestRepositories:
    """Test repositories."""

    @pytest.fixture
    async def session(self, db_manager):
        """Get database session."""
        async with db_manager.session_factory() as session:
            yield session

    @pytest.mark.asyncio
    async def test_base_repository_crud(self, session):
        """Test base repository CRUD operations."""
        repo = BaseRepository(session, User)

        # Create
        user = await repo.create(
            email="test@example.com",
            hashed_password="hashed",
        )
        assert user.id is not None

        # Get
        retrieved = await repo.get(user.id)
        assert retrieved is not None
        assert retrieved.email == "test@example.com"

        # Update
        updated = await repo.update(user.id, full_name="Updated Name")
        assert updated.full_name == "Updated Name"

        # Get all
        users = await repo.get_all(limit=10)
        assert len(users) >= 1

        # Count
        count = await repo.count()
        assert count >= 1

        # Exists
        exists = await repo.exists(user.id)
        assert exists is True

        # Delete
        deleted = await repo.delete(user.id)
        assert deleted is True
        assert await repo.get(user.id) is None

    @pytest.mark.asyncio
    async def test_conversation_repository(self, session):
        """Test conversation repository."""
        repo = ConversationRepository(session)

        user_id = uuid4()
        conv = await repo.create(
            user_id=user_id,
            title="Test",
        )
        assert conv.title == "Test"

        # Get by user
        convs = await repo.get_by_user(user_id)
        assert len(convs) == 1

    @pytest.mark.asyncio
    async def test_message_repository(self, session):
        """Test message repository."""
        repo = MessageRepository(session)

        conv_id = uuid4()
        msg = await repo.create(
            conversation_id=conv_id,
            role="user",
            content="Test",
        )
        assert msg.content == "Test"

        # Get by conversation
        msgs = await repo.get_by_conversation(conv_id)
        assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_agent_repository(self, session):
        """Test agent repository."""
        repo = AgentRepository(session)

        user_id = uuid4()
        agent = await repo.create(
            user_id=user_id,
            name="Test Agent",
            type="assistant",
        )
        assert agent.name == "Test Agent"

        # Get by user
        agents = await repo.get_by_user(user_id)
        assert len(agents) == 1

    @pytest.mark.asyncio
    async def test_memory_repositories(self, session):
        """Test memory repositories."""
        vector_repo = MemoryVectorRepository(session)
        entity_repo = MemoryEntityRepository(session)
        relation_repo = MemoryRelationRepository(session)

        user_id = uuid4()

        # Vector
        vec = await vector_repo.create(
            user_id=user_id,
            collection_name="test",
            vector_id="vec_1",
            content="Test content",
        )
        assert vec.vector_id == "vec_1"

        # Entity
        entity = await entity_repo.create(
            user_id=user_id,
            name="Test Entity",
            type="concept",
        )
        assert entity.name == "Test Entity"

        # Relation
        entity2 = await entity_repo.create(
            user_id=user_id,
            name="Test Entity 2",
            type="concept",
        )
        rel = await relation_repo.create(
            user_id=user_id,
            source_id=entity.id,
            target_id=entity2.id,
            relation_type="related_to",
        )
        assert rel.relation_type == "related_to"


class TestUnitOfWork:
    """Test Unit of Work pattern."""

    @pytest.mark.asyncio
    async def test_unit_of_work_commit(self, db_manager):
        """Test UoW commit."""
        async with unit_of_work(db_manager) as uow:
            user = await uow.users.create(
                email="test@example.com",
                hashed_password="hashed",
            )
            assert user.id is not None

        # Should be committed
        async with db_manager.session_factory() as session:
            from alan.database.repositories import BaseRepository
            repo = BaseRepository(session, User)
            retrieved = await repo.get(user.id)
            assert retrieved is not None

    @pytest.mark.asyncio
    async def test_unit_of_work_rollback(self, db_manager):
        """Test UoW rollback on error."""
        try:
            async with unit_of_work(db_manager) as uow:
                user = await uow.users.create(
                    email="rollback@example.com",
                    hashed_password="hashed",
                )
                raise ValueError("Force rollback")
        except ValueError:
            pass

        # Should be rolled back
        async with db_manager.session_factory() as session:
            from alan.database.repositories import BaseRepository
            repo = BaseRepository(session, User)
            retrieved = await repo.get(user.id)
            assert retrieved is None

    @pytest.mark.asyncio
    async def test_unit_of_work_repositories(self, db_manager):
        """Test UoW provides all repositories."""
        async with unit_of_work(db_manager) as uow:
            assert uow.conversations is not None
            assert uow.messages is not None
            assert uow.agents is not None
            assert uow.memory_vectors is not None
            assert uow.memory_entities is not None
            assert uow.memory_relations is not None