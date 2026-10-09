"""Local embeddings for the knowledge store.

Alan already runs Ollama for chat, so the second brain embeds through the same
local server. Nothing about the corpus leaves the machine to be *indexed*; only
a passage that retrieval actually selected, and only when the user asks a
question, may be handed to a cloud model for synthesis.

The embedder is deliberately optional. When Ollama is down, or the embedding
model has not been pulled, the store still ingests and still answers: retrieval
falls back to full-text search and every degraded result says so. A local-first
assistant must not become useless because one optional model is missing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. ``embed`` returns one vector per input."""

    name: str

    def available(self) -> tuple[bool, str]:
        """Whether vectors can be produced right now, and why not if they cannot."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each input; raises only on a genuine transport failure."""


class NullEmbedder:
    """No vectors at all: retrieval degrades to lexical-only and reports it."""

    name = "none"

    def available(self) -> tuple[bool, str]:
        return False, "embeddings disabled"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[] for _ in texts]


class OllamaEmbedder:
    """Embeddings via a local Ollama server.

    ``base_url`` should be the same Ollama Alan uses for chat -- the caller
    passes ``settings.ollama.base_url`` -- so indexing and conversation can
    never target two different servers.

    The batch endpoint (``/api/embed``) is tried first because indexing many
    chunks one HTTP request at a time is needlessly slow; older Ollama builds
    that predate it fall back to ``/api/embeddings`` per text.
    """

    name = "ollama-embed"

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11435",
        *,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.base_url = (base_url or "http://127.0.0.1:11435").rstrip("/")
        self.timeout = timeout
        #: Learned from the first successful response; 0 means "not yet known".
        self.dims = 0

    # -- availability --------------------------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            models = self._tags()
        except Exception as exc:  # noqa: BLE001 - availability must never raise
            return False, f"Ollama unreachable at {self.base_url} ({type(exc).__name__})"
        if _has_model(models, self.model):
            return True, f"{self.model} via {self.base_url}"
        if models:
            return False, (
                f"{self.model} is not pulled "
                f"(have: {', '.join(sorted(models)[:4])}); run: ollama pull {self.model}"
            )
        return False, "Ollama is running but has no models"

    def _tags(self) -> list[str]:
        req = urllib.request.Request(self.base_url + "/api/tags")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return [str(m.get("name", "")) for m in payload.get("models", [])]

    # -- embedding -----------------------------------------------------
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        batch = self._embed_batch(items)
        if batch is not None and len(batch) == len(items):
            return batch
        return [self._embed_one(text) for text in items]

    def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            return None
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or not vectors:
            return None
        return [[float(x) for x in vector] for vector in vectors]

    def _embed_one(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        vector = payload.get("embedding") or []
        return [float(x) for x in vector]


def _has_model(models: Sequence[str], wanted: str) -> bool:
    """Whether ``wanted`` is present, matching an untagged name to ``name:tag``."""
    for name in models:
        if name == wanted or name.split(":")[0] == wanted:
            return True
    return False


def build_embedder(model: str, base_url: str, *, enabled: bool = True) -> Embedder:
    """The embedder for a configuration; never raises on an unreachable server."""
    if not enabled or not model:
        return NullEmbedder()
    return OllamaEmbedder(model=model, base_url=base_url or "http://127.0.0.1:11435")
