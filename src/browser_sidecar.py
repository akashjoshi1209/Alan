"""Alan's browser sidecar.

Runs in the ISOLATED environment (``env\\browser\\python``), which cannot import
``alan``. Everything crosses the boundary as JSON over HTTP. Launched by
``scripts\\browser-serve.ps1``; bind address is loopback only.

Design notes
------------
**No browser binary is bundled.** Playwright normally downloads its own
Chromium (~150 MB), which would break the one-folder rule. browser-use 0.13
drives Chrome over CDP instead, so the browser already installed on the host is
used -- Chrome if present, otherwise Edge, both of which ship with Windows.

**browser-use 0.13 is asynchronous, and that shapes the whole process.** A
browser session cannot be reopened per request without losing the page state
that ``click`` and ``type_text`` depend on, so the session must outlive an
individual request. The HTTP handlers here are synchronous, so a private event
loop runs on a daemon thread and each request submits a coroutine to it and
waits. :class:`_Loop` is that bridge; every browser call goes through it.

**browser-use's agent loop is not used.** Alan already has a planner and an
orchestrator. Wiring in a second one would give the product two competing
brains. This process executes actions; it never decides what to do. That is also
why ``extract_content`` -- which wants an LLM -- is unused: readable text comes
from ``page.evaluate`` instead, so the sidecar needs no model at all.

**Only ``127.0.0.1``.** This endpoint can fetch arbitrary URLs and click things.
Authorization is Alan's job, in the permission gate, before a tool is invoked.
The sidecar deliberately has no notion of who is asking.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
import traceback
import urllib.parse
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Coroutine

HOST = "127.0.0.1"
PORT = int(os.environ.get("ALAN_BROWSER_PORT", "8022"))

_IS_WINDOWS = os.name == "nt"

#: How many times to poll for an orphan browser to actually exit after a polite
#: terminate, at 0.25 s apart. 20 polls is 5 s, which is far longer than a Chrome
#: shutdown needs and short enough that a stuck process does not stall a launch.
_REAP_POLLS = 20

#: Set by ``main`` so the shutdown path can stop ``serve_forever``.
_SERVER: Any = None

try:  # psutil is optional; it only sharpens stale-profile detection.
    import psutil as _PSUTIL
except Exception:  # noqa: BLE001
    _PSUTIL = None

#: Candidate browsers, in preference order. No download, no bundled binary.
BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

#: Hard ceiling on any single browser action, whatever the caller asks for.
_ACTION_TIMEOUT = 120.0

#: Ceiling on readable text handed back to a model. A 1.5B model has a small
#: context, and an unbounded page body would swamp it.
_MAX_TEXT = 20_000


def _find_browser() -> str:
    override = os.environ.get("ALAN_BROWSER_PATH", "")
    if override and os.path.exists(override):
        return override
    for candidate in BROWSER_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "no installed Chrome or Edge found. Alan's browser sidecar drives the "
        "browser already on the machine rather than downloading one. Set "
        "ALAN_BROWSER_PATH to point at a browser executable."
    )


# ---------------------------------------------------------------------------
# Async bridge
# ---------------------------------------------------------------------------
class _Loop:
    """A private asyncio loop on a daemon thread.

    Exists because browser-use is async while this HTTP layer is not, and the
    session has to persist between requests. One loop, one thread, serialised by
    a lock: this is a single-page browser, and concurrent navigations of one page
    would be meaningless at best.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _boot(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._boot, name="alan-browser-loop", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("the browser event loop did not start within 10s")

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float = _ACTION_TIMEOUT) -> Any:
        self.start()
        loop = self._loop
        if loop is None:
            raise RuntimeError("the browser event loop is not running")
        with self._lock:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            # Bounded. A hung navigation must return an error rather than wedge
            # the sidecar: the process stays up so the next request can try again.
            return future.result(timeout=timeout)


_LOOP = _Loop()
_SESSION: Any = None


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_WS = re.compile(r"[ \t\xa0]+")
_BLANKS = re.compile(r"\n{3,}")

#: Pull readable text in the page itself. Prefers main/article, drops the
#: furniture, and reads innerText so the result is what a person would read.
_READABLE_JS = """() => {
    const root = document.querySelector('main')
        || document.querySelector('article')
        || document.body;
    if (!root) return '';
    const clone = root.cloneNode(true);
    clone.querySelectorAll('script,style,nav,header,footer,aside,noscript,iframe')
        .forEach(n => n.remove());
    return clone.innerText || clone.textContent || '';
}"""


def _clean(text: str) -> str:
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


