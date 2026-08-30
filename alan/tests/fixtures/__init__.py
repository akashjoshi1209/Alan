"""Test fixtures for Alan."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class FixtureData:
    """Base fixture data."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class UserFixture(FixtureData):
    """User fixture."""
    email: str = "test@example.com"
    full_name: Optional[str] = "Test User"
    is_active: bool = True
    is_superuser: bool = False
    hashed_password: str = "hashed_password"


@dataclass
class ConversationFixture(FixtureData):
    """Conversation fixture."""
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: Optional[str] = "Test Conversation"
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    branch_from_id: Optional[uuid.UUID] = None
    branch_point_message_id: Optional[uuid.UUID] = None


@dataclass
class MessageFixture(FixtureData):
    """Message fixture."""
    conversation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    role: str = "user"
    content: str = "Test message"
    tool_calls: Optional[Dict[str, Any]] = None
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    cost_usd: Optional[float] = None
    provider: Optional[str] = None
    model: Optional[str] = None


@dataclass
class AgentFixture(FixtureData):
    """Agent fixture."""
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Test Agent"
    type: str = "assistant"
    config: Dict[str, Any] = field(default_factory=dict)
    system_prompt: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    is_active: bool = True


@dataclass
class ToolFixture(FixtureData):
    """Tool fixture."""
    name: str = "test_tool"
    description: Optional[str] = "A test tool"
    schema: Dict[str, Any] = field(default_factory=dict)
    category: Optional[str] = None
    is_builtin: bool = False
    config: Dict[str, Any] = field(default_factory=dict)
    permissions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryVectorFixture(FixtureData):
    """Memory vector fixture."""
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    collection_name: str = "test_collection"
    vector_id: str = "test_vector_id"
    content: str = "Test memory content"
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding_model: Optional[str] = None


@dataclass
class MemoryEntityFixture(FixtureData):
    """Memory entity fixture."""
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Test Entity"
    type: str = "concept"
    properties: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None


@dataclass
class MemoryRelationFixture(FixtureData):
    """Memory relation fixture."""
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    source_id: uuid.UUID = field(default_factory=uuid.uuid4)
    target_id: uuid.UUID = field(default_factory=uuid.uuid4)
    relation_type: str = "related_to"
    properties: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class MCPServerFixture(FixtureData):
    """MCP server fixture."""
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "test_server"
    transport: str = "stdio"
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, Any] = field(default_factory=dict)
    url: Optional[str] = None
    capabilities: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


@dataclass
class AuditLogFixture(FixtureData):
    """Audit log fixture."""
    user_id: Optional[uuid.UUID] = None
    action: str = "test_action"
    resource_type: Optional[str] = None
    resource_id: Optional[uuid.UUID] = None
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


# Factory functions
def create_user(**overrides) -> UserFixture:
    """Create user fixture."""
    return UserFixture(**overrides)


def create_conversation(**overrides) -> ConversationFixture:
    """Create conversation fixture."""
    return ConversationFixture(**overrides)


def create_message(**overrides) -> MessageFixture:
    """Create message fixture."""
    return MessageFixture(**overrides)


def create_agent(**overrides) -> AgentFixture:
    """Create agent fixture."""
    return AgentFixture(**overrides)


def create_tool(**overrides) -> ToolFixture:
    """Create tool fixture."""
    return ToolFixture(**overrides)


def create_memory_vector(**overrides) -> MemoryVectorFixture:
    """Create memory vector fixture."""
    return MemoryVectorFixture(**overrides)


def create_memory_entity(**overrides) -> MemoryEntityFixture:
    """Create memory entity fixture."""
    return MemoryEntityFixture(**overrides)


def create_memory_relation(**overrides) -> MemoryRelationFixture:
    """Create memory relation fixture."""
    return MemoryRelationFixture(**overrides)


def create_mcp_server(**overrides) -> MCPServerFixture:
    """Create MCP server fixture."""
    return MCPServerFixture(**overrides)


def create_audit_log(**overrides) -> AuditLogFixture:
    """Create audit log fixture."""
    return AuditLogFixture(**overrides)


# Sample data collections
SAMPLE_USERS = [
    UserFixture(email="user1@example.com", full_name="User One"),
    UserFixture(email="user2@example.com", full_name="User Two"),
    UserFixture(email="admin@example.com", full_name="Admin User", is_superuser=True),
]

SAMPLE_CONVERSATIONS = [
    ConversationFixture(title="First Conversation"),
    ConversationFixture(title="Research Topic"),
    ConversationFixture(title="Code Review"),
]

SAMPLE_MESSAGES = [
    MessageFixture(role="user", content="Hello, how are you?"),
    MessageFixture(role="assistant", content="I'm doing well, thank you!"),
    MessageFixture(role="user", content="Can you help me with something?"),
    MessageFixture(role="assistant", content="Of course! What do you need help with?"),
]

SAMPLE_AGENTS = [
    AgentFixture(name="Research Assistant", type="researcher"),
    AgentFixture(name="Code Assistant", type="coder"),
    AgentFixture(name="General Assistant", type="assistant"),
]

SAMPLE_TOOLS = [
    ToolFixture(
        name="web_search",
        description="Search the web",
        schema={"type": "object", "properties": {"query": {"type": "string"}}},
    ),
    ToolFixture(
        name="calculator",
        description="Perform calculations",
        schema={"type": "object", "properties": {"expression": {"type": "string"}}},
    ),
    ToolFixture(
        name="file_read",
        description="Read file contents",
        schema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
]