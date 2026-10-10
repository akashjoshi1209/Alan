"""Alan - configuration and paths.

Everything is resolved relative to the project root, so one folder holds the
whole assistant. Set ALAN_HOME to override the detected root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

from dotenv import load_dotenv


def _find_home() -> Path:
    env = os.environ.get("ALAN_HOME")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    # This file lives at <home>/src/alan/config.py
    return Path(__file__).resolve().parents[2]


HOME: Path = _find_home()
CONFIG_DIR: Path = HOME / "config"
DATA_DIR: Path = HOME / "data"
LOG_DIR: Path = DATA_DIR / "logs"
SESSION_DIR: Path = DATA_DIR / "sessions"
COGNEE_DIR: Path = DATA_DIR / "cognee"
MODELS_DIR: Path = HOME / "models"
ENV_FILE: Path = CONFIG_DIR / ".env"
SETTINGS_FILE: Path = CONFIG_DIR / "settings.toml"

for _d in (CONFIG_DIR, DATA_DIR, LOG_DIR, SESSION_DIR, COGNEE_DIR, MODELS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

# Load API keys from config/.env into the environment.
load_dotenv(ENV_FILE, override=False)


@dataclass
class ProviderSettings:
    name: str
    model: str = ""
    base_url: str = ""
    #: Lower wins when several backends are eligible for a request (0 = most
    #: preferred). 100 means "no opinion", which is what every provider reports
    #: when this file does not say otherwise -- so an unset priority never
    #: masquerades as a strong one. Alan ships cloud = 0, ollama = 10, which is
    #: what makes a configured key actually get used; set them equal to return
    #: to local-first.
    priority: int = 100
    #: What the model is good at: fast, reasoning, coding, vision, embedding,
    #: general. Empty means "infer from the model name", which is a guess --
    #: "gpt-4o-mini" and "qwen2.5:1.5b" both guess as {fast, general}, so the
    #: guess alone can never tell a coding specialist from a chat model. State
    #: them here when you know them. Unknown names are ignored, not fatal.
    categories: tuple[str, ...] = ()
    #: KV-cache window for local models. Every doubling of this costs memory
    #: whether or not a prompt ever gets that long, and on a 4 GB machine that
    #: memory is the difference between a model that stays resident and one that
    #: is evicted and paged back in mid-answer. 0 leaves Ollama's default.
    num_ctx: int = 0
    #: How long Ollama holds the model in RAM after a request, e.g. "30m".
    #: A chat turn pauses while the user reads and types; reloading the model
    #: costs ~12 s, which is charged entirely to the next answer.
    keep_alive: str = ""


#: The env var holding each provider's key.
#:
#: One map, used by :func:`get_api_key` and by the provider constructors, so a
#: provider cannot be added without its key being reachable -- and, guarded by a
#: test, without its name appearing in ``interfaces.types._SECRET_ENV_NAMES`` so
#: the key is masked before it can reach a log line or a remote model.
API_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

#: Every provider Alan can construct, in registration order. Ties in the router
#: fall back to this order, so it doubles as the precedence list; local comes
#: last on purpose, so a cloud model that scores the same wins.
#:
#: This tuple is the *only* list of provider names. ``llm._NAMES`` and
#: ``runtime.build_router`` both import it -- they each used to carry their own
#: copy, which meant a new provider could be constructed by one path and
#: silently unknown to the other.
PROVIDER_NAMES: tuple[str, ...] = ("openai", "anthropic", "groq", "openrouter", "ollama")


#: Built-in defaults per provider, and with them the shipped routing policy.
#:
#: ``priority`` here is what "a configured key is actually used" means in one
#: number: any cloud provider ranks (0) above the local model (10), so adding a
#: key to ``config/.env`` changes which model answers. Set them equal in
#: settings.toml to go back to local-first. Categories are stated rather than
#: probed -- they come from each vendor's own capability list, because the only
#: automatic alternative is guessing from the model name, and "gpt-4o-mini" and
#: "qwen2.5:1.5b" guess identically.
#:
#: Endpoints below were verified against the live services during
#: implementation (GET /models: groq 200 with qwen/qwen3.8-27b present,
#: openrouter 200 with 464 models), not taken from memory.
_PROVIDER_DEFAULTS: dict[str, dict] = {
    "openai": {
        "model": "gpt-4o-mini",
        "priority": 0,
        "categories": ("general", "fast", "coding"),
    },
    "anthropic": {
        "model": "claude-3-5-sonnet-latest",
        "priority": 0,
        "categories": ("general", "fast", "coding"),
    },
    "groq": {
        "model": "qwen/qwen3.8-27b",
        "base_url": "https://api.groq.com/openai/v1",
        "priority": 0,
        "categories": ("general", "fast", "coding"),
    },
    "openrouter": {
        "model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1",
        "priority": 0,
        "categories": ("general", "fast", "coding"),
    },
    "ollama": {
        "model": "qwen2.5:1.5b",
        "base_url": "http://127.0.0.1:11435",
        "priority": 10,
        "categories": ("general", "fast"),
    },
}


def _provider_defaults(name: str) -> ProviderSettings:
    """Defaults for one provider. One dict, so TOML parsing and ``Settings()``
    built in code can never disagree about what a provider's priority is."""
    preset = _PROVIDER_DEFAULTS.get(name, {})
    return ProviderSettings(
        name=name,
        model=str(preset.get("model", "")),
        base_url=str(preset.get("base_url", "")),
        priority=int(preset.get("priority", 100)),
        categories=tuple(preset.get("categories", ())),
        num_ctx=0,
        keep_alive="",
    )


