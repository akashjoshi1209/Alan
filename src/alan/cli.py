"""Alan command-line interface."""

from __future__ import annotations

import json
import time
from typing import Any, List, Optional

import typer
from rich.console import Console

from . import __version__
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
from .memory import CogneeMemory

app = typer.Typer(add_completion=False, help="Alan - a portable, self-contained AI assistant.")
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
#: Module and attribute name for each deferred Rich renderer.
_LAZY_RICH = {
    "Markdown": ("rich.markdown", "Markdown"),
    "Panel": ("rich.panel", "Panel"),
    "Table": ("rich.table", "Table"),
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


def _print_banner() -> None:
    console.print(
        Panel.fit(
            f"[bold cyan]Alan[/bold cyan] [dim]v{__version__}[/dim]\n"
            f"[dim]home: {HOME}[/dim]",
            border_style="cyan",
        )
    )


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
    """Show persistent-memory status (cognee sidecar)."""
    settings = load_settings()
    mem = CogneeMemory(settings.memory)
    health = mem.health()
    if health is None:
        console.print("[red]memory sidecar: unreachable[/red]")
        console.print(
            f"[dim]{settings.memory.base_url} — start it with scripts\\cognee-serve.ps1[/dim]"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[green]memory sidecar: ready[/green] [dim](cognee {health.get('version', '?')})[/dim]"
    )
    console.print(
        f"[dim]dataset: {settings.memory.dataset}   base_url: {settings.memory.base_url}[/dim]"
    )
    datasets = mem.datasets()
    if datasets:
        table = Table(title="memory datasets")
        table.add_column("name")
        table.add_column("id")
        for item in datasets:
            table.add_row(str(item.get("name", "")), str(item.get("id", "")))
        console.print(table)
    if query:
        hits = mem.recall(query)
        if not hits:
            console.print("[yellow]no memories matched[/yellow]")
        for hit in hits:
            console.print(Panel(hit, border_style="dim"))


@app.command()
def ingest(
    text: List[str] = typer.Argument(..., help="Text to store in Alan's memory."),
) -> None:
    """Store a note in Alan's persistent memory (waits for indexing)."""
    mem = CogneeMemory(load_settings().memory)
    if not mem.available():
        console.print("[red]memory sidecar unreachable — start scripts\\cognee-serve.ps1[/red]")
        raise typer.Exit(code=1)
    ok = mem.remember(" ".join(text), background=False)
    if ok:
        console.print("[green]stored in memory[/green]")
    else:
        console.print("[red]failed to store memory[/red]")
        raise typer.Exit(code=1)


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
    from .memory.providers import build_memory_provider

    settings = load_settings()
    provider, note = build_memory_provider(settings.memory, model=settings.ollama.model)
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


def _everything(provider) -> list:
    """Every stored item, whatever the query.

    `search` cannot be used for this: it is relevance-ranked and gated, so it
    would omit exactly the items a cleanup needs to see. Backends keep their
    own index, so this reads the ones Alan ships and returns nothing for a
    backend that does not expose one -- better to report "cannot list" than to
    silently delete the wrong thing.
    """
    from .interfaces.types import MemoryItem, MemoryLevel

    records: list = []

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


def _one_shot(text: str, use_stream: bool) -> None:
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
            for chunk in alan.stream(text, remember=False):
                console.print(chunk, end="", soft_wrap=True, markup=False)
            console.print()
        except ProviderError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
            raise typer.Exit(code=1)
    else:
        try:
            console.print(Markdown(alan.ask(text, remember=False)))
        except ProviderError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
            raise typer.Exit(code=1)


@app.command()
def ask(
    text: List[str] = typer.Argument(..., help="The question or prompt for Alan."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream the reply."),
) -> None:
    """Ask Alan a single question and exit."""
    _one_shot(" ".join(text), stream)


@app.command()
def chat(
    system: Optional[str] = typer.Option(
        None, "--system", "-s", help="Override the system prompt for this session."
    ),
) -> None:
    """Start an interactive conversation with Alan."""
    try:
        alan = Alan(confirmer=_confirm_on_console)
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if system:
        alan.system_prompt = system

    _print_banner()
    console.print(f"[dim]provider: {alan.provider.describe()}[/dim]")
    console.print("[dim]Type /help for commands, /exit to quit.[/dim]\n")

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

    while True:
        for line in feed.drain():
            console.print(f"[dim]{line}[/dim]")
        try:
            user = console.input("[bold green]you>[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not user:
            continue
        if user in ("/exit", "/quit", ":q"):
            break
        if user == "/help":
            console.print(
                "[bold]conversation[/bold]\n"
                "/reset or /new  clear conversation    /save  write session to data/sessions\n"
                "/sessions  list saved sessions        /load NAME  load a saved session\n"
                "/provider  show backend               /exit  quit\n"
                "[bold]memory[/bold]\n"
                "/remember TEXT  store a memory        /recall QUERY  search memory\n"
                "/memory  memory status                /forget  erase this dataset's memory\n"
                "[bold]always-on[/bold]\n"
                "/activity  tasks, approvals, recent runs      /approve ID | /reject ID\n"
                "/telegram  phone bridge state                          /schedule  list tasks\n"
                "/schedule add TEXT      schedule a task (include the when)\n"
                "/schedule cancel TERM   cancel task(s) by id or phrase\n"
                "/schedule start|stop    start/stop the background loop\n"
                "/schedule pause|resume|run ID   control one task\n"
                "e.g. /schedule add check the news every morning"
            )
            continue
        if user in ("/reset", "/new"):
            alan.reset()
            console.print("[dim]conversation cleared[/dim]")
            continue
        if user == "/provider":
            console.print(f"[dim]{alan.provider.describe()}[/dim]")
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
                        console.print("[bold cyan]alan>[/bold cyan] ", end="")
                        console.print(alan.ask(request))
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

        if user.split(" ", 1)[0] in ("/remember", "/recall", "/memory", "/forget"):
            if alan.memory is None:
                console.print("[yellow]memory is disabled in config/settings.toml[/yellow]")
                continue
            if user.startswith("/remember"):
                text = user[len("/remember") :].strip()
                if not text:
                    console.print("[yellow]usage: /remember TEXT[/yellow]")
                else:
                    with console.status("[dim]indexing memory...[/dim]"):
                        ok = alan.memory.remember(text, background=False)
                    console.print(
                        "[green]stored[/green]" if ok else "[red]could not store memory[/red]"
                    )
            elif user.startswith("/recall"):
                hits = alan.memory.recall(user[len("/recall") :].strip())
                if not hits:
                    console.print("[yellow]no memories matched[/yellow]")
                for hit in hits:
                    console.print(Panel(hit, border_style="dim"))
            elif user == "/memory":
                health = alan.memory.health()
                if health is None:
                    console.print("[red]memory sidecar unreachable[/red]")
                else:
                    names = ", ".join(
                        str(d.get("name")) for d in alan.memory.datasets()
                    ) or "none"
                    console.print(
                        f"[green]memory ready[/green] "
                        f"[dim](dataset: {alan.memory.dataset}; datasets: {names})[/dim]"
                    )
            elif user == "/forget":
                ok = alan.memory.forget(memory_only=True)
                console.print("[dim]memory cleared[/dim]" if ok else "[red]forget failed[/red]")
            continue

        console.print("[bold cyan]alan>[/bold cyan] ", end="")
        try:
            for chunk in alan.stream(user):
                console.print(chunk, end="", soft_wrap=True, markup=False)
            console.print("\n")
        except ProviderError as exc:
            console.print(f"\n[red]{exc}[/red]\n")
        except Exception as exc:  # one bad tool/model call must not end the chat
            console.print(f"\n[red]error: {type(exc).__name__}: {exc}[/red]\n")

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

    plan_ = HeuristicPlanner().plan(" ".join(text))
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
        console.print(Panel.fit(
            f"[bold cyan]Alan[/bold cyan] [dim]doctor[/dim]\n"
            f"models:   {status['model_note']}\n"
            f"memory:   {status['memory']['backend'] or 'none'} - {status['memory']['note']}\n"
            f"browser:  {status['browser']['provider'] or 'none'} - {status['browser']['note']}\n"
            f"internet: {status['internet']['provider'] or 'none'} - {status['internet']['note']}\n"
            f"vibecoder: {status['vibecoder']}\n"
            f"ecc:      {status['ecc']}\n"
            f"telegram: {status['telegram']['chat_id'] or 'none'} - {status['telegram']['note']}\n"
            f"tools:    {len(status['tools'])} registered\n"
            f"agents:   {', '.join(status['agents'])}\n"
            f"ceiling:  {status['risk_ceiling']}\n"
            f"traces:   {status['trace_file'] or 'in memory only'}",
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
