"""Logging setup for Alan."""

import logging
import sys
from pathlib import Path
from typing import Optional, List

import structlog
from structlog.types import Processor

from alan.config import get_settings
from alan.logging.formatters import get_formatter
from alan.logging.handlers import create_console_handler, create_file_handler, setup_handlers
from alan.logging.filters import create_filters
from alan.logging.context import add_app_context


def setup_logging(
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
    log_file: Optional[str] = None,
    log_max_size: Optional[str] = None,
    log_backup_count: Optional[int] = None,
    log_level_console: Optional[str] = None,
    log_level_file: Optional[str] = None,
    redact_secrets: bool = True,
    filter_health_checks: bool = True,
    max_duplicates: int = 3,
) -> None:
    """Configure structured logging for Alan."""
    # Get settings if not provided
    settings = get_settings()

    level = log_level or settings.log_level
    format_type = log_format or settings.logging.format
    file_path = log_file or settings.logging.file
    max_size = log_max_size or settings.logging.max_size
    backup_count = log_backup_count or settings.logging.backup_count
    console_level = log_level_console or settings.logging.level_console
    file_level = log_level_file or settings.logging.level_file

    # Parse log level
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Configure standard library logging
    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        handlers=[],  # We'll add our own handlers
        force=True,
    )

    # Silence noisy loggers
    noisy_loggers = [
        "httpx",
        "httpcore",
        "asyncio",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "uvicorn.access",
        "uvicorn.error",
        "watchfiles",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # Shared processors
    shared_processors: List[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_app_context,
    ]

    # Configure output processors
    if format_type == "json":
        # JSON for production/file logging
        output_processors = [structlog.processors.JSONRenderer()]
    else:
        # Pretty for console
        output_processors = [structlog.dev.ConsoleRenderer(colors=True)]

    structlog.configure(
        processors=shared_processors + output_processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set up handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Console handler
    console_handler = create_console_handler(
        level=console_level,
        format_type="pretty" if format_type != "json" else "console",
    )
    root_logger.addHandler(console_handler)

    # File handler
    if file_path:
        file_handler = create_file_handler(
            file_path=file_path,
            level=file_level,
            format_type=format_type,
            max_size=max_size,
            backup_count=backup_count,
        )
        root_logger.addHandler(file_handler)

    # Add filters
    filters = create_filters(
        log_level=level,
        redact_secrets=redact_secrets,
        filter_health_checks=filter_health_checks,
        max_duplicates=max_duplicates,
    )
    for handler in root_logger.handlers:
        for f in filters:
            handler.addFilter(f)

    # Log startup
    logger = structlog.get_logger("alan.logging")
    logger.info(
        "Logging configured",
        level=level,
        format=format_type,
        file=file_path,
        console_level=console_level,
        file_level=file_level,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


# Auto-configure on import
setup_logging()