@dataclass
class MemorySettings:
    enabled: bool = True
    #: auto | cognee | mem0 | json | none
    backend: str = "auto"
    base_url: str = "http://127.0.0.1:8012"
    dataset: str = "alan"
    top_k: int = 5
    max_context_tokens: int = 512
    auto_recall: bool = True
    auto_remember: bool = True
    #: Used when the primary backend is unreachable, so Alan never loses memory.
    fallback: str = "json"
    #: mem0 extraction model. Empty -> the configured Ollama model.
    mem0_model: str = ""
    mem0_embedder: str = "BAAI/bge-small-en-v1.5"
    mem0_base_url: str = "http://127.0.0.1:11435"
    mem0_store_path: str = ""


@dataclass
class PermissionSettings:
    """The operator's side of the permission model (brief section 12).

    ``ceiling`` is the highest risk any agent may reach. Agents cannot raise it;
    only this file can.
    """

    ceiling: str = "medium_risk"
    auto_confirm: bool = False
    #: Keep user facts and memory operations on this machine. True by default,
    #: because what Alan recalls is about the user. Setting it to false lets a
    #: cloud model handle a private request -- an operator decision, made
    #: deliberately here rather than inferred from a prompt.
    local_only: bool = True
    #: Capabilities to refuse outright, in addition to the hardcoded FORBIDDEN set.
    deny: tuple[str, ...] = ()
    #: Raw ``[[permissions.rules]]`` entries from settings.toml, validated at
    #: load time (an unknown effect fails loudly) and turned into a RuleSet by
    #: the runtime when it builds the gate. Rules can only come from this file;
    #: nothing in Alan writes them.
    rules: tuple[dict, ...] = ()


@dataclass
class SchedulerSettings:
    """Always-on background tasks (Phase 1).

    ``enabled=false`` builds no scheduler at all -- no thread, no store, and
    ``/activity`` says so. The defaults are deliberately conservative: one task
    at a time (structural, not configurable) and a ten-minute ceiling per run.
    """

    enabled: bool = True
    #: How often the loop looks for due work, in seconds.
    tick_seconds: float = 5.0
    #: Wall-clock ceiling for one task run. Exceeding it records a failed run
    #: and lets the loop continue; the abandoned worker finishes in background.
    task_timeout: float = 600.0


