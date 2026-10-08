"""HTTP client for the local cognee memory sidecar.

Only the handful of cognee REST endpoints Alan needs are wrapped here:

* ``GET  /health``              -> is the sidecar up?
* ``POST /api/v1/remember``     -> store text (add + cognify, optionally in the background)
* ``POST /api/v1/recall``       -> retrieval-only search (no LLM), returns passages
* ``POST /api/v1/forget``       -> delete memory or a dataset
* ``GET  /api/v1/datasets``     -> list datasets

All calls are best-effort: memory is an enhancement, so a missing sidecar or a
failed request degrades to "no memory" rather than breaking the conversation.
"""

from __future__ import annotations

from typing import Any

import requests

from ..config import MemorySettings
from ..core.probecache import probe


class MemoryUnavailable(RuntimeError):
    """Raised when the cognee sidecar cannot be reached."""


def _entry_text(entry: dict[str, Any]) -> str:
    """Pull the human-readable text out of one recall result entry.

    Recall entries are a discriminated union keyed by ``source``; graph hits
    carry ``text``, session QA hits carry ``answer``/``context``, and session
    context hits carry ``content``.
    """
    for key in ("text", "answer", "content", "context"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class CogneeMemory:
    """Thin, fail-soft HTTP client for the local cognee sidecar."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        timeout: float = 60.0,
        write_timeout: float = 900.0,
    ) -> None:
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")
        self.dataset = settings.dataset
        self.top_k = settings.top_k
        self.timeout = timeout
        # The first write after a fresh copy downloads the GLiNER extractor
        # (~750 MB) and can take several minutes; later writes are ~15 s.
        self.write_timeout = write_timeout

    # -- health -------------------------------------------------------
    def health(self, *, fresh: bool = False) -> dict[str, Any] | None:
        """Return the sidecar's health payload, or ``None`` if unreachable.

        Cached briefly. This is called on construction *and* before every
        write, and each uncached call is an HTTP round trip to a sidecar that
        may be busy building a graph -- so on a write path it was latency the
        user waited on for no new information. Failures are cached for only a
        few seconds so a sidecar that is still starting is picked up promptly.
        """
        payload, ok = probe(
            f"cognee|{self.base_url}", self._health, fresh=fresh
        )
        return payload if ok else None

    def _health(self) -> tuple[bool, dict[str, Any] | None]:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=4)
        except requests.RequestException:
            return False, None
        if response.status_code != 200:
            return False, None
        try:
            return True, response.json()
        except ValueError:
            return True, {}

    def available(self) -> bool:
        return self.health() is not None

    def require(self) -> None:
        if not self.available():
            raise MemoryUnavailable(f"memory sidecar not reachable at {self.base_url}")

    # -- write --------------------------------------------------------
    def remember(self, text: str, *, background: bool = True) -> bool:
        """Store one piece of text as a memory. Returns ``True`` on success.

        ``background=True`` lets cognee build the graph server-side; the HTTP
        call returns as soon as the data is accepted.
        """
        text = (text or "").strip()
        if not text:
            return False
        # ``raw_data`` is a list-of-str form field; send it as a multipart part
        # so a single entry is still parsed as a one-element list.
        files = [("raw_data", (None, text))]
        data = {
            "datasetName": self.dataset,
            "self_improvement": "false",
            "run_in_background": "true" if background else "false",
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/remember",
                files=files,
                data=data,
                timeout=self.write_timeout,
            )
        except requests.RequestException:
            return False
        return response.status_code == 200

    def remember_turn(self, user: str, assistant: str, *, background: bool = True) -> bool:
        return self.remember(f"User: {user}\nAlan: {assistant}", background=background)

    # -- read ---------------------------------------------------------
    def recall(
        self,
        query: str,
        *,
        top_k: int | None = None,
        search_type: str = "CHUNKS",
    ) -> list[str]:
        """Return up to ``top_k`` memory passages relevant to ``query``.

        ``CHUNKS``/``SUMMARIES`` are retrieval-only, so no LLM is invoked and
        Alan composes the answer itself.
        """
        query = (query or "").strip()
        if not query:
            return []
        payload: dict[str, Any] = {
            "query": query,
            "datasets": [self.dataset],
            "topK": top_k or self.top_k,
            "searchType": search_type,
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/recall",
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException:
            return []
        if response.status_code != 200:  # 404 == "no memory yet"
            return []
        try:
            results = response.json()
        except ValueError:
            return []
        texts: list[str] = []
        seen: set[str] = set()
        for entry in results if isinstance(results, list) else []:
            if not isinstance(entry, dict):
                continue
            text = _entry_text(entry)
            if text and text not in seen:
                seen.add(text)
                texts.append(text)
        return texts

    def context_block(self, query: str, *, max_chars: int = 2000) -> str:
        """Recalled memory formatted for injection into the system prompt."""
        hits = self.recall(query)
        if not hits:
            return ""
        lines: list[str] = []
        used = 0
        for hit in hits:
            trimmed = " ".join(hit.split())
            if not trimmed:
                continue
            addition = len(trimmed) + 3
            if used + addition > max_chars:
                break
            lines.append(f"- {trimmed}")
            used += addition
        if not lines:
            return ""
        return (
            "Relevant memories from previous conversations "
            "(use them if helpful; do not invent beyond them):\n" + "\n".join(lines)
        )

    # -- delete -------------------------------------------------------
    def forget(self, *, memory_only: bool = False, everything: bool = False) -> bool:
        payload: dict[str, Any] = {}
        if everything:
            payload["everything"] = True
        else:
            payload["dataset"] = self.dataset
            if memory_only:
                payload["memoryOnly"] = True
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/forget",
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException:
            return False
        return response.status_code == 200

    # -- introspection ------------------------------------------------
    def datasets(self) -> list[dict[str, Any]]:
        try:
            response = requests.get(f"{self.base_url}/api/v1/datasets", timeout=self.timeout)
        except requests.RequestException:
            return []
        if response.status_code != 200:
            return []
        try:
            data = response.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []
