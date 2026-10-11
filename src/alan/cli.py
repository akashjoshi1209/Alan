"""Alan command-line interface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.console import Console

from . import __version__
from . import theme
from .assistant import Alan, session_path
from .chatfeed import ChatFeed
from .config import (
    COGNEE_DIR,
    DATA_DIR,
    ENV_FILE,
    HOME,
    LOG_DIR,
    MODELS_DIR,
    SESSION_DIR,
    SETTINGS_FILE,
    load_settings,
)
from .core.scheduler import parse_when
from .interfaces.types import PermissionRequest
from .llm import ProviderError, known_providers
from .sidecars import ensure_detached

# Rich prints box-drawing glyphs (the plan table's borders, the step bullets),
# and on a Windows console still reporting cp1252 those raise
# UnicodeEncodeError and kill the command before it draws anything. Reconfigure
# to UTF-8 with replacement *before* the Console exists; guarded so a stream
# that cannot be reconfigured (a closed pipe, a StringIO) still works.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass
del _stream

app = typer.Typer(add_completion=False, help="Alan - a local-first personal assistant.")
console = Console()

#: Rich's renderers, imported on first use rather than at module scope.
#:
#: ``Markdown`` drags in ``markdown_it`` and ``pygments``, ~180 ms of import
#: work on a loaded box. It is used in exactly two commands (``ask`` and
#: ``run``), but importing it at the top of this module charged every single
#: command for it -- including ``version`` and ``doctor``, which never render
#: markup. Cost is paid once, on first use, and only by the commands that need it.
#:
#: ``Table`` and ``Panel`` are cheap on their own but are grouped here so the
#: whole rendering layer loads through one door rather than being split between an
#: eager import and a lazy one.
#: ``Live`` is the streaming reply renderer -- only chat and ask use it.
#: Module and attribute name for each deferred Rich renderer.
_LAZY_RICH = {
    "Markdown": ("rich.markdown", "Markdown"),
    "Panel": ("rich.panel", "Panel"),
    "Table": ("rich.table", "Table"),
    "Live": ("rich.live", "Live"),
}


def _resolve(name: str):
    """Import one Rich renderer on first use, then cache it for the process."""
    module_name, attr = _LAZY_RICH[name]
    from importlib import import_module

    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


class _LazyRenderer:
    """Stand-in for a Rich class, loaded on first use.

    A PEP 562 module ``__getattr__`` would only serve ``cli.Table`` attribute
    access. The call sites here are plain global lookups inside function bodies
    (``table = Table(...)``), which never consult the module, so a proxy object
    bound as a real global is what actually works. Calling or using it as a class
    method triggers the real import.

    Attribute access is forwarded rather than resolved eagerly, so
    ``Panel.fit(...)`` still finds ``fit`` on the real class -- which matters,
    because the expensive module is only reached at that point, not before.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def _target(self):
        # Re-read `globals()` each time so the cache written by `_resolve` wins
        # over this proxy, and the real class is used from the second call on.
        real = globals().get(self._name)
        if real is None or isinstance(real, _LazyRenderer):
            real = _resolve(self._name)
        return real

    def __call__(self, *args, **kwargs):
        return self._target()(*args, **kwargs)

    def __getattr__(self, item: str):
        return getattr(self._target(), item)

    def __repr__(self) -> str:
        return f"<lazy rich renderer {self._name}>"


Markdown = _LazyRenderer("Markdown")
Panel = _LazyRenderer("Panel")
Table = _LazyRenderer("Table")
Live = _LazyRenderer("Live")


def _capability_line(settings) -> str:
    """What this install switched on, read from settings alone.

    Settings, not probes: the banner must never wait on a sidecar to draw.
    Anything unreachable still shows itself later, in `alan doctor`.
    """
    flags = [
        ("memory", getattr(settings.memory, "enabled", False)),
        ("brain", getattr(settings.knowledge, "enabled", False)),
        ("voice", getattr(settings.voice, "enabled", False)),
        ("ecc", getattr(settings.ecc, "enabled", False)),
        ("scheduler", getattr(settings.scheduler, "enabled", False)),
        (
            "telegram",
            bool(getattr(getattr(settings, "telegram", None), "bot_token", "")),
        ),
    ]
    return "  ".join(
        f"{name} {theme.mark('ok' if enabled else 'off')}" for name, enabled in flags
    )


def _print_banner(settings) -> None:
    console.print(
        Panel.fit(
            f"[bold]Alan[/bold] [dim]v{__version__}[/dim]\n"
            f"[dim]{_capability_line(settings)}[/dim]",
            border_style="cyan",
        )
    )


#: The chat surface's commands. One list feeds both /help and the line
#: editor's Tab completion, so the two cannot drift apart.
_HELP_SECTIONS = [
    ("conversation", [
        ("/help", "show this screen"),
        ("/reset, /new", "clear the conversation"),
        ("/save", "write this session to data\\sessions"),
        ("/sessions", "list saved sessions"),
        ("/load NAME", "load a saved session"),
        ("/provider", "show the backend in use"),
        ("/voicemode", "speak your question, hear the answer (Ctrl+C to stop)"),
        ("/exit", "quit (or Ctrl+C)"),
    ]),
    ("memory", [
        ("/remember TEXT", "store a memory"),
        ("/recall QUERY", "search memory"),
        ("/memory", "memory status"),
        ("/forget", "clear this session's memory"),
    ]),
    ("always-on", [
        ("/activity", "tasks, approvals, recent runs"),
        ("/approve ID, /reject ID", "decide a pending approval"),
        ("/telegram", "phone bridge state"),
        ("/schedule", "list scheduled tasks"),
        ("/schedule add TEXT", "schedule a task (say when it should run)"),
        ("/schedule cancel TERM", "cancel task(s) by id or phrase"),
        ("/schedule start, stop", "control the background loop"),
        ("/schedule pause, resume, run ID", "control one task"),
    ]),
]


def _completion_options() -> list:
    """The words Tab completes in chat: each command's first token."""
    options = {"/help"}
    for _section, rows in _HELP_SECTIONS:
        for command, _description in rows:
            options.add(command.split(",")[0].split(" ")[0])
    return sorted(options)


def _help_table():
    """The /help screen as a table: commands on the left, meaning on the right."""
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
    table.add_column("command", style=theme.ASSISTANT, no_wrap=True)
    table.add_column("what it does")
    for section, rows in _HELP_SECTIONS:
        table.add_row(f"[bold]{section}[/bold]", "")
        for command, description in rows:
            table.add_row(command, description)
    return table


def _print_help() -> None:
    console.print(_help_table())


class _LineReader:
    """One chat prompt line, with a real line editor when the console allows one.

    On a real console, pyreadline3 draws the prompt and edits the line: up/down
    history (persisted under ``data\\`` so it survives the session), Tab
    completion of the slash commands, and Ctrl+C to abandon the line. Without a
    console -- a pipe, a test, a redirected session -- it falls back to Rich's
    plain input, which cannot edit but never fails. The chat loop reads the
    same way either way, and neither knows which it got.

    Every failure degrades to that fallback on purpose: a fancier prompt must
    never be the reason chat will not start.
    """

    def __init__(self, history_path: Path) -> None:
        self._history = history_path
        self._rl = None
        # pyreadline3 reads keystrokes straight off the Windows console; on a
        # piped stdin that would hang waiting for keys nobody can press.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        try:
            import pyreadline3.rlmain as rlmain

            rl = rlmain.Readline()
            # pyreadline exposes set_prompt_color only inside its .pyinputrc
            # exec context; the instance reads this attribute directly. 10 is
            # that same table's "green" (FOREGROUND_GREEN | INTENSITY), which
            # is what keeps the you> prompt the identity colour.
            rl.prompt_color = 10
            rl.parse_and_bind("tab: complete")
            rl.set_completer(self._complete)
            rl.set_history_length(500)
            # One Ctrl+C abandons the line, the same as plain input(): a first
            # tap that merely inserts a character reads as "the key is broken".
            rl.allow_ctrl_c = False
            try:
                rl.read_history_file(str(history_path))
            except (OSError, IOError):
                pass  # no history yet -- the first session starts it
            self._rl = rl
        except Exception:  # noqa: BLE001 - degrade, never refuse to chat
            self._rl = None

    @property
    def editing(self) -> bool:
        """True when the fancy editor is active (the banner says so)."""
        return self._rl is not None

    def read(self) -> str:
        """One stripped line. Raises KeyboardInterrupt / EOFError like input()."""
        if self._rl is not None:
            try:
                return self._rl.readline("you> ").strip()
            except (KeyboardInterrupt, EOFError):
                raise
            except Exception:  # noqa: BLE001 - editor broke mid-session
                self._rl = None
        return console.input("[bold green]you>[/bold green] ").strip()

    def save(self) -> None:
        """Persist this session's history. Best effort: losing it costs arrows,
        not data."""
        if self._rl is None:
            return
        try:
            self._history.parent.mkdir(parents=True, exist_ok=True)
            self._rl.write_history_file(str(self._history))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _complete(text: str, state: int):
        """Readline's completer protocol: matches for the word being typed."""
        matches = [c for c in _completion_options() if c.startswith(text)]
        return matches[state] if state < len(matches) else None