@dataclass
class TelegramSettings:
    """Phone bridge to the approval queue (Phase 4).

    Disabled in effect by default: a blank ``bot_token`` builds no bridge at
    all, starts no thread and never touches the network. When configured, only
    ``chat_id`` may decide -- anyone else is ignored entirely. The bridge
    long-polls the Telegram Bot API (Alan dials out), so there is no webhook,
    no open port and nothing for the local network to expose.
    """

    enabled: bool = True
    #: From @BotFather. LEAVE EMPTY to disable the whole bridge.
    bot_token: str = ""
    #: The one chat that may decide. A token that leaks to a wrong chat still
    #: leaks nothing but silence.
    chat_id: str = ""
    #: Seconds each ``getUpdates`` call waits for a message (Telegram caps 50).
    long_poll: float = 20.0
    #: Pause between polls after a network error.
    retry_seconds: float = 3.0
    #: Announce task.started/finished/failed to the operator's chat too, so the
    #: phone is a place results reach you, not just decisions. A task waiting
    #: for approval is already announced by the approval line, so it is not
    #: repeated here.
    notify_tasks: bool = True


@dataclass
class ToolSettings:
    """Which capability providers are wired up."""

    #: browser is off by default (ADR-003) and execution off until enabled.
    execution: bool = False
    #: The jail `LocalExecution` enforces: any path outside this root is
    #: refused by the executor itself. Empty falls back to Alan's own folder.
    #: This is what makes "whole-device" access scoped rather than rootless.
    execution_root: str = ""
    browser: bool = False
    coding: bool = False
    #: The internet backends (Exa search + Jina Reader) are zero-config, so
    #: they default on: `web.search` / `web.open` stop needing the browser
    #: sidecar to fetch a page. The permission gate is unchanged -- these
    #: backends run *through* the same web.* tool specs.
    internet: bool = True
    #: Namespaces to register from the standard tool set.
    include: tuple[str, ...] = ("filesystem", "shell", "web", "memory", "system")


@dataclass
class BrowserSettings:
    """Where the browser sidecar listens.

    The browser is a separate process in a separate environment (ADR-003), so
    all Alan needs is the address to talk to. 8022 is distinct from cognee's
    8012 and Ollama's 11435/11434, so a port collision is never ambiguous.
    """

    base_url: str = "http://127.0.0.1:8022"
    #: Per-request ceiling in seconds, applied by the sidecar as well as here.
    timeout: float = 60.0
    #: The sidecar never downloads a browser; it drives an installed one.
    allow_download: bool = False


@dataclass
class VibecoderSettings:
    """How Alan hands coding work to Vibecoder.

    Vibecoder is the tool that writes code; Alan plans, delegates and reports.

    ``provider`` is pinned to ``nvidia`` by default rather than left as
    ``auto``. Vibecoder's auto-router prefers the heaviest model available, and
    on the reference machine that silently meant a 550B model on NVIDIA. That is
    the right model for writing code, but an unpinned route means a software
    update can change which company gets billed for a task Alan was asked to do.
    Pinning makes the choice visible and changeable; ``auto`` remains available
    for an operator who wants the routing.
    """

    #: ``nvidia``, ``groq``, ``ollama``, ``openai``, ``anthropic``, or ``auto``.
    provider: str = "nvidia"
    #: Path to the executable. Empty means "find it on PATH". A path given here
    #: is honoured exclusively -- a typo yields "not installed" rather than a
    #: silent fallback to a different binary.
    launcher: str = ""
    #: Where work happens when a request names no directory. Absolute, because a
    #: relative default depends on the shell Alan happened to be started from and
    #: a coding agent would then edit whatever it found there.
    project_dir: str = ""
    #: Ceiling on Vibecoder's own step budget, below its documented default of 40
    #: so a runaway task is bounded while a real read-edit-test still fits.
    max_steps: int = 25
    #: Wall-clock ceiling for one delegation, in seconds.
    timeout: float = 900.0


@dataclass
class EccSettings:
    """ECC's skills, commands, agents and advisory dev tools.

    Off in code and on in this install's ``settings.toml``: ECC is an
    integration someone chose, not a property of Alan, so a fresh checkout
    behaves exactly as before until an operator turns it on.

    Everything under this switch is read-only. ``enabled`` loads the catalog
    and the gated ``skill.*`` discovery tools; ``devtools`` additionally
    registers the eight advisory ``ecc.*`` tools, which analyse a project and
    return the commands to run rather than running them. The permission gate
    governs all of them -- nothing here grants, raises or bypasses a ceiling
    (ADR-006), and an operator rule on ``skill.*`` / ``ecc.*`` blocks the
    namespaces outright.
    """

    #: Master switch: load the ECC catalog + ``skill.*`` tools.
    enabled: bool = False
    #: Also register the advisory ``ecc.*`` dev tools.
    devtools: bool = False
    #: Where ECC lives. Empty means auto-detect: ``vendor/ECC``, then the
    #: OpenCode install at ``~/.config/opencode``.
    root: str = ""
    #: Cap on skill text pulled into one run, in characters. A 400-line skill
    #: would otherwise fill a local model's whole context window.
    max_chars: int = 4000


