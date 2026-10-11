"""Provider registry and selection logic."""

from __future__ import annotations

from ..config import PROVIDER_NAMES, Settings
from .base import Message, Provider, ProviderError
from .cloud import AnthropicProvider, OpenAIProvider
from .local import OllamaProvider

__all__ = [
    "Message",
    "Provider",
    "ProviderError",
    "OpenAIProvider",
    "AnthropicProvider",
    "OllamaProvider",
    "build_provider",
    "known_providers",
]

#: Provider names, in precedence order (a router tie falls back to it).
#:
#: This used to be a second, independent tuple -- ``runtime.build_router`` had
#: its own copy -- so adding a provider to one place silently left it unknown to
#: the other and the new backend never reached the router at all. It now comes
#: from ``config.PROVIDER_NAMES`` so there is exactly one list to extend.
_NAMES = PROVIDER_NAMES


def _to_categories(values) -> frozenset:
    """Config's category strings to ``ModelCategory``, ignoring unknown ones.

    A typo must not stop Alan from booting: categories affect *ranking*, never
    whether a request can be answered, so an unknown name costs that provider
    its specialist claim rather than costing the run. ``settings.toml`` documents
    the accepted names next to the key.
    """
    from ..interfaces.model import ModelCategory

    out = set()
    for value in values or ():
        try:
            out.add(ModelCategory(str(value).strip().lower()))
        except ValueError:
            continue
    return frozenset(out)


def _construct(name: str, settings: Settings) -> Provider:
    from ..config import ProviderSettings

    table = getattr(settings, name, None)
    if not isinstance(table, ProviderSettings):
        raise ProviderError(f"Unknown provider: {name!r}")

    # Routing signals ride along with every backend, so a provider can never be
    # constructed in a state where its priority silently defaults to something
    # other than what settings.toml asked for.
    common: dict = {
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "priority": table.priority,
        "categories": _to_categories(table.categories),
        # Ride along with every backend, so no provider can be constructed
        # with a handshake ceiling other than what settings.toml asked for.
        "connect_timeout": table.connect_timeout,
    }
    if name == "ollama":
        return OllamaProvider(
            model=table.model,
            base_url=table.base_url,
            num_ctx=table.num_ctx,
            keep_alive=table.keep_alive,
            **common,
        )
    if name == "anthropic":
        return AnthropicProvider(
            model=table.model, base_url=table.base_url, **common
        )
    # Everything else speaks the OpenAI wire format, so it needs only the key
    # to look up (by provider name) and the endpoint to call.
    return OpenAIProvider(
        model=table.model, base_url=table.base_url, provider=name, **common
    )


def build_provider(settings: Settings) -> Provider:
    """Resolve settings.provider into a ready-to-use Provider.

    provider = "auto" picks the first available backend in ``_NAMES`` order:
    openai -> anthropic -> groq -> openrouter -> ollama.
    """
    requested = (settings.provider or "auto").strip().lower()

    if requested not in ("auto", *_NAMES):
        raise ProviderError(
            f"Unknown provider '{requested}'. Choose one of: auto, {', '.join(_NAMES)}."
        )

    if requested == "auto":
        candidates = list(_NAMES)
    else:
        candidates = [requested]

    problems: list[str] = []
    for name in candidates:
        provider = _construct(name, settings)
        ok, reason = provider.available()
        if ok:
            return provider
        provider.close()
        problems.append(f"  - {name}: {reason}")

    raise ProviderError(
        "No usable AI backend was found.\n"
        + "\n".join(problems)
        + "\n\nAdd an API key to config/.env, or start Ollama and pull a model."
    )


def known_providers(settings: Settings) -> list[tuple[str, str, bool, str, str]]:
    """Return (name, model, available, reason, detail) for every provider."""
    rows = []
    for name in _NAMES:
        provider = _construct(name, settings)
        try:
            ok, reason = provider.available()
            rows.append((name, provider.model, ok, reason, provider.describe()))
        finally:
            provider.close()
    return rows
