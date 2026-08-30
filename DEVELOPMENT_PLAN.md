# Alan - Personal AI Operating System
## Development Plan

---

## 1. Vision

**Alan** is a modular, production-ready personal AI operating system that serves as an intelligent companion, knowledge manager, and automation engine. It combines local-first privacy with cloud AI capabilities, providing a unified interface for conversation, knowledge management, tool execution, voice interaction, and autonomous agent workflows.

### Core Principles
- **Local-First Privacy**: Data stays local by default; cloud AI is opt-in
- **Provider Agnostic**: Seamlessly switch between OpenAI, Anthropic, Gemini, Ollama, and local models
- **Modular Architecture**: Plugin-based architecture for tools, agents, memory, and voice
- **MCP-Native**: First-class Model Context Protocol support for extensibility
- **Voice-First**: Native voice interface with wake-word, STT, TTS, and VAD
- **Agent-Native**: Multi-agent orchestration with planning, memory, and tool use
- **Developer-First**: Clean APIs, CLI, and MCP servers for extensibility

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ALAN OS ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   CLI/REPL   │  │   REST API   │  │  WebSocket   │  │   MCP Server │    │
│  │   (Typer)    │  │  (FastAPI)   │  │  (WebSocket) │  │   (stdio)    │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │                 │            │
│         └─────────────────┼─────────────────┼─────────────────┘            │
│                           ▼                                               │
│              ┌────────────────────────┐                                  │
│              │      API Gateway       │                                  │
│              │   (Routing, Auth,      │                                  │
│              │    Rate Limit, Log)    │                                  │
│              └───────────┬────────────┘                                  │
│                          │                                               │
│         ┌────────────────┼────────────────┐                              │
│         ▼                ▼                ▼                              │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                         │
│  │  Core Kernel │ │  Event Bus  │ │  Lifecycle  │                         │
│  │  (Runtime)   │ │  (EventBus) │ │  Manager    │                         │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘                         │
│         │               │               │                                 │
│         └───────────────┼───────────────┘                                 │
│                         ▼                                                 │
│         ┌─────────────────────────────────────────┐                       │
│         │           AGENT ORCHESTRATOR             │                       │
│         │  (Planning, Memory, Tools, Handoffs)     │                       │
│         └──────────────┬──────────────────────────┘                       │
│                        │                                                  │
│        ┌───────────────┼───────────────┐                                  │
│        ▼               ▼               ▼                                  │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                          │
│ │   Agents    │ │   Tools     │ │   Memory    │                          │
│ │  (Registry) │ │  (Registry) │ │  (Vector +  │                          │
│ └──────┬──────┘ └──────┬──────┘ │   Graph)    │                          │
│        │               │        └──────┬──────┘                          │
│        │               │               │                                  │
│        ▼               ▼               ▼                                  │
│ ┌─────────────────────────────────────────────────┐                       │
│ │              AI PROVIDER ABSTRACTION             │                       │
│ │  (OpenAI, Anthropic, Gemini, Ollama, Local)     │                       │
│ └──────────────────────┬──────────────────────────┘                       │
│                        │                                                  │
│        ┌───────────────┼───────────────┐                                  │
│        ▼               ▼               ▼                                  │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                          │
│ │   Voice     │ │    MCP      │ │  Database   │                          │
│ │  (STT/TTS/  │ │  (Servers/  │ │  (Postgres/ │                          │
│ │   Wake/VAD) │ │   Clients)  │ │   SQLite/   │                          │
│ └─────────────┘ └─────────────┘ │   Vector)   │                          │
│                                 └─────────────┘                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layer Definitions

| Layer | Responsibility | Key Modules |
|-------|---------------|-------------|
| **Interface** | User interaction entry points | CLI, REST API, WebSocket, MCP Server |
| **Gateway** | Routing, auth, rate limiting, observability | API Gateway, Auth, Rate Limiter |
| **Core** | Kernel, event bus, lifecycle, DI container | Core, EventBus, Lifecycle, DI |
| **Orchestration** | Agent planning, execution, memory, tools | AgentOrchestrator, Planner, ToolExecutor |
| **Capabilities** | Agents, Tools, Memory, Voice, MCP | Agents, Tools, Memory, Voice, MCP |
| **AI Abstraction** | Provider-agnostic LLM/Embedding interface | AI Providers (OpenAI, Anthropic, etc.) |
| **Infrastructure** | Database, Vector DB, Cache, Voice I/O | Database, VectorDB, Redis, Voice I/O |

---

## 3. Folder Responsibilities