async def _readable_text(page: Any) -> str:
    try:
        value = await page.evaluate(_READABLE_JS)
        if value and str(value).strip():
            return _clean(str(value))[:_MAX_TEXT]
    except Exception:
        pass
    return ""


async def _await_text(page: Any, *, settle: float = 6.0) -> str:
    """Read the page's text, waiting briefly for it to actually render.

    Without this, ``open`` returns HTTP success with an empty body whenever the
    document has not painted yet -- which reads to a caller as "that page is
    blank" rather than "we looked too early". An empty result should mean the
    page really has no text, not that we raced it.
    """
    text = await _readable_text(page)
    if text:
        return text
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        await asyncio.sleep(0.35)
        text = await _readable_text(page)
        if text:
            return text
    return text


async def _page_info(page: Any) -> dict[str, str]:
    title = url = ""
    try:
        title = str(await page.get_title() or "")
    except Exception:
        title = ""
    try:
        url = str(await page.get_url() or "")
    except Exception:
        url = ""
    return {"title": title, "url": url}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
# No search API key, no account, nothing shipped. Each engine is read out of its
# results page.
#
# These were chosen by measurement, not preference. On this machine DuckDuckGo's
# HTML endpoint answers with a captcha wall ("Unfortunately, bots use
# DuckDuckGo too") and Mojeek returns 403, so both are unusable however correct
# they look on paper. Bing and Marginalia both return real results.
#
# DuckDuckGo stays registered because it is the interface's historical default
# and works from some networks. It is tried, and if it yields nothing the error
# says which engines were attempted rather than blaming the network.

_BING_JS = """
() => {
    const out = [];
    for (const li of document.querySelectorAll('li.b_algo')) {
        const a = li.querySelector('h2 a');
        if (!a) { continue; }
        const cap = li.querySelector('.b_caption p') || li.querySelector('p');
        out.push({
            title: (a.textContent || '').trim(),
            href: a.href || '',
            snippet: cap ? (cap.textContent || '').trim().slice(0, 240) : ''
        });
        if (out.length >= 12) { break; }
    }
    return JSON.stringify(out);
}
"""

#: Marginalia has no stable result class, so every external link with real text
#: is collected and the site's own navigation is filtered out by hostname and by
#: relevance to the query.
_MARGINALIA_JS = """
() => {
    const out = [];
    for (const a of document.querySelectorAll('a')) {
        const title = (a.textContent || '').trim();
        if (a.href && a.href.startsWith('http') && title.length > 8) {
            out.push({ title: title, href: a.href, snippet: '' });
        }
        if (out.length >= 40) { break; }
    }
    return JSON.stringify(out);
}
"""

_DDG_JS = """
() => {
    const out = [];
    for (const a of document.querySelectorAll('a.result__a')) {
        out.push({ title: (a.textContent || '').trim(), href: a.href || '', snippet: '' });
        if (out.length >= 10) { break; }
    }
    return JSON.stringify(out);
}
"""

#: Hosts that are the search engine's own furniture, never a result.
_SELF_HOSTS = (
    "bing.com", "microsoft.com", "duckduckgo.com", "marginalia.nu",
    "marginalia-search.com", "w3.org", "microsofttranslator.com",
    "startpage.com", "searx.",
)

#: Very common words that carry no topical signal. Without these, "how to bake
#: sourdough bread" would "match" any link containing "how".
_STOPWORDS = frozenset(
    """how to the a an of and or in on for is are was were be been it its this that
    with from by at as but not you your we our they their can will would should
    about into over under what which who whom when where why how""".split()
)

#: Minimum meaningful tokens a result must share with the query. One is enough
#: to be plausible; requiring more throws away legitimate long-tail results.
_MIN_OVERLAP = 1


def _query_terms(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", query.lower())
        if token not in _STOPWORDS
    }


def _is_relevant(title: str, url: str, snippet: str, terms: set[str]) -> bool:
    """Does this result share any meaningful token with the query?

    This is not a nicety. On the network this was built on, search pages arrive
    rewritten: the page reports the right URL and title while its result rows
    belong to an entirely different query -- one fetch returned a full page of
    "yt jeffsy" Pinkbike results in answer to a sourdough question. Quoting that
    back to a user as an answer is worse than admitting the engine is unusable,
    so anything with zero lexical overlap is discarded rather than returned.
    """
    if not terms:
        return True
    blob = f"{title} {url} {snippet}".lower()
    return any(term in blob for term in terms)


