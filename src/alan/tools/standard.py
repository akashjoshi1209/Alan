"""Alan's built-in tools.

One :class:`~alan.interfaces.tools.ToolProvider` returns every tool Alan ships,
each as its own :class:`~alan.interfaces.types.ToolSpec` with an explicit risk
level. Registering them individually is what makes permissions, timeouts and
audit records per-operation rather than per-library.

Namespace map:

    filesystem.read / list / write / delete
    shell.execute / python
    system.info
    memory.search / remember
    web.search / open        (internet backends when available; browser otherwise)

``shell.*`` and ``filesystem.delete`` only exist when an
:class:`~alan.interfaces.capabilities.ExecutionProvider` was supplied, which
itself is gated by ``[tools] execution = false`` in configuration. Alan ships
with local execution switched off.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Sequence

from ..config import HOME, Settings
from ..interfaces.capabilities import BrowserProvider, ExecutionProvider
from ..interfaces.memory import MemoryProvider
from ..interfaces.tools import ToolProvider
from ..interfaces.types import MemoryItem, MemoryLevel, MemoryType, RiskLevel, ToolSpec
from .internet import Internet, format_results


class StandardTools(ToolProvider):
    """Filesystem, shell, system, memory and web tools."""

    name = "standard"

    def __init__(
        self,
        settings: Settings,
        *,
        execution: ExecutionProvider | None = None,
        memory: MemoryProvider | None = None,
        browser: BrowserProvider | None = None,
        internet: Internet | None = None,
    ) -> None:
        self.settings = settings
        self.execution = execution
        self.memory = memory
        self.browser = browser
        self.internet = internet

    # -- assembly ------------------------------------------------------
    def tools(self) -> Sequence[ToolSpec]:
        specs: list[ToolSpec] = [
            self._filesystem_read(),
            self._filesystem_list(),
            self._filesystem_write(),
            self._system_info(),
            self._system_open(),
            self._memory_search(),
            self._memory_remember(),
            self._web_search(),
            self._web_open(),
        ]
        if self.execution is not None:
            specs.append(self._filesystem_delete())
            specs.append(self._shell_execute())
            specs.append(self._shell_python())
        return tuple(specs)

    def available(self) -> tuple[bool, str]:
        parts = [f"memory={'yes' if self.memory else 'no'}"]
        parts.append(f"execution={'yes' if self.execution else 'disabled'}")
        parts.append(f"browser={'yes' if self.browser else 'disabled'}")
        parts.append(f"internet={'yes' if self.internet else 'disabled'}")
        return True, ", ".join(parts)

    # -- filesystem ----------------------------------------------------
    def _filesystem_read(self) -> ToolSpec:
        return ToolSpec(
            name="filesystem.read",
            description="Read a UTF-8 text file from this machine.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file to read"},
                    "max_bytes": {"type": "integer", "default": 1_000_000},
                },
                "required": ["path"],
            },
            output_schema={"type": "string"},
            risk=RiskLevel.SAFE,
            permissions=("filesystem.read",),
            timeout=30.0,
            executor=self._read,
            tags=("filesystem", "read"),
        )

    def _read(self, path: str, max_bytes: int = 1_000_000) -> str:
        return _exec(self.execution, "read_file", path, max_bytes=max_bytes)

    def _filesystem_list(self) -> ToolSpec:
        return ToolSpec(
            name="filesystem.list",
            description="List the entries of a directory.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string", "default": "*"},
                },
                "required": ["path"],
            },
            output_schema={"type": "array", "items": {"type": "string"}},
            risk=RiskLevel.SAFE,
            permissions=("filesystem.list",),
            timeout=30.0,
            executor=self._list,
            tags=("filesystem", "read"),
        )

    def _list(self, path: str, pattern: str = "*") -> list[str]:
        if self.execution is not None:
            return self.execution.list_dir(path, pattern=pattern)
        return sorted(str(p) for p in Path(path).expanduser().iterdir()
                      if pattern in ("*", "") or p.match(pattern))

    def _filesystem_write(self) -> ToolSpec:
        return ToolSpec(
            name="filesystem.write",
            description="Create or overwrite a text file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": True},
                },
                "required": ["path", "content"],
            },
            output_schema={"type": "boolean"},
            risk=RiskLevel.LOW_RISK,
            permissions=("filesystem.write",),
            timeout=30.0,
            executor=self._write,
            tags=("filesystem", "write"),
        )

    def _write(self, path: str, content: str, overwrite: bool = True) -> bool:
        if self.execution is not None:
            return self.execution.write_file(path, content, overwrite=overwrite)
        target = Path(path).expanduser()
        if target.exists() and not overwrite:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return True

    def _filesystem_delete(self) -> ToolSpec:
        return ToolSpec(
            name="filesystem.delete",
            description="Delete one file. Requires confirmation and a high ceiling.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            output_schema={"type": "boolean"},
            risk=RiskLevel.HIGH_RISK,
            permissions=("filesystem.delete",),
            timeout=30.0,
            executor=self._delete,
            tags=("filesystem", "destructive"),
        )

    def _delete(self, path: str) -> bool:
        if self.execution is None:
            raise PermissionError("local execution is disabled")
        deleter = getattr(self.execution, "delete_file", None)
        if deleter is None:
            raise NotImplementedError(
                f"{type(self.execution).__name__} cannot delete files"
            )
        return deleter(path)

    # -- shell ---------------------------------------------------------
    def _shell_execute(self) -> ToolSpec:
        return ToolSpec(
            name="shell.execute",
            description="Run a command. Pass an argument list, never a shell string.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "argv, e.g. ['git','status']",
                    },
                    "cwd": {"type": "string"},
                    "timeout": {"type": "number", "default": 120},
                },
                "required": ["command"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.MEDIUM_RISK,
            permissions=("shell.execute",),
            timeout=180.0,
            executor=self._execute,
            tags=("shell", "execution"),
        )

    def _execute(
        self, command: Sequence[str] | str, cwd: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        if self.execution is None:
            raise PermissionError("local execution is disabled")
        result = self.execution.run_command(command, cwd=cwd, timeout=timeout)
        return {
            "exit_code": result.exit_code,
            "ok": result.ok,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    def _shell_python(self) -> ToolSpec:
        return ToolSpec(
            name="shell.python",
            description="Run a Python snippet in a bounded child interpreter.",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "timeout": {"type": "number", "default": 60},
                },
                "required": ["source"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.MEDIUM_RISK,
            permissions=("shell.execute",),
            timeout=90.0,
            executor=self._run_python,
            tags=("shell", "execution", "python"),
        )

    def _run_python(self, source: str, timeout: float = 60.0) -> dict[str, Any]:
        if self.execution is None:
            raise PermissionError("local execution is disabled")
        result = self.execution.run_python(source, timeout=timeout)
        return {
            "exit_code": result.exit_code,
            "ok": result.ok,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    # -- system --------------------------------------------------------
    def _system_info(self) -> ToolSpec:
        return ToolSpec(
            name="system.info",
            description="Report Alan's environment: version, paths, providers.",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            risk=RiskLevel.SAFE,
            permissions=("system.info",),
            timeout=15.0,
            executor=self._info,
            tags=("system",),
        )

    # -- opening applications and websites -------------------------------
    def _system_open(self) -> ToolSpec:
        return ToolSpec(
            name="system.open",
            description=(
                "Open a website or start an installed application on this "
                "machine, so the user can see it. Accepts a bare name "
                "('notepad'), a Start Menu name ('chrome'), a full path "
                "('C:\\\\Tools\\\\app.exe') or an address ('youtube.com'). "
                "Use this when the user wants something opened; use web.open "
                "only to read a page's text."
            ),
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.MEDIUM_RISK,
            permissions=("app.launch",),
            timeout=30.0,
            executor=self._do_open,
            tags=("system",),
        )

    def _do_open(self, target: str) -> dict[str, Any]:
        # Imported here so a non-Windows install of Alan does not fail to import
        # this module; the launcher reports the platform rather than crashing.
        from . import opening

        return opening.launch(target)

    def _info(self) -> dict[str, Any]:
        settings = self.settings
        return {
            "home": str(HOME),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "provider_setting": settings.provider,
            "ollama": settings.ollama.base_url,
            "ollama_model": settings.ollama.model,
            "memory_backend": settings.memory.backend,
            "memory_url": settings.memory.base_url,
            "risk_ceiling": settings.permissions.ceiling,
        }

    # -- memory --------------------------------------------------------
    def _memory_search(self) -> ToolSpec:
        return ToolSpec(
            name="memory.search",
            description="Search Alan's memory for facts relevant to a query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            output_schema={"type": "array"},
            risk=RiskLevel.SAFE,
            permissions=("memory.search",),
            timeout=30.0,
            executor=self._memory_do_search,
            tags=("memory", "read"),
        )

    def _memory_do_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self.memory is None:
            return []
        return [item.to_dict() for item in self.memory.search(query, top_k=top_k)]

    def _memory_remember(self) -> ToolSpec:
        return ToolSpec(
            name="memory.remember",
            description="Store a durable fact about the user.",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "level": {
                        "type": "string",
                        "enum": [str(level) for level in MemoryLevel],
                        "default": str(MemoryLevel.L4_USER),
                    },
                    "type": {"type": "string", "default": MemoryType.FACT.value},
                },
                "required": ["content"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.LOW_RISK,
            permissions=("memory.remember",),
            timeout=60.0,
            executor=self._memory_do_remember,
            tags=("memory", "write"),
        )

    def _memory_do_remember(
        self, content: str, level: str = "L4_USER", type: str = "fact"
    ) -> dict[str, Any]:
        if self.memory is None:
            raise PermissionError("memory is not configured")
        try:
            chosen = MemoryLevel[level] if level in MemoryLevel.__members__ else MemoryLevel(int(level))
        except (KeyError, ValueError):
            chosen = MemoryLevel.L4_USER
        try:
            kind = MemoryType(type)
        except ValueError:
            kind = MemoryType.FACT
        item = self.memory.remember(
            MemoryItem(content=content, level=chosen, type=kind, source="tool")
        )
        return {"id": item.id, "level": str(item.level), "stored": True}

    # -- web -----------------------------------------------------------
    def _web_search(self) -> ToolSpec:
        return ToolSpec(
            name="web.search",
            description=(
                "Search the web and return titles, links and snippets. The "
                "default path is Exa semantic search (no setup, no key). "
                "Naming an engine opts into the browser sidecar's HTML "
                "scraping instead, which needs the sidecar and is slower."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "engine": {
                        "type": "string",
                        "default": "",
                        "enum": ["", "marginalia", "bing", "duckduckgo"],
                        "description": (
                            "Leave empty for Exa (semantic search). Naming a "
                            "keyless engine uses the browser sidecar's scraped "
                            "engines, which block or rewrite each other; only "
                            "do this when Exa is the wrong tool."
                        ),
                    },
                },
                "required": ["query"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.LOW_RISK,
            permissions=("browser.search",),
            timeout=60.0,
            executor=self._web_do_search,
            tags=("web", "browser"),
        )

    def _web_do_search(
        self, query: str, engine: str = ""
    ) -> dict[str, Any]:
        # An empty engine means "just search" -> the zero-config Exa path.
        # Naming an engine is an explicit choice to use the scraped engines,
        # which only the browser sidecar can read.
        if not engine and self.internet is not None:
            ok, _reason = self.internet.available()
            if ok:
                results = self.internet.search(query, count=5)
                return {
                    "content": format_results(results),
                    "url": "",
                    "data": {"results": results, "source": "exa", "engine": ""},
                }
        if self.browser is None:
            raise RuntimeError(
                "web search needs the internet tools (Exa via mcporter) or "
                "the browser sidecar; enable [tools] internet / [tools] "
                "browser in config/settings.toml"
            )
        result = self.browser.search(query, engine=engine)
        if not result.ok:
            raise RuntimeError(result.error or "browser search failed")
        return {"content": result.content, "url": result.url, "data": result.data}

    def _web_open(self) -> ToolSpec:
        return ToolSpec(
            name="web.open",
            description="Open a URL and return its readable text.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.HIGH_RISK,
            permissions=("browser.open",),
            timeout=60.0,
            executor=self._web_do_open,
            tags=("web", "browser"),
        )

    def _web_do_open(self, url: str) -> dict[str, Any]:
        # Forgive a missing scheme. A strict parser turns "youtube.com" into a
        # failed fetch whose error message points at the network rather than at
        # the missing "https://", which is not a useful thing to be told.
        from .opening import normalize_url

        target = normalize_url(url)
        if self.internet is not None:
            ok, _reason = self.internet.available()
            if ok:
                # Jina fetches on its own servers, so this path cannot reach a
                # machine on Alan's own network -- the risk a browser-based
                # fetch carried. The gate still treats web.open the same way.
                try:
                    text = self.internet.read(target)
                except Exception:
                    if self.browser is None:
                        raise
                    return self._browser_open(target)
                return {"content": text, "url": target, "title": ""}
        if self.browser is None:
            raise RuntimeError(
                "web.open needs the internet tools (Jina Reader via curl) or "
                "the browser sidecar; enable [tools] internet / [tools] "
                "browser in config/settings.toml"
            )
        return self._browser_open(target)

    def _browser_open(self, target: str) -> dict[str, Any]:
        result = self.browser.open(target)  # type: ignore[union-attr]
        if not result.ok:
            raise RuntimeError(result.error or "browser open failed")
        return {"content": result.content, "url": result.url, "title": result.title}


def _exec(execution: ExecutionProvider | None, method: str, *args: Any, **kwargs: Any) -> Any:
    if execution is None:
        raise PermissionError(
            "local execution is disabled, so filesystem access via the execution "
            "provider is unavailable"
        )
    return getattr(execution, method)(*args, **kwargs)