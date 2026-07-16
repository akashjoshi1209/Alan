#!/usr/bin/env python
"""Database seeding script for Alan.

This script seeds the database with initial data:
- Default users
- Default agents
- Default tools
- System configurations
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alan.config import get_settings
from alan.database import DatabaseManager
from alan.database.models import Agent, Tool, User
from sqlalchemy import select


async def seed_users(manager: DatabaseManager) -> None:
    """Seed default users."""
    async with manager.session() as session:
        # Check if admin user exists
        result = await session.execute(select(User).where(User.email == "admin@alan.local"))
        admin = result.scalar_one_or_none()

        if not admin:
            admin = User(
                email="admin@alan.local",
                full_name="Alan Admin",
                hashed_password="$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj/RK.PZvO.S",  # "password"
                is_active=True,
                is_superuser=True,
            )
            session.add(admin)
            print("Created admin user: admin@alan.local")
        else:
            print("Admin user already exists")

        # Check if demo user exists
        result = await session.execute(select(User).where(User.email == "demo@alan.local"))
        demo = result.scalar_one_or_none()

        if not demo:
            demo = User(
                email="demo@alan.local",
                full_name="Demo User",
                hashed_password="$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj/RK.PZvO.S",  # "password"
                is_active=True,
                is_superuser=False,
            )
            session.add(demo)
            print("Created demo user: demo@alan.local")
        else:
            print("Demo user already exists")

        await session.commit()


async def seed_agents(manager: DatabaseManager) -> None:
    """Seed default agents."""
    async with manager.session() as session:
        agents_data = [
            {
                "name": "assistant",
                "type": "assistant",
                "description": "General purpose AI assistant",
                "system_prompt": "You are Alan, a helpful AI assistant. Be concise, accurate, and helpful.",
                "config": {"temperature": 0.7, "max_tokens": 4096},
                "tools": [],
                "is_builtin": True,
                "is_active": True,
            },
            {
                "name": "researcher",
                "type": "researcher",
                "description": "Deep research agent with citations",
                "system_prompt": "You are a research agent. Conduct thorough research and provide citations.",
                "config": {"temperature": 0.3, "max_tokens": 8192},
                "tools": ["web_search", "web_fetch"],
                "is_builtin": True,
                "is_active": True,
            },
            {
                "name": "coder",
                "type": "coder",
                "description": "Code generation and debugging agent",
                "system_prompt": "You are a coding agent. Write clean, efficient, well-documented code.",
                "config": {"temperature": 0.2, "max_tokens": 8192},
                "tools": ["code_exec", "filesystem"],
                "is_builtin": True,
                "is_active": True,
            },
        ]

        for agent_data in agents_data:
            result = await session.execute(
                select(Agent).where(Agent.name == agent_data["name"])
            )
            agent = result.scalar_one_or_none()

            if not agent:
                agent = Agent(**agent_data)
                session.add(agent)
                print(f"Created agent: {agent_data['name']}")
            else:
                print(f"Agent already exists: {agent_data['name']}")

        await session.commit()


async def seed_tools(manager: DatabaseManager) -> None:
    """Seed default tools."""
    async with manager.session() as session:
        tools_data = [
            {
                "name": "filesystem",
                "description": "File system operations (read, write, list, glob, grep)",
                "category": "filesystem",
                "schema": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["read", "write", "list", "glob", "grep"]},
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "pattern": {"type": "string"},
                    },
                    "required": ["operation", "path"],
                },
                "is_builtin": True,
                "is_active": True,
                "requires_approval": False,
            },
            {
                "name": "web_search",
                "description": "Search the web using Brave/Serper/DuckDuckGo",
                "category": "web",
                "schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
                "is_builtin": True,
                "is_active": True,
                "requires_approval": False,
            },
            {
                "name": "web_fetch",
                "description": "Fetch and extract content from web pages",
                "category": "web",
                "schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "extract": {"type": "boolean", "default": True},
                    },
                    "required": ["url"],
                },
                "is_builtin": True,
                "is_active": True,
                "requires_approval": False,
            },
            {
                "name": "code_exec",
                "description": "Execute Python/bash/JavaScript code in sandbox",
                "category": "code",
                "schema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "language": {"type": "string", "enum": ["python", "bash", "javascript"]},
                    },
                    "required": ["code", "language"],
                },
                "is_builtin": True,
                "is_active": True,
                "requires_approval": True,
            },
            {
                "name": "calculator",
                "description": "Perform mathematical calculations",
                "category": "utility",
                "schema": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string"},
                    },
                    "required": ["expression"],
                },
                "is_builtin": True,
                "is_active": True,
                "requires_approval": False,
            },
            {
                "name": "datetime_tool",
                "description": "Date and time operations",
                "category": "utility",
                "schema": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["now", "parse", "format", "add", "diff"]},
                        "value": {"type": "string"},
                        "format": {"type": "string"},
                    },
                    "required": ["operation"],
                },
                "is_builtin": True,
                "is_active": True,
                "requires_approval": False,
            },
        ]

        for tool_data in tools_data:
            result = await session.execute(
                select(Tool).where(Tool.name == tool_data["name"])
            )
            tool = result.scalar_one_or_none()

            if not tool:
                tool = Tool(**tool_data)
                session.add(tool)
                print(f"Created tool: {tool_data['name']}")
            else:
                print(f"Tool already exists: {tool_data['name']}")

        await session.commit()


async def main():
    """Main seeding function."""
    print("=" * 50)
    print("Alan - Database Seeding")
    print("=" * 50)

    settings = get_settings()

    # Override database URL for SQLite if not set
    if not settings.database.url or "postgresql" in settings.database.url:
        settings.database.url = "sqlite+aiosqlite:///./alan.db"
        print("Using SQLite database for development")

    manager = DatabaseManager(settings)

    try:
        await seed_users(manager)
        await seed_agents(manager)
        await seed_tools(manager)

        print("=" * 50)
        print("Seeding complete!")
        print("=" * 50)

    except Exception as e:
        print(f"Seeding failed: {e}")
        raise
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())