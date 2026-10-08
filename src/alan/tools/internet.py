"""Internet backends over the agent-reach command line: Exa + Jina Reader.

Alan already had a web path -- ``web.search`` / ``web.open`` drove the browser
sidecar (ADR-003). That sidecar is for *interacting* with a page: opening it in
a real browser, clicking, screenshotting. Reading a page and searching the web
do not need that machinery, and an assistant whose only web path is a sidecar
that has to be started first has effectively no web.

These two backends are zero-config on a stick install:

* **Exa** — ``mcporter call exa.web_search_exa ...`` A hosted semantic search;
  no key, no browser, no scraping.
* **Jina Reader** — ``curl https://r.jina.ai/<url>``. Fetches a page *on Jina's
  servers* and returns its readable text. Because the fetch happens remotely,
  ``internet.read`` cannot be pointed at a machine on Alan's own network --
  that is the local-SSRF hole a browser-based ``web.open`` has to guard against.

Both run as argv-list subprocesses: no shell string is ever built, so a query
or URL that contains shell metacharacters cannot become a command. Probing for
their presence is cheap (``shutil.which``), so ``internet`` may be checked on
every runtime build -- unlike the browser, there is nothing to start.

Alan calls them *through* the existing ``web.search`` / ``web.open`` tool specs
so the same permission gate that already guards those tools guards these
backends too: this module owns *how* the page is fetched, the registry owns
*whether* it may be.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..core.probecache import forget_key, probe_reason

#: Windows: keep the console window from flashing for the duration of a call.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

#: Ceiling on a single search call.
SEARCH_TIMEOUT = 30.0
#: Ceiling on a single page read.
READ_TIMEOUT = 45.0
#: How much of a page's text a read may return. Real pages are megabytes; what
#: a 1.5B model can look at is not.
MAX_READ_CHARS = 80_000
#: How much of each result's highlight passes into the tool's output.
MAX_SNIPPET_CHARS = 260
#: Search result count is clamped to this.
MAX_RESULTS = 8

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

#: A lone ``name:`` prefix that is not http(s) -- e.g. ``javascript:...``,
#: ``ftp:...``. A bare ``host:port`` (``example.com:8080``) also starts with a
#: recognised ``name:``, so the prefix alone is not enough to refuse; the
#: caller decides from what follows the colon.
_SCHEME_PREFIX_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_+.\-]*:", re.IGNORECASE)


class InternetUnavailable(RuntimeError):
    """A backend is not installed, has no configuration, or timed out."""


def _run(argv: Sequence[str], timeout: float) -> str:
    """Run one argv list, no shell, and return stdout. Raises on failure."""
    binary = shutil.which(argv[0]) or argv[0]
    # npm tools on Windows install as ``name.CMD``/``name.PS1`` shims, and
    # ``CreateProcess`` does not resolve a bare ``name`` to those. Whatever
    # ``shutil.which`` found is what we actually spawn, so an npm tool is a
    # plain argv list too.
    actual = [binary, *list(argv)[1:]]
    try:
        result = subprocess.run(
            actual,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise InternetUnavailable(
            f"{argv[0]} did not answer within {timeout:.0f}s (timeout)"
        )
    except OSError as exc:
        raise InternetUnavailable(
            f"could not run {argv[0]}: {type(exc).__name__}: {exc}"
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")
        raise InternetUnavailable(
            f"{argv[0]} exited {result.returncode}: {detail[:240] or 'no output'}"
        )
    return result.stdout or ""


def _validate_url(url: str) -> str:
    """Coerce a URL or refuse one that a remote proxy must never touch."""
    text = (url or "").strip()
    if not text:
        raise ValueError("give me a URL to read")
    if "://" in text:
        scheme = text.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"refusing to fetch {text!r}: only http(s) URLs are readable")
        candidate = text
    elif _SCHEME_PREFIX_RE.match(text):
        # A lone scheme prefix: "javascript:alert(1)", "ftp:...". A bare
        # "host:port" is NOT one of these -- "example.com:8080" ends in digits,
        # and refusing it would break a perfectly ordinary URL.
        _, _, rest = text.partition(":")
        if rest[:1].isdigit():
            candidate = f"https://{text}"
        else:
            raise ValueError(f"refusing to fetch {text!r}: only http(s) URLs are readable")
    else:
        # No scheme at all: "example.com/page" means https.
        candidate = f"https://{text}"
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"refusing to fetch {text!r}: no valid host in the URL")
    return candidate


# -- Exa -------------------------------------------------------------------


class ExaSearch:
    """``mcporter call exa.web_search_exa`` — a hosted semantic search."""

    name = "exa"

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[str], float], str] | None = None,
        mcporter: str = "mcporter",
    ) -> None:
        # `runner` is injectable so tests can prove the exact argv without a
        # network. It has the same shape as `_run`.
        self._runner = runner or _run
        self.mcporter = mcporter

    def available(self) -> tuple[bool, str]:
        """True when the mcporter CLI exists. No network, cheap, cached."""
        return probe_reason(
            f"internet|exa|{self.mcporter}", self._probe
        )

    def _probe(self) -> tuple[bool, str]:
        if shutil.which(self.mcporter) is None:
            return False, (
                "Exa search needs `mcporter` on PATH; agent-reach ships it "
                "(install: https://github.com/Panniantong/agent-reach)"
            )
        return True, "ready (Exa semantic search via mcporter)"

    def search(self, query: str, count: int = 5, *, timeout: float = SEARCH_TIMEOUT) -> list[dict[str, str]]:
        query = (query or "").strip()
        if not query:
            raise ValueError("give me something to search for")
        count = int(count) if count else 5
        count = 5 if count <= 0 else min(count, MAX_RESULTS)
        argv = [
            self.mcporter,
            "call",
            "exa.web_search_exa",
            "--output",
            "json",
            f"query={query}",
            f"numResults={count}",
        ]
        try:
            raw = self._runner(argv, timeout)
        except InternetUnavailable:
            # A probe that said "ready" was wrong: forget it so the next
            # report is truthful.
            forget_key(f"internet|exa|{self.mcporter}")
            raise
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> list[dict[str, str]]:
        import json as _json

        try:
            body = _json.loads(raw)
        except ValueError:
            raise InternetUnavailable("Exa returned a non-JSON reply")
        text = ""
        for item in body.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
                break
        if not text:
            raise InternetUnavailable("Exa returned no search results text")
        results: list[dict[str, str]] = []
        for block in re.split(r"\n---\s*\n", text):
            block = block.strip()
            if not block or not block.startswith("Title:"):
                continue
            title, url, published, author, snippet = _transcript_block(block)
            if not url:
                continue
            results.append(
                {
                    "title": title,
                    "url": url,
                    "published": published or "",
                    "author": author or "",
                    "snippet": snippet or "",
                }
            )
        return results


# -- Jina Reader -----------------------------------------------------------


class JinaReader:
    """``curl https://r.jina.ai/<url>`` — page text fetched on Jina's servers."""

    name = "jina"
    BASE = "https://r.jina.ai/"

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[str], float], str] | None = None,
        curl: str = "curl",
        base: str = BASE,
    ) -> None:
        self._runner = runner or _run
        self.curl = curl
        self.base = base.rstrip("/")

    def available(self) -> tuple[bool, str]:
        return probe_reason(f"internet|jina|{self.curl}", self._probe)

    def _probe(self) -> tuple[bool, str]:
        if shutil.which(self.curl) is None:
            return False, "reading pages needs `curl` on PATH"
        return True, "ready (Jina Reader via curl)"

    def read(self, url: str, *, timeout: float = READ_TIMEOUT) -> str:
        target = _validate_url(url)
        reader_url = f"{self.base}/{target}"
        argv = [
            self.curl,
            "-s",
            "-L",
            "--max-time",
            str(int(timeout)),
            reader_url,
        ]
        try:
            text = self._runner(argv, timeout + 5.0)
        except InternetUnavailable:
            forget_key(f"internet|jina|{self.curl}")
            raise
        if not text.strip():
            raise InternetUnavailable(f"Jina Reader returned nothing for {url}")
        return text[:MAX_READ_CHARS]


