"""Date/time utilities."""

from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Union
import calendar


def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def now_tz(tz: Optional[timezone] = None) -> datetime:
    """Get current datetime in timezone."""
    return datetime.now(tz or timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """Convert datetime to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def from_utc(dt: datetime, tz: timezone) -> datetime:
    """Convert UTC datetime to timezone."""
    return to_utc(dt).astimezone(tz)


def parse_iso8601(value: str) -> datetime:
    """Parse ISO 8601 datetime string."""
    # Handle common formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unable to parse datetime: {value}")


def format_iso8601(dt: datetime, include_microseconds: bool = True) -> str:
    """Format datetime as ISO 8601 string."""
    if include_microseconds:
        return dt.isoformat()
    return dt.replace(microsecond=0).isoformat()


def timestamp(dt: datetime) -> float:
    """Get Unix timestamp from datetime."""
    return to_utc(dt).timestamp()


def from_timestamp(ts: Union[int, float]) -> datetime:
    """Create datetime from Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def start_of_day(dt: Union[datetime, date], tz: Optional[timezone] = None) -> datetime:
    """Get start of day (00:00:00)."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.min)
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if tz:
        return dt.astimezone(tz)
    return dt


def end_of_day(dt: Union[datetime, date], tz: Optional[timezone] = None) -> datetime:
    """Get end of day (23:59:59.999999)."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.max)
    dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    if tz:
        return dt.astimezone(tz)
    return dt


def add_business_days(dt: datetime, days: int) -> datetime:
    """Add business days to datetime."""
    sign = 1 if days >= 0 else -1
    days = abs(days)
    current = dt

    for _ in range(days):
        current += timedelta(days=sign)
        while current.weekday() >= 5:  # Saturday=5, Sunday=6
            current += timedelta(days=sign)

    return current


def is_business_day(dt: datetime) -> bool:
    """Check if datetime is a business day."""
    return dt.weekday() < 5


def next_business_day(dt: datetime) -> datetime:
    """Get next business day."""
    result = dt + timedelta(days=1)
    while result.weekday() >= 5:
        result += timedelta(days=1)
    return result


def previous_business_day(dt: datetime) -> datetime:
    """Get previous business day."""
    result = dt - timedelta(days=1)
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def age(birthdate: date, reference: Optional[date] = None) -> int:
    """Calculate age from birthdate."""
    reference = reference or date.today()
    years = reference.year - birthdate.year
    if (reference.month, reference.day) < (birthdate.month, birthdate.day):
        years -= 1
    return years


def days_in_month(year: int, month: int) -> int:
    """Get number of days in month."""
    return calendar.monthrange(year, month)[1]


def month_range(year: int, month: int) -> tuple[date, date]:
    """Get first and last day of month."""
    first = date(year, month, 1)
    last = date(year, month, days_in_month(year, month))
    return first, last


def quarter_range(year: int, quarter: int) -> tuple[date, date]:
    """Get first and last day of quarter."""
    if not 1 <= quarter <= 4:
        raise ValueError("Quarter must be 1-4")
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    first = date(year, start_month, 1)
    last = date(year, end_month, days_in_month(year, end_month))
    return first, last


def format_duration(seconds: Union[int, float]) -> str:
    """Format duration as human-readable string."""
    if seconds < 0:
        return "-" + format_duration(-seconds)

    seconds = int(seconds)
    parts = []

    days = seconds // 86400
    if days:
        parts.append(f"{days}d")
        seconds %= 86400

    hours = seconds // 3600
    if hours:
        parts.append(f"{hours}h")
        seconds %= 3600

    minutes = seconds // 60
    if minutes:
        parts.append(f"{minutes}m")
        seconds %= 60

    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def parse_duration(text: str) -> timedelta:
    """Parse duration string (e.g., '1d 2h 30m')."""
    import re
    pattern = r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?"
    match = re.match(pattern, text.strip())
    if not match:
        raise ValueError(f"Invalid duration format: {text}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)

    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def time_ago(dt: datetime, reference: Optional[datetime] = None) -> str:
    """Format datetime as time ago string."""
    reference = reference or utc_now()
    diff = reference - to_utc(dt)

    if diff.total_seconds() < 0:
        return "just now"

    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    if seconds < 604800:
        days = seconds // 86400
        return f"{days}d ago"
    weeks = seconds // 604800
    return f"{weeks}w ago"


def cron_next(cron_expr: str, after: Optional[datetime] = None) -> datetime:
    """Get next datetime matching cron expression."""
    from croniter import croniter
    after = after or utc_now()
    cron = croniter(cron_expr, after)
    return cron.get_next(datetime)


def cron_match(cron_expr: str, dt: datetime) -> bool:
    """Check if datetime matches cron expression."""
    from croniter import croniter
    cron = croniter(cron_expr, dt)
    return cron.get_next(datetime) == dt