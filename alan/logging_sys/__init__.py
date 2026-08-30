"""Logging package for Alan."""

from alan.logging.setup import setup_logging, get_logger
from alan.logging.formatters import JSONFormatter, PrettyFormatter, get_formatter
from alan.logging.handlers import create_console_handler, create_file_handler, setup_handlers, parse_size
from alan.logging.context import (
    bind_context,
    request_context,
    trace_context,
    get_context,
    clear_context,
    add_app_context,
    with_context,
)
from alan.logging.filters import create_filters

__all__ = [
    "setup_logging",
    "get_logger",
    "JSONFormatter",
    "PrettyFormatter",
    "get_formatter",
    "create_console_handler",
    "create_file_handler",
    "setup_handlers",
    "parse_size",
    "bind_context",
    "request_context",
    "trace_context",
    "get_context",
    "clear_context",
    "add_app_context",
    "with_context",
    "create_filters",
]

# Auto-setup on import
setup_logging()