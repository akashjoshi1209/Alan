"""Local provider backed by Ollama."""

from __future__ import annotations

from typing import Iterator, Sequence

from .base import Message, Provider, ProviderError, _merge_system
from ..core.probecache import forget_key, probe_reason


def _extract_content(resp: object) -> str:
    """Handle both dict and pydantic-style ollama responses."""
    msg = getattr(resp, "message", None)
    if msg is None and isinstance(resp, dict):
        msg = resp.get("message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    return content or ""


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2.5:1.5b",
        base_url: str = "http://127.0.0.1:11435",
        num_ctx: int = 0,
        keep_alive: str = "",
        **kwargs,
    ):
        super().__init__(model=model, base_url=base_url, **kwargs)
        self._client = None
        self.num_ctx = int(num_ctx or 0)
        self.keep_alive = str(keep_alive or "")
        # Set by `alan doctor` so diagnostics bypass the probe cache.
        self.fresh_probe = bool(kwargs.get("fresh_probe", False))

    def _get_client(self):
        if self._client is None:
            from ollama import Client

            self._client = Client(host=self.base_url or "http://127.0.0.1:11435")
        return self._client

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        inner = getattr(client, "_client", None)
        try:
            if inner is not None:
                inner.close()
        except Exception:  # noqa: BLE001
            pass

    def available(self) -> tuple[bool, str]:
        """Is the model servable right now?

        Cached briefly: this costs ~3 s on a loaded box and used to run on every
        single invocation, before Alan had done anything. ``fresh=True`` is for
        diagnostics, which must not report a remembered answer.
        """
        return probe_reason(
            self._probe_key(), self._probe, fresh=self.fresh_probe
        )

    def _probe_key(self) -> str:
        return f"ollama|{self.base_url}|{self.model}"

    def _probe(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
            models = client.list()
        except Exception as exc:  # noqa: BLE001
            return False, f"Ollama not reachable at {self.base_url} ({exc})"
        names = []
        for m in getattr(models, "models", []) or []:
            model_name = getattr(m, "model", None) or getattr(m, "name", None)
            if model_name:
                names.append(model_name)
        if self.model and not any(n == self.model or n.startswith(self.model) for n in names):
            return (
                False,
                f"Ollama is running but model '{self.model}' is not pulled. "
                f"Run: scripts\\pull-model.ps1",
            )
        return True, "ready"

    def _options(self) -> dict:
        options = {"temperature": self.temperature, "num_predict": self.max_tokens}
        if self.num_ctx > 0:
            options["num_ctx"] = self.num_ctx
        return options

    def _request(self) -> dict:
        """Request kwargs shared by chat and stream."""
        kwargs: dict = {}
        # Ollama unloads a model after 5 minutes idle by default. A chat turn
        # pauses while the user reads and types, so the next turn was paying a
        # ~12 s reload; that cost lands on the answer the user is waiting for.
        if self.keep_alive:
            kwargs["keep_alive"] = self.keep_alive
        return kwargs

    def chat(self, messages: Sequence[Message], system: str | None = None) -> str:
        client = self._get_client()
        try:
            resp = client.chat(
                model=self.model,
                messages=_merge_system(messages, system),  # type: ignore[arg-type]
                options=self._options(),
                **self._request(),
            )
        except Exception as exc:  # noqa: BLE001
            forget_key(self._probe_key())
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        return _extract_content(resp)

    def stream(
        self, messages: Sequence[Message], system: str | None = None
    ) -> Iterator[str]:
        client = self._get_client()
        try:
            for chunk in client.chat(
                model=self.model,
                messages=_merge_system(messages, system),  # type: ignore[arg-type]
                options=self._options(),
                stream=True,
                **self._request(),
            ):
                text = _extract_content(chunk)
                if text:
                    yield text
        except Exception as exc:  # noqa: BLE001
            forget_key(self._probe_key())
            raise ProviderError(f"Ollama stream failed: {exc}") from exc