```
Projects/Alan/
├── alan/                          # Main package root
│   ├── __init__.py                # Package exports, version
│   ├── __main__.py                # CLI entry point (python -m alan)
│   └── py.typed                   # PEP 561 marker
│
├── alan/config/                   # Configuration Management
│   ├── __init__.py
│   ├── settings.py                # Pydantic Settings (env + YAML)
│   ├── loader.py                  # Config loading/merging/validation
│   ├── schemas.py                 # Config schemas (Pydantic models)
│   ├── environments.py            # Dev/Staging/Prod configs
│   └── secrets.py                 # Secret management (keyring, env)
│
├── alan/core/                     # Core Kernel
│   ├── __init__.py
│   ├── kernel.py                  # AlanKernel - main entry point
│   ├── lifecycle.py               # Startup/Shutdown/Health checks
│   ├── events.py                  # EventBus (pub/sub, async)
│   ├── container.py               # DI Container (dependency injection)
│   ├── registry.py                # Component registry
│   ├── signals.py                 # Signal handling (SIGTERM, SIGHUP)
│   └── state.py                   # Global application state
│
├── alan/database/                 # Database Layer
│   ├── __init__.py
│   ├── base.py                    # Base model, session management
│   ├── session.py                 # Async session factory
│   ├── migrations/                # Alembic migrations
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   ├── models/                    # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── conversation.py
│   │   ├── message.py
│   │   ├── agent.py
│   │   ├── tool.py
│   │   ├── memory.py
│   │   ├── mcp_server.py
│   │   └── audit.py
│   ├── repositories/              # Repository pattern
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── conversation.py
│   │   ├── agent.py
│   │   └── memory.py
│   └── unit_of_work.py            # Unit of Work pattern
│
├── alan/ai_providers/             # AI Provider Abstractions
│   ├── __init__.py
│   ├── base.py                    # BaseProvider, BaseEmbeddingProvider
│   ├── registry.py                # Provider registry & factory
│   ├── models.py                  # Provider models, capabilities
│   ├── openai/                    # OpenAI provider
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── embeddings.py
│   │   ├── models.py
│   │   └── streaming.py
│   ├── anthropic/                 # Anthropic provider
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   └── models.py
│   ├── gemini/                    # Google Gemini provider
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   └── embeddings.py
│   ├── ollama/                    # Ollama local provider
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── embeddings.py
│   │   └── models.py
│   ├── local/                     # Local models (transformers, llama.cpp)
│   │   ├── __init__.py
│   │   ├── transformers_chat.py
│   │   ├── llama_cpp_chat.py
│   │   └── embeddings.py
│   ├── cohere/                    # Cohere provider
│   ├── mistral/                   # Mistral provider
│   ├── groq/                      # Groq provider
│   ├── together/                  # Together AI provider
│   ├── replicate/                 # Replicate provider
│   ├── huggingface/               # Hugging Face provider
│   ├── router.py                  # Smart routing (cost, latency, quality)
│   ├── fallback.py                # Fallback chain logic
│   ├── streaming.py               # Unified streaming interface
│   └── costs.py                   # Cost tracking & optimization
│
├── alan/agents/                   # Agent Framework
│   ├── __init__.py
│   ├── base.py                    # BaseAgent, AgentConfig
│   ├── registry.py                # Agent registry & discovery
│   ├── orchestrator.py            # Multi-agent orchestration
│   ├── planner.py                 # Planning & task decomposition
│   ├── executor.py                # Step execution & tool calling
│   ├── memory.py                  # Agent-specific memory
│   ├── handoff.py                 # Agent-to-agent handoff
│   ├── types.py                   # Agent types (assistant, researcher, coder, etc.)
│   ├── builtins/                  # Built-in agents
│   │   ├── __init__.py
│   │   ├── assistant.py           # General assistant
│   │   ├── researcher.py          # Deep research agent
│   │   ├── coder.py               # Code generation agent
│   │   ├── analyst.py             # Data analysis agent
│   │   ├── planner_agent.py       # Planning agent
│   │   └── orchestrator_agent.py  # Meta-orchestrator
│   ├── prompts/                   # System prompts
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── researcher.py
│   │   ├── coder.py
│   │   └── analyst.py
│   └── runtime.py                 # Agent runtime context
│
├── alan/tools/                    # Tool Ecosystem
│   ├── __init__.py
│   ├── base.py                    # BaseTool, ToolResult, ToolSchema
│   ├── registry.py                # Tool registry & discovery
│   ├── executor.py                # Tool executor (sandboxed)
│   ├── sandbox/                   # Code execution sandbox
│   │   ├── __init__.py
│   │   ├── docker.py              # Docker sandbox
│   │   ├── subprocess.py          # Subprocess sandbox
│   │   ├── wasm.py                # WASM sandbox (future)
│   │   └── policies.py            # Security policies
│   ├── builtins/                  # Built-in tools
│   │   ├── __init__.py
│   │   ├── filesystem.py          # File operations
│   │   ├── web_search.py          # Web search (Brave, Serper, etc.)
│   │   ├── web_fetch.py           # Web content fetching
│   │   ├── code_exec.py           # Code execution
│   │   ├── database.py            # SQL query tool
│   │   ├── calculator.py          # Calculator
│   │   ├── datetime_tool.py       # Date/time operations
│   │   ├── email.py               # Email sending
│   │   ├── github.py              # GitHub API
│   │   ├── linear.py              # Linear API
│   │   ├── notion.py              # Notion API
│   │   ├── slack.py               # Slack API
│   │   └── custom.py              # Custom tool loader
│   ├── mcp_tools.py               # MCP tool adapter
│   ├── validation.py              # Input/output validation
│   └── permissions.py             # Tool permissions & approval
│
├── alan/memory/                   # Memory Architecture
│   ├── __init__.py
│   ├── base.py                    # MemoryBase, MemoryItem
│   ├── vector/                    # Vector memory (RAG)
│   │   ├── __init__.py
│   │   ├── store.py               # Vector store abstraction
│   │   ├── chroma.py              # ChromaDB backend
│   │   ├── pinecone.py            # Pinecone backend
│   │   ├── weaviate.py            # Weaviate backend
│   │   ├── pgvector.py            # pgvector backend
│   │   ├── embeddings.py          # Embedding generation
│   │   ├── chunking.py            # Text chunking strategies
│   │   ├── retrieval.py           # Retrieval strategies
│   │   └── reranking.py           # Cross-encoder reranking
│   ├── graph/                     # Graph memory (knowledge graph)
│   │   ├── __init__.py
│   │   ├── store.py               # Graph store abstraction
│   │   ├── neo4j.py               # Neo4j backend
│   │   ├── networkx.py            # NetworkX backend (local)
│   │   ├── entities.py            # Entity extraction
│   │   ├── relations.py           # Relation extraction
│   │   └── queries.py             # Graph queries
│   ├── episodic/                  # Episodic memory (conversations)
│   │   ├── __init__.py
│   │   ├── store.py
│   │   ├── summarization.py       # Conversation summarization
│   │   └── consolidation.py       # Memory consolidation
│   ├── semantic/                  # Semantic memory (facts, concepts)
│   │   ├── __init__.py
│   │   ├── store.py
│   │   ├── extraction.py          # Fact extraction
│   │   └── consolidation.py
│   ├── working/                   # Working memory (context window)
│   │   ├── __init__.py
│   │   ├── buffer.py              # Context buffer
│   │   ├── compression.py         # Context compression
│   │   └── prioritization.py      # Relevance prioritization
│   ├── manager.py                 # Unified memory manager
│   ├── consolidation.py           # Cross-memory consolidation
│   └── privacy.py                 # Privacy filters, PII redaction
│
├── alan/mcp/                      # Model Context Protocol
│   ├── __init__.py
│   ├── server/                    # MCP Server
│   │   ├── __init__.py
│   │   ├── server.py              # MCP server implementation
│   │   ├── resources.py           # Resource providers
│   │   ├── tools.py               # Tool providers
│   │   ├── prompts.py             # Prompt providers
│   │   ├── transport/             # Transport implementations
│   │   │   ├── __init__.py
│   │   │   ├── stdio.py
│   │   │   ├── sse.py
│   │   │   └── websocket.py
│   │   └── registry.py            # Server capability registry
│   ├── client/                    # MCP Client
│   │   ├── __init__.py
│   │   ├── client.py              # MCP client
│   │   ├── connections.py         # Connection pool
│   │   └── discovery.py           # Server discovery
│   ├── protocol/                  # Protocol types
│   │   ├── __init__.py
│   │   ├── messages.py            # MCP message types
│   │   ├── types.py               # Type definitions
│   │   └── validation.py          # Message validation
│   └── servers/                   # Built-in MCP servers
│       ├── __init__.py
│       ├── filesystem.py          # File system server
│       ├── github.py              # GitHub server
│       ├── database.py            # Database server
│       └── memory.py              # Memory server
│
├── alan/api/                      # API Layer
│   ├── __init__.py
│   ├── main.py                    # FastAPI app factory
│   ├── routes/                    # API routes
│   │   ├── __init__.py
│   │   ├── conversations.py
│   │   ├── messages.py
│   │   ├── agents.py
│   │   ├── tools.py
│   │   ├── memory.py
│   │   ├── mcp.py
│   │   ├── voice.py
│   │   ├── health.py
│   │   └── admin.py
│   ├── middleware/                # Middleware
│   │   ├── __init__.py
│   │   ├── auth.py                # JWT auth
│   │   ├── rate_limit.py          # Rate limiting
│   │   ├── logging.py             # Request logging
│   │   ├── cors.py                # CORS
│   │   └── error_handling.py      # Error handling
│   ├── dependencies.py            # FastAPI dependencies
│   ├── schemas/                   # Pydantic schemas
│   │   ├── __init__.py
│   │   ├── conversation.py
│   │   ├── message.py
│   │   ├── agent.py
│   │   ├── tool.py
│   │   └── voice.py
│   ├── websocket/                 # WebSocket handlers
│   │   ├── __init__.py
│   │   ├── manager.py             # Connection manager
│   │   ├── handlers.py            # Message handlers
│   │   └── events.py              # Event types
│   └── openapi.py                 # OpenAPI customization
│
├── alan/cli/                      # Command Line Interface
│   ├── __init__.py
│   ├── main.py                    # Typer app entry
│   ├── commands/                  # Command modules
│   │   ├── __init__.py
│   │   ├── chat.py                # Interactive chat
│   │   ├── agent.py               # Agent management
│   │   ├── memory.py              # Memory commands
│   │   ├── tool.py                # Tool management
│   │   ├── mcp.py                 # MCP commands
│   │   ├── voice.py               # Voice commands
│   │   ├── config.py              # Config management
│   │   ├── db.py                  # Database commands
│   │   └── server.py              # Server management
│   ├── console.py                 # Rich console utilities
│   ├── completers.py              # Tab completion
│   ├── themes.py                  # Color themes
│   └── repl.py                    # REPL implementation
│
├── alan/voice/                    # Voice Processing
│   ├── __init__.py
│   ├── pipeline.py                # Voice pipeline orchestration
│   ├── wake_word/                 # Wake word detection
│   │   ├── __init__.py
│   │   ├── porcupine.py           # Picovoice Porcupine
│   │   ├── openwakeword.py        # OpenWakeWord
│   │   └── custom.py              # Custom wake word
│   ├── stt/                       # Speech-to-Text
│   │   ├── __init__.py
│   │   ├── base.py                # STT base class
│   │   ├── faster_whisper.py      # Faster-Whisper
│   │   ├── openai_whisper.py      # OpenAI Whisper API
│   │   ├── vosk.py                # Vosk (offline)
│   │   ├── google_stt.py          # Google STT
│   │   └── streaming.py           # Streaming STT
│   ├── tts/                       # Text-to-Speech
│   │   ├── __init__.py
│   │   ├── base.py                # TTS base class
│   │   ├── pyttsx3.py             # pyttsx3 (offline)
│   │   ├── openai_tts.py          # OpenAI TTS
│   │   ├── elevenlabs.py          # ElevenLabs
│   │   ├── coqui.py               # Coqui TTS
│   │   ├── piper.py               # Piper TTS
│   │   └── streaming.py           # Streaming TTS
│   ├── vad/                       # Voice Activity Detection
│   │   ├── __init__.py
│   │   ├── webrtc_vad.py          # WebRTC VAD
│   │   ├── silero_vad.py          # Silero VAD
│   │   └── energy_vad.py          # Energy-based VAD
│   ├── audio/                     # Audio I/O
│   │   ├── __init__.py
│   │   ├── input.py               # Microphone input
│   │   ├── output.py              # Speaker output
│   │   ├── devices.py             # Device management
│   │   └── processing.py          # Audio processing
│   ├── conversation.py            # Voice conversation manager
│   └── commands.py                # Voice command parsing
│
├── alan/utils/                    # Utilities
│   ├── __init__.py
│   ├── async_utils.py             # Async helpers
│   ├── text.py                    # Text processing
│   ├── json.py                    # JSON utilities (orjson)
│   ├── hashing.py                 # Hashing utilities
│   ├── datetime.py                # Date/time utilities
│   ├── formatting.py              # Formatting utilities
│   ├── validation.py              # Validation helpers
│   ├── retry.py                   # Retry logic
│   ├── cache.py                   # Caching utilities
│   └── paths.py                   # Path utilities
│
├── alan/logging/                  # Logging System
│   ├── __init__.py
│   ├── config.py                  # Logging configuration
│   ├── formatters.py              # Formatters (JSON, pretty)
│   ├── handlers.py                # Custom handlers
│   ├── context.py                 # Contextual logging
│   ├── filters.py                 # Log filters
│   └── setup.py                   # Setup function
│
├── alan/tests/                    # Test Suite
│   ├── __init__.py
│   ├── conftest.py                # Pytest fixtures
│   ├── unit/                      # Unit tests
│   │   ├── test_config.py
│   │   ├── test_core.py
│   │   ├── test_database.py
│   │   ├── test_ai_providers.py
│   │   ├── test_agents.py
│   │   ├── test_tools.py
│   │   ├── test_memory.py
│   │   ├── test_mcp.py
│   │   ├── test_api.py
│   │   ├── test_cli.py
│   │   ├── test_voice.py
│   │   └── test_utils.py
│   ├── integration/               # Integration tests
│   │   ├── test_agent_workflows.py
│   │   ├── test_memory_pipeline.py
│   │   ├── test_voice_pipeline.py
│   │   ├── test_mcp_integration.py
│   │   └── test_api_integration.py
│   ├── e2e/                       # End-to-end tests
│   │   ├── test_chat_flow.py
│   │   ├── test_agent_orchestration.py
│   │   └── test_voice_conversation.py
│   ├── fixtures/                  # Test fixtures
│   │   ├── __init__.py
│   │   ├── conversations.py
│   │   ├── agents.py
│   │   └── tools.py
│   └── mocks/                     # Mock implementations
│       ├── __init__.py
│       ├── ai_provider.py
│       ├── vector_store.py
│       └── database.py
│
├── alan/scripts/                  # Utility Scripts
│   ├── __init__.py
│   ├── setup.py                   # Initial setup
│   ├── migrate.py                 # Database migration
│   ├── seed.py                    # Seed data
│   ├── benchmark.py               # Benchmarks
│   ├── download_models.py         # Model downloader
│   └── generate_docs.py           # Documentation generation
│
└── pyproject.toml                 # Project configuration
```

---

## 4. Development Phases

### Phase 1: Foundation (Weeks 1-2)
**Goal**: Core infrastructure, configuration, database, logging

| Task | Module | Deliverable |
|------|--------|-------------|
| 1.1 | config | Pydantic Settings with env/YAML support, secrets management |
| 1.2 | core | Kernel, EventBus, LifecycleManager, DI Container |
| 1.3 | database | SQLAlchemy models, Alembic migrations, repositories, UoW |
| 1.4 | logging | Structured logging (structlog), JSON/pretty formats, context |
| 1.5 | utils | Async utilities, text processing, caching, validation |
| 1.6 | tests | Test infrastructure, fixtures, mocks |
| 1.7 | scripts | Setup, migration, seed scripts |

**Exit Criteria**: `python -m alan` starts kernel, loads config, connects to DB, logs properly

---

### Phase 2: AI Provider Abstraction (Weeks 3-4)
**Goal**: Unified interface for all AI providers with routing/fallback

| Task | Module | Deliverable |
|------|--------|-------------|
| 2.1 | ai_providers/base | BaseProvider, BaseEmbeddingProvider, ProviderRegistry |
| 2.2 | ai_providers/openai | Chat, Embeddings, Streaming, Models, Cost tracking |
| 2.3 | ai_providers/anthropic | Chat, Streaming, Models |
| 2.4 | ai_providers/gemini | Chat, Embeddings, Streaming |
| 2.5 | ai_providers/ollama | Chat, Embeddings, Model management |
| 2.6 | ai_providers/local | Transformers, llama.cpp, Local embeddings |
| 2.7 | ai_providers/router | Smart routing (cost/latency/quality) |
| 2.8 | ai_providers/fallback | Fallback chains, circuit breaker |
| 2.9 | ai_providers/streaming | Unified streaming interface |
| 2.10 | tests | Provider tests, mock provider |

**Exit Criteria**: `alan.chat("hello")` works with any configured provider

---

### Phase 3: Memory Architecture (Weeks 5-6)
**Goal**: Multi-modal memory system (vector, graph, episodic, semantic, working)

| Task | Module | Deliverable |
|------|--------|-------------|
| 3.1 | memory/vector | VectorStore abstraction, Chroma/Pinecone/Weaviate/pgvector |
| 3.2 | memory/vector/embeddings | Embedding generation with provider abstraction |
| 3.3 | memory/vector/chunking | Semantic chunking, recursive, markdown-aware |
| 3.4 | memory/vector/retrieval | Hybrid search (vector + BM25), MMR, filters |
| 3.5 | memory/vector/reranking | Cross-encoder reranking |
| 3.6 | memory/graph | GraphStore abstraction, Neo4j/NetworkX, entity/relation extraction |
| 3.7 | memory/episodic | Conversation storage, summarization, consolidation |
| 3.8 | memory/semantic | Fact extraction, concept storage, consolidation |
| 3.9 | memory/working | Context buffer, compression, prioritization |
| 3.10 | memory/manager | Unified MemoryManager with privacy filters |
| 3.11 | memory/consolidation | Cross-memory consolidation pipeline |
| 3.12 | tests | Memory integration tests |

**Exit Criteria**: `memory.store()`, `memory.retrieve()`, `memory.consolidate()` work end-to-end

---

### Phase 4: Tool Ecosystem (Weeks 7-8)
**Goal**: Secure, extensible tool system with sandboxing

| Task | Module | Deliverable |
|------|--------|-------------|
| 4.1 | tools/base | BaseTool, ToolSchema, ToolResult, validation |
| 4.2 | tools/registry | ToolRegistry, discovery, categories |
| 4.3 | tools/executor | ToolExecutor, timeout, retries, error handling |
| 4.4 | tools/sandbox/docker | Docker sandbox with resource limits |
| 4.5 | tools/sandbox/subprocess | Subprocess sandbox (fallback) |
| 4.5 | tools/sandbox/policies | Security policies (network, fs, CPU, memory) |
| 4.6 | tools/builtins/filesystem | Read, write, list, glob, grep |
| 4.7 | tools/builtins/web_search | Brave, Serper, DuckDuckGo |
| 4.8 | tools/builtins/web_fetch | Fetch, extract, summarize |
| 4.9 | tools/builtins/code_exec | Python, bash, JavaScript execution |
| 4.10 | tools/builtins/database | SQL query, schema inspection |
| 4.11 | tools/builtins/integrations | GitHub, Linear, Notion, Slack, Email |
| 4.12 | tools/mcp_tools | MCP tool adapter |
| 4.13 | tools/permissions | Approval workflows, allowlists |
| 4.14 | tests | Tool execution tests, sandbox tests |

**Exit Criteria**: Agent can execute tools safely with approval workflows

---

### Phase 5: Agent Framework (Weeks 9-11)
**Goal**: Multi-agent orchestration with planning, memory, and handoffs

| Task | Module | Deliverable |
|------|--------|-------------|
| 5.1 | agents/base | BaseAgent, AgentConfig, AgentState |
| 5.2 | agents/registry | AgentRegistry, discovery, metadata |
| 5.3 | agents/planner | TaskPlanner, decomposition, dependencies |
| 5.4 | agents/executor | StepExecutor, tool calling, error recovery |
| 5.5 | agents/memory | AgentMemory (working + long-term) |
| 5.6 | agents/handoff | Handoff protocol, context transfer |
| 5.7 | agents/orchestrator | Multi-agent orchestration, supervision |
| 5.8 | agents/builtins/assistant | General assistant agent |
| 5.9 | agents/builtins/researcher | Deep research with citations |
| 5.10 | agents/builtins/coder | Code generation, debugging, refactoring |
| 5.11 | agents/builtins/analyst | Data analysis, visualization |
| 5.12 | agents/builtins/planner_agent | Strategic planning agent |
| 5.13 | agents/prompts | System prompts per agent type |
| 5.14 | agents/runtime | Runtime context, streaming, interrupts |
| 5.15 | tests | Agent workflow tests, multi-agent tests |

**Exit Criteria**: `agent.run("research quantum computing")` produces cited report

---

### Phase 6: MCP Integration (Weeks 12-13)
**Goal**: Full MCP server/client implementation

| Task | Module | Deliverable |
|------|--------|-------------|
| 6.1 | mcp/protocol | MCP message types, validation |
| 6.2 | mcp/server/server | MCP server core |
| 6.3 | mcp/server/transport/stdio | Stdio transport |
| 6.4 | mcp/server/transport/sse | SSE transport |
| 6.5 | mcp/server/transport/websocket | WebSocket transport |
| 6.6 | mcp/server/resources | Resource providers |
| 6.7 | mcp/server/tools | Tool providers |
| 6.8 | mcp/server/prompts | Prompt providers |
| 6.9 | mcp/client | MCP client, connection pool |
| 6.10 | mcp/client/discovery | Server discovery (mDNS, config) |
| 6.11 | mcp/servers/filesystem | Filesystem MCP server |
| 6.12 | mcp/servers/github | GitHub MCP server |
| 6.13 | mcp/servers/database | Database MCP server |
| 6.14 | mcp/servers/memory | Memory MCP server |
| 6.15 | tests | MCP compliance tests |

**Exit Criteria**: `alan mcp serve` works with Claude Desktop, `alan mcp connect` discovers servers

---

### Phase 7: API Layer (Weeks 14-15)
**Goal**: Production-ready REST/gRPC/WebSocket API

| Task | Module | Deliverable |
|------|--------|-------------|
| 7.1 | api/main | FastAPI app, lifespan, middleware |
| 7.2 | api/routes/conversations | CRUD, branching, export |
| 7.3 | api/routes/messages | Streaming, attachments, reactions |
| 7.4 | api/routes/agents | Agent management, execution |
| 7.5 | api/routes/tools | Tool registry, execution |
| 7.6 | api/routes/memory | Memory query, management |
| 7.7 | api/routes/mcp | MCP server management |
| 7.8 | api/routes/voice | Voice session management |
| 7.9 | api/middleware/auth | JWT, API keys, scopes |
| 7.10 | api/middleware/rate_limit | Token bucket, Redis backend |
| 7.11 | api/websocket | Real-time chat, agent events |
| 7.12 | api/schemas | Request/response models |
| 7.13 | tests | API contract tests, load tests |

**Exit Criteria**: OpenAPI docs at `/docs`, WebSocket chat works, auth enforced

---

### Phase 8: CLI & REPL (Weeks 16-17)
**Goal**: Rich interactive CLI with REPL

| Task | Module | Deliverable |
|------|--------|-------------|
| 8.1 | cli/main | Typer app, command groups |
| 8.2 | cli/commands/chat | Interactive chat with streaming |
| 8.3 | cli/commands/agent | Agent CRUD, run, logs |
| 8.4 | cli/commands/memory | Memory query, stats, export |
| 8.5 | cli/commands/tool | Tool list, test, approve |
| 8.6 | cli/commands/mcp | Server list, connect, logs |
| 8.7 | cli/commands/voice | Start/stop, device test |
| 8.8 | cli/commands/config | View, set, validate, migrate |
| 8.9 | cli/commands/db | Migrate, seed, backup, shell |
| 8.10 | cli/commands/server | Start API, MCP, health |
| 8.11 | cli/console | Rich themes, progress, panels |
| 8.12 | cli/completers | Tab completion for all commands |
| 8.13 | cli/repl | REPL with history, syntax highlight |
| 8.14 | tests | CLI integration tests |

**Exit Criteria**: `alan chat` starts streaming conversation, `alan agent run researcher "topic"` works

---

### Phase 9: Voice Architecture (Weeks 18-20)
**Goal**: Full voice pipeline with wake word, STT, TTS, VAD

| Task | Module | Deliverable |
|------|--------|-------------|
| 9.1 | voice/pipeline | VoicePipeline orchestration |
| 9.2 | voice/wake_word/porcupine | Porcupine wake word |
| 9.3 | voice/wake_word/openwakeword | OpenWakeWord (open source) |
| 9.4 | voice/stt/faster_whisper | Faster-Whisper (local) |
| 9.5 | voice/stt/openai_whisper | OpenAI Whisper API |
| 9.6 | voice/stt/vosk | Vosk (offline) |
| 9.7 | voice/stt/streaming | Streaming STT with partial results |
| 9.8 | voice/tts/pyttsx3 | pyttsx3 (offline) |
| 9.9 | voice/tts/openai_tts | OpenAI TTS |
| 9.10 | voice/tts/elevenlabs | ElevenLabs |
| 9.11 | voice/tts/piper | Piper TTS (local, fast) |
| 9.12 | voice/tts/streaming | Streaming TTS with chunking |
| 9.13 | voice/vad/webrtc_vad | WebRTC VAD |
| 9.14 | voice/vad/silero_vad | Silero VAD (better accuracy) |
| 9.15 | voice/audio/input | Microphone capture, device selection |
| 9.16 | voice/audio/output | Speaker playback, device selection |
| 9.17 | voice/conversation | Turn management, barge-in, context |
| 9.18 | voice/commands | Voice command parsing, intent |
| 9.19 | tests | Voice pipeline integration tests |

**Exit Criteria**: `alan voice start` → "Hey Alan" → voice conversation works

---

### Phase 10: Polish & Production (Weeks 21-24)
**Goal**: Production hardening, documentation, benchmarks

| Task | Module | Deliverable |
|------|--------|-------------|
| 10.1 | all | Comprehensive integration tests |
| 10.2 | all | E2E test scenarios |
| 10.3 | core | Health checks, readiness, metrics (Prometheus) |
| 10.4 | core | Graceful degradation, circuit breakers |
| 10.5 | database | Connection pooling, read replicas |
| 10.6 | ai_providers | Cost optimization, caching, batching |
| 10.7 | memory | Vector index optimization, quantization |
| 10.8 | security | Audit, penetration testing, secrets scan |
| 10.9 | performance | Load testing, latency profiling |
| 10.10 | docs | API docs, architecture docs, user guide |
| 10.11 | scripts | Release automation, Docker images |
| 10.12 | CI/CD | GitHub Actions, pre-commit, release |
| 10.13 | packaging | PyPI publishing, Docker Hub |

**Exit Criteria**: Production deployment ready, <100ms p99 latency, 99.9% uptime

---

## 5. Module Dependencies

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DEPENDENCY GRAPH                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  config ◄──────────────────────────────────────────────────────────────┐    │
│     │                                                                    │    │
│     ▼                                                                    │    │
│  core ◄────────────────────────────────────────────────────────────┐   │    │
│     │                                                              │   │    │
│     ├──► logging                                                    │   │    │
│     │                                                              │   │    │
│     ├──► database ◄────────────────────────────────────────┐       │   │    │
│     │        │                                             │       │   │    │
│     │        ▼                                             │       │   │    │
│     │   migrations                                         │       │   │    │
│     │                                                      │       │   │    │
│     ├──► ai_providers ◄────────────────────────────────┐   │       │   │    │
│     │        │                                         │   │       │   │    │
│     │        ├──► openai                               │   │       │   │    │
│     │        ├──► anthropic                            │   │       │   │    │
│     │        ├──► gemini                               │   │       │   │    │
│     │        ├──► ollama                               │   │       │   │    │
│     │        ├──► local                                │   │       │   │    │
│     │        ├──► router                               │   │       │   │    │
│     │        └──► fallback                             │   │       │   │    │
│     │                                                    │   │       │   │    │
│     ├──► memory ◄──────────────────────────────────┐   │   │       │   │    │
│     │        │                                     │   │   │       │   │    │
│     │        ├──► vector ◄──── ai_providers ──────┘   │   │       │   │    │
│     │        ├──► graph                                │   │       │   │    │
│     │        ├──► episodic ◄──── database ────────────┘   │       │   │    │
│     │        ├──► semantic                               │       │   │    │
│     │        ├──► working                                │       │   │    │
│     │        └──► manager                                │       │   │    │
│     │                                                    │       │   │    │
│     ├──► tools ◄─────────────────────────────────────┐   │       │   │    │
│     │        │                                       │   │       │   │    │
│     │        ├──► sandbox ◄──── database ───────────┘   │       │   │    │
│     │        ├──► builtins                               │       │   │    │
│     │        ├──► mcp_tools ◄──── mcp ──────────────────┘       │   │    │
│     │        └──► executor                               │       │   │    │
│     │                                                    │       │   │    │
│     ├──► agents ◄─────────────────────────────────────┐  │       │   │    │
│     │        │                                        │  │       │   │    │
│     │        ├──► planner ◄──── ai_providers ─────────┘  │       │   │    │
│     │        ├──► executor ◄──── tools ──────────────────┘       │   │    │
│     │        ├──► memory ◄──── memory ───────────────────────────┘   │    │
│     │        ├──► orchestrator ◄──── agents ────────────────────────┘    │
│     │        └──► builtins                                           │    │
│     │                                                                │    │
│     ├──► mcp ◄─────────────────────────────────────────────────────┐ │    │
│     │        │                                                     │ │    │
│     │        ├──► server ◄──── tools, memory, database ────────────┘ │    │
│     │        └──► client                                            │    │
│     │                                                              │    │
│     ├──► api ◄───────────────────────────────────────────────────┐  │    │
│     │        │                                                    │  │    │
│     │        ├──► routes ◄──── agents, tools, memory, mcp, voice ─┘  │    │
│     │        ├──► middleware ◄──── config, database ────────────────┘    │
│     │        └──► websocket ◄──── agents, events ──────────────────────┘ │
│     │                                                                     │
│     ├──► cli ◄────────────────────────────────────────────────────────┐  │
│     │        │                                                        │  │
│     │        └──► commands ◄──── api, agents, tools, memory, mcp ────┘  │
│     │                                                                     │
│     ├──► voice ◄─────────────────────────────────────────────────────┐   │
│     │        │                                                        │   │
│     │        ├──► stt ◄──── ai_providers ────────────────────────────┘   │
│     │        ├──► tts ◄──── ai_providers ───────────────────────────────┘
│     │        └──► conversation ◄──── agents, memory ─────────────────────┘
│     │                                                                     │
│     └──► utils (used by all)                                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Dependency Rules
1. **config** → No dependencies (leaf)
2. **core** → config, logging, utils
3. **database** → config, core, utils
4. **logging** → config, utils
5. **utils** → config (optional)
6. **ai_providers** → config, core, utils
7. **memory** → config, core, database, ai_providers, utils
8. **tools** → config, core, database, ai_providers, utils
9. **mcp** → config, core, tools, memory, database, utils
10. **agents** → config, core, ai_providers, tools, memory, mcp, utils
11. **api** → config, core, database, agents, tools, memory, mcp, voice, utils
12. **cli** → config, core, api, agents, tools, memory, mcp, voice, utils
13. **voice** → config, core, ai_providers, agents, memory, utils

---

## 6. AI Provider Strategy

### Provider Matrix

| Capability | OpenAI | Anthropic | Gemini | Ollama | Local (Transformers) | Local (llama.cpp) |
|------------|--------|-----------|--------|--------|---------------------|-------------------|
| Chat | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Streaming | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Function Calling | ✅ | ✅ | ✅ | ✅ | ⚠️ | ⚠️ |
| Vision | ✅ | ✅ | ✅ | ⚠️ | ✅ | ⚠️ |
| Embeddings | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Fine-tuning | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ |
| Cost Tracking | ✅ | ✅ | ✅ | Free | Free | Free |
| Latency (local) | High | High | High | Low | Medium | Low |
| Privacy | Cloud | Cloud | Cloud | Local | Local | Local |