def _unwrap_bing(href: str) -> str:
    """Recover the real destination from a Bing ``/ck/a`` redirect wrapper.

    Bing hides result URLs behind a click tracker. Without this the caller gets
    ``https://www.bing.com/ck/a?...`` links that are useless for fetching and
    misleading to quote back. The ``u`` parameter is base64url with a leading
    ``a1`` version marker.
    """
    if "bing.com/ck/a" not in href:
        return href
    raw = urllib.parse.parse_qs(
        urllib.parse.urlparse(href).query
    ).get("u", [""])[0]
    if not raw:
        return href
    if raw.startswith("a1"):
        raw = raw[2:]
    raw += "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return href
    return decoded if decoded.startswith("http") else href


def _unwrap_redirect(href: str) -> str:
    """Unwrap DuckDuckGo's ``uddg`` redirect."""
    if "uddg=" in href:
        target = urllib.parse.parse_qs(
            urllib.parse.urlparse(href).query
        ).get("uddg", [""])[0]
        if target:
            return target
    return _unwrap_bing(href)


#: name -> (search URL template, JS extractor, keep_snippets)
SEARCH_ENGINES: dict[str, tuple[str, str, bool]] = {
    "bing": (
        "https://www.bing.com/search?q={query}",
        _BING_JS,
        True,
    ),
    "marginalia": (
        "https://search.marginalia.nu/search?query={query}",
        _MARGINALIA_JS,
        False,
    ),
    "duckduckgo": (
        "https://html.duckduckgo.com/html/?q={query}",
        _DDG_JS,
        False,
    ),
}

#: Tried in order when the caller names no engine.
#:
#: Marginalia leads because it is the only engine measured to return results
#: that actually match the query from this machine. Bing is second: it works
#: often enough to be worth trying and is the only one that returns snippets,
#: but it also returns cached results for unrelated queries, which the relevance
#: filter below discards. DuckDuckGo answers with a captcha wall here and Mojeek
#: with 403, so both are kept only because they work from other networks.
#:
#: This ordering is a measurement, not a preference. See the note on
#: ``_is_relevant`` for why a non-empty result set is not trusted on its own.
DEFAULT_ENGINE = "marginalia"

_FALLBACK_ORDER = ("marginalia", "bing", "duckduckgo")

#: A first fetch of a search page frequently comes back empty here. One retry
#: turned Marginalia from "0 links" into 40 real results, so it is worth the
#: extra round trip before declaring an engine dead.
_ATTEMPTS_PER_ENGINE = 2


def _is_self_link(href: str) -> bool:
    host = urllib.parse.urlparse(href).netloc.lower()
    return any(host == bad or host.endswith("." + bad) for bad in _SELF_HOSTS)


async def _extract_rows(page: Any, engine: str) -> list[dict[str, Any]]:
    """Run the engine's extractor and normalise whatever shape it returns."""
    _template, script, _ = SEARCH_ENGINES[engine]
    raw = await page.evaluate(script)
    try:
        rows = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


async def _run_search(
    session: Any,
    engine: str,
    query: str,
    top_k: int,
    *,
    settle: float = 8.0,
    attempts: int = _ATTEMPTS_PER_ENGINE,
) -> list[dict[str, str]]:
    """Search in a tab of its own.

    Sharing the long-lived page with ``open``/``click`` made results
    unpredictable. Search is stateless, so it gets a dedicated tab that is
    closed afterwards. The stateful actions keep the long-lived page, because
    ``click`` and ``type_text`` genuinely need the page they acted on to still
    be there.
    """
    template, _script, keep_snippets = SEARCH_ENGINES[engine]
    target = template.format(query=urllib.parse.quote_plus(query))
    terms = _query_terms(query)
    last_seen = 0

    for attempt in range(1, max(1, attempts) + 1):
        page = await session.new_page(target)
        try:
            # Results are rendered by script, so reading the document the
            # instant navigation returns usually catches an empty page. Poll
            # until rows appear, bounded -- an engine that never renders must not
            # hold the request open.
            rows: list[dict[str, Any]] = []
            deadline = time.monotonic() + settle
            while True:
                try:
                    rows = await _extract_rows(page, engine)
                except Exception:
                    rows = []
                if rows or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.4)

            last_seen = len(rows)

            results: list[dict[str, str]] = []
            seen: set[str] = set()
            dropped = 0
            for row in rows:
                href = _unwrap_redirect(unescape(str(row.get("href") or "")))
                if not href.startswith("http") or _is_self_link(href) or href in seen:
                    continue
                title = _clean(str(row.get("title") or ""))
                snippet = (
                    _clean(str(row.get("snippet") or ""))[:240]
                    if keep_snippets else ""
                )
                if not title or not _is_relevant(title, href, snippet, terms):
                    dropped += 1
                    continue
                seen.add(href)
                results.append({
                    "title": title, "url": href, "snippet": snippet,
                })
                if len(results) >= top_k:
                    break

            # Log what was actually on screen when the rows were read, and how
            # many the relevance filter removed. Results that do not match the
            # query are otherwise indistinguishable from a parser bug.
            try:
                page_title = str(await page.evaluate("() => document.title"))
                page_url = str(await page.get_url())
            except Exception:
                page_title = page_url = "?"
            print(
                f"[sidecar] search engine={engine} attempt={attempt} "
                f"rows={last_seen} kept={len(results)} dropped_irrelevant={dropped} "
                f"title={page_title[:52]!r} url={page_url[:80]}",
                flush=True,
            )

            if results:
                return results
        finally:
            try:
                await session.close_page(page)
            except Exception:
                pass

    return []


