#!/usr/bin/env python
"""Initial setup script for Alan.

This script performs initial setup tasks:
- Creates necessary directories
- Copies .env.example to .env if not exists
- Runs database migrations
- Seeds initial data
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alan.config import get_settings
from alan.database import DatabaseManager


async def create_directories():
    """Create necessary directories."""
    dirs = [
        Path("data/chromadb"),
        Path("logs"),
        Path("config"),
        Path("models"),
        Path("cache"),
    ]

    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {dir_path}")


def setup_env_file():
    """Copy .env.example to .env if not exists."""
    env_example = Path(".env.example")
    env_file = Path(".env")

    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
        print("Created .env from .env.example")
        print("Please edit .env with your configuration")
    elif env_file.exists():
        print(".env already exists, skipping")
    else:
        print("Warning: .env.example not found")


async def run_migrations():
    """Run database migrations."""
    print("Running database migrations...")
    settings = get_settings()

    # Override database URL for SQLite if not set
    if not settings.database.url or "postgresql" in settings.database.url:
        settings.database.url = "sqlite+aiosqlite:///./alan.db"
        print("Using SQLite database for development")

    manager = DatabaseManager(settings)
    try:
        await manager.create_tables()
        print("Database tables created successfully")
    except Exception as e:
        print(f"Migration failed: {e}")
        raise
    finally:
        await manager.close()


async def seed_database():
    """Seed database with initial data."""
    print("Seeding database...")
    # This will be implemented when seed.py is created
    pass


async def main():
    """Main setup function."""
    print("=" * 50)
    print("Alan - Initial Setup")
    print("=" * 50)

    # Create directories
    await create_directories()

    # Setup environment file
    setup_env_file()

    # Run migrations
    await run_migrations()

    # Seed database
    await seed_database()

    print("=" * 50)
    print("Setup complete!")
    print("Run 'python -m alan' to start Alan")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())