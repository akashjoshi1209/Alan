"""Text processing utilities."""

import re
import html
from typing import List, Optional
from unicodedata import normalize


def slugify(text: str, max_length: int = 100) -> str:
    """Convert text to URL-friendly slug."""
    text = normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:max_length]


def truncate(text: str, length: int, suffix: str = "...") -> str:
    """Truncate text to specified length."""
    if len(text) <= length:
        return text
    return text[:length - len(suffix)].rstrip() + suffix


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html.unescape(clean)


def extract_code_blocks(text: str) -> List[dict]:
    """Extract code blocks from markdown text."""
    pattern = r"```(\w+)?\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    return [{"language": lang or "text", "code": code.strip()} for lang, code in matches]


def extract_urls(text: str) -> List[str]:
    """Extract URLs from text."""
    url_pattern = r"https?://[^\s<>{}\|\\^`\[\]]+"
    return re.findall(url_pattern, text)


def extract_emails(text: str) -> List[str]:
    """Extract email addresses from text."""
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    return re.findall(email_pattern, text)


def word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def split_into_chunks(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
    separator: str = "\n\n",
) -> List[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Try to find separator near end
        if end < len(text):
            sep_pos = text.rfind(separator, start, end)
            if sep_pos != -1 and sep_pos > start + chunk_size // 2:
                end = sep_pos + len(separator)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap
        if start >= len(text):
            break

    return chunks


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize filename for filesystem."""
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    # Remove trailing dots and spaces
    filename = filename.strip(". ")
    # Truncate
    if len(filename) > max_length:
        name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        if ext:
            filename = name[:max_length - len(ext) - 1] + "." + ext
        else:
            filename = filename[:max_length]
    return filename or "unnamed"


def mask_sensitive(text: str, patterns: Optional[List[tuple]] = None) -> str:
    """Mask sensitive information in text."""
    default_patterns = [
        (re.compile(r"(?i)(password|secret|key|token|api_key)\s*[:=]\s*\S+"), r"\1=***MASKED***"),
        (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ***MASKED***"),
        (re.compile(r"(?i)authorization:\s*\S+"), "Authorization: ***MASKED***"),
    ]

    for pattern, replacement in (patterns or default_patterns):
        text = pattern.sub(replacement, text)

    return text


def camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def snake_to_camel(name: str) -> str:
    """Convert snake_case to CamelCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def highlight_terms(text: str, terms: List[str], tag: str = "mark") -> str:
    """Highlight search terms in text."""
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        text = pattern.sub(f"<{tag}>\\g<0></{tag}>", text)
    return text