def _draw_reply(chunks, show_time: bool = True) -> str:
    """Render a streamed reply: a spinner until the first token, then markdown
    that grows in place, then a dim footer with how long it took.

    The spinner exists because the honest gap -- asking a model, waiting for a
    provider -- used to print nothing at all, which reads as a hang. Once the
    first token arrives the answer draws itself as it streams, so bold, lists
    and code blocks look like the document they are rather than like source.

    Ctrl+C during a reply cancels *the reply*, not the session: the chat loop
    keeps the prompt. Returns everything that arrived before the end or the
    interrupt.
    """
    iterator = iter(chunks)
    text = ""
    started = time.monotonic()
    try:
        with console.status(f"[dim]{theme.INFO} thinking[/dim]", spinner="dots"):
            try:
                first = next(iterator)
            except StopIteration:
                first = None
            if first is not None:
                text = first
        if text:
            with Live(
                Markdown(text),
                console=console,
                refresh_per_second=10,
                vertical_overflow="visible",
            ) as live:
                for chunk in iterator:
                    text += chunk
                    live.update(Markdown(text))
    except KeyboardInterrupt:
        console.print(f"\n[dim]{theme.OFF} cancelled[/dim]")
        return text
    if not text:
        console.print(f"[dim]{theme.INFO} no reply[/dim]")
    elif show_time:
        console.print(f"[dim]{theme.elapsed(time.monotonic() - started)}[/dim]")
    return text


@app.command()
def version() -> None:
    """Show Alan's version and home directory."""
    console.print(f"Alan [bold]{__version__}[/bold]")
    console.print(f"home: {HOME}")


@app.command()
def where() -> None:
    """Show where Alan keeps its files."""
    table = Table(show_header=False, box=None)
    table.add_row("home", str(HOME))
    table.add_row("settings", str(SETTINGS_FILE))
    table.add_row("env (API keys)", str(ENV_FILE))
    table.add_row("data", str(DATA_DIR))
    table.add_row("memory", str(COGNEE_DIR))
    table.add_row("models", str(MODELS_DIR))
    console.print(table)


@app.command()
def providers() -> None:
    """List every backend and whether it is ready."""
    settings = load_settings()
    current = settings.provider
    table = Table(title="Alan providers", show_lines=False)
    table.add_column("provider")
    table.add_column("model")
    table.add_column("status")
    table.add_column("detail")
    for name, model, ok, reason, _detail in known_providers(settings):
        status = "[green]ready[/green]" if ok else "[red]unavailable[/red]"
        table.add_row(name, model, status, "" if ok else reason)
    console.print(table)
    console.print(f"configured provider: [bold]{current}[/bold]")
    console.print("Set the provider in config/settings.toml, or add keys to config/.env.")


@app.command()
def memory(
    query: Optional[str] = typer.Argument(None, help="Optional query to test a recall."),
) -> None:
    """Show persistent-memory status."""
    provider, note = _open_memory()
    if provider is None:
        console.print(f"[red]no memory backend: {note}[/red]")
        raise typer.Exit(code=1)
    try:
        console.print(f"[green]memory: {provider.name}[/green] [dim]{note}[/dim]")
        # Only a store that owns its rows can say how many it holds. Asking a
        # remote backend to count would be another round trip for a number
        # `alan backends` already reports more honestly.
        stats = getattr(provider, "stats", None)
        if callable(stats):
            data = stats()
            console.print(
                f"[dim]items: {data.get('total', 0)}   "
                f"sessions: {data.get('sessions', 0)}   "
                f"embedded: {data.get('embedded', 0)}   "
                f"size: {_human_bytes(data.get('db_bytes', 0))}[/dim]"
            )
            by_level = data.get("by_level") or {}
            if by_level:
                table = Table(title="by level")
                table.add_column("level")
                table.add_column("items", justify="right")
                for level, count in by_level.items():
                    table.add_row(str(level), str(count))
                console.print(table)
        if query:
            hits = provider.search(query, top_k=5)
            if not hits:
                console.print("[yellow]no memories matched[/yellow]")
            for hit in hits:
                console.print(Panel(hit.content, border_style="dim"))
    finally:
        _close_memory(provider)


def _open_memory():
    """Build the configured backend through the same factory everything uses.

    `memory` and `ingest` used to construct ``CogneeMemory`` directly, which
    meant both commands were broken the moment cognee stopped being the
    default -- reporting an unreachable sidecar while a perfectly healthy
    store sat one line away. One factory, one truth.
    """
    from .memory.providers import build_memory_provider

    settings = load_settings()
    return build_memory_provider(
        settings.memory,
        model=settings.ollama.model,
        ollama_base_url=settings.ollama.base_url,
    )


def _close_memory(provider) -> None:
    closer = getattr(provider, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001 - closing must not fail a command
            pass


def _human_bytes(size) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


@app.command()
def ingest(
    text: List[str] = typer.Argument(..., help="Text to store in Alan's memory."),
) -> None:
    """Store a note in Alan's persistent memory."""
    from .interfaces.types import MemoryItem, MemoryLevel

    provider, note = _open_memory()
    if provider is None:
        console.print(f"[red]no memory backend: {note}[/red]")
        raise typer.Exit(code=1)
    body = " ".join(text).strip()
    if not body:
        console.print("[red]nothing to store[/red]")
        raise typer.Exit(code=1)
    try:
        # L4, not L2: this is a fact the user chose to persist, so it competes
        # for recall after the session that wrote it is gone. The store derives
        # its own `kind` label from the item, so `alan forget --list` can tell
        # a note from an episode without the caller guessing.
        provider.remember(MemoryItem(
            content=body,
            level=MemoryLevel.L4_USER,
            source="user",
        ))
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        console.print(f"[red]failed to store memory: {exc}[/red]")
        raise typer.Exit(code=1)
    else:
        console.print("[green]stored in memory[/green]")
    finally:
        _close_memory(provider)


@app.command()
def forget(
    contains: Optional[str] = typer.Option(
        None, "--contains", help="Remove every memory whose text contains this."
    ),
    everything: bool = typer.Option(
        False, "--all", help="Remove every memory. This cannot be undone."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm a destructive --all."
    ),
    show: bool = typer.Option(
        False, "--list", help="List what is stored instead of removing anything."
    ),
) -> None:
    """Remove things from Alan's persistent memory.

    `ingest` could add a note but nothing could remove one, which made a
    mistaken or superseded note permanent -- there was no way back to a clean
    store. Removals are matched on text so they do not need an id the user
    would first have to go and find.

    `--all` is the one destructive path in Alan, so it needs `--yes` as well:
    a typo must not be able to erase the user's memory.
    """
    provider, note = _open_memory()
    if provider is None:
        console.print(f"[red]no memory backend available: {note}[/red]")
        raise typer.Exit(code=1)

    try:
        if show:
            items = _everything(provider)
            if not items:
                console.print("[yellow]memory is empty[/yellow]")
                return
            table = Table(title=f"stored memory ({len(items)})")
            table.add_column("id")
            table.add_column("level")
            table.add_column("text")
            for item in items:
                table.add_row(
                    item.id[:10], str(item.level).replace("MemoryLevel.", ""),
                    item.content.strip().replace("\n", " ")[:70],
                )
            console.print(table)
            return

        if everything:
            if not yes:
                console.print(
                    "[yellow]--all removes every memory and cannot be undone. "
                    "Add --yes to confirm.[/yellow]"
                )
                raise typer.Exit(code=1)
            removed = provider.forget(everything=True)
            console.print(f"[green]removed {removed} item(s) from memory[/green]")
            return

        if not contains:
            console.print("[yellow]pass --contains TEXT, --list, or --all[/yellow]")
            raise typer.Exit(code=1)

        needle = contains.strip().lower()
        if not needle:
            console.print("[yellow]--contains was empty[/yellow]")
            raise typer.Exit(code=1)

        victims = [i for i in _everything(provider) if needle in i.content.lower()]
        if not victims:
            console.print(f"[yellow]nothing stored matches {contains!r}[/yellow]")
            return
        table = Table(title=f"will remove {len(victims)} item(s)")
        table.add_column("id")
        table.add_column("text")
        for item in victims:
            table.add_row(item.id[:10], item.content.strip().replace("\n", " ")[:70])
        console.print(table)
        removed = sum(provider.forget(item_id=item.id) for item in victims)
        console.print(f"[green]removed {removed} item(s)[/green]")
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


def _memory_slash(alan, user: str) -> bool:
    """Handle ``/remember``, ``/recall``, ``/memory`` and ``/forget``.

    Split out of the chat loop so it can be tested, because it was written
    against the cognee client's API -- a ``remember`` that took a string and a
    ``background`` flag, plus ``recall``, ``health`` and ``datasets`` -- and
    every one of those raises on a provider. All four commands were broken the
    day memory stopped defaulting to the sidecar, in the one surface a person
    reaches for to check that memory works at all.

    Returns True when the line was one of these commands.
    """
    from .interfaces.types import MemoryItem, MemoryLevel

    if user.split(" ", 1)[0] not in ("/remember", "/recall", "/memory", "/forget"):
        return False
    if alan.memory is None:
        console.print("[yellow]memory is disabled in config/settings.toml[/yellow]")
        return True

    store = alan.memory
    if user.startswith("/remember"):
        text = user[len("/remember"):].strip()
        if not text:
            console.print("[yellow]usage: /remember TEXT[/yellow]")
            return True
        # A durable fact, not a session note: what is typed here is meant to
        # outlive the conversation that typed it.
        with console.status("[dim]storing memory...[/dim]"):
            try:
                store.remember(MemoryItem(
                    content=text,
                    level=MemoryLevel.L4_USER,
                    source="user",
                ))
            except Exception as exc:  # noqa: BLE001 - chat must survive this
                console.print(f"[red]could not store memory: {exc}[/red]")
            else:
                console.print("[green]stored[/green]")
    elif user.startswith("/recall"):
        query = user[len("/recall"):].strip()
        if not query:
            # An empty query is not "match everything": a substring test
            # against "" is true for every item, so the phrase bonus would
            # pass the relevance gate wholesale and dump the store into the
            # conversation.
            console.print("[yellow]usage: /recall QUERY[/yellow]")
            return True
        try:
            hits = store.search(query, top_k=5, session_id=alan.session_id)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]recall failed: {exc}[/red]")
            return True
        if not hits:
            console.print("[yellow]no memories matched[/yellow]")
        for hit in hits:
            label = str(hit.level).replace("MemoryLevel.", "")
            console.print(Panel(hit.content, title=label, border_style="dim"))
    elif user == "/memory":
        healthy, detail = store.available()
        if not healthy:
            console.print(f"[red]memory unavailable[/red] [dim]{detail}[/dim]")
        else:
            console.print(f"[green]memory ready[/green] "
                          f"[dim]backend: {store.name}[/dim]")
        # Only a store that owns its rows can count them; asking anything else
        # would be another round trip for a number `alan backends` reports.
        stats = getattr(store, "stats", None)
        if callable(stats):
            data = stats()
            console.print(f"[dim]{data.get('total', 0)} items, "
                          f"{data.get('sessions', 0)} sessions[/dim]")
        recent = getattr(store, "recent", None)
        if callable(recent) and alan.session_id:
            items = recent(session_id=alan.session_id, limit=10)
            if items:
                console.print("[dim]this session:[/dim]")
                for item in items:
                    console.print(f"  [dim]{item.content.strip()[:100]}[/dim]")
    elif user == "/forget":
        # The whole store is `alan forget --all --yes`. In a live conversation
        # the honest reading of "forget" is the one thing that conversation
        # owns: an unconfirmed wipe of every durable fact must not be one
        # keystroke away.
        forget_session = getattr(store, "forget_session", None)
        if callable(forget_session) and alan.session_id:
            removed = forget_session(alan.session_id)
            console.print(f"[dim]cleared {removed} item(s) from this session[/dim]")
            console.print(
                "[dim]use 'alan forget --all --yes' to clear everything[/dim]"
            )
        else:
            ok = store.forget(everything=True)
            console.print("[dim]memory cleared[/dim]" if ok
                          else "[red]forget failed[/red]")
    return True