@dataclass
class KnowledgeSettings:
    """The second brain: a local, citable knowledge base.

    On by default, because it is the point of the feature and it is safe by
    construction: nothing is indexed until a path is named, retrieval never
    leaves the machine, and a missing embedding model degrades to keyword search
    instead of failing.

    ``ollama_base_url`` empty means "use the same Ollama as chat", which is the
    setting that matters: indexing and conversation must never target two
    different servers. The store path empty means ``data/knowledge/knowledge.db``.
    """

    enabled: bool = True
    #: Empty -> ``DATA_DIR/knowledge/knowledge.db``.
    store_path: str = ""
    #: Embedding model on the local Ollama server.
    embedder: str = "nomic-embed-text"
    #: Empty -> follow ``[ollama] base_url`` at build time.
    ollama_base_url: str = ""
    #: Retrieval window, in characters, and the overlap between windows.
    chunk_size: int = 1200
    overlap: int = 200
    #: How many cited passages one search returns.
    top_k: int = 8
    #: Allow a cloud model to synthesise an answer over retrieved passages.
    #: Ignored when ``[permissions] local_only`` is on -- both must agree before
    #: a passage ever leaves the machine.
    allow_cloud_synthesis: bool = True
    #: Largest file indexed, in megabytes. Bigger files are refused with a reason.
    max_file_mb: float = 2.0


@dataclass
class Settings:
    name: str = "Alan"
    provider: str = "auto"
    system_prompt: str = (
        "You are Alan, a helpful, concise, and friendly AI assistant. "
        "Answer clearly and get to the point."
    )
    temperature: float = 0.7
    max_tokens: int = 1024
    #: Provider tables. ``_provider_defaults`` supplies model, base_url,
    #: priority and categories for each, so a table only has to say what it
    #: wants to change; ``load_settings`` overlays every entry in
    #: ``PROVIDER_NAMES`` the same way, so adding a provider does not mean
    #: adding another branch here that someone can forget.
    openai: ProviderSettings = field(
        default_factory=lambda: _provider_defaults("openai")
    )
    anthropic: ProviderSettings = field(
        default_factory=lambda: _provider_defaults("anthropic")
    )
    groq: ProviderSettings = field(default_factory=lambda: _provider_defaults("groq"))
    openrouter: ProviderSettings = field(
        default_factory=lambda: _provider_defaults("openrouter")
    )
    ollama: ProviderSettings = field(
        default_factory=lambda: _provider_defaults("ollama")
    )
    memory: MemorySettings = field(default_factory=MemorySettings)
    permissions: PermissionSettings = field(default_factory=PermissionSettings)
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    vibecoder: VibecoderSettings = field(default_factory=VibecoderSettings)
    ecc: EccSettings = field(default_factory=EccSettings)
    knowledge: KnowledgeSettings = field(default_factory=KnowledgeSettings)


def _provider_from_table(name: str, table: dict) -> ProviderSettings:
    """Overlay one ``[provider]`` table onto that provider's defaults.

    Every key is optional: an older settings.toml that names only ``model``
    keeps the shipped priority and categories rather than silently losing them
    and dropping back to local-only routing.
    """
    default = _provider_defaults(name)
    categories = table.get("categories", default.categories)
    return ProviderSettings(
        name=name,
        model=str(table.get("model", default.model)),
        base_url=str(table.get("base_url", default.base_url)),
        # Read directly rather than through .get(), because priority 0 is a real
        # value ("prefer this") and .get()'s `or 0` fallback style would treat
        # an explicit 0 as absent and silently reinstate the shipped default.
        priority=int(table["priority"]) if "priority" in table else default.priority,
        categories=tuple(str(value) for value in (categories or ())),
        num_ctx=int(table.get("num_ctx", 0) or 0),
        keep_alive=str(table.get("keep_alive", "") or ""),
    )


