"""Logging formatters for Alan."""

import logging
import sys
from typing import Optional


class JSONFormatter(logging.Formatter):
    """JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime

        log_obj = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in [
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "message", "pathname", "process", "processName", "thread",
                "threadName", "exc_info", "exc_text", "stack_info",
            ]:
                log_obj[key] = value

        # Add exception info
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str)


class PrettyFormatter(logging.Formatter):
    """Pretty console formatter with colors."""

    # Color codes
    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
        "RESET": "\033[0m",
    }

    def __init__(self, *args, colors: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.colors = colors and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        level_color = self.COLORS.get(record.levelname, "")
        reset = self.COLORS["RESET"] if self.colors else ""

        # Format timestamp
        from datetime import datetime
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        # Base message
        message = record.getMessage()

        # Add context
        context_parts = []
        if hasattr(record, "request_id"):
            context_parts.append(f"request_id={record.request_id}")
        if hasattr(record, "user_id"):
            context_parts.append(f"user_id={record.user_id}")

        context_str = f" [{', '.join(context_parts)}]" if context_parts else ""

        # Format
        if self.colors:
            return f"{level_color}{timestamp} {record.levelname:8}{reset} {record.name}: {message}{context_str}"
        else:
            return f"{timestamp} {record.levelname:8} {record.name}: {message}{context_str}"


def get_formatter(format_type: str = "json", **kwargs) -> logging.Formatter:
    """Get a formatter by type."""
    formatters = {
        "json": JSONFormatter,
        "pretty": PrettyFormatter,
        "console": PrettyFormatter,
    }

    formatter_class = formatters.get(format_type, JSONFormatter)
    return formatter_class(**kwargs)


class StructlogRenderer:
    """Structlog processor for rendering."""

    @staticmethod
    def console(event_dict):
        """Console renderer."""
        return structlog.dev.ConsoleRenderer(colors=True)(event_dict)

    @staticmethod
    def json(event_dict):
        """JSON renderer."""
        import json
        return json.dumps(event_dict, default=str)

    @staticmethod
    def key_value(event_dict):
        """Key-value renderer."""
        return " ".join(f"{k}={v}" for k, v in event_dict.items())