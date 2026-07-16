"""Log filters for Alan."""

import logging
import re
from typing import Optional, Set


class LevelFilter(logging.Filter):
    """Filter by log level."""

    def __init__(self, min_level: int = logging.DEBUG, max_level: int = logging.CRITICAL):
        super().__init__()
        self.min_level = min_level
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return self.min_level <= record.levelno <= self.max_level


class LoggerFilter(logging.Filter):
    """Filter by logger name."""

    def __init__(self, allowed_loggers: Optional[Set[str]] = None, blocked_loggers: Optional[Set[str]] = None):
        super().__init__()
        self.allowed_loggers = allowed_loggers or set()
        self.blocked_loggers = blocked_loggers or set()

    def filter(self, record: logging.LogRecord) -> bool:
        if self.allowed_loggers and record.name not in self.allowed_loggers:
            return False
        if record.name in self.blocked_loggers:
            return False
        return True


class PatternFilter(logging.Filter):
    """Filter by message pattern."""

    def __init__(self, pattern: str, exclude: bool = False):
        super().__init__()
        self.pattern = re.compile(pattern)
        self.exclude = exclude

    def filter(self, record: logging.LogRecord) -> bool:
        match = self.pattern.search(record.getMessage())
        return not match if self.exclude else match


class SensitiveDataFilter(logging.Filter):
    """Filter that redacts sensitive data from logs."""

    SENSITIVE_PATTERNS = [
        (re.compile(r'(?i)(password|secret|key|token|api_key)\s*[:=]\s*\S+'), r'\1=***REDACTED***'),
        (re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*'), 'Bearer ***REDACTED***'),
        (re.compile(r'(?i)authorization:\s*\S+'), 'Authorization: ***REDACTED***'),
        (re.compile(r'(?i)api[_-]?key["\']?\s*[:=]\s*\S+'), 'api_key=***REDACTED***'),
        (re.compile(r'(?i)secret["\']?\s*[:=]\s*\S+'), 'secret=***REDACTED***'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


class DuplicateFilter(logging.Filter):
    """Filter that prevents duplicate log messages."""

    def __init__(self, max_duplicates: int = 3):
        super().__init__()
        self.max_duplicates = max_duplicates
        self._counts: dict = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.msg)
        count = self._counts.get(key, 0)

        if count >= self.max_duplicates:
            if count == self.max_duplicates:
                record.msg = f"{record.msg} (repeated {count} times)"
                record.args = ()
                return True
            return False

        self._counts[key] = count + 1
        return True


class HealthCheckFilter(logging.Filter):
    """Filter out health check logs."""

    def __init__(self, paths: Optional[list] = None):
        super().__init__()
        self.paths = paths or ['/health', '/ready', '/ping', '/metrics']

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for path in self.paths:
            if path in message and ('GET' in message or '200' in message):
                return False
        return True


def create_filters(
    log_level: str = "DEBUG",
    redact_secrets: bool = True,
    filter_health_checks: bool = True,
    max_duplicates: int = 3,
) -> list:
    """Create standard log filters."""
    filters = []

    # Level filter
    filters.append(LevelFilter(min_level=getattr(logging, log_level.upper())))

    # Sensitive data filter
    if redact_secrets:
        filters.append(SensitiveDataFilter())

    # Health check filter
    if filter_health_checks:
        filters.append(HealthCheckFilter())

    # Duplicate filter
    if max_duplicates > 0:
        filters.append(DuplicateFilter(max_duplicates))

    return filters