# ---------------------------------------------------------------------------
# Session and actions
# ---------------------------------------------------------------------------
#: browser-use's ``_copy_profile`` copies any profile path that is not already
#: a browser-use temp directory into a *new* random one, so a caller that
#: supplies its own path still ends up with a random one -- and a random one
#: cannot be identified later, which is exactly what is needed to reap an
#: orphaned browser. The name below contains the marker that function looks for,
#: which is its own escape hatch for "already a temp directory, do not copy".
#:
#: Do not rename this to something tidier. A clean-looking name silently puts
#: Chrome back on a fresh random temp profile on every launch, and the
#: reclamation in ``_reap_orphans`` stops matching, and the leak returns.
_PROFILE_MARKER = "browser-use-user-data-dir-sidecar"


def _profile_dir() -> Path:
    """Where the browser keeps its cookies, cache and profile.

    This used to be left to the browser-use default: a fresh random
    ``browser-use-user-data-dir-XXXX`` under the system temp directory, per
    launch. Combined with a shutdown path that never ran (see ``main``), every
    start/stop cycle of the sidecar leaked a whole Chrome process tree *and* a
    profile directory that nothing ever cleaned up. Sixteen restarts left 125
    processes and 731 MB resident on a 4 GB machine.

    A fixed path under Alan's own data directory makes the footprint bounded
    and visible, makes an orphaned browser identifiable, and lets the operator
    find it if they ever need to.
    """
    override = os.environ.get("ALAN_BROWSER_PROFILE_DIR", "").strip()
    if override:
        return Path(override)
    home = os.environ.get("ALAN_HOME", "").strip()
    if not home:
        home = str(Path(__file__).resolve().parents[1])
    return Path(home) / "data" / "browser-profile" / f"{_PROFILE_MARKER}-{PORT}"


def _clear_stale_profile() -> None:
    """Remove a profile left behind by a sidecar that died without cleaning up.

    Only ever touches Alan's own ``sidecar-<port>`` directory, and only when no
    Chrome is holding it -- this must never reach the user's own browser data,
    or a running browser session, for any reason.
    """
    path = _profile_dir()
    if not path.exists():
        return
    if _profile_in_use(path):
        print(f"[sidecar] profile {path} is in use; leaving it alone", flush=True)
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
        print(f"[sidecar] removed stale browser profile at {path}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[sidecar] could not remove stale profile: {exc}", flush=True)


def _profile_in_use(path: Path) -> bool:
    """True when a Chrome process was started with this exact profile dir."""
    if not _IS_WINDOWS or not _PSUTIL:
        # Chrome's lock files are the portable signal.
        return (path / "SingletonLock").exists() or (path / "lockfile").exists()
    try:
        for proc in _PSUTIL.process_iter(["pid", "cmdline"]):
            cmdline = proc.info.get("cmdline") or []
            if any(str(part).startswith(str(path)) for part in cmdline):
                return True
    except Exception:  # noqa: BLE001
        # If we cannot tell, assume it is in use: leaking a profile is a much
        # smaller problem than deleting one a live browser is writing to.
        return True
    return False


