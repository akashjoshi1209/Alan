#!/usr/bin/env python
"""Database migration management script for Alan."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alembic import command
from alembic.config import Config

from alan.config import get_settings


def get_alembic_config() -> Config:
    """Get Alembic configuration."""
    settings = get_settings()
    alembic_cfg = Config("alembic.ini")

    # Override database URL from settings
    if settings.database.url:
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database.url)

    return alembic_cfg


def create_migration(message: str) -> None:
    """Create a new migration revision."""
    alembic_cfg = get_alembic_config()
    command.revision(alembic_cfg, message=message, autogenerate=True)
    print(f"Created migration: {message}")


def upgrade_database(revision: str = "head") -> None:
    """Upgrade database to a specific revision."""
    alembic_cfg = get_alembic_config()
    command.upgrade(alembic_cfg, revision)
    print(f"Upgraded database to: {revision}")


def downgrade_database(revision: str = "-1") -> None:
    """Downgrade database by one revision."""
    alembic_cfg = get_alembic_config()
    command.downgrade(alembic_cfg, revision)
    print(f"Downgraded database to: {revision}")


def show_history() -> None:
    """Show migration history."""
    alembic_cfg = get_alembic_config()
    command.history(alembic_cfg)


def show_current() -> None:
    """Show current revision."""
    alembic_cfg = get_alembic_config()
    command.current(alembic_cfg)


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Alan database migration management")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Create migration
    create_parser = subparsers.add_parser("create", help="Create new migration")
    create_parser.add_argument("message", help="Migration message")

    # Upgrade
    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade database")
    upgrade_parser.add_argument("revision", nargs="?", default="head", help="Target revision")

    # Downgrade
    downgrade_parser = subparsers.add_parser("downgrade", help="Downgrade database")
    downgrade_parser.add_argument("revision", nargs="?", default="-1", help="Target revision")

    # History
    subparsers.add_parser("history", help="Show migration history")

    # Current
    subparsers.add_parser("current", help="Show current revision")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "create": lambda: create_migration(args.message),
        "upgrade": lambda: upgrade_database(args.revision),
        "downgrade": lambda: downgrade_database(args.revision),
        "history": show_history,
        "current": show_current,
    }

    try:
        commands[args.command]()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()