### Routing Strategy

```python
# Priority-based routing with fallback
ROUTING_RULES = [
    # Privacy-first: prefer local for sensitive data
    {"condition": "privacy_sensitive", "providers": ["ollama", "local_transformers", "local_llama_cpp"]},
    
    # Quality-first: complex reasoning
    {"condition": "complex_reasoning", "providers": ["anthropic", "openai", "gemini"]},
    
    # Cost-optimized: simple queries
    {"condition": "simple_query", "providers": ["ollama", "groq", "together"]},
    
    # Speed-optimized: real-time
    {"condition": "low_latency", "providers": ["groq", "ollama", "local_llama_cpp"]},
    
    # Default fallback chain
    {"condition": "default", "providers": ["openai", "anthropic", "gemini", "ollama"]},
]
```

### Cost Management
- Per-provider cost tracking (input/output tokens)
- Monthly budgets with alerts
- Automatic routing to cheaper providers for non-critical tasks
- Caching for repeated embeddings/queries

### Provider Health Monitoring
- Latency tracking (p50, p95, p99)
- Error rate monitoring
- Automatic failover on degradation
- Circuit breaker pattern

---

## 7. MCP Integration Plan

### Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│                      ALAN MCP ECOSYSTEM                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    MCP Protocol (JSON-RPC 2.0)    ┌────────┐  │
│  │  Alan Core   │ ◄─────────────────────────────────► │ Client │  │
│  │  (Host)      │   stdio / SSE / WebSocket           │(Claude │  │
│  └──────┬───────┘                                     │Desktop)│  │
│         │                                             └────────┘  │
│         │                                                ▲         │
│         ▼                                                │         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐        │
│  │ MCP Server 1 │    │ MCP Server 2 │    │ MCP Server N │        │
│  │ (Filesystem) │    │   (GitHub)   │    │  (Memory)    │        │
│  └──────────────┘    └──────────────┘    └──────────────┘        │
│         │                    │                    │                │
│         └────────────────────┼────────────────────┘                │
│                              ▼                                     │
│                    ┌──────────────────┐                            │
│                    │  Tool Registry   │                            │
│                    │  (Unified Tool   │                            │
│                    │   Interface)     │                            │
│                    └────────┬─────────┘                            │
│                             │                                      │
│                    ┌────────▼─────────┐                            │
│                    │  Agent Executor  │                            │
│                    └──────────────────┘                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Built-in MCP Servers

| Server | Resources | Tools | Prompts |
|--------|-----------|-------|---------|
| **filesystem** | Files, directories | read, write, list, glob, grep | - |
| **github** | Repos, issues, PRs | create_issue, create_pr, search | code_review |
| **database** | Tables, schemas | query, explain, schema | - |
| **memory** | Memories, collections | store, retrieve, search | - |
| **web** | Pages, search results | fetch, search, extract | summarize |
| **alan-internal** | Agents, tools, config | execute_agent, list_tools | - |

### Client Integration
- **Claude Desktop**: stdio transport, auto-discovery via config
- **VS Code Extension**: WebSocket transport
- **Custom Clients**: SSE/HTTP transport
- **Server Discovery**: mDNS, config file, registry

### Security
- Per-server tool allowlists
- Resource access scopes
- Rate limiting per connection
- Audit logging for all MCP calls

---

## 8. Memory Architecture

### Memory Types

```
┌────────────────────────────────────────────────────────────────────────┐
│                        MEMORY HIERARCHY                                 │
├────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────┐                                                   │
│  │  WORKING MEMORY │  ← Active context (token budget managed)         │
│  │  (Short-term)   │     • Conversation history                       │
│  └────────┬────────┘     • Current task state                        │
│           │              • Tool results                              │
│           ▼                                                   │
│  ┌─────────────────┐                                                   │
│  │  EPISODIC MEMORY│  ← Conversation episodes                       │
│  │  (Conversations)│     • Full history with metadata               │
│  └────────┬────────┘     • Summaries at multiple granularities      │
│           │              • Temporal links                           │
│           ▼                                                   │
│  ┌─────────────────┐     ┌─────────────────┐                      │
│  │  SEMANTIC MEMORY│     │   GRAPH MEMORY  │                      │
│  │   (Facts)       │     │  (Knowledge)    │                      │
│  └────────┬────────┘     └────────┬────────┘                      │
│           │                       │                               │
│           ▼                       ▼                               │
│  ┌─────────────────────────────────────────┐                       │
│  │         VECTOR MEMORY (RAG)             │                       │
│  │  • Document chunks + embeddings         │                       │
│  │  • Hybrid search (vector + BM25)        │                       │
│  │  • Metadata filtering                   │                       │
│  │  • Cross-encoder reranking              │                       │
│  └─────────────────────────────────────────┘                       │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

### Consolidation Pipeline

```
Raw Conversation
       │
       ▼
┌──────────────────┐
│  Segmentation    │  ← Topic boundary detection
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Summarization   │  ← Hierarchical summaries (turn → session → topic)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Fact Extraction │  ← Entities, relations, claims
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Graph Update    │  ← Entity resolution, relation inference
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Vector Index    │  ← Chunk + embed + upsert
└──────────────────┘
```

### Privacy Architecture
- **Local-first**: All memory stored locally by default
- **PII Detection**: Automatic redaction before cloud processing
- **Encryption**: At-rest encryption for sensitive memories
- **Retention Policies**: Configurable TTL per memory type
- **Export/Delete**: GDPR-compliant data portability

---

## 9. Voice Architecture

### Pipeline Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        VOICE PIPELINE                                   │
├────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐         │
│  │  Audio   │───►│   VAD    │───►│   │   Wake   │───►│   STT    │         │
│  │  Input   │    │          │   │   Word   │    │          │         │
│  └──────────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘         │
│                       │               │               │                │
│                       ▼               │               ▼                │
│              ┌─────────────────┐     │      ┌─────────────────┐       │
│              │  Silence/       │     │      │  Transcript +   │       │
│              │  Speech Segments│     │      │  Confidence     │       │
│              └─────────────────┘     │      └────────┬────────┘       │
│                                      │               │                │
│                       ┌──────────────┘               │                │
│                       ▼                              ▼                │
│              ┌──────────────────────────────────────────────┐         │
│              │          TURN MANAGER                         │         │
│              │  • Endpoint detection                        │         │
│              │  • Barge-in handling                         │         │
│              │  • Interruption recovery                     │         │
│              └────────────────────┬─────────────────────────┘         │
│                                   │                                    │
│                                   ▼                                    │
│              ┌──────────────────────────────────────────────┐         │
│              │          AGENT ORCHESTRATOR                   │         │
│              │  • Context injection                         │         │
│              │  • Streaming response                        │         │
│              │  • Tool execution                            │         │
│              └────────────────────┬─────────────────────────┘         │
│                                   │                                    │
│                                   ▼                                    │
│              ┌──────────────────────────────────────────────┐         │
│              │          TTS PIPELINE                         │         │
│              │  • Text chunking                             │         │
│              │  • Streaming synthesis                       │         │
│              │  • Audio playback                            │         │
│              └──────────────────────────────────────────────┘         │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

### Provider Matrix

| Component | Local (Privacy) | Cloud (Quality) | Hybrid |
|-----------|-----------------|-----------------|--------|
| **Wake Word** | Porcupine, OpenWakeWord | - | - |
| **VAD** | WebRTC VAD, Silero | - | - |
| **STT** | Faster-Whisper, Vosk | OpenAI Whisper API, Google STT | Local fallback |
| **TTS** | Piper, Coqui, pyttsx3 | OpenAI TTS, ElevenLabs | Stream local, fallback cloud |

### Audio Configuration
- **Sample Rate**: 16kHz (STT), 22-48kHz (TTS)
- **Channels**: Mono (input), Stereo (output)
- **Format**: PCM 16-bit
- **Buffer**: 20-100ms chunks for streaming
- **VAD Threshold**: Configurable (default 0.5)

---

## 10. Database Design

### Core Tables

```sql
-- Users & Authentication
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    avatar_url TEXT,
    is_active BOOLEAN DEFAULT true,
    is_superuser BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- API Keys
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    key_hash VARCHAR(255) NOT NULL,
    scopes JSONB DEFAULT '[]',
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Conversations
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500),
    agent_id VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    branch_from_id UUID REFERENCES conversations(id),
    branch_point_message_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Messages
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,  -- user, assistant, system, tool
    content TEXT NOT NULL,
    tool_calls JSONB,
    tool_call_id VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    tokens_input INTEGER,
    tokens_output INTEGER,
    cost_usd DECIMAL(10, 6),
    provider VARCHAR(50),
    model VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Agents
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(50) NOT NULL,  -- assistant, researcher, coder, etc.
    config JSONB DEFAULT '{}',
    system_prompt TEXT,
    tools JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Tools
