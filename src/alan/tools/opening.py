"""Resolving "open X" to something the operating system can actually start.

Why this exists
---------------
``web.open`` drives a *headless* browser and returns the page's text. That is the
right tool for "what does this page say" and the wrong tool for "open YouTube",
which means "put it on my screen". Folding the second meaning into the first
would mean raising a HIGH_RISK network capability to serve a MEDIUM_RISK local
one, and would still not show the user anything.

So opening is a separate capability, and this module is its resolution logic.

How a target is resolved
------------------------
A person says "open notepad", "launch chrome", "go to youtube.com". None of
those are paths. Windows already knows where those programs are, in places that
are meant to be authoritative rather than hard-coded:

1. ``App Paths`` in the registry -- the system's own answer to "where is this
   executable", covering anything installed by an installer.
2. Start Menu shortcuts, which is what a user double-clicks.
3. ``PATH``, for command-line programs.
4. A small table of Windows built-ins that predate App Paths.

A URL is passed straight to the shell's own handler, because for a URL the
operating system is already the resolver and second-guessing it would be worse
than useless.

Safety
------
:func:`launch` never uses ``shell=True`` and never builds a command string, so
there is no interpreter to inject into. On top of that:

* metacharacters are refused outright, so a target that would mean something
  different to ``cmd.exe`` cannot be smuggled through -- defence in depth for the
  cases where an association does route through a shell;
* an explicit path must carry an allow-listed extension, so pointing this at an
  arbitrary file such as a script or a document does not execute it;
* ``launch`` does not accept arguments. "Open notepad" is a launcher; "run this
  with these flags" is ``shell.execute``, and is gated separately.

Reference: ``HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths``.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"

#: Characters that mean something to cmd.exe even though we never build a command
#: string. Refused rather than escaped: escaping would imply we intended to honour
#: them, and we do not.
_METACHARS = set('&|;<>`^%!*?()"\'\r\n\t')

#: Extensions an explicit path may point at. Deliberately excludes ``.bat``/``.cmd``
#: (ShellExecute routes those through a command interpreter) and every document
#: and script type, so this cannot become a general "run this file".
ALLOWED_SUFFIXES = {".exe", ".lnk", ".msc", ".msi"}

#: A target longer than this is not a program name. Guards against a pasted
#: command line being mistaken for an application to open.
MAX_TARGET = 260

#: Windows built-ins that predate App Paths. Small on purpose -- this is a
#: convenience for the handful of names people actually say, not a second copy of
#: the system's program database.
_BUILTINS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "registry editor": "regedit.exe",
    "services": "services.msc",
    "wordpad": "write.exe",
    "sticky notes": "stickynotes.exe",
}

#: Multi-word names of well-known *websites* that spelling alone cannot turn into
#: an address. "open youtube" works by the bare-word guess below, but "open yt
#: music" cannot: the guess strips nothing, so the phrase would become the
#: non-existent host ``yt music.com``. A person asking to open a site should get
#: the site, not a menu of instructions -- so the names that are common enough to
#: have a *known* destination are listed here, spelled the way people say them.
#:
#: This is deliberately not the "list of well-known sites" the guess below
#: refuses. That refusal is about inventing a domain for any bare word (``youtube``
#: -> ``youtube.com``) from spelling. Here the spelling gives nothing to work with
#: -- there is no rule that turns "yt music" into ``music.youtube.com`` -- so the
#: only honest options are a short, curated map or a failed request. Keep it small
#: and only add a name whose destination is unambiguous.
_SITE_ALIASES = {
    "yt music": "https://music.youtube.com",
    "youtube music": "https://music.youtube.com",
    "yt": "https://youtube.com",
    "youtube": "https://youtube.com",
    "gmail": "https://mail.google.com",
    "google mail": "https://mail.google.com",
    "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "google docs": "https://docs.google.com",
    "google calendar": "https://calendar.google.com",
    "google photos": "https://photos.google.com",
    "google scholar": "https://scholar.google.com",
    "google translate": "https://translate.google.com",
    "github": "https://github.com",
    "github gist": "https://gist.github.com",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "reddit": "https://reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "facebook": "https://facebook.com",
    "instagram": "https://instagram.com",
    "whatsapp web": "https://web.whatsapp.com",
    "linkedin": "https://linkedin.com",
    "netflix": "https://netflix.com",
    "spotify": "https://open.spotify.com",
    "spotify web": "https://open.spotify.com",
    "amazon": "https://amazon.com",
    "wikipedia": "https://wikipedia.org",
    "chatgpt": "https://chatgpt.com",
    "openai": "https://openai.com",
    "hugging face": "https://huggingface.co",
    "huggingface": "https://huggingface.co",
    "twitch": "https://twitch.tv",
    "discord": "https://discord.com",
    "telegram web": "https://web.telegram.org",
    "gmail inbox": "https://mail.google.com",
    "youtube studio": "https://studio.youtube.com",
    "prime video": "https://primevideo.com",
    "hotstar": "https://hotstar.com",
    "flipkart": "https://flipkart.com",
}


def _alias(target: str) -> str | None:
    """The known website for a spoken name, or ``None`` if there is none.

    Matching is on the lowercased, whitespace-collapsed phrase: "YT Music",
    "yt  music" and "yt music" are the same request.
    """
    key = " ".join((target or "").lower().split())
    return _SITE_ALIASES.get(key)


_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://")
#: The only schemes Alan will hand to Windows or fetch over the network.
#:
#: Anything with an explicit scheme used to be treated as a web address, which
#: quietly made this an outbound-request primitive for every protocol Windows
#: understands. ``file://`` mattered most: it skipped the extension allow-list
#: that an explicit ``C:\...`` path has to pass, so the one spelling of a local
#: path with no restrictions on it was a URL. ``smb://`` was worse -- it makes
#: Windows connect to a remote host, turning "open a website" into an outbound
#: connection to wherever the string pointed.
#:
#: Refusing by name keeps the reason specific and keeps the rule narrow: this
#: opens websites and starts programs, and nothing else is in the capability.
_WEB_SCHEMES = frozenset({"http", "https"})
#: One bare DNS label: ``youtube``, ``code``, ``notepad``. No dots, so it can be
#: neither a filename nor a host that has already spelled itself out.
_SINGLE_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$", re.I)
#: Bare hostnames we accept as a URL: at least one dot, a plausible TLD, no
#: spaces. ``notepad`` has no dot and so cannot be mistaken for one, which is the
#: property that lets "open notepad" and "open youtube.com" share a verb.
_BARE_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$", re.I)
#: ``host:port`` with no path, e.g. ``localhost:11434``. Needs its own form
#: because a colon otherwise reads as a drive letter and the target looks like a
#: filename. A Windows path always has a backslash before anything else follows
#: the colon, so this cannot swallow ``C:\...``.
_HOST_PORT = re.compile(r"^(?:[a-z0-9-]+\.)*[a-z0-9-]+:\d{1,5}$", re.I)

#: Extensions that make a dotted name a *file*, never a website. The dotted-name
#: rule above is unavoidably ambiguous -- ``notes.txt`` and ``setup.exe`` are both
#: shaped exactly like ``example.com`` -- and resolving that ambiguity the wrong
#: way means "open setup.exe" opens a browser pointed at a non-existent domain
#: instead of the program the caller named. Only *unambiguous* file spellings are
#: listed here, so ``example.co.uk`` is unaffected.
#:
#: Deliberately absent: ``.com``. It is a genuine top-level domain *and* a legacy
#: Windows executable extension, so it cannot be excluded without breaking
#: "open youtube.com", which is the most ordinary request there is. It is safe
#: to leave out because :func:`resolve` decides by existence first -- a real
#: ``game.com`` on disk is found as a file and then refused by the extension
#: allow-list, while ``youtube.com`` resolves as the website it is. A wrong
#: reading costs a browser tab; the opposite error costs the actual feature.
_AMBIGUOUS_SUFFIXES = {
    # executables and installers
    ".exe", ".bat", ".cmd", ".msi", ".lnk", ".scr", ".pif", ".cpl", ".dll", ".sys",
    ".drv", ".reg",
    # documents and data
    ".txt", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log",
    ".pdf", ".rtf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".ods", ".md", ".htm", ".html", ".py", ".js", ".ts", ".rb", ".go", ".rs",
    # archives, media and images
    ".zip", ".7z", ".rar", ".tar", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".svg", ".ico", ".webp", ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wav",
    # user and system data
    ".db", ".sqlite", ".bak", ".tmp",
}


def looks_like_url(target: str) -> bool:
    """True when ``target`` is a website rather than an application name.

    ``open-browser-use`` would answer this by pattern-matching against a list of
    known browser names, which breaks the moment a program is renamed. Deciding
    "this looks like a web address" is a question about spelling, so this answers
    it by spelling.
    """
    text = (target or "").strip()
    if not text or any(c.isspace() for c in text):
        return False
    scheme = _SCHEME.match(text)
    if scheme:
        # Only a web address is a URL here. ``file://`` and ``smb://`` name a
        # protocol rather than a website, and this function is what decides what
        # gets handed to a browser or to Windows.
        return scheme.group(1).lower() in _WEB_SCHEMES
    if Path(text).suffix.lower() in _AMBIGUOUS_SUFFIXES:
        return False
    return bool(_BARE_HOST.match(text) or _HOST_PORT.match(text))


def normalize_url(target: str) -> str:
    """Coerce a loosely-typed URL into something fetchable.

    People and small models write ``youtube.com``, ``www.youtube.com`` and
    ``HTTP://YouTube.com``. All three mean the same thing to a person and all
    three are rejected or mangled by a strict parser, so be forgiving here rather
    than making the user learn the protocol.
    """
    text = (target or "").strip().strip("\"'").rstrip(".,;:!?")
    if not text:
        return text
    if _SCHEME.match(text):
        return text
    if _HOST_PORT.match(text):
        # A bare port means a local server, and local servers are almost never
        # behind a certificate. Defaulting to https here would turn the single
        # most common development request into a failed connection.
        return "http://" + text
    if _BARE_HOST.match(text):
        return "https://" + text
    return text


def _app_paths(name: str) -> str | None:
    """Ask Windows where an installed program lives.

    This is the system's own mapping and covers anything installed with an
    installer, which is the overwhelming majority of desktop applications.
    """
    if not IS_WINDOWS:
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - not reachable on Windows
        return None

    # App Paths keys are conventionally named after the executable.
    candidates = [f"{name}.exe", name] if not name.lower().endswith(".exe") else [name]
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, getattr(winreg, "KEY_WOW64_64KEY", 0)):
            for candidate in candidates:
                try:
                    with winreg.OpenKey(hive, f"{sub}\\{candidate}", 0, winreg.KEY_READ | view) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                except OSError:
                    continue
                if not value:
                    continue
                # The value may carry arguments; take only the executable.
                quoted = re.match(r'\s*"([^"]+)"', value)
                path = quoted.group(1) if quoted else value.split(" ", 1)[0]
                if path and Path(path).is_file():
                    return path
    return None


def _start_menu(name: str) -> str | None:
    """Find a Start Menu shortcut by name.

    This is what the user actually clicks, so matching it means "open spotify"
    works for however Spotify chose to register itself.

    Matching is deliberately strict. A substring search looks tempting and is
    actively harmful: on a machine with SQL Server and a Photoshop codec
    installed it resolves ``server`` to *SQL Server Management Studio*, ``code``
    to *PSD Codec User Guide* and ``word`` to *Wordpad*. Opening the wrong
    program in response to a correct request is worse than reporting nothing.
    Two forms are accepted, and they are the two Windows actually uses:

    * the whole shortcut name -- "Notepad", "Spotify";
    * the *final* word of it -- "chrome" for "Google Chrome", "code" for
      "Visual Studio Code". Requiring the final word is what keeps "server" from
      matching "SQL Server Management Studio", whose last word is "Studio".
    """
    if not IS_WINDOWS:
        return None
    roots = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("AppData", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    wanted = name.strip().lower()
    if not wanted:
        return None

    matches: list[tuple[int, int, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for entry in root.rglob("*.lnk"):
                stem = entry.stem.lower()
                if stem == wanted:
                    score = 0
                elif len(wanted) >= 3 and stem.endswith(" " + wanted):
                    score = 1
                else:
                    continue
                # Shallower shortcuts win: a top-level entry is the one the user
                # actually clicks, and a nested match is usually a component of
                # something else.
                matches.append((score, len(entry.parts), entry))
        except OSError:
            continue
        if matches:
            break
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return str(matches[0][2])


def _which(name: str) -> str | None:
    found = shutil.which(name)
    return found or (shutil.which(f"{name}.exe") if not name.lower().endswith(".exe") else None)


def _validate(raw: str) -> str:
    """Reject anything that should not reach the launcher, and say why."""
    target = (raw or "").strip().strip("\"'")
    if not target:
        raise ValueError("no application or website given")
    if len(target) > MAX_TARGET:
        raise ValueError(f"target is longer than {MAX_TARGET} characters; that is a command line, not an application")
    bad = sorted(_METACHARS & set(target))
    if bad:
        raise ValueError(
            "refusing characters that a command shell would interpret: "
            + " ".join(bad)
            + ". This opens a program; it does not run a command line."
        )
    if any(ord(c) < 32 for c in target):
        raise ValueError("refusing control characters in the target")
    return target


def resolve(raw: str) -> dict[str, Any]:
    """Work out what ``raw`` refers to. Never launches anything."""
    target = _validate(raw)

    # An explicit scheme settles it: nothing that names a protocol is a filename,
    # and this has to be tested before the path branch below, whose "contains a
    # separator" rule matches the "//" in every https URL.
    scheme = _SCHEME.match(target)
    if scheme:
        # Only web schemes are accepted -- see _WEB_SCHEMES for why the others
        # are refused by name rather than forwarded to Windows.
        if scheme.group(1).lower() not in _WEB_SCHEMES:
            raise ValueError(
                f"refusing to open a '{scheme.group(1).lower()}:' address. This "
                f"opens websites and starts applications. Give an http or https "
                f"address, or the name of a program."
            )
        return {"kind": "url", "target": target, "path": None}

    # An explicit path, or a bare name that is a real file where we stand, is
    # taken at face value. Existence is checked before the URL reading so that a
    # file called ``example.com`` in the working directory resolves to the file:
    # a caller who named something that exists meant that thing.
    #
    # A colon only signals a path when it is a *drive* letter. Bare ``host:port``
    # has a colon too, and reading that as a filename produces a baffling
    # "no such file: localhost:3000".
    is_drive_letter = len(target) > 1 and target[1] == ":"
    looks_like_path = (
        os.sep in target or (os.altsep or "") in target or is_drive_letter
    )
    if not looks_like_path:
        try:
            looks_like_path = Path(target).is_file()
        except OSError:  # pragma: no cover - exotic path errors
            looks_like_path = False

    if looks_like_path:
        path = Path(target).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
            raise ValueError(
                f"{path.name} is a {path.suffix or 'file with no extension'}, which this "
                f"will not execute. Allowed: {allowed}"
            )
        return {"kind": "path", "target": str(path), "path": str(path)}

    if looks_like_url(target):
        return {"kind": "url", "target": normalize_url(target), "path": None}

    builtin = _BUILTINS.get(target.lower())
    if builtin:
        found = _which(builtin)
        if found:
            return {"kind": "builtin", "target": target, "path": found}

    for finder, kind in ((_app_paths, "installed"), (_start_menu, "shortcut"), (_which, "path")):
        found = finder(target)
        if found:
            return {"kind": kind, "target": target, "path": found}

    # No installed program answers to that name. For a *known* website that is the
    # expected case ("open yt music" means a site, not a program), so open it --
    # the alternative is the "could not find an application" menu a person should
    # never see for a request this ordinary. Placed after the app search so a real
    # installed application always wins, and before the bare-word guess so a
    # multi-word name like "yt music" -- which the guess cannot handle, since it
    # only strips spaces into a bogus ``yt music.com`` -- resolves to the site it
    # actually names.
    known_site = _alias(target)
    if known_site:
        return {
            "kind": "url",
            "target": known_site,
            "path": None,
            "assumed": True,
            "note": (
                f"{target!r} is a known website, opened as {known_site}. "
                f"Give the full address to be explicit."
            ),
        }

    # Nothing installed answers to that name, and the caller used a single word
    # with no dot. "open youtube" is the ordinary way to ask for a website, and
    # failing it with "no such application" makes a working request look broken.
    # So assume it was a site name.
    #
    # This is an assumption, and it is reported as one: the result carries
    # ``assumed=True`` so the caller can say "I could not find an application
    # called X, so I opened X.com" rather than pretending it was certain. The
    # alternative -- maintaining a list of well-known sites -- is the same
    # assumption with extra maintenance, a list that goes stale, and no way to
    # reach a site nobody thought to include.
    #
    # The single-label requirement is what keeps this to genuine guesses. Testing
    # only for "no spaces" let dotted filenames through, and ``notepad.txt``
    # became ``https://notepad.txt.com`` -- a domain that cannot exist, reached
    # only because nothing matched the name. A name with a dot in it has already
    # told us what it is: a file, or a host that spelled itself out. Neither is a
    # bare word, so neither gets a suffix bolted on.
    if _SINGLE_LABEL.match(target) and Path(target).suffix.lower() not in _AMBIGUOUS_SUFFIXES:
        guess = f"https://{target}.com"
        return {
            "kind": "url",
            "target": guess,
            "path": None,
            "assumed": True,
            "note": (
                f"no application called {target!r} is installed, so "
                f"{guess} was assumed. Give the full address to be explicit."
            ),
        }

    raise FileNotFoundError(
        f"could not find an application called {target!r}, and it is not a known "
        "website. If the user wants something opened on the web, do not ask them "
        "how -- open the address for them with system.open, using the site's "
        "domain (for example 'example.com'). Ask only when you cannot tell which "
        "site they mean."
    )


#: Carried in every result so the caller cannot mistake "Windows accepted it" for
#: "it is on screen". Passed on to whichever agent asked, who would otherwise
#: report a window that was never shown.
_UNCONFIRMED_APP = (
    "Windows accepted the launch request. Alan does not watch for a window, so "
    "do not tell the user it is open -- say it has been started and may still be "
    "appearing."
)
_UNCONFIRMED_URL = (
    "Windows handed the address to the default browser. Alan cannot confirm the "
    "page loaded, so do not describe what it says."
)

def launch(raw: str) -> dict[str, Any]:
    """Open a website or start an application. Returns what it did.

    ``launched`` means Windows accepted the request, and nothing more. That is a
    narrower claim than "it is now open", and the difference is not academic:
    several Windows 11 applications ship as a stub executable that hands off to a
    packaged app, so ``startfile`` returns success and no window ever appears.
    Without the distinction below, the caller reported a successful open that had
    not happened -- telling the user a window was on screen when none was.
    """
    found = resolve(raw)
    target, path = found["target"], found["path"]

    if found["kind"] == "url":
        # ShellExecute knows how to open a URL; it is the resolver here.
        os.startfile(target)  # noqa: S606 - Windows-only, by design
        return {**found, "launched": True, "via": "default handler for the URL",
                "confirmed": _UNCONFIRMED_URL}

    if not IS_WINDOWS:  # pragma: no cover - not reachable on Windows
        raise RuntimeError("opening applications is only supported on Windows")

    os.startfile(path)  # noqa: S606 - validated path, no shell, no arguments
    return {**found, "launched": True, "via": found["kind"],
            "confirmed": _UNCONFIRMED_APP}
