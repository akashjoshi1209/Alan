"""Formatting utilities."""

import re
import json
from typing import Any, Optional
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date


def format_number(
    value: Union[int, float, Decimal],
    decimals: int = 2,
    thousands_sep: str = ",",
    decimal_sep: str = ".",
) -> str:
    """Format number with separators."""
    if isinstance(value, Decimal):
        value = float(value)

    # Round to specified decimals
    fmt = f"{{:,.{decimals}f}}"
    formatted = fmt.format(value)

    # Replace separators
    if thousands_sep != "," or decimal_sep != ".":
        parts = formatted.split(".")
        integer = parts[0].replace(",", thousands_sep)
        if len(parts) > 1:
            return f"{integer}{decimal_sep}{parts[1]}"
        return integer
    return formatted


def format_currency(
    value: Union[int, float, Decimal],
    currency: str = "USD",
    locale: str = "en_US",
    decimals: Optional[int] = None,
) -> str:
    """Format as currency."""
    if decimals is None:
        decimals = 2 if currency != "JPY" else 0

    formatted = format_number(value, decimals=decimals)

    symbols = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
    }
    symbol = symbols.get(currency, currency + " ")

    if locale in ["en_US", "en_GB"]:
        return f"{symbol}{formatted}"
    return f"{formatted} {symbol}"


def format_percentage(
    value: Union[int, float, Decimal],
    decimals: int = 1,
    multiply: bool = True,
) -> str:
    """Format as percentage."""
    if multiply:
        value = float(value) * 100
    return f"{format_number(value, decimals=decimals)}%"