# -- facade ----------------------------------------------------------------


@dataclass
class Internet:
    """The whole internet surface, as the runtime sees it.

    Both defaults are the stock install's zero-config choices; tests inject
    fakes with the same shapes.
    """

    search_backend: ExaSearch = field(default_factory=ExaSearch)
    read_backend: JinaReader = field(default_factory=JinaReader)

    def available(self) -> tuple[bool, str]:
        search_ok, search_reason = self.search_backend.available()
        read_ok, read_reason = self.read_backend.available()
        if search_ok and read_ok:
            return True, "internet ready (Exa search + Jina Reader)"
        broken = [r for ok, r in ((search_ok, search_reason), (read_ok, read_reason)) if not ok]
        return False, "; ".join(broken)

    def search(self, query: str, count: int = 5) -> list[dict[str, str]]:
        return self.search_backend.search(query, count=count)

    def read(self, url: str) -> str:
        return self.read_backend.read(url)

    def describe(self) -> str:
        ok, reason = self.available()
        return f"internet: {reason}" if ok else f"internet: unavailable ({reason})"


def _transcript_block(block: str) -> tuple[str, str, str, str, str]:
    """One ``Title:/URL:/Published:/Author:/Highlights:`` block -> fields."""
    title = url = published = author = ""
    highlights: list[str] = []
    in_highlights = False
    for line in block.splitlines():
        if not in_highlights:
            if line.startswith("Title: "):
                title = line[len("Title: "):].strip()
            elif line.startswith("URL: "):
                url = line[len("URL: "):].strip()
            elif line.startswith("Published: "):
                published = line[len("Published: "):].strip()
            elif line.startswith("Author: "):
                author = line[len("Author: "):].strip()
            elif line.strip() == "Highlights:":
                in_highlights = True
        else:
            highlights.append(line.rstrip())
    snippet = "\n".join(highlights).strip()
    if len(snippet) > MAX_SNIPPET_CHARS:
        snippet = snippet[:MAX_SNIPPET_CHARS] + " …"
    return title, url, published, author, snippet


def format_results(results: list[dict[str, str]]) -> str:
    """Search results as the tool's output text (a small model reads this)."""
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result['title'] or '(no title)'}")
        lines.append(f"   {result['url']}")
        if result.get("snippet"):
            lines.append(f"   {result['snippet']}")
    return "\n".join(lines)


__all__ = [
    "ExaSearch",
    "Internet",
    "InternetUnavailable",
    "JinaReader",
    "MAX_READ_CHARS",
    "MAX_SNIPPET_CHARS",
    "MAX_RESULTS",
    "SEARCH_TIMEOUT",
    "READ_TIMEOUT",
    "format_results",
]