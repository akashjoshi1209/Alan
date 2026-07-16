"""Logging handlers for Alan."""

import logging
import sys
from pathlib import Path
from typing import Optional

from logging.handlers import RotatingFileHandler

from alan.logging.formatters import get_formatter


def create_console_handler(
    level: str = "INFO",
    format_type: str = "pretty",
    stream: Optional[object] = None,
) -> logging.Handler:
    """Create a console handler."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))
    handler.setFormatter(get_formatter(format_type))
    return handler


def create_file_handler(
    file_path: str,
    level: str = "DEBUG",
    format_type: str = "json",
    max_size: str = "100MB",
    backup_count: int = 10,
) -> logging.Handler:
    """Create a rotating file handler."""
    # Parse max size
    size_bytes = parse_size(max_size)

    # Ensure directory exists
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        file_path,
        maxBytes=size_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(getattr(logging, level.upper()))
    handler.setFormatter(get_formatter(format_type))
    return handler


def parse_size(size_str: str) -> int:
    """Parse size string like '100MB' to bytes."""
    size_str = size_str.upper().strip()
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
    }

    for suffix, multiplier in multipliers.items():
        if size_str.endswith(suffix):
            return int(size_str[:-len(suffix)]) * multiplier

    return int(size_str)


def setup_handlers(
    console_level: str = "INFO",
    file_path: Optional[str] = None,
    file_level: str = "DEBUG",
    format_type: str = "json",
) -> list:
    """Set up all logging handlers."""
    handlers = []

    # Console
    console_handler = create_console_handler(console_level, "pretty")
    handlers.append(console_handler)

    # File
    if file_path:
        file_handler = create_file_handler(file_path, file_level, format_type)
        handlers.append(file_handler)

    return handlers