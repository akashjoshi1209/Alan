"""The paint: shared styles, glyphs and formatters for Alan's terminal surface.

Deciding once that "green means ready" and "dim is context" is what keeps every
command looking like one product instead of a pile of prints. Nothing here
talks to a model, a file or a network -- it is presentation only, and safe to
import from anywhere (including modules that must start fast).

The glyphs are chosen to read *without* color first: every state differs in
shape (check, triangle, cross, circle), so a color-blind operator or a
monochrome console still sees the difference, and color only reinforces it.
"""

from __future__ import annotations

# -- glyphs -----------------------------------------------------------------
# Each state gets its own shape, not just its own hue.
OK = "✔"  # works right now
WARN = "▲"  # works, with a caveat
ERR = "✖"  # does not work
OFF = "○"  # switched off (not broken)
INFO = "•"  # a neutral fact

# -- styles -----------------------------------------------------------------
OK_STYLE = "green"
WARN_STYLE = "yellow"
ERR_STYLE = "red"
OFF_STYLE = "dim"
INFO_STYLE = "dim"

#: Who is speaking. The prompt pair (`you>` / the reply) is product identity;
#: these names exist so a restyle is one edit here, not twenty in cli.py.
USER = "bold green"
ASSISTANT = "bold cyan"

_STYLES = {
    "ok": (OK, OK_STYLE),
    "warn": (WARN, WARN_STYLE),
    "err": (ERR, ERR_STYLE),
    "off": (OFF, OFF_STYLE),
    "info": (INFO, INFO_STYLE),
}


def mark(state: str) -> str:
    """A glyph wired to its style, as Rich markup: ``mark("ok")`` -> ``"[green]✔[/green]"``.

    Unknown states degrade to the neutral info mark rather than raising: this
    is paint, and a typo here must never be why a command crashes.
    """
    glyph, style = _STYLES.get(state, _STYLES["info"])
    return f"[{style}]{glyph}[/{style}]"


def elapsed(seconds: float) -> str:
    """A reply-time footer: ``0.42s``, ``3.1s``, ``12.8s``, ``1m 05s``."""
    if seconds < 1:
        return f"{seconds:.2f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"
