"""Cloud providers: OpenAI and Anthropic."""

from __future__ import annotations

from typing import Iterator, Sequence

from .base import Message, Provider, ProviderError, _merge_system, connect_hint
from ..config import API_KEY_ENV, get_api_key

#: Read/write ceiling passed to the SDKs. Equal to their own default, so
#: widening the connect phase below is the only behavioural change: a hung
#: generation is bounded exactly as it was before.
_READ_TIMEOUT = 600.0


class OpenAIProvider(Provider):
    """Any backend that speaks the OpenAI wire format.

    OpenAI itself, Groq, OpenRouter, DeepSeek, xAI, Mistral and Google's
    OpenAI-compatible endpoint all work through this one class: they differ only
    in which env var holds the key and what the base URL is, which is precisely
    what ``provider`` (for the key, via ``API_KEY_ENV``) and ``base_url`` are
    for. Anthropic, which does not speak that format, has its own class below.

    Giving it the *provider name* rather than the key's env var name keeps
    ``config.API_KEY_ENV`` as the single map: a new provider then only has to
    appear there, and both key lookup and redaction follow automatically.
    """

    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "",
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model=model, base_url=base_url, **kwargs)
        self.name = provider
        self._api_key = get_api_key(provider)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            from openai import Timeout as OpenAITimeout

            params: dict = {
                "api_key": self._api_key,
                # Only the connect phase is widened. The SDK defaults it to
                # 5.0s, which assumes a clean link and cannot survive a TLS
                # inspecting proxy or an antivirus web filter -- both of which
                # push the handshake well past that and then get reported as a
                # provider timeout. Read/write stay at the SDK's default.
                "timeout": OpenAITimeout(
                    _READ_TIMEOUT, connect=self.connect_timeout
                ),
            }
            if self.base_url:
                params["base_url"] = self.base_url
            self._client = OpenAI(**params)
        return self._client

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            env = API_KEY_ENV.get(self.name, f"{self.name.upper()}_API_KEY")
            return False, f"{env} is not set (see config/.env)"
        return True, "ready"

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def chat(self, messages: Sequence[Message], system: str | None = None) -> str:
        try:
            # Constructing the client is inside the try: with no key it raises
            # before any request is made, and an SDK error escaping here would
            # reach the caller unlabelled -- without the backend's name, so the
            # operator could not tell which provider was broken.
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self.model,
                messages=_merge_system(messages, system),  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            # Names the backend that actually failed. When this class serves
            # Groq, an error reported as "OpenAI request failed" sends the
            # operator to the wrong dashboard to look for it.
            raise ProviderError(
                f"{self.name} request failed: {exc}"
                f"{connect_hint(exc, self.connect_timeout)}"
            ) from exc
        return resp.choices[0].message.content or ""

    def stream(
        self, messages: Sequence[Message], system: str | None = None
    ) -> Iterator[str]:
        try:
            client = self._get_client()
            events = client.chat.completions.create(
                model=self.model,
                messages=_merge_system(messages, system),  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
            )
            for event in events:
                delta = event.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"{self.name} stream failed: {exc}"
                f"{connect_hint(exc, self.connect_timeout)}"
            ) from exc


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(
        self, model: str = "claude-3-5-sonnet-latest", base_url: str = "", **kwargs
    ):
        super().__init__(model=model, base_url=base_url, **kwargs)
        self._api_key = get_api_key("anthropic")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic
            from anthropic import Timeout as AnthropicTimeout

            params: dict = {
                "api_key": self._api_key,
                # Same reasoning as OpenAIProvider: widen only the connect
                # phase, which is where a local HTTPS filter shows up.
                "timeout": AnthropicTimeout(
                    _READ_TIMEOUT, connect=self.connect_timeout
                ),
            }
            if self.base_url:
                params["base_url"] = self.base_url
            self._client = Anthropic(**params)
        return self._client

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "ANTHROPIC_API_KEY is not set (see config/.env)"
        return True, "ready"

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    @staticmethod
    def _split(messages: Sequence[Message], system: str | None) -> tuple[str, list]:
        system_parts = [system] if system else []
        convo = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(str(m.get("content", "")))
            else:
                convo.append({"role": m["role"], "content": m.get("content", "")})
        return "\n\n".join(p for p in system_parts if p), convo

    def chat(self, messages: Sequence[Message], system: str | None = None) -> str:
        client = self._get_client()
        sys_text, convo = self._split(messages, system)
        try:
            resp = client.messages.create(
                model=self.model,
                system=sys_text or None,  # type: ignore[arg-type]
                messages=convo,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"Anthropic request failed: {exc}"
                f"{connect_hint(exc, self.connect_timeout)}"
            ) from exc
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )

    def stream(
        self, messages: Sequence[Message], system: str | None = None
    ) -> Iterator[str]:
        client = self._get_client()
        sys_text, convo = self._split(messages, system)
        try:
            with client.messages.stream(
                model=self.model,
                system=sys_text or None,  # type: ignore[arg-type]
                messages=convo,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            ) as events:
                for text in events.text_stream:
                    yield text
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"Anthropic stream failed: {exc}"
                f"{connect_hint(exc, self.connect_timeout)}"
            ) from exc
