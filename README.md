# Alan - Personal Android AI Coding Agent

A local-first AI coding assistant for Android, built from the open-jarvis codebase.

## Features

- **Hybrid LLM Support**: Cloud (Groq, Anthropic, OpenAI, Google, OpenRouter) + Local (llama.cpp, Ollama, LM Studio)
- **Code Tools**: File operations, Shell commands, Git, Testing, Linting
- **Memory System**: Room database + Vector search for code context
- **Termux Bridge**: Execute tools via Unix socket
- **MCP Support**: Extensible tool ecosystem
- **Local Models**: Download and run models on-device
- **Beautiful VOID-inspired UI**: Dark theme with smooth animations

## Architecture

```
Alan/
├── agent/           # Core agent loop, planner, tool executor
├── bridge/          # Termux Unix socket bridge
├── llm/             # LLM providers (8+ cloud + local)
├── local/           # Local model management (llama.cpp)
├── mcp/             # Model Context Protocol support
├── memory/          # Room DB + Vector search
├── skills/          # Code skill templates
├── tools/           # File, Shell, Git, Test, Lint tools
├── ui/              # Compose UI (Chat, Settings, Memory)
└── security/        # Encrypted preferences
```

## Building

```bash
./gradlew assembleDebug
```

## Requirements

- Android 8.0+ (API 26)
- Termux (for shell tool execution)
- 4GB+ RAM recommended for local models

## Configuration

1. Open Alan app
2. Go to Settings → AI Provider
3. Select provider and enter API key
4. Optionally enable local models
5. Grant storage permissions for project access

## Usage

1. Open a project (or create new)
2. Chat with Alan about code tasks
3. Alan will use tools to explore, edit, test code
4. All context persisted in local memory

## License

MIT