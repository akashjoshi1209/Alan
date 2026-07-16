"""Validation utilities."""

import re
import ipaddress
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional, Union
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, field_validator, ValidationError


class ValidationError(Exception):
    """Custom validation error."""
    def __init__(self, message: str, field: str = "", value: Any = None):
        self.message = message
        self.field = field
        self.value = value
        super().__init__(f"{field}: {message}" if field else message)


def validate_email(email: str) -> bool:
    """Validate email address format."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_url(url: str, schemes: Optional[list] = None) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        if not all([result.scheme, result.netloc]):
            return False
        if schemes and result.scheme not in schemes:
            return False
        return True
    except Exception:
        return False


def validate_uuid(value: str, version: int = 4) -> bool:
    """Validate UUID format."""
    try:
        UUID(value, version=version)
        return True
    except ValueError:
        return False


def validate_ip_address(ip: str) -> bool:
    """Validate IP address (IPv4 or IPv6)."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def validate_ip_network(network: str) -> bool:
    """Validate IP network CIDR."""
    try:
        ipaddress.ip_network(network, strict=False)
        return True
    except ValueError:
        return False


def validate_phone(phone: str, country: str = "US") -> bool:
    """Basic phone number validation."""
    # Remove common separators
    cleaned = re.sub(r"[\s\-\(\)\.]+", "", phone)
    # Check if it's all digits and reasonable length
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    return cleaned.isdigit() and 7 <= len(cleaned) <= 15


def validate_credit_card(number: str) -> bool:
    """Validate credit card using Luhn algorithm."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False

    # Luhn algorithm
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def validate_hex_color(color: str) -> bool:
    """Validate hex color code."""
    pattern = r"^#?([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$"
    return bool(re.match(pattern, color))


def validate_slug(slug: str) -> bool:
    """Validate URL slug."""
    pattern = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    return bool(re.match(pattern, slug))


def validate_json_schema(data: dict, schema: dict) -> bool:
    """Validate dict against JSON schema."""
    try:
        import jsonschema
        jsonschema.validate(data, schema)
        return True
    except jsonschema.ValidationError:
        return False


def validate_base64(data: str) -> bool:
    """Validate base64 encoded string."""
    import base64
    try:
        base64.b64decode(data, validate=True)
        return True
    except Exception:
        return False


def validate_datetime_string(
    value: str,
    formats: Optional[list] = None,
) -> Optional[datetime]:
    """Parse datetime string with multiple format attempts."""
    if formats is None:
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def validate_date_string(value: str) -> Optional[date]:
    """Parse date string."""
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def validate_version(version: str) -> bool:
    """Validate semantic version."""
    pattern = r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?$"
    return bool(re.match(pattern, version))


def validate_semver(version: str) -> tuple[int, int, int]:
    """Parse semantic version."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValidationError(f"Invalid semantic version: {version}")
    return tuple(map(int, match.groups()))


def validate_password_strength(
    password: str,
    min_length: int = 8,
    require_upper: bool = True,
    require_lower: bool = True,
    require_digit: bool = True,
    require_special: bool = True,
) -> tuple[bool, list[str]]:
    """Validate password strength."""
    errors = []

    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters")

    if require_upper and not re.search(r"[A-Z]", password):
        errors.append("Password must contain uppercase letter")

    if require_lower and not re.search(r"[a-z]", password):
        errors.append("Password must contain lowercase letter")

    if require_digit and not re.search(r"\d", password):
        errors.append("Password must contain digit")

    if require_special and not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        errors.append("Password must contain special character")

    return len(errors) == 0, errors


def validate_required_fields(data: dict, required: list[str]) -> list[str]:
    """Check for required fields."""
    missing = [field for field in required if field not in data or data[field] is None]
    return missing


def validate_choices(value: Any, choices: list) -> bool:
    """Validate value is in allowed choices."""
    return value in choices


def validate_range(
    value: Union[int, float],
    min_val: Optional[Union[int, float]] = None,
    max_val: Optional[Union[int, float]] = None,
) -> bool:
    """Validate numeric range."""
    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True


def validate_length(
    value: Union[str, list, dict],
    min_len: Optional[int] = None,
    max_len: Optional[int] = None,
) -> bool:
    """Validate length of string/collection."""
    length = len(value)
    if min_len is not None and length < min_len:
        return False
    if max_len is not None and length > max_len:
        return False
    return True


def sanitize_input(text: str, allowed_tags: Optional[list] = None) -> str:
    """Sanitize HTML input."""
    import html

    if allowed_tags is None:
        # Escape all HTML
        return html.escape(text)

    # Allow specific tags
    allowed = set(allowed_tags)
    # Simple tag stripping (for more robust, use bleach)
    pattern = re.compile(r"</?(\w+)[^>]*>")
    return pattern.sub(lambda m: m.group(0) if m.group(1) in allowed else "", text)


def validate_file_extension(filename: str, allowed: list[str]) -> bool:
    """Validate file extension."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in allowed


def validate_mime_type(mime: str, allowed: list[str]) -> bool:
    """Validate MIME type."""
    return mime in allowed


class ValidationResult:
    """Result of validation with errors."""

    def __init__(self):
        self.errors: dict[str, list[str]] = {}

    def add_error(self, field: str, message: str) -> None:
        if field not in self.errors:
            self.errors[field] = []
        self.errors[field].append(message)

    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def get_errors(self) -> dict:
        return self.errors

    def raise_if_invalid(self) -> None:
        if not self.is_valid():
            raise ValidationError("Validation failed", value=self.errors)


def validate_object(obj: Any, schema: type[BaseModel]) -> ValidationResult:
    """Validate object against Pydantic model."""
    result = ValidationResult()
    try:
        schema.model_validate(obj)
    except ValidationError as e:
        for error in e.errors():
            field = ".".join(str(x) for x in error["loc"])
            result.add_error(field, error["msg"])
    return result