def _everything(provider) -> list:
    """Every stored item, whatever the query.

    `search` cannot be used for this: it is relevance-ranked and gated, so it
    would omit exactly the items a cleanup needs to see.

    A backend that implements `items()` is asked directly. The two fallbacks
    below reach into internals that only two providers still expose, and they
    are the reason this function exists in this shape: when the default
    backend became the SQLite store, `forget --list` reported an empty memory
    and `--contains` matched nothing, because neither `_index` nor `_load` is
    a method that store has. Everything looked fine -- `ingest` reported
    "stored in memory" -- while removal had quietly stopped working. An
    unsupported listing must fail visibly, never silently.
    """
    from .interfaces.types import MemoryItem, MemoryLevel

    records: list = []

    # The supported path: a backend that can enumerate its own rows does so.
    enumerated = getattr(provider, "items", None)
    if callable(enumerated):
        try:
            listed = list(enumerated())
        except Exception:  # noqa: BLE001
            listed = []
        if listed:
            records = listed

    if not records:
        index = getattr(provider, "_index", None)
        if isinstance(index, dict):
            # cognee: fingerprint -> serialised MemoryItem
            records = list(index.values())
        else:
            loader = getattr(provider, "_load", None)
            if callable(loader):
                for level in MemoryLevel:
                    try:
                        records.extend(loader(level))
                    except Exception:  # noqa: BLE001
                        continue

    items: list = []
    for record in records:
        try:
            items.append(
                record if isinstance(record, MemoryItem)
                else MemoryItem.from_dict(record)
            )
        except Exception:  # noqa: BLE001
            continue
    return items


def _confirm_on_console(request: PermissionRequest) -> bool:
    """Ask the operator, on the terminal, whether a capability may run.

    This is the only place in Alan where a destructive or outbound action is
    approved by a person, and it is deliberately the *only* place: the gate
    refuses anything it cannot confirm, and this is the one surface that can.

    Anything ambiguous is a no. Ctrl-C, a closed stdin, a non-interactive pipe
    and a bare Enter all decline, because a capability that acts without a clear
    yes has not been approved -- it has merely not been stopped. The prompt names
    the capability and its risk rather than the tool, because the capability is
    what the operator actually granted access to.
    """
    try:
        console.print()
        console.print(
            f"[bold yellow]Approval needed:[/bold yellow] {request.capability} "
            f"(risk={request.risk})",
            style="yellow",
        )
        if request.reason:
            console.print(f"  {request.reason}", style="dim")
        if request.arguments:
            shown = ", ".join(
                f"{key}={value!r}" for key, value in sorted(request.arguments.items())
            )
            console.print(f"  arguments: {shown}", style="dim")
        answer = typer.prompt("Allow this?", default="n", show_default=True)
    except (KeyboardInterrupt, EOFError, typer.Abort):
        # A withdrawn hand is a refusal. Aborting the tool call without letting
        # it run is the whole point of asking.
        return False
    except Exception:  # noqa: BLE001 - a prompt that cannot be shown cannot approve
        return False
    return str(answer).strip().lower() in {"y", "yes"}


def _speak(reply: str, *, announce: bool = False) -> None:
    """Speak a finished reply, if the operator asked for speech.

    Deliberately after the answer is on screen and deliberately best-effort:
    a speaker that is off, down or absent costs the sound and never the turn
    that already succeeded.

    ``announce`` prints the note on success as well as failure. In voice mode
    the room going quiet is otherwise ambiguous -- is it thinking, or did it
    fail? -- so a spoken turn says what it said and how long it took. A typed
    ``ask`` keeps quiet on success because nothing there needs confirming.
    """
    if not reply:
        return
    settings = load_settings()
    if not getattr(settings.voice, "tts_enabled", False):
        return
    from . import voice as voice_mod

    spoken, note = voice_mod.say(settings.voice, reply)
    if announce or not spoken:
        console.print(f"[dim]{note}[/dim]")


def _one_shot(text: str, use_stream: bool) -> None:
    # Kicked off before the runtime is built so a sidecar that is down boots
    # in parallel with startup instead of being waited on: memory falls back
    # to JSON until it answers, and doctor names which one is in use.
    ensure_detached()
    try:
        alan = Alan(confirmer=_confirm_on_console)
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(code=1)

    if use_stream:
        try:
            # _draw_reply returns everything that streamed, which is exactly
            # what speech needs -- the spoken reply and the printed one are
            # then the same text rather than two versions that can diverge.
            reply = _draw_reply(alan.stream(text, remember=False))
        except ProviderError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
            raise typer.Exit(code=1)
    else:
        try:
            reply = alan.ask(text, remember=False)
            console.print(Markdown(reply))
        except ProviderError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
            raise typer.Exit(code=1)

    _speak(reply)


