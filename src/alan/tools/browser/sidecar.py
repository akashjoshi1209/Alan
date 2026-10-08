"""Browser automation as an Alan :class:`BrowserProvider`.

The browser runs in a **separate process** in a **separate Python environment**
(``env\\browser\\python``). This module is only the client. The split is not
incidental:

* ``browser-use`` pins its dependencies with ``==``. Those pins conflict with
  Alan's main environment -- ``openai`` 3.24 vs 2.x, ``rich`` 15.0 vs 14.3,
  ``requests`` 2.34 vs 2.33. Sharing one environment would downgrade the
  provider layer and the CLI to satisfy a capability that is off by default
  (ADR-003).
* A browser that crashes a hundred times must not be able to take Alan's
  assistant down with it.

What this module deliberately does *not* do:

* **It does not import ``browser_use`` or ``playwright`.** Neither exists in
  Alan's main environment, and importing them would defeat the isolation. The
  coupling is HTTP, nothing else.
* **It does not start a browser.** Connection to the sidecar is lazy, so
  building a runtime never launches a browser process.

Alan's own orchestrator plans and the browser merely executes. ``browser-use``'s
agent loop is intentionally unused: Alan already has a planner, and wiring in a
second one would give the product two competing brains (the "no duplicate
runtimes" constraint).
"""

from __future__ import annotations

import base64
from typing import Any

import requests

from ...interfaces.capabilities import BrowserProvider, BrowserResult

#: Where the sidecar listens. Deliberately not 8012 (cognee) or the Ollama
#: ports: each subsystem gets a fixed port so a collision is never ambiguous.
DEFAULT_BASE_URL = "http://127.0.0.1:8022"

#: Health probe timeout. Short, because this runs inside a tool call that
#: already has its own budget.
_HEALTH_TIMEOUT = 3.0


class BrowserUnavailable(RuntimeError):
    """The sidecar is not running or not answering."""


class SidecarBrowser(BrowserProvider):
    """A :class:`BrowserProvider` backed by the browser sidecar over HTTP."""

    name = "browser-sidecar"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- transport ------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any] | None = None) -> BrowserResult:
        url = f"{self.base_url}{path}"
        try:
            response = requests.post(url, json=payload or {}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BrowserUnavailable(
                f"the browser sidecar at {self.base_url} did not answer "
                f"({exc.__class__.__name__}). Start it with scripts\\browser-serve.ps1 "
                f"and set [tools] browser = true."
            ) from exc

        if response.status_code != 200:
            raise BrowserUnavailable(
                f"browser sidecar returned HTTP {response.status_code} for {path}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise BrowserUnavailable(
                f"browser sidecar returned a non-JSON body for {path}"
            ) from exc
        return _to_result(body)

    def available(self) -> tuple[bool, str]:
        """Cheap liveness probe. Never raises, never launches a browser."""
        from ...core.probecache import probe_reason

        return probe_reason(
            f"browser|{self.base_url}", self._probe, fresh=getattr(self, "fresh_probe", False)
        )

    def _probe(self) -> tuple[bool, str]:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=_HEALTH_TIMEOUT)
        except requests.RequestException as exc:
            return False, f"sidecar not reachable at {self.base_url} ({exc.__class__.__name__})"
        if response.status_code != 200:
            return False, f"sidecar unhealthy (HTTP {response.status_code})"
        try:
            body = response.json()
        except ValueError:
            return False, "sidecar health returned a non-JSON body"
        engine = str(body.get("engine") or "unknown")
        version = str(body.get("browser_use") or "?")
        return True, f"ready ({engine}, browser-use {version})"

    # -- BrowserProvider ------------------------------------------------
    def open(self, url: str, *, timeout: float = 30.0) -> BrowserResult:
        return self._post("/open", {"url": url, "timeout": timeout})

    def search(self, query: str, *, engine: str = "") -> BrowserResult:
        """Run a search.

        ``engine=""`` is the default and means "let the sidecar choose and fall
        back". It has to be: engines block and rewrite each other's results, so
        a caller that names one engine opts out of every other one. The engine
        known to work from a given network is a property of that network, which
        the sidecar can observe and a hard-coded default cannot.
        """
        return self._post("/search", {"query": query, "engine": engine})

    def navigate(self, url: str) -> BrowserResult:
        return self._post("/navigate", {"url": url})

    def click(self, selector: str) -> BrowserResult:
        return self._post("/click", {"selector": selector})

    def type_text(self, selector: str, text: str) -> BrowserResult:
        return self._post("/type_text", {"selector": selector, "text": text})

    def extract(self, selector: str) -> BrowserResult:
        return self._post("/extract", {"selector": selector})

    def screenshot(self, *, full_page: bool = False) -> BrowserResult:
        """PNG bytes, base64-encoded on the wire and decoded back here."""
        result = self._post("/screenshot", {"full_page": full_page})
        encoded = result.data.pop("png_base64", "")
        if encoded:
            try:
                result.screenshot = base64.b64decode(encoded)
            except (ValueError, TypeError) as exc:
                raise BrowserUnavailable(
                    "browser sidecar returned a screenshot that is not valid base64"
                ) from exc
        return result

    def close(self) -> BrowserResult:
        return self._post("/close")

    def describe(self) -> str:
        ok, reason = self.available()
        return f"{self.name}: {reason}"


def _to_result(body: dict[str, Any]) -> BrowserResult:
    """Map the sidecar's JSON onto Alan's own result type."""
    return BrowserResult(
        ok=bool(body.get("ok", False)),
        content=str(body.get("content") or ""),
        url=str(body.get("url") or ""),
        title=str(body.get("title") or ""),
        data=dict(body.get("data") or {}),
        error=body.get("error"),
    )


def build_browser_provider(base_url: str = "") -> SidecarBrowser:
    """Build the provider from settings, defaulting to the local sidecar."""
    from ...config import load_settings

    url = base_url or getattr(load_settings().browser, "base_url", "") or DEFAULT_BASE_URL
    return SidecarBrowser(url)


__all__ = [
    "DEFAULT_BASE_URL",
    "BrowserUnavailable",
    "SidecarBrowser",
    "build_browser_provider",
]