def _reap_orphans() -> None:
    """Kill browsers left over from a sidecar that was killed outright.

    Windows has no SIGTERM for ``Stop-Process -Force``: it calls
    TerminateProcess, which runs no handler and no ``finally``, so the browser
    this sidecar started keeps running with nothing left to stop it. The
    previous instance's Chrome is still sitting on this sidecar's own profile
    directory, which is exactly how it can be identified -- and identifying it
    by that path is what makes this safe to do automatically.

    The match is on Alan's own deterministic profile path, so this can never
    reach the operator's own browser or any other application's data.
    """
    if not _IS_WINDOWS or not _PSUTIL:
        return
    marker = str(_profile_dir()).lower()
    doomed: list[tuple[int, str]] = []
    try:
        for proc in _PSUTIL.process_iter(["pid", "name", "cmdline"]):
            name = str(proc.info.get("name") or "").lower()
            if not name.startswith(("chrome", "msedge", "chromium")):
                continue
            cmdline = proc.info.get("cmdline") or []
            if any(marker in str(part).lower() for part in cmdline):
                doomed.append((int(proc.info["pid"]), name))
    except Exception as exc:  # noqa: BLE001
        print(f"[sidecar] could not scan for orphan browsers: {exc}", flush=True)
        return
    for pid, name in doomed:
        try:
            _PSUTIL.Process(pid).terminate()
            print(f"[sidecar] reaped orphaned {name} (pid {pid})", flush=True)
        except Exception:  # noqa: BLE001 - whether it died or refused, the poll below decides
            pass

    if doomed:
        # Wait for the processes to actually *exit* rather than assuming a fixed
        # sleep is long enough. Chrome spawns a tree, and a parent can be gone
        # while a child still holds the profile directory. Relying on 1 s meant
        # that on a slow, memory-starved machine the reap was still in progress
        # when the next launch started -- and a Chrome launched against a profile
        # another Chrome still holds does not fail loudly. It hands the command
        # line to the running instance and exits immediately, which surfaces as
        # "exited before CDP became available" and looks like a broken Chrome
        # rather than a stale one.
        #
        # Bounded by a poll count rather than a wall-clock deadline: the two are
        # equivalent in production, but a deadline loop spins at full speed the
        # moment ``sleep`` is stubbed, which is exactly what a test does.
        survivors: list[tuple[int, str]] = []
        for _ in range(_REAP_POLLS):
            survivors = [
                (pid, name) for pid, name in doomed
                if _PSUTIL.pid_exists(pid)
            ]
            if not survivors:
                break
            time.sleep(0.25)

        # Anything still alive after a polite terminate gets killed outright.
        # Only ever reached for processes already identified as holding *this*
        # sidecar's own profile path.
        for pid, name in survivors:
            try:
                _PSUTIL.Process(pid).kill()
                print(f"[sidecar] killed stubborn {name} (pid {pid})", flush=True)
            except Exception:  # noqa: BLE001
                pass
        if survivors:
            time.sleep(0.5)

    _PSUTIL.process_iter(["pid"])  # refresh the process cache before deleting


