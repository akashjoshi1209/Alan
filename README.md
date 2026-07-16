# Alan - Personal AI Operating System

A modular, production-ready personal AI operating system built with Python.

## Architecture

```
Alan/
├── config/          # Configuration management
├── core/            # Core system components
├── memory/          # Memory management & storage
├── ai_providers/    # AI provider integrations
├── agents/          # Agent orchestration
├── tools/           # Tool ecosystem
├── mcp/             # Model Context Protocol
├── api/             # REST/gRPC API layer
├── database/        # Database layer
├── cli/             # Command-line interface
├── voice/           # Voice processing
├── utils/           # Utilities
├── logging/         # Logging system
├── tests/           # Test suite
├── docs/            # Documentation
├── scripts/         # Utility scripts
├── config/          # Configuration
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Run Alan
python -m alan
```

## Architecture Overview

### Core Modules
- **config/** - Configuration management (YAML/JSON/env)
- **core/** - Core kernel, event bus, lifecycle management
- **memory/** - Vector storage, embeddings, retrieval
- **ai_providers/** - OpenAI, Anthropic, Ollama, local models
- **agents/** - Agent framework, orchestration, planning
- **tools/** - Tool registry, execution, sandboxing
- **mcp/** - Model Context Protocol server/client
- **api/** - REST/gRPC/WebSocket API layer
- **database/** - PostgreSQL/SQLite/Vector DB abstraction
- **cli/** - Rich CLI with REPL
- **voice/** - STT/TTS, wake word, VAD
- **utils/** - Common utilities
- **logging/** - Structured logging

## Development

```bash
# Run tests
pytest

# Run linters
ruff check .
mypy .

# Format code
ruff format .
```

## License

MIT