CREATE TABLE tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    schema JSONB NOT NULL,
    category VARCHAR(50),
    is_builtin BOOLEAN DEFAULT false,
    config JSONB DEFAULT '{}',
    permissions JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Memory (Vector references)
CREATE TABLE memory_vectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    collection_name VARCHAR(100) NOT NULL,
    vector_id VARCHAR(255) NOT NULL,  -- Chroma/Pinecone ID
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding_model VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Memory (Graph)
CREATE TABLE memory_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(100) NOT NULL,
    properties JSONB DEFAULT '{}',
    embedding VECTOR(1536),  -- pgvector
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE memory_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    source_id UUID REFERENCES memory_entities(id) ON DELETE CASCADE,
    target_id UUID REFERENCES memory_entities(id) ON DELETE CASCADE,
    relation_type VARCHAR(100) NOT NULL,
    properties JSONB DEFAULT '{}',
    confidence FLOAT DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- MCP Servers
CREATE TABLE mcp_servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    transport VARCHAR(50) NOT NULL,  -- stdio, sse, websocket
    command TEXT,
    args JSONB DEFAULT '[]',
    env JSONB DEFAULT '{}',
    url VARCHAR(500),
    capabilities JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Audit Log
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id UUID,
    details JSONB DEFAULT '{}',
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_memory_vectors_user_collection ON memory_vectors(user_id, collection_name);
CREATE INDEX idx_memory_entities_user ON memory_entities(user_id);
CREATE INDEX idx_audit_logs_user_time ON audit_logs(user_id, created_at);
```

### Vector Database Schema (ChromaDB)

```python
# Collections per user
collections = {
    f"user_{user_id}_conversations": {
        "metadata": {"user_id": user_id, "type": "conversation"},
        "hnsw_space": "cosine"
    },
    f"user_{user_id}_documents": {
        "metadata": {"user_id": user_id, "type": "document"},
        "hnsw_space": "cosine"
    },
    f"user_{user_id}_code": {
        "metadata": {"user_id": user_id, "type": "code"},
        "hnsw_space": "cosine"
    }
}
```

---

## 11. Security Plan

### Threat Model

| Threat | Mitigation |
|--------|------------|
| **Prompt Injection** | Input validation, system prompt isolation, tool approval |
| **Data Exfiltration** | Sandboxed tool execution, network egress control, PII redaction |
| **Unauthorized Access** | JWT auth, API keys, RBAC, audit logging |
| **Model Extraction** | Rate limiting, query monitoring, watermarking |
| **Supply Chain** | Dependency scanning, pinned versions, SBOM |
| **Local Code Execution** | Docker sandbox, resource limits, seccomp profiles |

### Security Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                     SECURITY LAYERS                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  NETWORK                                                          │
│  ├── TLS 1.3 for all external connections                       │
│  ├── mTLS for service-to-service                                │
│  ├── Firewall: deny by default, allowlist only                  │
│  └── VPN/Tailscale for local access                             │
│                                                                  │
│  APPLICATION                                                      │
│  ├── Authentication: JWT + API Keys                             │
│  ├── Authorization: RBAC (admin, user, agent, readonly)        │
│  ├── Rate Limiting: Token bucket per user/IP                    │
│  ├── Input Validation: Pydantic on all boundaries               │
│  ├── Output Encoding: Prevent XSS in web UI                     │
│  └── Security Headers: CSP, HSTS, X-Frame-Options              │
│                                                                  │
│  DATA                                                             │
│  ├── Encryption at Rest: AES-256 (database, vector DB)          │
│  ├── Encryption in Transit: TLS 1.3                             │
│  ├── Secrets: HashiCorp Vault / OS keyring / env (dev only)     │
│  ├── PII Detection: Presidio / custom patterns                  │
│  ├── Data Retention: Configurable TTL, auto-purge               │
│  └── Backup Encryption: Age/GPG encrypted backups               │
│                                                                  │
│  EXECUTION                                                        │
│  ├── Tool Sandbox: Docker with --cap-drop=ALL                   │
│  ├── Resource Limits: CPU, memory, pids, network                │
│  ├── Filesystem: Read-only root, mounted volumes only           │
│  ├── Seccomp: Default deny, allowlist syscalls                  │
│  └── Timeout: Hard limits on all executions                     │
│                                                                  │
│  AUDIT & MONITORING                                              │
│  ├── Structured Audit Logs: All authz, tool exec, data access   │
│  ├── Anomaly Detection: Unusual patterns, geo anomalies         │
│  ├── Alerting: PagerDuty/Slack on critical events               │
│  └── SIEM Integration: JSON logs → Elastic/Splunk               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Compliance Checklist
- [ ] GDPR: Data portability, right to deletion, DPIA
- [ ] SOC 2 Type II: Access controls, encryption, monitoring
- [ ] ISO 27001: Risk assessment, incident response
- [ ] HIPAA (if applicable): BAA, PHI handling, audit trails

---

## 12. Testing Strategy

### Test Pyramid

```
                    ┌─────────────┐
                    │   E2E Tests │  ◄── 10% (Critical user journeys)
                    │   (Playwright│
                    │    / Cypress)│
                   ┌┴─────────────┴┐
                  ┌┴───────────────┴┐
                 ┌┴─────────────────┴┐
                ┌┴───────────────────┴┐
               ┌┴─────────────────────┴┐
              ┌┴───────────────────────┴┐
             ┌┴─────────────────────────┴┐
            ┌┴───────────────────────────┴┐
           ┌┴─────────────────────────────┴┐
          ┌┴───────────────────────────────┴┐
         ┌┴─────────────────────────────────┴┐
        ┌┴───────────────────────────────────┴┐
       ┌┴─────────────────────────────────────┴┐
      ┌┴───────────────────────────────────────┴┐
     ┌┴─────────────────────────────────────────┴┐
    ┌┴───────────────────────────────────────────┴┐
   ┌┴─────────────────────────────────────────────┴┐
  ┌┴───────────────────────────────────────────────┴┐
 ┌┴─────────────────────────────────────────────────┴┐