@app.command()
def ask(
    text: List[str] = typer.Argument(..., help="The question or prompt for Alan."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream the reply."),
) -> None:
    """Ask Alan a single question and exit."""
    _one_shot(" ".join(text), stream)


@app.command()
def voice(
    transcribe_only: bool = typer.Option(
        False, "--transcribe-only", help="Print the transcript and stop; do not ask."
    ),
    prompt: Optional[str] = typer.Option(
        None, "--prompt", help="Vocabulary hint for this turn (spelling of names/jargon)."
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream the reply."),
) -> None:
    """Talk to Alan: ask by voice, get the answer a typed ask would give.

    Recording is push-to-talk in one breath: speak, then pause. Partials
    appear as you go, and the turn ends after [voice] silence_ms of quiet.
    The transcript then goes through the ordinary ask pipeline, so a spoken
    request gets the same memory, permissions and reporting as a typed one.
    """
    from . import voice as voice_mod

    settings = load_settings()
    if prompt is not None:
        settings.voice.prompt = prompt

    if not settings.voice.enabled:
        console.print(
            # The backslash keeps rich from eating [voice] as a markup tag.
            "[yellow]voice input is off - set \\[voice] enabled = true "
            "in config/settings.toml[/yellow]"
        )
        raise typer.Exit(code=1)

    ready_ok, note = voice_mod.ready(settings.voice)
    if not ready_ok:
        console.print(f"[red]whisper sidecar {note}[/red]")
        console.print(
            "[dim]start it with scripts\\whisper-serve.ps1 "
            "(scripts\\run.ps1 voice starts it for you)[/dim]"
        )
        raise typer.Exit(code=1)

    client_ok, client_note = voice_mod.recorder_available()
    if not client_ok:
        console.print(f"[red]voice client {client_note}[/red]")
        raise typer.Exit(code=1)

    console.print("[dim]speak now - the turn ends when you pause[/dim]")
    try:
        transcript = voice_mod.listen(settings.voice)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not transcript:
        console.print(
            "[yellow]heard nothing - try again, or lower \\[voice] threshold "
            "if the room is quiet[/yellow]"
        )
        raise typer.Exit(code=0)

    console.print(f"[dim]you> {transcript}[/dim]")
    if transcribe_only:
        # Plain text on stdout so it can be piped; the dim echo above is for
        # a human at a terminal.
        console.print(transcript, markup=False)
        return
    _one_shot(transcript, stream)


@app.command()
def chat(
    system: Optional[str] = typer.Option(
        None, "--system", "-s", help="Override the system prompt for this session."
    ),
) -> None:
    """Start an interactive conversation with Alan."""
    # Same fire-and-forget ensure as `ask`: started before the runtime build
    # so a cold cognee warms up in parallel with startup, never in front of it.
    ensure_detached()
    try:
        alan = Alan(confirmer=_confirm_on_console)
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if system:
        alan.system_prompt = system

    _print_banner(load_settings())
    console.print(f"[dim]{alan.provider.describe()}[/dim]")
    reader = _LineReader(DATA_DIR / "chat_history.txt")
    console.print(
        "[dim]/help commands · /exit quit"
        + (" · ↑↓ history · tab completes" if reader.editing else "")
        + "[/dim]\n"
    )

    # A chat session owns the background scheduler: this is the only surface
    # that is long-lived enough for it to mean anything.
    if alan.start_scheduler():
        console.print("[dim]scheduler running (see /activity)[/dim]")

    # ... and the phone bridge, when the operator configured one. Both loops
    # belong to chat; one-shot commands never spawn either.
    if alan.start_telegram():
        console.print("[dim]telegram bridge running (approvals on your phone)[/dim]")

    # Background task lines are collected between turns and printed at the safe
    # moment -- after a reply, before the next prompt -- never mid-prompt.
    feed = ChatFeed(alan.runtime.bus)

    # Voice mode is a toggle on this loop rather than a loop of its own. That
    # is deliberate: the conversation, its history, Tab completion and the
    # keyboard all keep working across the switch, and leaving is a word away
    # rather than a nested session you have to climb out of.
    voice_on = False
    voice_settings = load_settings().voice

    while True:
        for line in feed.drain():
            console.print(f"[dim]{line}[/dim]")
        if voice_on:
            # Imported here because it is only paid for while the mode is on.
            from . import voice as voice_mod

            # Printed from here rather than left to the client, because there
            # is a gap before it says anything of its own -- interpreter start,
            # model load, opening the socket. Silence across that gap reads as
            # a hang, and a hang is what reaches for Ctrl+C.
            console.print("[dim]listening...[/dim]")
            try:
                heard = voice_mod.listen(voice_settings)
            except KeyboardInterrupt:
                # One Ctrl+C drops back to typing; it ends the mode, not the
                # session. The recorder is the thing that was interrupted, so
                # treating this as "quit chat" would punish the wrong gesture.
                voice_on = False
                console.print("[dim]voice mode off - typing again[/dim]")
                continue
            except RuntimeError as exc:
                # A sidecar that died mid-session takes the mode with it, not
                # the conversation: this is a mode, not the only way in.
                console.print(f"[red]{exc}[/red]")
                voice_on = False
                continue
            if not heard:
                # Heard nothing: stay in the mode and listen again. Silences
                # are normal -- a pause is how you end a turn, not a failure.
                continue
            # Echoed so trust is checkable: if Whisper heard the wrong words,
            # the operator sees it before the answer is built on them.
            console.print(f"[bold green]you>[/bold green] {heard}")
            if voice_mod.is_exit_phrase(heard):
                voice_on = False
                console.print("[dim]voice mode off[/dim]")
                continue
            user = heard
        else:
            try:
                user = reader.read()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
        if not user:
            continue
        if user in ("/exit", "/quit", ":q"):
            break
        if user == "/help":
            _print_help()
            continue
        if user in ("/reset", "/new"):
            alan.reset()
            console.print("[dim]conversation cleared[/dim]")
            continue
        if user == "/provider":
            console.print(f"[dim]{alan.provider.describe()}[/dim]")
            continue
        if user == "/voicemode":
            # Deliberately no /voice alias: `alan voice` is already a top-level
            # command with different behaviour (one ask, then quit), and a
            # second spelling that differs by three letters would be a trap.
            from . import voice as voice_mod

            if voice_on:
                voice_on = False
                console.print("[dim]voice mode off - typing again[/dim]")
                continue
            # Probed before entering, not discovered on the first word: a mode
            # that opens onto a dead microphone reads as a broken prompt when
            # it is really a sidecar nobody started. Re-read here so an edit
            # to settings.toml takes effect at this boundary rather than
            # needing a whole new chat session.
            voice_settings = load_settings().voice
            can_start, note = voice_mod.mode_status(voice_settings)
            if not can_start:
                console.print(f"[red]{note}[/red]")
                continue
            voice_on = True
            console.print(f"[green]voice mode on[/green] [dim]- {note}[/dim]")
            console.print(
                '[dim]speak; say "stop listening", or Ctrl+C, to type again[/dim]'
            )
            continue
        if user == "/save":
            path = alan.save_session()
            console.print(f"[dim]saved: {path}[/dim]")
            continue
        if user == "/sessions":
            names = sorted(p.stem for p in SESSION_DIR.glob("*.json"))
            console.print("[dim]" + (", ".join(names) if names else "no saved sessions") + "[/dim]")
            continue
        if user.startswith("/load "):
            name = user[len("/load ") :].strip()
            try:
                target = session_path(name)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            if target.exists():
                alan.load_session(target)
                console.print(f"[dim]loaded: {target.name} ({len(alan.history)} messages)[/dim]")
            else:
                console.print("[red]session not found[/red]")
            continue

        # -- always-on: scheduler and approvals -------------------------
        if user.startswith(("/approve ", "/reject ")):
            approve = user.startswith("/approve")
            target = user[len("/approve ") if approve else len("/reject ") :].strip()
            if not target:
                console.print(f"[yellow]usage: {user.split()[0]} <approval id>[/yellow]")
            else:
                console.print(alan.decide(target, approve))
            continue
        if user == "/telegram":
            if alan.telegram is None:
                console.print(
                    "[yellow]telegram bridge not configured "
                    "(set bot_token and chat_id in settings.toml)[/yellow]"
                )
            else:
                state = alan.telegram.status()
                console.print(
                    f"[dim]telegram bridge: chat {state['chat_id']} "
                    f"({('running' if state['running'] else 'stopped')}"
                    f"{('/' + state['note']) if state['note'] else ''})[/dim]"
                )
            continue
        if user == "/activity" or user == "/schedule":
            alan.start_scheduler()
            console.print(Panel(alan.activity(), border_style="dim"))
            continue
        if user.startswith("/schedule "):
            rest = user[len("/schedule ") :].strip()
            if rest in ("start", "run"):
                console.print(
                    "[green]scheduler started[/green]"
                    if alan.start_scheduler()
                    else "[yellow]scheduler disabled in settings[/yellow]"
                )
            elif rest == "stop":
                alan.stop_scheduler()
                console.print("[dim]scheduler stopped; tasks resume next chat[/dim]")
            elif rest.split(" ", 1)[0] == "add" and " " in rest:
                request = rest.split(" ", 1)[1].strip()
                if not request:
                    console.print("[yellow]usage: /schedule add TEXT[/yellow]")
                else:
                    try:
                        _draw_reply(alan.stream(request))
                    except Exception as exc:  # the chat loop must survive an ask
                        console.print(f"\n[red]error: {type(exc).__name__}: {exc}[/red]\n")
            elif rest.split(" ", 1)[0] == "cancel" and " " in rest:
                target = rest.split(" ", 1)[1].strip()
                result = alan.runtime.registry.invoke("schedule.remove", {"match": target})
                console.print(result.output if result.ok else f"[red]{result.error}[/red]")
            elif rest.split(" ", 1)[0] in ("pause", "resume", "run"):
                action, _, target_ = rest.partition(" ")
                target_ = target_.strip()
                if not target_:
                    console.print(f"[yellow]usage: /schedule {action} <task id>[/yellow]")
                else:
                    method = {
                        "pause": alan.scheduler.pause,
                        "resume": alan.scheduler.resume,
                        "run": alan.scheduler.run_now,
                    }[action]
                    task = method(target_)
                    console.print(
                        f"[dim]{task.id}: {task.status} (next {task.next_run_at})[/dim]"
                        if task
                        else f"[red]no task {target_!r}[/red]"
                    )
            else:
                console.print(
                    "[dim]/schedule add TEXT | /schedule cancel TERM | "
                    "/schedule pause|resume|run ID[/dim]"
                )
            continue

        if _memory_slash(alan, user):
            continue

        try:
            reply = _draw_reply(alan.stream(user))
        except ProviderError as exc:
            console.print(f"\n[red]{exc}[/red]\n")
        except Exception as exc:  # one bad tool/model call must not end the chat
            console.print(f"\n[red]error: {type(exc).__name__}: {exc}[/red]\n")
        else:
            # Announced rather than silent: in a hands-free turn the room going
            # quiet is ambiguous between "still thinking" and "it failed", and
            # the note says which, with the time it took.
            if voice_on:
                _speak(reply, announce=True)

    reader.save()
    alan.stop_scheduler()
    alan.stop_telegram()
    feed.close()
    pending = alan.pending_memories()
    if pending:
        console.print(f"[dim]storing {pending} memory item(s); this can take a moment...[/dim]")
    alan.flush_memory()


# --------------------------------------------------------------------------
# Orchestrator commands (the full request pipeline)
# --------------------------------------------------------------------------
def _runtime():
    from .runtime import build_runtime

    return build_runtime(attach_trace=True)


@app.command()
def plan(text: List[str] = typer.Argument(..., help="The request to plan.")) -> None:
    """Show the plan Alan would follow, without running anything."""
    from .core.planner import HeuristicPlanner
    from .ecc import EccCatalog, find_ecc_root

    # Built the same way runtime builds it: a bare HeuristicPlanner has no
    # skill catalog, so `alan plan "use the security-review skill"` would show
    # a plan that the real run does not follow.
    planner = HeuristicPlanner()
    settings = load_settings()
    if settings.ecc.enabled:
        root = find_ecc_root(settings.ecc.root)
        if root is not None:
            planner = HeuristicPlanner(skills=EccCatalog.load(root))
    plan_ = planner.plan(" ".join(text))
    table = Table(title=f"plan: {plan_.goal}", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("action")
    table.add_column("risk")
    table.add_column("description")
    for index, step in enumerate(plan_.steps, start=1):
        table.add_row(str(index), step.action, str(step.risk), step.description)
    console.print(table)
    console.print(
        f"[dim]intent: {plan_.intent}   complexity: {plan_.complexity}/5   "
        f"model: {plan_.category}   needs memory: {plan_.needs_memory}[/dim]"
    )
    console.print(f"[dim]{plan_.rationale}[/dim]")


@app.command()
def lessons(
    clear: bool = typer.Option(False, "--clear", help="Forget every recorded lesson."),
) -> None:
    """Show what earlier failures taught Alan, and what has since worked."""
    from .core.lessons import LessonStore

    store = LessonStore()
    if clear:
        store.clear()
        console.print("[dim]forgotten every recorded lesson.[/dim]")
        return

    rows = store.lessons()
    if not rows:
        console.print(
            "[dim]nothing recorded - Alan has not failed at anything it remembers.[/dim]"
        )
        return

    table = Table(title=f"lessons ({len(rows)} recorded)")
    for column in ("task", "state", "attempts", "last reason", "what worked"):
        table.add_column(column)
    for lesson in rows:
        table.add_row(
            lesson.task[:60],
            "[red]open[/red]" if lesson.open else "[green]solved[/green]",
            f"{lesson.failures} fail / {lesson.successes} ok",
            lesson.last_cause[:70] or "-",
            lesson.winner[:40] or "-",
        )
    console.print(table)
    open_rows = sum(1 for lesson in rows if lesson.open)
    console.print(
        f"[dim]{open_rows} still open and shown to the next matching request; "
        "the rest are history.[/dim]"
    )


@app.command()
def tools() -> None:
    """List every registered tool with its risk level and permissions."""
    runtime = _runtime()
    try:
        table = Table(title="registered tools")
        for column in ("tool", "risk", "permissions", "timeout", "description"):
            table.add_column(column)
        for spec in sorted(runtime.tools(), key=lambda s: s.name):
            table.add_row(
                spec.name,
                str(spec.risk),
                ", ".join(spec.permissions) or "-",
                f"{spec.timeout:g}s",
                spec.description,
            )
        console.print(table)
        console.print(f"[dim]risk ceiling: {runtime.gate.ceiling}[/dim]")
    finally:
        runtime.close()


@app.command()
def agents() -> None:
    """List the subagents and the tools each one may use."""
    table = Table(title="subagents")
    table.add_column("agent")
    table.add_column("risk ceiling")
    table.add_column("model")
    table.add_column("tools")
    table.add_column("description")
    for name in runtime_agent_names():
        agent = runtime_agent(name)
        if agent is None:
            continue
        table.add_row(
            agent.name,
            str(agent.ceiling),
            "yes" if agent.needs_model else "no",
            ", ".join(sorted(agent.tools)) or "none",
            agent.description,
        )
    console.print(table)


def runtime_agent_names() -> list:
    from .agents.standard import standard_agents

    return [agent.name for agent in standard_agents()]


def runtime_agent(name: str):
    from .agents.standard import standard_agents

    return next((a for a in standard_agents() if a.name == name), None)


@app.command()
def route(
    request: Optional[str] = typer.Argument(None, help="Describe the task to route."),
    local_only: bool = typer.Option(False, "--local-only", help="Never use a cloud model."),
) -> None:
    """Explain which model Alan would choose for a task, and why."""
    from .interfaces.model import ModelCategory
    from .models.router import RoutingRequest

    runtime = _runtime()
    try:
        category = ModelCategory.REASONING
        req = RoutingRequest(category=category, local_only=local_only, complexity=3)
        chosen = runtime.router.route(req)
        console.print(f"[bold]task:[/bold] {(request or 'general reasoning')}")
        console.print(f"[bold]chosen:[/bold] [green]{chosen.describe()}[/green]")
        console.print(f"[bold]local:[/bold] {chosen.local}")
        console.print("\n[bold]candidates, best first:[/bold]")
        console.print(runtime.router.explain(req))
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
    finally:
        runtime.close()


@app.command()
def trace(
    trace_id: Optional[str] = typer.Argument(None, help="Trace id; omit for the latest."),
    limit: int = typer.Option(40, help="Maximum events to show."),
) -> None:
    """Show the structured events for a request."""
    path = LOG_DIR / "events.jsonl"
    if not path.is_file():
        console.print("[yellow]no traces recorded yet[/yellow]")
        raise typer.Exit(code=1)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if trace_id:
        lines = [row for row in lines if row.get("trace_id") == trace_id]
        if not lines:
            console.print(f"[red]no events for trace {trace_id}[/red]")
            raise typer.Exit(code=1)
    else:
        latest = next((row["trace_id"] for row in reversed(lines) if row.get("trace_id")), None)
        lines = [row for row in lines if row.get("trace_id") == latest][-limit:]
        console.print(f"[dim]latest trace: {latest}[/dim]")

    table = Table(show_header=True)
    for column in ("event", "agent", "ms", "detail"):
        table.add_column(column)
    for row in lines[-limit:]:
        detail = {k: v for k, v in row.items()
                  if k not in ("name", "trace_id", "agent", "duration_ms", "timestamp")}
        table.add_row(
            row.get("name", ""),
            row.get("agent") or "-",
            f"{row['duration_ms']:.0f}" if row.get("duration_ms") else "-",
            ", ".join(f"{k}={str(v)[:40]}" for k, v in detail.items())[:90],
        )
    console.print(table)


def _looks_down(note: str) -> bool:
    """True when a status note says the thing is not working right now.

    The status dict is mostly prose, so the icon needs to read it. These are
    Alan's own words (the notes are written here, in this codebase), not a
    user's -- and the worst a miss can do is draw a wrong glyph in a
    diagnostic, which the note next to it still explains in full.
    """
    lowered = (note or "").lower()
    return any(
        token in lowered
        for token in ("unreachable", "not reachable", "not running", "failed")
    )


def _memory_state(memory: dict) -> str:
    """The memory row's mark: ready, degraded, switched off, or broken."""
    note = (memory.get("note") or "").lower()
    if "disabled" in note or not memory.get("backend"):
        return "off" if "disabled" in note else "err"
    if _looks_down(note):
        return "err"
    if memory["backend"] == "json":
        # Working, but the dumb local fallback: everything else was asked for
        # and nothing answered.
        return "warn"
    return "ok"


def _doctor_rows(status: dict) -> list:
    """(part, state, note) per row: Alan's status dict, mapped to what a human
    sees. Split out so the mapping is one readable function, not markup
    tangled through a print."""
    memory = status["memory"]
    browser = status["browser"]
    internet = status["internet"]
    voice = status["voice"]
    speech = status["speech"]
    knowledge = status["knowledge"]
    telegram = status["telegram"]
    models_ready = any(model["available"] for model in status["models"])
    scheduler = status["scheduler"]
    if scheduler["enabled"]:
        # A running loop is green. Enabled-but-stopped only earns a triangle
        # when there are tasks it is actually missing; with nothing scheduled,
        # "not running" is a fact, not a problem.
        sched_state = (
            "ok" if scheduler["running"]
            else "warn" if scheduler.get("tasks", 0)
            else "info"
        )
        sched_note = (
            f"{scheduler.get('tasks', 0)} task(s), "
            f"{scheduler.get('waiting', 0)} waiting, "
            f"{scheduler.get('due', 0)} due"
        )
        if scheduler.get("note"):
            sched_note = f"{sched_note} - {scheduler['note']}"
    else:
        sched_state = "off"
        sched_note = scheduler.get("note") or "disabled in settings"
    pending = status["pending_approvals"]
    return [
        ("models", "ok" if models_ready else "err", status["model_note"]),
        (
            "memory",
            _memory_state(memory),
            f"{memory['backend'] or 'none'} - {memory['note']}",
        ),
        (
            "brain",
            # Enabled is not the same as working: an unreachable embedder is
            # a red mark even though the feature is switched on.
            (
                "err"
                if _looks_down(str(knowledge["note"]))
                else "ok" if knowledge["enabled"] else "off"
            ),
            str(knowledge["note"]),
        ),
        (
            "browser",
            "err"
            if browser["provider"] is None or _looks_down(browser["note"])
            else "ok",
            f"{browser['provider'] or 'none'} - {browser['note']}",
        ),
        (
            "internet",
            "err"
            if internet["provider"] is None or _looks_down(internet["note"])
            else "ok",
            f"{internet['provider'] or 'none'} - {internet['note']}",
        ),
        (
            "vibecoder",
            "err" if status["vibecoder"] == "not registered" else "ok",
            status["vibecoder"],
        ),
        ("ecc", "err" if status["ecc"] == "not registered" else "ok", status["ecc"]),
        (
            "voice",
            {"ready": "ok", "configured": "warn", "off": "off"}.get(
                voice["state"], "err"
            ),
            voice["note"],
        ),
        (
            "speech",
            {"ready": "ok", "configured": "warn", "off": "off"}.get(
                speech["state"], "err"
            ),
            speech["note"],
        ),
        (
            "telegram",
            (
                "off"
                if not telegram["enabled"]
                else "ok"
                if telegram["running"]
                else "warn"
            ),
            (
                f"chat {telegram['chat_id']} - {telegram['note']}"
                if telegram["enabled"]
                else telegram["note"]
            ),
        ),
        ("scheduler", sched_state, sched_note),
        (
            "approvals",
            "warn" if pending else "info",
            f"{pending} waiting on you" if pending else "nothing waiting",
        ),
        ("tools", "info", f"{len(status['tools'])} registered"),
        ("agents", "info", ", ".join(status["agents"])),
        ("ceiling", "info", status["risk_ceiling"]),
        ("traces", "info", status["trace_file"] or "in memory only"),
    ]


@app.command()
def doctor() -> None:
    """Report on every part of Alan: models, memory, tools, agents, limits."""
    # Probes are cached elsewhere so ordinary commands start fast; a
    # diagnostic that reported a remembered "ready" would be worse than no
    # diagnostic at all. Clear the cache first, then let everything re-probe.
    from .core.probecache import forget

    forget()
    runtime = _runtime()
    try:
        status = runtime.status()
        rows = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
        rows.add_column("state", justify="center", no_wrap=True)
        rows.add_column("part", style="bold", no_wrap=True)
        rows.add_column("note")
        for part, state, note in _doctor_rows(status):
            rows.add_row(theme.mark(state), part, note)
        console.print(Panel(
            rows,
            title="[bold]Alan[/bold] [dim]doctor[/dim]",
            border_style="cyan",
        ))
        table = Table(title="models")
        for column in ("provider", "model", "local", "available"):
            table.add_column(column)
        for model in status["models"]:
            table.add_row(
                model["provider"], model["model"], str(model["local"]),
                "[green]yes[/green]" if model["available"] else "[red]no[/red]",
            )
        console.print(table)
        if not status["models"]:
            console.print(
                "[yellow]No model backend is available.[/yellow] "
                "Start Ollama with scripts\\run.ps1, or add a key to config\\.env"
            )
    finally:
        runtime.close()


@app.command()
def backends() -> None:
    """Show which memory backends are installed and reachable."""
    from .memory.providers import describe_backends

    table = Table(title="memory backends")
    for column in ("backend", "status", "role", "detail"):
        table.add_column(column)
    for row in describe_backends():
        table.add_row(
            row["backend"],
            "[green]ready[/green]" if row["status"] == "ready" else "[yellow]unavailable[/yellow]",
            row["note"],
            row["detail"][:70],
        )
    console.print(table)


@app.command()
def skills(
    query: List[str] = typer.Argument(None),
    kind: str = typer.Option(
        "", "--kind", help="Restrict to one family: skill, command or agent."
    ),
    read: str = typer.Option("", "--read", help="Print one entry's full content."),
    limit: int = typer.Option(20, "--limit", min=1, max=300),
) -> None:
    """Browse the ECC library: list entries, search them, or read one.

    Read-only and independent of the model stack -- this command builds the
    catalog directly, so it works with no provider configured.
    """
    from .ecc import EccCatalog, find_ecc_root

    settings = load_settings()
    if not settings.ecc.enabled:
        console.print(
            "[yellow]ECC is off.[/yellow] "
            "Set [ecc] enabled = true in config\\settings.toml."
        )
        raise typer.Exit(1)
    root = find_ecc_root(settings.ecc.root)
    if root is None:
        console.print(
            "[red]ECC not found.[/red] Clone "
            "https://github.com/affaan-m/ECC to vendor\\ECC, or set [ecc] root."
        )
        raise typer.Exit(1)
    catalog = EccCatalog.load(root)

    if read:
        entry = catalog.find(read)
        if entry is None:
            console.print(f"[red]no entry named[/red] {read!r}. Try: alan skills {read}")
            raise typer.Exit(1)
        console.print(f"[bold]{entry.ref}[/bold] - {entry.description}")
        console.print(entry.content)
        return

    if query:
        terms = " ".join(query)
        hits = catalog.search(terms, kind=kind or None, limit=limit)
        if not hits:
            console.print(f"no ECC entries match {terms!r}.")
            raise typer.Exit(1)
        table = Table(title=f"ecc search: {terms}")
        for column in ("ref", "description"):
            table.add_column(column)
        for entry in hits:
            table.add_row(entry.ref, entry.description[:110])
        console.print(table)
        return

    counts = catalog.counts()
    console.print(
        f"ecc: {counts['skill']} skills, {counts['command']} commands, "
        f"{counts['agent']} agents from {root}"
    )
    listed = 0
    for family in ("skill", "command", "agent"):
        if kind and family != kind:
            continue
        names = [entry.name for entry in catalog.all(family)]
        if not names:
            console.print(f"{family}s: (none)")
            continue
        shown = names[:limit]
        listed += len(shown)
        line = ", ".join(shown)
        if len(names) > len(shown):
            line += f", ... and {len(names) - len(shown)} more"
        console.print(f"{family}s ({len(names)}): {line}")
    if not listed:
        console.print(f"[red]no family named[/red] {kind!r} (skill, command, agent).")
        raise typer.Exit(1)


@app.command()
def run(
    text: List[str] = typer.Argument(..., help="The request for the full pipeline."),
    agent: Optional[str] = typer.Option(None, "--agent", help="Run one named agent."),
    trace_it: bool = typer.Option(True, "--trace/--no-trace", help="Record events."),
) -> None:
    """Run a request through the full orchestrator: plan, memory, tools, agents."""
    runtime = _runtime()
    try:
        result = runtime.orchestrator.run(" ".join(text), agent=agent)
        if not result.success:
            console.print(f"[red]{result.error}[/red]")
            raise typer.Exit(code=1)

        console.print(Markdown(result.output))
        artifacts = result.artifacts
        detail = Table(show_header=False, box=None)
        detail.add_row("intent", str(artifacts.get("plan", {}).get("intent", "-")))
        detail.add_row("model", str(artifacts.get("model", result.agent)))
        detail.add_row("steps", ", ".join(result.steps) or "-")
        detail.add_row("recalled", f"{artifacts.get('recalled', 0)} memory item(s)")
        if result.memory_writes:
            detail.add_row("stored", f"{len(result.memory_writes)} memory item(s)")
        if artifacts.get("observations"):
            detail.add_row("observed", "; ".join(artifacts["observations"])[:120])
        if trace_it and artifacts.get("trace_id"):
            detail.add_row("trace", f"alan trace {artifacts['trace_id']}")
        console.print(Panel(detail, title="run", border_style="dim"))
    finally:
        runtime.close()


def _knowledge_or_exit(runtime):
    """The store, or a clean exit explaining why there is none."""
    if runtime.knowledge is None:
        console.print(
            "[yellow]the second brain is off "
            "([knowledge] enabled = false in config/settings.toml)[/yellow]"
        )
        raise typer.Exit(code=1)
    return runtime.knowledge


def _brain_answer(runtime, question: str) -> None:
    """Retrieve cited passages, then let a model synthesise over them."""
    from .interfaces.model import ModelCategory
    from .knowledge import search
    from .models.router import RoutingRequest

    hits = search(
        runtime.knowledge,
        question,
        embedder=runtime.knowledge_embedder,
        top_k=runtime.settings.knowledge.top_k,
    )
    if not hits:
        console.print("[dim]nothing in the second brain matches that.[/dim]")
        console.print("[dim]index something first: alan brain ingest <path>[/dim]")
        return

    context = "\n\n".join(
        f"[{index}] {hit.citation()}\n{hit.text.strip()}"
        for index, hit in enumerate(hits, start=1)
    )
    system = (
        "You answer questions using ONLY the numbered sources provided. "
        "Cite sources inline as [n]. If the sources do not answer the "
        "question, say so plainly instead of guessing."
    )
    messages = [{"role": "user", "content": f"Question: {question}\n\nSources:\n{context}"}]
    # Retrieval is always local; this flag only governs whether a cloud model
    # may read the passages. A local-only permission setting wins outright.
    local_only = (
        runtime.settings.permissions.local_only
        or not runtime.settings.knowledge.allow_cloud_synthesis
    )
    request = RoutingRequest(
        category=ModelCategory.REASONING, complexity=3, local_only=local_only
    )
    reply, provider = runtime.router.complete(messages, system=system, request=request)
    console.print(Markdown(reply))
    console.print(f"\n[dim]answered by {provider.describe()}[/dim]")
    console.print("[bold]Sources[/bold]")
    for index, hit in enumerate(hits, start=1):
        console.print(f"[dim][{index}] {hit.citation()}[/dim]")


@app.command()
def brain(
    action: str = typer.Argument(
        "stats", help="stats, list, search, ask, ingest, reindex, forget."
    ),
    query: List[str] = typer.Argument(None, help="Query (search/ask), path(s) (ingest/forget)."),
) -> None:
    """Inspect, query or grow the second brain."""
    from .knowledge import ingest_paths, search

    runtime = _runtime()
    try:
        store = _knowledge_or_exit(runtime)
        term = " ".join(query or []).strip()

        if action == "stats":
            stats = store.stats()
            console.print(
                f"[bold]{stats['documents']}[/bold] document(s), "
                f"[bold]{stats['chunks']}[/bold] chunk(s), "
                f"[bold]{stats['embedded']}[/bold] embedded"
            )
            console.print(f"[dim]store: {stats['path']}[/dim]")
            console.print(f"[dim]{runtime.knowledge_note}[/dim]")
        elif action == "list":
            rows = store.list_documents()
            if not rows:
                console.print("[dim]nothing indexed yet - run `alan brain ingest <path>`[/dim]")
            table = Table(title=f"{len(rows)} document(s)")
            for column in ("source", "kind", "chunks", "title"):
                table.add_column(column)
            for row in rows:
                table.add_row(row["source"], row["kind"], str(row["chunks"]), row["title"])
            console.print(table)
        elif action == "search":
            if not term:
                console.print('[yellow]usage: alan brain search "QUERY"[/yellow]')
                raise typer.Exit(code=1)
            hits = search(
                store, term,
                embedder=runtime.knowledge_embedder,
                top_k=runtime.settings.knowledge.top_k,
            )
            if not hits:
                console.print("[dim]no matching passages[/dim]")
            for hit in hits:
                console.print(f"\n[bold]{hit.citation()}[/bold] [dim]({hit.score:.4f})[/dim]")
                console.print(hit.text.strip()[:600])
        elif action == "ask":
            if not term:
                console.print('[yellow]usage: alan brain ask "QUESTION"[/yellow]')
                raise typer.Exit(code=1)
            _brain_answer(runtime, term)
        elif action in ("ingest", "reindex"):
            if not query:
                console.print("[yellow]usage: alan brain ingest <path> [<path>...][/yellow]")
                raise typer.Exit(code=1)
            kb = runtime.settings.knowledge
            report = ingest_paths(
                store,
                list(query),
                embedder=runtime.knowledge_embedder,
                chunk_size=kb.chunk_size,
                overlap=kb.overlap,
                max_bytes=int(kb.max_file_mb * 1_000_000),
                reindex=(action == "reindex"),
            )
            console.print(f"[green]{report.describe()}[/green]")
            for error in report.errors[:10]:
                console.print(f"[dim]  - {error}[/dim]")
        elif action == "forget":
            if not term:
                console.print("[yellow]usage: alan brain forget <source-path>[/yellow]")
                raise typer.Exit(code=1)
            removed = store.forget(source=term)
            console.print(f"[green]removed {removed} document(s)[/green]")
        else:
            console.print(
                "[yellow]usage: alan brain [stats|list|search|ask|ingest|reindex|forget][/yellow]"
            )
            raise typer.Exit(code=1)
    finally:
        runtime.close()


def _stop_daemon(settings) -> None:
    """Write the kill switch for a configured daemon. Opens no database.

    Stopping is the one action that must work when everything else is broken,
    so it touches exactly one file -- the flag the loop already checks -- and
    nothing that could itself fail to open.
    """
    from .core.daemon import resolve_daemon_paths, stop_flag_for

    lock_path, _ = resolve_daemon_paths(settings)
    flag = stop_flag_for(lock_path)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")
    console.print(f"[yellow]stop requested[/yellow] via {flag.name}")
    console.print("[dim]the daemon exits on its next tick, releasing its lock.[/dim]")


def _daemon_status(settings) -> None:
    """Report whether a daemon runs, without building one.

    ``status`` is read-only, so it does not open the timeline (opening it
    *creates* it, which is not what asking a question should do) and it does
    not ask a freshly constructed ``Daemon`` whether it is running -- that
    object has no loop and would answer "stopped" while a daemon ran in the
    next process.

    The lock is the only honest signal, and it is probed the same way the
    daemon itself would: take it and give it straight back. If we get it,
    nobody holds it; if we do not, someone does, and the pid file says who.
    The probe writes our pid only when it succeeds, and releasing then unlinks
    it, so a real holder's pid is never overwritten.
    """
    from .core.daemon import resolve_daemon_paths

    lock_path, store_path = resolve_daemon_paths(settings)
    from .core.daemon import SingleInstance

    lock = SingleInstance(lock_path)
    held_elsewhere = not lock.acquire()
    holder = lock.holder_pid() if held_elsewhere else 0
    if not held_elsewhere:
        lock.release()

    console.print(
        f"[bold]{'running' if held_elsewhere else 'stopped'}[/bold] "
        f"(tick every {settings.daemon.tick_seconds:.0f}s)"
    )
    if held_elsewhere:
        console.print(f"[dim]held by pid {holder or 'unknown'}[/dim]")
    console.print(f"[dim]lock: {lock_path}[/dim]")
    if store_path.exists():
        from .core.timeline import TimelineStore

        store = TimelineStore(store_path)
        try:
            console.print(f"[dim]timeline: {store.count()} event(s)[/dim]")
        finally:
            store.close()
    else:
        console.print("[dim]timeline: none yet[/dim]")
    console.print(
        "[dim]sensing: not implemented in this phase -- heartbeats only[/dim]"
    )
    if not settings.daemon.enabled:
        console.print("[dim]daemon.enabled = false[/dim]")


@app.command()
def daemon(
    action: str = typer.Argument(
        "status", help="start, stop, status, tick, digest."
    ),
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Run in this terminal until stopped."
    ),
) -> None:
    """Run or inspect the always-on daemon (Phase 2).

    **Off until switched on, and it captures nothing.** This slice owns the
    loop, the single-instance lock and the event timeline. Activity sensing and
    screen understanding are later phases behind their own switches, so
    turning the daemon on does not turn capture on.
    """
    from .core.daemon import build_daemon

    settings = load_settings()

    if action == "stop":
        _stop_daemon(settings)
        return

    if action == "status":
        _daemon_status(settings)
        return

    if not settings.daemon.enabled:
        console.print("[yellow]the daemon is off.[/yellow] Nothing is running.")
        console.print(
            "Set [bold]daemon.enabled = true[/bold] in [bold]"
            f"{SETTINGS_FILE}[/bold] to run it."
        )
        raise typer.Exit(code=1)

    daemon = build_daemon(settings)
    try:
        if action == "tick":
            notes = daemon.tick()
            for note in notes:
                console.print(f"[green]{note}[/green]")
            console.print(
                f"[dim]{daemon.timeline.count()} event(s) in the timeline[/dim]"
            )

        elif action == "start":
            if not daemon.lock.acquire():
                holder = daemon.lock.holder_pid()
                detail = f" (held by pid {holder})" if holder else ""
                console.print(f"[yellow]already running{detail}[/yellow]")
                raise typer.Exit(code=0)
            daemon.lock.release()

            if foreground:
                console.print(
                    "[green]daemon running[/green] in this terminal; "
                    "press Ctrl+C to stop."
                )
                try:
                    daemon.start()
                    while daemon.running:
                        time.sleep(0.25)
                except KeyboardInterrupt:
                    console.print("\n[dim]stopping...[/dim]")
                finally:
                    daemon.stop()
            else:
                _start_daemon_detached()
                console.print("[green]daemon started[/green] in the background.")
                console.print("[dim]alan daemon status  |  alan daemon stop[/dim]")

        elif action == "digest":
            digest = daemon.timeline.digest()
            console.print(digest.render())

        else:
            console.print(
                "[yellow]usage: alan daemon "
                "[start|stop|status|tick|digest][/yellow]"
            )
            raise typer.Exit(code=1)
    finally:
        daemon.timeline.close()


def _start_daemon_detached() -> None:
    """Spawn this command again, in the foreground, detached from the terminal.

    A ``daemon=True`` thread dies with its process, so an in-process "start"
    would return a prompt and a daemon that had already been reaped. The work
    has to happen in a child that outlives this one -- and that child runs the
    *same* command, so there is one code path rather than two that can drift.
    """
    argv = [sys.executable, "-m", "alan", "daemon", "start", "--foreground"]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(HOME),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:  # pragma: no cover - exercised on the platform that has it
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


@app.command()
def timeline(
    action: str = typer.Argument("recent", help="recent, digest, days, purge."),
    count: int = typer.Option(20, "--count", "-n", help="Rows to show."),
    days: float = typer.Option(
        14.0, "--days", help="Retention window for purge."
    ),
) -> None:
    """Read the local event timeline (Phase 2). Append-only.

    Read-only except for an explicit, age-bounded ``purge`` -- the retention
    policy, made callable rather than hidden behind a helper that deletes more
    than it says.
    """
    from .config import DATA_DIR
    from .core.timeline import TimelineStore

    settings = load_settings()
    store_path = (
        Path(settings.daemon.store_path)
        if settings.daemon.store_path
        else DATA_DIR / "timeline" / "timeline.db"
    )
    if not store_path.exists() and action in ("recent", "digest", "days"):
        console.print("[dim]no timeline yet -- the daemon has not run.[/dim]")
        return

    store = TimelineStore(store_path)
    try:
        if action == "recent":
            events = store.recent(limit=count)
            if not events:
                console.print("[dim]the timeline is empty.[/dim]")
                return
            for event in events:
                subject = event.project or event.app or ""
                label = f" [dim]{subject}[/dim]" if subject else ""
                console.print(
                    f"[dim]{event.ts}[/dim] [cyan]{event.kind}[/cyan]{label}"
                )
                if event.title:
                    console.print(f"    {event.title}")
            console.print(f"[dim]{len(events)} event(s)[/dim]")
        elif action == "digest":
            console.print(store.digest().render())
        elif action == "days":
            listed = store.days()
            if not listed:
                console.print("[dim]no days recorded.[/dim]")
            for day in listed:
                console.print(day)
        elif action == "purge":
            removed = store.purge_older_than(days)
            console.print(f"[green]removed {removed} event(s)[/green] older than {days:g}d")
        else:
            console.print(
                "[yellow]usage: alan timeline [recent|digest|days|purge][/yellow]"
            )
            raise typer.Exit(code=1)
    finally:
        store.close()


def main() -> None:
    app()


@app.command()
def activity(
    history: bool = typer.Option(
        False, "--history", help="Include summaries of recent runs."
    ),
) -> None:
    """Show scheduled tasks, approvals awaiting a decision and recent runs."""
    runtime = _runtime()
    try:
        console.print(runtime.activity_text(include_history=history))
    finally:
        runtime.close()


@app.command()
def schedule(
    action: str = typer.Argument(
        ...,
        help="What to do: add, list, show, remove, pause, resume, run, clear-done.",
    ),
    text: List[str] = typer.Argument(
        None, help="task request (add), id or phrase (others)."
    ),
    when: str = typer.Option(
        "",
        "--when",
        help="Timing phrase for add: 'every day at 07:30', 'in 2 hours', etc.",
    ),
) -> None:
    """Manage scheduled background tasks (the scheduler itself runs in chat)."""
    runtime = _runtime()
    try:
        if runtime.scheduler is None:
            console.print("[yellow]scheduler is disabled in config/settings.toml[/yellow]")
            raise typer.Exit(code=1)
        sched = runtime.scheduler
        arguments = " ".join(text or []).strip()

        if action == "list":
            console.print("\n".join(sched.activity_lines()))
        elif action == "add":
            if not arguments:
                console.print("[yellow]usage: alan schedule add \"TASK\" --when \"...\"[/yellow]")
                raise typer.Exit(code=1)
            try:
                spec = parse_when(when)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            task = sched.add(
                arguments,
                kind=spec.kind,
                every_seconds=spec.every_seconds,
                at_time=spec.at_time if spec.kind == "interval" else "",
                first_at=spec.first_at,
                created_by="cli",
            )
            console.print(
                f"[green]scheduled {task.id}[/green] {arguments!r} -- "
                f"{spec.describe()}, next run {task.next_run_at}"
            )
        elif action == "show":
            if not arguments:
                console.print("[yellow]usage: alan schedule show <id>[/yellow]")
                raise typer.Exit(code=1)
            task = sched.store.get(arguments)
            if task is None:
                console.print(f"[red]no task {arguments!r}[/red]")
                raise typer.Exit(code=1)
            console.print(Panel(_task_detail(task), title=task.id, border_style="dim"))
        elif action == "remove":
            if not arguments:
                console.print("[yellow]usage: alan schedule remove <id-or-phrase>[/yellow]")
                raise typer.Exit(code=1)
            result = runtime.registry.invoke("schedule.remove", {"match": arguments})
            console.print(result.output if result.ok else f"[red]{result.error}[/red]")
        elif action in ("pause", "resume", "run"):
            if not arguments:
                console.print(f"[yellow]usage: alan schedule {action} <id>[/yellow]")
                raise typer.Exit(code=1)
            method = {
                "pause": sched.pause,
                "resume": sched.resume,
                "run": sched.run_now,
            }[action]
            task = method(arguments)
            if task is None:
                console.print(f"[red]no task {arguments!r}[/red]")
            else:
                console.print(
                    f"[green]{action} {task.id}[/green] status={task.status} "
                    f"next={task.next_run_at}"
                )
        elif action == "clear-done":
            removed = [
                t.id
                for t in sched.store.list()
                if t.status == "done" and sched.remove(t.id)
            ]
            console.print(f"[dim]removed {len(removed)} finished task(s)[/dim]")
        else:
            console.print(
                "[red]unknown action[/red]. Actions: add, list, show, remove, "
                "pause, resume, run, clear-done. (The scheduler loop itself "
                "runs in `alan chat`.)"
            )
            raise typer.Exit(code=1)
    finally:
        runtime.close()


def _start_always_on(alan: Alan) -> tuple[bool, bool]:
    """Start the scheduler and the phone bridge; report which are now on.

    Kept separate from the blocking loop so tests can assert the start/stop
    contract without owning a terminal.
    """
    return alan.start_scheduler(), alan.start_telegram()


@app.command()
def serve(
    seconds: float = typer.Option(
        0, "--seconds", help="Exit after this many seconds (default: until Ctrl+C)."
    ),
) -> None:
    """Headless always-on: run the scheduler and (if configured) the Telegram
    approval bridge with no chat surface.

    This is the keep-working-while-the-terminal-is-closed mode. It is explicit,
    never implied: background loops still launch only from `alan chat` or this
    command. Approval safety is unchanged -- a task stopped at the permission
    gate waits in the queue until somebody decides from the phone bridge
    (ADR-011), chat, or /activity. Nothing proceeds unasked, and nothing runs
    unless you asked for this command.
    """
    alan = None
    try:
        alan = Alan()
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    scheduler_on, telegram_on = _start_always_on(alan)
    if not scheduler_on and not telegram_on:
        console.print(
            "[yellow]nothing to serve[/yellow] -- enable [scheduler] and/or "
            "add a [telegram] token + chat_id in config/settings.toml"
        )
        alan.close()
        raise typer.Exit(code=1)

    on = ", ".join(
        name
        for name, is_on in (("scheduler", scheduler_on), ("telegram bridge", telegram_on))
        if is_on
    )
    console.print(f"[green]alan serving[/green] ({on}) -- Ctrl+C to stop")
    try:
        if seconds > 0:
            # Sleep in capped chunks so a long `--seconds` still notices
            # Ctrl+C promptly. One uneapped sleep of e.g. 7200s would make
            # Ctrl+C wait up to an hour; capping restores responsiveness.
            remaining = float(seconds)
            while remaining > 0:
                time.sleep(min(remaining, 3600.0))
                remaining -= 3600.0
        else:
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
    finally:
        alan.stop_scheduler()
        alan.stop_telegram()
        alan.close()


def _task_detail(task: Any) -> str:
    """One task, laid out for `alan schedule show`."""
    lines = [
        f"request:    {task.request}",
        f"kind:       {task.kind}"
        + (f" every {task.every_seconds:g}s" if task.every_seconds else "")
        + (f" at {task.at_time}" if task.at_time else ""),
        f"status:     {task.status}",
        f"runs:       {task.run_count}",
        f"next run:   {task.next_run_at}",
        f"created:    {task.created_at} by {task.created_by}",
    ]
    if task.blocked_by:
        lines.append(f"waiting on: approval {task.blocked_by}")
    for run in task.runs:
        mark = "ok" if run.get("ok") else "FAILED"
        lines.append(
            f"[{mark}] {run.get('at', '')}  {str(run.get('summary', ''))[:90]}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