async def _get_session() -> Any:
    """Create the session on first use. Nothing launches at import or startup.

    Reaping happens *here*, immediately before the launch, not only in
    :func:`main`. That distinction is the whole bug.

    ``main`` reaps once at startup, which protects against a browser left by a
    sidecar that was killed outright. It does nothing about a browser that
    started *after* this sidecar did, or one that survived a session reset --
    and the session is cached and can be torn down and rebuilt while the Chrome
    process tree lives on. Those cases are the common ones, not the rare one.

    When that happens, launching against a profile another Chrome already holds
    fails in a way that misdirects: Windows treats the new process as a
    second-instance message, hands over its command line and exits, so
    browser-use reports "Browser process exited before CDP became available" and
    every reasonable reading of that message is a broken Chrome install.
    """
    global _SESSION
    from browser_use import BrowserSession

    if _SESSION is None:
        # Free the profile before asking for it. Safe unconditionally: the match
        # is on this sidecar's own deterministic profile path, so nothing outside
        # it is ever touched, and _SESSION being None means no browser of ours is
        # supposed to be alive anyway.
        _reap_orphans()

        profile = _profile_dir()
        try:
            profile.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # noqa: BLE001
            print(f"[sidecar] could not create profile directory: {exc}", flush=True)
        # A profile directory still holding a dead instance's lock file makes
        # Chrome exit immediately. Now that nothing holds it, clear it.
        _clear_stale_profile()

        _SESSION = BrowserSession(
            executable_path=_find_browser(),
            headless=os.environ.get("ALAN_BROWSER_HEADLESS", "1") == "1",
            user_data_dir=str(profile),
            args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            await _SESSION.start()
        except Exception:
            # Do not cache a session object that failed to start. Leaving it
            # assigned means every later call sees a non-None _SESSION and skips
            # this whole block, so the browser is never even retried.
            _SESSION = None
            raise
        print(f"[sidecar] browser session started (profile {profile})", flush=True)
    return _SESSION


async def _close_session() -> None:
    """Stop the browser and remove its profile.

    Stopping the session is not enough: Chrome survives an unclean exit and the
    profile directory is left behind, which is what accumulated.
    """
    global _SESSION
    session, _SESSION = _SESSION, None
    if session is not None:
        try:
            await session.stop()
        except Exception as exc:  # noqa: BLE001
            print(f"[sidecar] session stop reported: {exc}", flush=True)
    try:
        _clear_stale_profile()
    except Exception:  # noqa: BLE001
        pass


async def _current_page() -> Any:
    """Return a live page, restarting a dead browser session once.

    The session is cached for the life of the sidecar, which is what lets
    ``click`` act on the page ``open`` left behind. But a cached session can
    die -- the browser crashes, the watchdog resets it, or someone closes the
    window -- and ``get_current_page()`` then returns None. Without a recovery
    path that surfaces as ``'NoneType' object has no attribute 'goto'``, an
    internal detail, and the capability stays broken until a human restarts the
    sidecar. One retry with a fresh session turns a permanently dead capability
    into merely a slow request.
    """
    for attempt in (1, 2):
        session = await _get_session()
        try:
            page = await session.get_current_page()
        except Exception:
            page = None
        if page is not None:
            return page
        if attempt == 1:
            print(
                "[sidecar] browser has no live page; restarting the session",
                flush=True,
            )
            await _close_session()
    raise RuntimeError(
        "the browser has no live page. It was restarted once and still had none; "
        "check that no other tool is holding the browser, then retry."
    )


async def act_open(url: str, timeout: float) -> dict[str, Any]:
    page = await _current_page()
    await asyncio.wait_for(page.goto(url), timeout=timeout)
    content = await _await_text(page)
    return {
        "content": content,
        **(await _page_info(page)),
        "data": {"engine": "browser-use", "chars": len(content)},
    }


async def act_navigate(url: str) -> dict[str, Any]:
    page = await _current_page()
    await page.goto(url)
    content = await _await_text(page)
    return {
        "content": content,
        **(await _page_info(page)),
        "data": {"chars": len(content)},
    }


async def act_search(query: str, engine: str = "", top_k: int = 8) -> dict[str, Any]:
    """Search, falling through the engine list before giving up.

    A named engine that returns nothing is a hint, not a verdict: the usual
    cause is that engine blocking this network, which is exactly what happened
    with DuckDuckGo here. So the remaining engines are tried and the response
    records which one actually answered.
    """
    if engine and engine not in SEARCH_ENGINES:
        raise ValueError(
            f"unknown search engine {engine!r}; "
            f"available: {', '.join(sorted(SEARCH_ENGINES))}"
        )

    session = await _get_session()
    order = [engine] if engine else [DEFAULT_ENGINE, *_FALLBACK_ORDER]
    if not engine:
        # De-duplicate while keeping the preferred engine first.
        seen: set[str] = set()
        order = [e for e in order if not (e in seen or seen.add(e))]

    attempted: list[str] = []
    for name in order:
        attempted.append(name)
        try:
            results = await _run_search(session, name, query, top_k)
        except Exception as exc:
            # One engine being unreachable must not end the request.
            print(f"[sidecar] engine {name} failed: {exc}", flush=True)
            continue
        if results:
            lines = []
            for index, item in enumerate(results, 1):
                snippet = f"\n   {item['snippet']}" if item.get("snippet") else ""
                lines.append(f"{index}. {item['title']}\n   {item['url']}{snippet}")
            return {
                "content": "\n".join(lines),
                "url": SEARCH_ENGINES[name][0].format(query=urllib.parse.quote_plus(query)),
                "title": f"search: {query}",
                "data": {"results": results, "engine": name, "attempted": attempted},
            }

    raise LookupError(
        f"no search engine returned results relevant to {query!r}. "
        f"Tried: {', '.join(attempted)}. "
        f"Search pages are being filtered or rewritten on this network, so "
        f"results could not be trusted rather than returned unchecked."
    )


async def act_navigate(url: str) -> dict[str, Any]:
    page = await _current_page()
    await page.goto(url)
    return {"content": await _readable_text(page), **(await _page_info(page))}


async def act_click(selector: str) -> dict[str, Any]:
    page = await _current_page()
    elements = await page.get_elements_by_css_selector(selector)
    if not elements:
        raise LookupError(f"no element matched {selector!r}")
    await elements[0].click()
    return {
        "content": await _await_text(page),
        **(await _page_info(page)),
        "data": {"clicked": selector, "matches": len(elements)},
    }


async def act_type_text(selector: str, text: str) -> dict[str, Any]:
    page = await _current_page()
    elements = await page.get_elements_by_css_selector(selector)
    if not elements:
        raise LookupError(f"no element matched {selector!r}")
    await elements[0].fill(text)
    return {
        "content": f"typed {len(text)} character(s) into {selector}",
        **(await _page_info(page)),
    }


async def act_extract(selector: str) -> dict[str, Any]:
    # Uses Page.evaluate rather than Element.evaluate. Page.evaluate documents
    # an explicit (page_function, *args) signature, while Element.evaluate hands
    # the function no element reference -- so `el` arrives undefined and any
    # expression touching it throws. Selecting inside the page sidesteps that.
    page = await _current_page()
    # The JS returns a JSON marker rather than null/text directly. CDP does not
    # reliably surface a JS null as a Python None, so testing `value is None`
    # silently reported success for a selector that matched nothing. An explicit
    # {ok:false} marker makes "no such element" distinguishable from "an element
    # with no text" without having to trust the bridge's null handling.
    raw = await page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return JSON.stringify({ ok: false });
            return JSON.stringify({ ok: true, text: el.innerText || el.textContent || '' });
        }""",
        selector,
    )
    try:
        found = json.loads(str(raw))
    except (TypeError, ValueError):
        raise LookupError(
            f"could not interpret the page's answer for {selector!r}"
        ) from None
    if not found.get("ok"):
        raise LookupError(f"no element matched {selector!r}")
    return {
        "content": _clean(str(found.get("text") or ""))[:_MAX_TEXT],
        **(await _page_info(page)),
        "data": {"selector": selector},
    }


async def act_screenshot(_full_page: bool) -> dict[str, Any]:
    page = await _current_page()
    raw = await page.screenshot(format="png")
    # browser-use returns a str, which is base64 in practice. Accept bytes too
    # rather than assume, because a version bump could change that.
    if isinstance(raw, (bytes, bytearray)):
        blob = bytes(raw)
        encoded = base64.b64encode(blob).decode("ascii")
    else:
        encoded = str(raw)
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        blob = base64.b64decode(encoded)
    return {"content": "", "data": {"png_base64": encoded, "bytes": len(blob)}}


async def act_close() -> dict[str, Any]:
    await _close_session()
    return {"content": "browser closed"}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "alan-browser/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter than default
        sys.stderr.write("[sidecar] " + (fmt % args) + "\n")

    def handle_one_request(self) -> None:
        """Answer one request, tolerating a client that hangs up mid-flight.

        A health probe with a short timeout closes the socket while the server
        is still writing, which raises ConnectionResetError from deep inside
        http.server. That is the probe working as intended, not a fault, and
        left unhandled it fills the log with tracebacks that look like outages.
        """
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    # -- helpers --------------------------------------------------------
    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        blob = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _ok(self, result: dict[str, Any]) -> None:
        self._send({
            "ok": True,
            "content": result.get("content", ""),
            "url": result.get("url", ""),
            "title": result.get("title", ""),
            "data": result.get("data", {}),
        })

    def _fail(self, error: str, status: int = 200) -> None:
        # Failures are still HTTP 200 with ok=false: the sidecar answered, the
        # action just did not succeed. Non-200 is reserved for transport faults.
        self._send({"ok": False, "error": error}, status=status)

    # -- routes ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") == "/health":
            self._send({
                "ok": True,
                "engine": "browser-use",
                "browser_use": _version("browser-use"),
                "executable": _browser_name(),
                "session": "started" if _SESSION is not None else "not started",
            })
            return
        self._fail(f"no such endpoint: {self.path}", status=404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = self.path.rstrip("/")
        payload = self._body()

        try:
            if route == "/shutdown":
                # A graceful stop. `browser-serve.ps1 -Stop` used to kill the
                # process outright, which on Windows runs no cleanup at all and
                # leaked the whole browser. This closes the session first, then
                # exits; the caller force-kills only if this does not return.
                #
                # Sent with _send, not _ok: _ok keeps only the four keys the
                # action handlers use, so a `stopping` key would be silently
                # dropped and the caller would think the request failed.
                self._send({
                    "ok": True,
                    "stopping": True,
                    "pid": os.getpid(),
                    "profile": str(_profile_dir()),
                })
                threading.Thread(target=_graceful_exit, daemon=True).start()
                return

            if route == "/open":
                url = _require(payload, "url")
                timeout = float(payload.get("timeout") or 30.0)
                # Bounded on this side too: a plan must not be able to ask the
                # sidecar for an unbounded wait.
                timeout = max(1.0, min(timeout, _ACTION_TIMEOUT))
                self._ok(_LOOP.run(act_open(url, timeout), timeout + 10))
                return

            if route == "/search":
                query = _require(payload, "query")
                top_k = max(1, min(int(payload.get("top_k") or 8), 20))
                # Empty engine means "use the default and fall back", which is
                # what the client sends unless it was given a specific engine.
                engine = str(payload.get("engine") or "").strip().lower()
                self._ok(_LOOP.run(act_search(query, engine, top_k)))
                return

            if route == "/navigate":
                self._ok(_LOOP.run(act_navigate(_require(payload, "url"))))
                return

            if route == "/click":
                self._ok(_LOOP.run(act_click(_require(payload, "selector"))))
                return

            if route == "/type_text":
                self._ok(_LOOP.run(act_type_text(
                    _require(payload, "selector"), str(payload.get("text") or "")
                )))
                return

            if route == "/extract":
                self._ok(_LOOP.run(act_extract(_require(payload, "selector"))))
                return

            if route == "/screenshot":
                self._ok(_LOOP.run(act_screenshot(bool(payload.get("full_page")))))
                return

            if route == "/close":
                self._ok(_LOOP.run(act_close()))
                return

            self._fail(f"no such endpoint: {route}", status=404)
        except Exception as exc:
            # An action must never kill the process: the next request may well
            # succeed. Report the failure and carry on serving.
            self._fail(f"{type(exc).__name__}: {exc}")
            traceback.print_exc()


def _require(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _version(module: str) -> str:
    try:
        from importlib.metadata import version

        return version(module)
    except Exception:
        return "not installed"


def _browser_name() -> str:
    try:
        return os.path.basename(_find_browser())
    except Exception as exc:
        return f"unavailable: {exc}"


def _graceful_exit() -> None:
    """Close the browser, then end the process. Used by ``POST /shutdown``."""
    global _SERVER
    time.sleep(0.2)  # let the response reach the caller first
    try:
        if _SERVER is not None:
            _SERVER.shutdown()
    except Exception:  # noqa: BLE001
        pass
    _shutdown_now()
    # `os._exit` rather than `sys.exit`: this runs on a daemon thread, where
    # sys.exit only ends the thread and the process would linger.
    os._exit(0)


def _install_shutdown_handlers(server: Any) -> None:
    """Make every way of stopping the sidecar also stop its browser.

    This is the fix for the process leak. ``main`` used to clean up only in a
    ``finally`` around ``serve_forever()``, which runs for Ctrl+C but *not* for
    SIGTERM, and on Windows ``Stop-Process`` does not run any Python cleanup at
    all. ``browser-serve.ps1 -Stop`` stops the sidecar by stopping the process,
    so every stop leaked a Chrome tree and a profile directory. Nothing the
    ``finally`` block did was enough on the path that was actually used.

    So: SIGTERM is handled (that is the signal a stop script sends), SIGINT
    keeps its KeyboardInterrupt behaviour, and ``atexit`` is the backstop for
    any exit that is neither.
    """
    def _terminate(signum: int, _frame: Any) -> None:
        print(f"[sidecar] signal {signum}; shutting down", flush=True)
        # shutdown() stops serve_forever() so the caller falls into its own
        # cleanup. A signal handler must not do the slow work itself.
        threading.Thread(target=server.shutdown, daemon=True).start()

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _terminate)
        except (ValueError, OSError):
            # Not on the main thread, or unsupported on this platform.
            pass

    atexit.register(_shutdown_now)


def _shutdown_now() -> None:
    """Stop the browser from a context that cannot await."""
    global _SESSION
    if _SESSION is None:
        return
    try:
        _LOOP.run(_close_session(), timeout=15)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    global _SERVER
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    _SERVER = server
    print(f"[sidecar] alan browser sidecar listening on http://{HOST}:{PORT}", flush=True)
    print(f"[sidecar] browser-use {_version('browser-use')}; "
          f"browser: {_browser_name()}", flush=True)
    print("[sidecar] no browser is launched until the first request", flush=True)
    _install_shutdown_handlers(server)
    # A sidecar that was killed outright leaves its browser running. Reap it
    # before doing anything else, then clear the profile it was using, so a
    # restart starts from a known-clean state instead of inheriting a dead one.
    try:
        _reap_orphans()
        _clear_stale_profile()
    except Exception:  # noqa: BLE001
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _LOOP.run(_close_session(), timeout=15)
        except Exception:
            pass
        server.server_close()
        print("[sidecar] stopped", flush=True)


if __name__ == "__main__":
    main()