┌┴───────────────────────────────────────────────────┴┐
│                  UNIT TESTS                          │  ◄── 70% (Fast, isolated)
│  • Config, Utils, Models, Validators                │
│  • Provider mocks, Memory operations                │
│  • Tool schemas, Agent state machines               │
└──────────────────────────────────────────────────────┘
```

### Test Categories

| Category | Tools | Coverage Target | When |
|----------|-------|-----------------|------|
| **Unit** | pytest, pytest-mock | 90%+ | Every commit |
| **Integration** | pytest, testcontainers | 70%+ | PR merge |
| **Contract** | schemathesis, pact | 100% APIs | Release |
| **E2E** | Playwright | 10 critical paths | Nightly |
| **Load** | locust, k6 | <200ms p99 | Weekly |
| **Chaos** | chaosmesh | Resilience | Monthly |
| **Security** | bandit, semgrep, trivy | 0 critical | Every commit |

### Key Test Scenarios

```python
# tests/integration/test_agent_workflows.py
async def test_research_agent_end_to_end():
    """Research agent: query → plan → search → synthesize → cite"""
    
async def test_coder_agent_debug_cycle():
    """Coder: write code → run tests → fix errors → refactor"""
    
async def test_multi_agent_handoff():
    """Planner → Researcher → Coder → Reviewer"""

# tests/integration/test_memory_pipeline.py
async def test_memory_consolidation():
    """Conversation → Summary → Facts → Graph → Vector"""

async def test_cross_memory_retrieval():
    """Query spans vector + graph + episodic"""

# tests/e2e/test_voice_conversation.py
async def test_wake_word_to_response():
    """Audio → VAD → Wake → STT → Agent → TTS → Audio"""
```

### Test Infrastructure

```yaml
# docker-compose.test.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
  redis:
    image: redis:7-alpine
  chromadb:
    image: chromadb/chroma:latest
  neo4j:
    image: neo4j:5
  ollama:
    image: ollama/ollama:latest
  localstack:  # For AWS mocking
    image: localstack/localstack
```

---

## 13. Deployment Strategy

### Environments

| Environment | Purpose | Infrastructure | Data |
|-------------|---------|----------------|------|
| **Local** | Development | Docker Compose | SQLite, Chroma local |
| **Staging** | Integration testing | Kubernetes (kind/k3d) | Postgres, Redis, Chroma |
| **Production** | Live traffic | Kubernetes (EKS/GKE) | Managed PG, Redis, Pinecone |

### Deployment Architecture (Production)

```
┌─────────────────────────────────────────────────────────────────┐
│                        PRODUCTION                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐                                               │
│  │   Cloudflare │  ◄── WAF, DDoS, CDN, TLS termination          │
│  │   (DNS/WAF)  │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│  ┌──────▼───────┐                                               │
│  │   Ingress    │  ◄── NGINX Ingress, mTLS, rate limiting      │
│  │   Controller │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│  ┌──────▼────────────────────────────────────────────────┐      │
│  │              SERVICE MESH (Istio/Linkerd)              │      │
│  └──────┬──────────────────────┬─────────────────────────┘      │
│         │                      │                                │
│  ┌──────▼──────┐        ┌──────▼──────┐                         │
│  │  API Pods   │        │  MCP Pods   │  ◄── Horizontal scaling│
│  │  (x3-10)    │        │  (x2-5)     │      HPA: CPU>70%      │
│  └──────┬──────┘        └──────┬──────┘                         │
│         │                      │                                │
│  ┌──────▼──────────────────────▼──────┐                         │
│  │         STATEFUL SERVICES           │                         │
│  ├────────────────────────────────────┤                         │
│  │ PostgreSQL (Primary + Replicas)   │                         │
│  │ Redis Cluster (Sentinel)          │                         │
│  │ ChromaDB / Pinecone (Managed)     │                         │
│  │ Neo4j (if graph memory enabled)   │                         │
│  └────────────────────────────────────┘                         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### CI/CD Pipeline

```yaml
# .github/workflows/ci.yml
stages:
  - lint: [ruff, mypy, bandit, semgrep]
  - test: [unit, integration, contract]
  - build: [docker, sbom, sign]
  - scan: [trivy, grype, dependency-check]
  - deploy-staging: [helm, kustomize, smoke-tests]
  - deploy-prod: [blue-green, canary, rollback-ready]
```

### Release Strategy
- **Semantic Versioning**: MAJOR.MINOR.PATCH
- **Release Branches**: `release/v1.x`
- **Tags**: `v1.0.0`, `v1.0.1-rc.1`
- **Changelog**: Auto-generated from conventional commits
- **Rollback**: Helm rollback / ArgoCD sync revert

---

## 14. Milestones

| Milestone | Target | Criteria |
|-----------|--------|----------|
| **M1: Foundation** | Week 2 | Kernel boots, config loads, DB migrates, logs structured |
| **M2: AI Providers** | Week 4 | All 7+ providers work, routing/fallback functional |
| **M3: Memory System** | Week 6 | Vector+Graph+Episodic+Semantic+Working integrated |
| **M4: Tool Ecosystem** | Week 8 | 15+ built-in tools, sandboxed execution, approvals |
| **M5: Agent Framework** | Week 11 | 5 built-in agents, multi-agent orchestration |
| **M6: MCP Integration** | Week 13 | Server + Client, 4 built-in servers, Claude Desktop works |
| **M7: API Layer** | Week 15 | REST + WS + Auth + Rate limit, OpenAPI docs |
| **M8: CLI/REPL** | Week 17 | Full feature parity with API, rich UX |
| **M9: Voice Pipeline** | Week 20 | Wake→STT→Agent→TTS loop, <500ms latency |
| **M10: Production Ready** | Week 24 | Load tested, secured, documented, packaged |

### Success Metrics

| Metric | Target |
|--------|--------|
| API p99 latency | <200ms |
| Voice pipeline latency | <500ms |
| Memory retrieval p99 | <100ms |
| Tool execution p99 | <5s (sandboxed) |
| Test coverage | >85% |
| Critical vulnerabilities | 0 |
| Deployment frequency | Daily |
| MTTR | <30min |

---

## Appendix: Technology Decisions

| Category | Choice | Rationale |
|----------|--------|-----------|
| **Language** | Python 3.11+ | AI ecosystem, async support, type hints |
| **Async Framework** | AnyIO + FastAPI | Structured concurrency, modern async |
| **Config** | Pydantic Settings | Type-safe, env + file support |
| **Database** | PostgreSQL + pgvector | ACID, vector search, mature |
| **Vector DB** | ChromaDB (local), Pinecone (cloud) | Embedded + managed options |
| **Graph DB** | Neo4j (prod), NetworkX (local) | Scale vs simplicity |
| **Cache** | Redis | Pub/sub, distributed locking |
| **Logging** | structlog + loguru | Structured + pretty |
| **CLI** | Typer + Rich | Modern, composable, beautiful |
| **Testing** | pytest + pytest-asyncio | Standard, async support |
| **Containers** | Docker + BuildKit | Multi-stage, cache |
| **Orchestration** | Kubernetes | Self-healing, scaling |
| **Service Mesh** | Istio | mTLS, traffic management |
| **Observability** | Prometheus + Grafana + Jaeger | Open standards |

---

*Document Version: 1.0*  
*Last Updated: 2025*  
*Project: Alan - Personal AI Operating System*