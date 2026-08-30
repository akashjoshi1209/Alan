"""JSON utilities using orjson."""

import orjson
from typing import Any, Callable, Optional, Type, TypeVar

from alan.utils.datetime import parse_datetime, format_datetime, now_utc


T = TypeVar("T")


def json_dumps(
    obj: Any,
    default: Optional[Callable[[Any], Any]] = None,
    option: int = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NAIVE_UTC | orjson.OPT_SORT_KEYS,
) -> str:
    """Serialize to JSON string."""
    if default is None:
        def default(o):
            if hasattr(o, "__json__"):
                return o.__json__()
            if isinstance(o, (set, frozenset)):
                return list(o)
            if isinstance(o, bytes):
                return o.decode("utf-8", errors="replace")
            raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return orjson.dumps(obj, default=default, option=option).decode()


def json_loads(data: Union[str, bytes]) -> Any:
    """Deserialize from JSON."""
    if isinstance(data, str):
        data = data.encode()
    return orjson.loads(data)


def json_dumps_bytes(obj: Any, **kwargs) -> bytes:
    """Serialize to JSON bytes."""
    return orjson.dumps(obj, **kwargs)


def json_loads_bytes(data: bytes) -> Any:
    """Deserialize from JSON bytes."""
    return orjson.loads(data)


class JSONEncoder:
    """Custom JSON encoder for common types."""

    @staticmethod
    def default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            # Pydantic v2
            return obj.model_dump()
        if hasattr(obj, "dict"):
            # Pydantic v1
            return obj.dict()
        if hasattr(obj, "__json__"):
            return obj.__json__()
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, datetime):
            return format_datetime(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, Path):
            return str(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def pretty_json(obj: Any, indent: int = 2) -> str:
    """Format as pretty JSON."""
    return orjson.dumps(
        obj,
        option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        default=JSONEncoder.default,
    ).decode()


def compact_json(obj: Any) -> str:
    """Format as compact JSON."""
    return orjson.dumps(obj, default=JSONEncoder.default).decode()


def validate_json(data: Union[str, bytes]) -> bool:
    """Check if data is valid JSON."""
    try:
        json_loads(data)
        return True
    except orjson.JSONDecodeError:
        return False


def extract_json(text: str) -> list[Any]:
    """Extract JSON objects from text."""
    results = []
    decoder = orjson.JSONDecoder()

    pos = 0
    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text[pos:])
            results.append(obj)
            pos += end
        except orjson.JSONDecodeError:
            pos += 1

    return results


def merge_json(base: dict, update: dict) -> dict:
    """Deep merge two JSON objects."""
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_json(result[key], value)
        else:
            result[key] = value
    return result


def json_diff(old: dict, new: dict) -> dict:
    """Compute diff between two JSON objects."""
    diff = {"added": {}, "removed": {}, "changed": {}, "unchanged": {}}
    all_keys = set(old.keys()) | set(new.keys())

    for key in all_keys:
        if key not in old:
            diff["added"][key] = new[key]
        elif key not in new:
            diff["removed"][key] = old[key]
        elif old[key] != new[key]:
            diff["changed"][key] = {"old": old[key], "new": new[key]}
        else:
            diff["unchanged"][key] = old[key]

    return diff


def flatten_json(obj: dict, separator: str = ".", prefix: str = "") -> dict:
    """Flatten nested JSON."""
    result = {}
    for key, value in obj.items():
        new_key = f"{prefix}{separator}{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten_json(value, separator, new_key))
        else:
            result[new_key] = value
    return result


def unflatten_json(obj: dict, separator: str = ".") -> dict:
    """Unflatten JSON."""
    result = {}
    for key, value in obj.items():
        parts = key.split(separator)
        target = result
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value
    return result