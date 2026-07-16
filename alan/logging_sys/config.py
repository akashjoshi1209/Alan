"""Logging configuration for Alan."""

import logging
import logging.config
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import structlog
from structlog.types import EventDict, Processor

from alan.config import get_settings


def add_app_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add application context to log entries."""
    event_dict["app"] = "alan"
    return event_dict


def add_severity_level(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add severity level to log entries."""
    event_dict["level"] = method_name.upper()
    return event_dict


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    log_file: Optional[str] = None,
    log_max_size: str = "100MB",
    log_backup_count: int = 10,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
) -> None:
    """Configure structured logging for Alan."""

    # Parse log level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
    )

    # Silence noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

    # Shared processors
    shared_processors: list[Processor] = [
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

    # Configure structlog
    if log_format == "json":
        # JSON format for production/file logging
        processors = shared_processors + [
            structlog.processors.JSONRenderer()
        ]
    else:
        # Pretty format for development console
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True)
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure file logging if requested
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # File handler with rotation
        from logging.handlers import RotatingFileHandler

        # Parse max size
        size_str = log_max_size.upper()
        if size_str.endswith("MB"):
            max_bytes = int(size_str[:-2]) * 1024 * 1024
        elif size_str.endswith("GB"):
            max_bytes = int(size_str[:-2]) * 1024 * 1024 * 1024
        elif size_str.endswith("KB"):
            max_bytes = int(size_str[:-2]) * 1024
        else:
            max_bytes = 100 * 1024 * 1024  # 100MB default

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=log_backup_count,
            encoding="utf-8",
        )

        # File formatter
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(getattr(logging, file_level.upper()))

        # Add to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

    # Set console level
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_level.upper()))
    root_logger = logging.getLogger()
    root_logger.addHandler(console_handler)

    # Log startup message
    logger = structlog.get_logger("alan.logging")
    logger.info(
        "Logging configured",
        level=log_level,
        format=log_format,
        file=log_file,
        console_level=console_level,
        file_level=file_level,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


# Configure logging on import
setup_logging()