def format_bytes(size: int, binary: bool = True) -> str:
    """Format bytes as human-readable."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] if binary else ["B", "KB", "MB", "GB", "TB", "PB"]
    divisor = 1024 if binary else 1000

    size = float(size)
    for unit in units:
        if size < divisor:
            return f"{size:.1f} {unit}"
        size /= divisor
    return f"{size:.1f} {units[-1]}"


def format_time(seconds: Union[int, float], short: bool = False) -> str:
    """Format time duration."""
    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s" if short else f"{seconds} seconds"

    minutes = seconds // 60
    seconds %= 60

    if minutes < 60:
        if short:
            return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
        return f"{minutes} minute{'s' if minutes != 1 else ''} {seconds} second{'s' if seconds != 1 else ''}"

    hours = minutes // 60
    minutes %= 60

    if hours < 24:
        if short:
            parts = [f"{hours}h"]
            if minutes:
                parts.append(f"{minutes}m")
            if seconds:
                parts.append(f"{seconds}s")
            return " ".join(parts)
        parts = [f"{hours} hour{'s' if hours != 1 else ''}"]
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if seconds:
            parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        return " ".join(parts)

    days = hours // 24
    hours %= 24

    if short:
        parts = [f"{days}d"]
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        return " ".join(parts)

    parts = [f"{days} day{'s' if days != 1 else ''}"]
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


def format_date(
    dt: Union[datetime, date],
    format: str = "%Y-%m-%d",
    locale: Optional[str] = None,
) -> str:
    """Format date."""
    if isinstance(dt, datetime):
        return dt.strftime(format)
    return dt.strftime(format)


def format_datetime(
    dt: datetime,
    format: str = "%Y-%m-%d %H:%M:%S",
    locale: Optional[str] = None,
) -> str:
    """Format datetime."""
    return dt.strftime(format)


def format_relative_time(
    dt: datetime,
    relative_to: Optional[datetime] = None,
    short: bool = False,
) -> str:
    """Format datetime as relative time."""
    from .datetime import time_ago
    return time_ago(dt, relative_to)


def truncate(text: str, length: int, suffix: str = "...") -> str:
    """Truncate text to length."""
    if len(text) <= length:
        return text
    return text[:length - len(suffix)] + suffix


def slugify(text: str, max_length: Optional[int] = None) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = text.strip("-")
    if max_length and len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text


def camel_to_snake(text: str) -> str:
    """Convert CamelCase to snake_case."""
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).lower()


def snake_to_camel(text: str, capitalize_first: bool = False) -> str:
    """Convert snake_case to CamelCase."""
    parts = text.split("_")
    if capitalize_first:
        return "".join(p.capitalize() for p in parts)
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def pluralize(count: int, singular: str, plural: Optional[str] = None) -> str:
    """Return singular or plural form."""
    if count == 1:
        return singular
    return plural or singular + "s"


def ordinal(n: int) -> str:
    """Convert number to ordinal string."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_list(
    items: list[str],
    conjunction: str = "and",
    oxford_comma: bool = True,
) -> str:
    """Format list as human-readable string."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f" {conjunction} ".join(items)

    if oxford_comma:
        return ", ".join(items[:-1]) + f", {conjunction} " + items[-1]
    return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]


def pretty_json(obj: Any, indent: int = 2) -> str:
    """Format as pretty JSON."""
    return json.dumps(obj, indent=indent, default=str, sort_keys=True)


def format_table(
    headers: list[str],
    rows: list[list[Any]],
    align: Optional[list[str]] = None,
) -> str:
    """Format as ASCII table."""
    if not rows:
        return ""

    # Convert to strings
    str_rows = [[str(cell) for cell in row] for row in rows]
    all_rows = [headers] + str_rows

    # Calculate column widths
    widths = [max(len(row[i]) for row in all_rows) for i in range(len(headers))]

    # Default alignment: left for strings, right for numbers
    if align is None:
        align = []
        for i in range(len(headers)):
            try:
                [float(row[i]) for row in str_rows]
                align.append(">")
            except ValueError:
                align.append("<")

    # Format rows
    def format_row(row: list[str]) -> str:
        return " | ".join(
            f"{cell:{align[i]}{widths[i]}}"
            for i, cell in enumerate(row)
        )

    # Build table
    lines = []
    lines.append(format_row(headers))
    lines.append("-+-".join("-" * w for w in widths))
    for row in str_rows:
        lines.append(format_row(row))

    return "\n".join(lines)


def highlight(text: str, terms: list[str], tag: str = "mark") -> str:
    """Highlight search terms in text."""
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        text = pattern.sub(f"<{tag}>\\g<0></{tag}>", text)
    return text


def mask_sensitive(text: str, patterns: Optional[list[tuple[str, str]]] = None) -> str:
    """Mask sensitive information in text."""
    default_patterns = [
        (r"(?i)(password|secret|key|token|api_key)\s*[:=]\s*\S+", r"\1=***MASKED***"),
        (r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***MASKED***"),
        (r"(?i)authorization:\s*\S+", "Authorization: ***MASKED***"),
        (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "**** **** **** ****"),
        (r"\b\d{3}-\d{2}-\d{4}\b", "***-**-****"),
    ]

    for pattern, replacement in (patterns or default_patterns):
        text = re.sub(pattern, replacement, text)

    return text


def align_text(text: str, width: int, align: str = "left", fill: str = " ") -> str:
    """Align text to width."""
    if align == "left":
        return text.ljust(width, fill)
    elif align == "right":
        return text.rjust(width, fill)
    elif align == "center":
        return text.center(width, fill)
    return text


def wrap_text(text: str, width: int, indent: int = 0) -> list[str]:
    """Wrap text to width."""
    import textwrap
    wrapper = textwrap.TextWrapper(width=width)
    wrapper.initial_indent = " " * indent
    wrapper.subsequent_indent = " " * indent
    return wrapper.wrap(text)


def format_exception(exc: Exception, verbose: bool = False) -> str:
    """Format exception as string."""
    import traceback
    if verbose:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return f"{type(exc).__name__}: {exc}"


def format_stack_trace(limit: Optional[int] = None) -> str:
    """Format current stack trace."""
    import traceback
    return "".join(traceback.format_stack(limit=limit))