def load_settings() -> Settings:
    """Load config/settings.toml, falling back to built-in defaults."""
    settings = Settings()
    if not SETTINGS_FILE.is_file() or tomllib is None:
        return settings

    with SETTINGS_FILE.open("rb") as fh:
        raw = tomllib.load(fh)

    alan = raw.get("alan", {})
    settings.name = alan.get("name", settings.name)
    settings.provider = alan.get("provider", settings.provider)
    settings.system_prompt = alan.get("system_prompt", settings.system_prompt)
    settings.temperature = float(alan.get("temperature", settings.temperature))
    settings.max_tokens = int(alan.get("max_tokens", settings.max_tokens))

    for key in PROVIDER_NAMES:
        if key in raw:
            setattr(settings, key, _provider_from_table(key, raw[key]))

    return _apply_tables(settings, raw)


_TRUE_WORDS = {"true", "yes", "on", "1"}
_FALSE_WORDS = {"false", "no", "off", "0"}


def _as_bool(value: Any, key: str) -> bool:
    """Coerce a TOML boolean, tolerating quoted spellings, loudly otherwise.

    ``bool("false")`` is ``True``, so a hand-edited ``enabled = "false"``
    (accidentally quoted) silently inverted a security setting. A real TOML
    boolean always exists by the time we get here; a string is accepted only in
    an obvious spelling, and anything else refuses to load rather than doing the
    wrong thing all session.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise ValueError(f"{key} must be true or false (got {value!r})")


def _apply_tables(settings: Settings, raw: dict) -> Settings:
    """Overlay the optional ``[memory]``, ``[permissions]``, ``[scheduler]``,
    ``[telegram]``, ``[tools]``, ``[browser]``, ``[vibecoder]``, ``[ecc]`` and
    ``[knowledge]`` tables.

    Every key is optional and every section absent from settings.toml keeps its
    built-in default, so an older config file still loads unchanged.
    """
    mem = raw.get("memory", {})
    if mem:
        m = settings.memory
        m.enabled = _as_bool(mem.get("enabled", m.enabled), "memory.enabled")
        m.backend = str(mem.get("backend", m.backend))
        m.base_url = str(mem.get("base_url", m.base_url))
        m.dataset = str(mem.get("dataset", m.dataset))
        m.top_k = int(mem.get("top_k", m.top_k))
        m.max_context_tokens = int(mem.get("max_context_tokens", m.max_context_tokens))
        m.auto_recall = _as_bool(mem.get("auto_recall", m.auto_recall), "memory.auto_recall")
        m.auto_remember = _as_bool(
            mem.get("auto_remember", m.auto_remember), "memory.auto_remember"
        )
        m.fallback = str(mem.get("fallback", m.fallback))
        m.mem0_model = str(mem.get("mem0_model", m.mem0_model))
        m.mem0_embedder = str(mem.get("mem0_embedder", m.mem0_embedder))
        m.mem0_base_url = str(mem.get("mem0_base_url", m.mem0_base_url))
        m.mem0_store_path = str(mem.get("mem0_store_path", m.mem0_store_path))

    vibe = raw.get("vibecoder", {})
    if vibe:
        v = settings.vibecoder
        v.provider = str(vibe.get("provider", v.provider))
        v.launcher = str(vibe.get("launcher", v.launcher))
        v.project_dir = str(vibe.get("project_dir", v.project_dir))
        v.max_steps = int(vibe.get("max_steps", v.max_steps))
        v.timeout = float(vibe.get("timeout", v.timeout))

    ecc = raw.get("ecc", {})
    if ecc:
        e = settings.ecc
        e.enabled = _as_bool(ecc.get("enabled", e.enabled), "ecc.enabled")
        e.devtools = _as_bool(ecc.get("devtools", e.devtools), "ecc.devtools")
        e.root = str(ecc.get("root", e.root))
        # Clamped, not validated: a ceiling that is merely silly (10 chars) is
        # harmless in a way a security setting is not, and refusing to start
        # over it would be the wrong kind of strict.
        e.max_chars = max(500, min(int(ecc.get("max_chars", e.max_chars)), 20000))

    perms = raw.get("permissions", {})
    if perms:
        p = settings.permissions
        p.ceiling = str(perms.get("ceiling", p.ceiling))
        p.auto_confirm = _as_bool(
            perms.get("auto_confirm", p.auto_confirm), "permissions.auto_confirm"
        )
        p.local_only = _as_bool(
            perms.get("local_only", p.local_only), "permissions.local_only"
        )
        p.deny = tuple(str(x) for x in perms.get("deny", p.deny))
        raw_rules = perms.get("rules", ())
        if raw_rules:
            if not isinstance(raw_rules, (list, tuple)) or not all(
                isinstance(entry, dict) for entry in raw_rules
            ):
                raise ValueError(
                    "permissions.rules must be an array of tables "
                    "(use [[permissions.rules]] entries)"
                )
            # Validate eagerly: a typo in a *security* setting must fail
            # visibly at startup, never degrade into "I thought that was off".
            from .permissions.rules import parse_rules

            parse_rules(raw_rules)  # raises ValueError on an unknown effect
            p.rules = tuple(dict(entry) for entry in raw_rules)

    sched = raw.get("scheduler", {})
    if sched:
        s = settings.scheduler
        s.enabled = _as_bool(sched.get("enabled", s.enabled), "scheduler.enabled")
        s.tick_seconds = float(sched.get("tick_seconds", s.tick_seconds))
        s.task_timeout = float(sched.get("task_timeout", s.task_timeout))

    tg = raw.get("telegram", {})
    if tg:
        t = settings.telegram
        t.enabled = _as_bool(tg.get("enabled", t.enabled), "telegram.enabled")
        t.bot_token = str(tg.get("bot_token", t.bot_token))
        t.chat_id = str(tg.get("chat_id", t.chat_id))
        t.long_poll = float(tg.get("long_poll", t.long_poll))
        t.retry_seconds = float(tg.get("retry_seconds", t.retry_seconds))
        t.notify_tasks = _as_bool(
            tg.get("notify_tasks", t.notify_tasks), "telegram.notify_tasks"
        )

    tools = raw.get("tools", {})
    if tools:
        t = settings.tools
        t.execution = _as_bool(tools.get("execution", t.execution), "tools.execution")
        t.execution_root = str(tools.get("execution_root", t.execution_root))
        t.browser = _as_bool(tools.get("browser", t.browser), "tools.browser")
        t.coding = _as_bool(tools.get("coding", t.coding), "tools.coding")
        t.internet = _as_bool(tools.get("internet", t.internet), "tools.internet")
        t.include = tuple(str(x) for x in tools.get("include", t.include))

    browser = raw.get("browser", {})
    if browser:
        b = settings.browser
        b.base_url = str(browser.get("base_url", b.base_url))
        b.timeout = float(browser.get("timeout", b.timeout))
        b.allow_download = _as_bool(
            browser.get("allow_download", b.allow_download), "browser.allow_download"
        )

    kb = raw.get("knowledge", {})
    if kb:
        k = settings.knowledge
        k.enabled = _as_bool(kb.get("enabled", k.enabled), "knowledge.enabled")
        k.store_path = str(kb.get("store_path", k.store_path))
        k.embedder = str(kb.get("embedder", k.embedder))
        k.ollama_base_url = str(kb.get("ollama_base_url", k.ollama_base_url))
        # Clamped, like the ECC cap: a silly window is harmless, and refusing to
        # start over it would be the wrong kind of strict.
        k.chunk_size = max(200, min(int(kb.get("chunk_size", k.chunk_size)), 8000))
        k.overlap = max(0, min(int(kb.get("overlap", k.overlap)), k.chunk_size // 2))
        k.top_k = max(1, min(int(kb.get("top_k", k.top_k)), 50))
        k.allow_cloud_synthesis = _as_bool(
            kb.get("allow_cloud_synthesis", k.allow_cloud_synthesis),
            "knowledge.allow_cloud_synthesis",
        )
        k.max_file_mb = max(0.05, float(kb.get("max_file_mb", k.max_file_mb)))

    return settings


def get_api_key(provider: str) -> str | None:
    env = API_KEY_ENV.get(provider)
    if not env:
        return None
    value = os.environ.get(env, "").strip()
    return value or None
