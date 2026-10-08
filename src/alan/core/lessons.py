"""What Alan remembers about attempts that did not work.

Alan used to report a failure and forget it. The same request asked again took
the same route, met the same obstacle and failed the same way -- the only thing
that changed was how quickly it got there. This module is the record that
outlives a run, so the second attempt starts knowing about the first.

Three rules keep it useful rather than noisy:

* **A lesson is *open* only while the task is still failing.** ``failures >
  successes`` is the test. Once the same task has succeeded, the hint stops
  being injected: the store still records what happened, but it does not
  lecture about a failure that has since been beaten.
* **A lesson carries what was tried, not only what broke.** "This failed" tells
  the next attempt nothing it can act on, so the remedy Alan hands itself is
  the approach that eventually worked (``winner``), and until there is one the
  list of approaches already burned (``tried``) is what it gets.
* **Matching is by content words, not by string.** Two phrasings of the same
  job share their content words; a different job does not. Every hint quotes
  the task it came from, so a close-but-not-identical match is visible to
  whoever reads it instead of being applied silently.

A lesson also names the stage that broke (``run``, ``step:vibecoder_delegate``)
because the reason decides the next move: a missing tool and a downed model call
for opposite fixes.

The file is a memory, never a dependency. Every entrypoint catches, and a store
that cannot be read or written degrades to "Alan forgets", which is where this
started.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA_DIR
from ..interfaces.types import redact

#: Where the record lives. Module level so a test can repoint it, the same way
#: the probe cache does -- an integration test must not have to fight the
#: operator's real history to assert what a fresh one looks like.
_STATE_PATH = DATA_DIR / "state" / "lessons.json"

#: Words that carry no meaning when matching one task against another. Without
#: this, "fix the bug in the parser" and "fix the bug in the renderer" score
#: 1.0 on the shared scaffolding alone and every failure looks like every other.
_STOP = frozenset(
    """
    a an the and or but if then else for to of in on at by with from as is are
    was were be been being it its this that these those i you he she we they me
    my your our their do does did done doing have has had having will would can
    could should shall may might must not no yes so than too very just about
    into over under again more most other such only own same all any both each
    few nor here there when where what which who whom why how than then also
    get got make made just please need needs want try tried use using used
    """.split()
)

#: Content words only: letters and digits, lowercased. Underscores are kept so
#: ``read_file`` stays a phrase rather than splitting into two lookalikes.
_WORD = re.compile(r"[a-z0-9_]+")

#: Longest signature kept. Past this a request is mostly boilerplate, and a
#: longer set only dilutes the overlap that does the matching.
MAX_SIGNATURE_TOKENS = 24

#: How alike two token sets must be before a lesson counts as the same task.
#: The overlap coefficient (shared / smaller set) rather than a Jaccard ratio,
#: so "fix bug" still finds the lesson for "fix the bug in the parser and add
#: tests for it" -- the short form is the same job, said shortly.
MATCH_THRESHOLD = 0.6

#: Ceilings. The store is capped so it cannot grow without bound, and a single
#: lesson keeps only a few approaches so one catastrophic task cannot become a
#: transcript.
MAX_LESSONS = 200
MAX_TRIED = 4

#: How long an in-process copy of the file may be reused. One request reads the
#: store at least twice -- once for the hint, once to record the outcome -- and
#: re-reading a small JSON file between them buys nothing.
_MEMO_TTL = 5.0

#: How much of one rendered hint may reach a prompt. Hints are context, not the
#: point; a runaway store must not push the user's own request off the window.
MAX_HINT_CHARS = 700


def tokens(text: str) -> set[str]:
    """Content words of ``text``, lowercased, stopwords removed."""
    return {
        word
        for word in _WORD.findall((text or "").lower())
        if len(word) > 1 and word not in _STOP
    }


def signature(text: str) -> str:
    """Order-independent identity for a task.

    Sorted so "fix the login bug" and "login bug, fix it" are the same lesson,
    and capped so a pasted log cannot swamp the words that actually name the
    job.
    """
    kept = sorted(tokens(text))[:MAX_SIGNATURE_TOKENS]
    return " ".join(kept)


def similarity(left: str, right: str) -> float:
    """Overlap of two signatures in ``[0, 1]``.

    The overlap coefficient divides by the *smaller* set, not the union: a
    terse retry is the same task as the long request it came from, and a
    Jaccard ratio would score that pair low purely because one side is shorter.
    """
    a, b = set(left.split()), set(right.split())
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _squeeze(text: str) -> str:
    """One line, no matter what a backend put in the error."""
    return " ".join((text or "").split())


@dataclass
class Lesson:
    """One task's history of attempts."""

    #: Sorted content words; the identity everything else is keyed by.
    signature: str
    #: The request as it was first asked, kept so a hint can quote itself back.
    task: str = ""
    #: Outright failures plus runs where a required capability did not deliver.
    failures: int = 0
    #: Runs where every planned step delivered.
    successes: int = 0
    #: Why it last failed, one line.
    last_cause: str = ""
    #: ``run`` | ``step:<action>`` | ``agent:<name>``.
    last_stage: str = ""
    #: Distinct approaches that ended in failure, oldest first.
    tried: list[str] = field(default_factory=list)
    #: The approach that succeeded, and the model that produced it. Empty
    #: until something does -- which is exactly what makes it a remedy.
    winner: str = ""
    winner_model: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def open(self) -> bool:
        """Still worth warning about: more failures than successes."""
        return self.failures > self.successes

    @property
    def account(self) -> str:
        """What was being attempted, for display and for the hint."""
        if self.tried:
            return ", ".join(self.tried)
        return self.last_stage or "the same approach"

    def to_dict(self) -> dict:
        return {
            "signature": self.signature,
            "task": self.task,
            "failures": self.failures,
            "successes": self.successes,
            "last_cause": self.last_cause,
            "last_stage": self.last_stage,
            "tried": list(self.tried),
            "winner": self.winner,
            "winner_model": self.winner_model,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: object) -> Lesson | None:
        """Rebuild a lesson, or ``None`` if the record is unusable.

        A hand-edited or truncated file must not take the store down: one bad
        row costs one forgotten lesson, a raised error costs every future one.
        """
        if not isinstance(data, dict):
            return None
        sig = data.get("signature")
        if not isinstance(sig, str) or not sig:
            return None
        raw_tried = data.get("tried")
        tried = [t for t in raw_tried if isinstance(t, str)] if isinstance(raw_tried, list) else []
        return cls(
            signature=sig,
            task=_squeeze(str(data.get("task") or ""))[:240],
            failures=_int(data.get("failures")),
            successes=_int(data.get("successes")),
            last_cause=_squeeze(str(data.get("last_cause") or ""))[:400],
            last_stage=_squeeze(str(data.get("last_stage") or ""))[:80],
            tried=[_squeeze(t)[:80] for t in tried][:MAX_TRIED],
            winner=_squeeze(str(data.get("winner") or ""))[:160],
            winner_model=_squeeze(str(data.get("winner_model") or ""))[:80],
            first_seen=_float(data.get("first_seen")),
            last_seen=_float(data.get("last_seen")),
        )


def _int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return out if out > 0 else 0.0


def _age(row: object) -> float:
    """``last_seen`` of a stored row, tolerating a row that is not a dict."""
    return _float(row.get("last_seen")) if isinstance(row, dict) else 0.0


def render_hint(lessons: list[Lesson], *, max_chars: int = MAX_HINT_CHARS) -> str:
    """The block handed to the planner and the model.

    Every line quotes the task it belongs to. That matters because matching is
    approximate: a hint about one bug shown next to a request about another
    must be readable as *a hint about that other bug*, not as an instruction
    about this one.
    """
    if not lessons:
        return ""
    lines = ["Lessons from earlier attempts at the same task:"]
    for lesson in lessons:
        task = lesson.task or lesson.signature
        reason = lesson.last_cause or "no reason was recorded"
        if lesson.winner:
            using = f" using {lesson.winner_model}" if lesson.winner_model else ""
            lines.append(
                f'- "{task}" failed {lesson.failures} time(s) ({reason}). '
                f"It later worked with: {lesson.winner}{using}. Prefer that approach."
            )
        else:
            lines.append(
                f'- "{task}" failed {lesson.failures} time(s). Reason: {reason}. '
                f"Already attempted: {lesson.account}. Do not repeat that unchanged "
                "-- establish what is different before trying again."
            )
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 4)].rstrip() + " ..."


class LessonStore:
    """Durable, self-limiting record of what has and has not worked.

    Every method swallows storage trouble. A store that cannot be written is a
    store that remembers nothing, and remembering nothing is an acceptable
    outcome -- crashing the assistant that asked is not.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_lessons: int = MAX_LESSONS,
    ) -> None:
        self.path = Path(path) if path else _STATE_PATH
        self.max_lessons = max_lessons
        self._memo: dict[str, dict] = {}
        self._memo_path: str = ""
        self._memo_at: float = 0.0

    # -- persistence ------------------------------------------------------
    def _load(self) -> dict[str, dict]:
        now = time.time()
        key = str(self.path)
        if key == self._memo_path and now - self._memo_at < _MEMO_TTL:
            return self._memo
        empty: dict[str, dict] = {}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # Missing file is the normal first-run case, not an error.
            self._memo, self._memo_path, self._memo_at = empty, key, now
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            # A truncated file loses the history; it never loses the run.
            self._memo, self._memo_path, self._memo_at = empty, key, now
            return {}
        data = parsed.get("lessons") if isinstance(parsed, dict) else None
        data = data if isinstance(data, dict) else {}
        self._memo, self._memo_path, self._memo_at = data, key, now
        return data

    def _save(self, data: dict[str, dict]) -> None:
        from .atomic import write_atomic

        if len(data) > self.max_lessons:
            order = sorted(data.items(), key=lambda item: _age(item[1]))
            data = dict(order[-self.max_lessons:])
        # Memo first: even a read-only home leaves this process working.
        self._memo, self._memo_path, self._memo_at = data, str(self.path), time.time()
        try:
            write_atomic(
                self.path,
                json.dumps({"version": 1, "lessons": data}, indent=1, sort_keys=True),
            )
        except (OSError, TypeError, ValueError):
            return

    # -- reading ----------------------------------------------------------
    def lessons(self) -> list[Lesson]:
        """Every recorded lesson, most recently touched first."""
        out: list[Lesson] = []
        for raw in self._load().values():
            lesson = Lesson.from_dict(raw)
            if lesson is not None:
                out.append(lesson)
        out.sort(key=lambda item: item.last_seen, reverse=True)
        return out

    def __len__(self) -> int:
        return len(self._load())

    def recall(self, request: str, *, top_k: int = 2) -> list[Lesson]:
        """Open lessons about a task like ``request``, most relevant first.

        Closed lessons are excluded by construction: a failure Alan has since
        beaten is history, not advice, and repeating it would teach the model
        to ignore these lines.
        """
        if not top_k:
            return []
        probe = signature(request)
        if not probe:
            return []
        matched: list[tuple[float, Lesson]] = []
        for raw in self._load().values():
            lesson = Lesson.from_dict(raw)
            if lesson is None or not lesson.open:
                continue
            score = similarity(probe, lesson.signature)
            if score >= MATCH_THRESHOLD:
                matched.append((score, lesson))
        # Relevance first, then how badly it is going: the lesson worth hearing
        # about is the one that is both close and still not working out.
        matched.sort(
            key=lambda pair: (
                pair[0],
                pair[1].failures - pair[1].successes,
                pair[1].last_seen,
            ),
            reverse=True,
        )
        return [lesson for _, lesson in matched[:top_k]]

    def hint(self, request: str, *, top_k: int = 2) -> str:
        """:func:`render_hint` of everything :meth:`recall` finds."""
        return render_hint(self.recall(request, top_k=top_k))

    # -- writing ----------------------------------------------------------
    def _existing(self, data: dict[str, dict], request: str, now: float) -> Lesson:
        sig = signature(request) or _squeeze(request)[:160]
        lesson = Lesson.from_dict(data.get(sig))
        if lesson is None:
            lesson = Lesson(
                signature=sig,
                task=_squeeze(request)[:240],
                first_seen=now,
                last_seen=now,
            )
        return lesson

    def note_failure(
        self,
        request: str,
        *,
        stage: str,
        cause: str,
        actions: tuple[str, ...] | list[str] = (),
    ) -> Lesson:
        """Record that ``request`` did not come off, and why.

        ``cause`` is redacted on the way in. The reason text comes from backend
        errors, which are exactly where a key or a header shows up, and this
        file sits next to the traces an operator shares.
        """
        now = time.time()
        data = self._load()
        lesson = self._existing(data, request, now)
        lesson.failures += 1
        lesson.last_cause = redact(_squeeze(cause))[:400]
        lesson.last_stage = _squeeze(stage)[:80]
        lesson.last_seen = now
        if not lesson.first_seen:
            lesson.first_seen = now
        attempt = " ".join(str(a) for a in actions if a) or lesson.last_stage
        if attempt and attempt not in lesson.tried:
            lesson.tried = (lesson.tried + [attempt[:80]])[-MAX_TRIED:]
        data[lesson.signature] = lesson.to_dict()
        self._save(data)
        return lesson

    def note_success(
        self,
        request: str,
        *,
        actions: tuple[str, ...] | list[str] = (),
        model: str = "",
    ) -> Lesson | None:
        """Credit a task that has failed before, and remember what worked.

        A task that has never failed is deliberately not written. The file
        exists to hold what did not work, and recording every successful turn
        would bury those entries under the trivial ones until the cap evicted
        them -- the store would be full of things Alan already knew.
        """
        now = time.time()
        data = self._load()
        sig = signature(request)
        existing = Lesson.from_dict(data.get(sig)) if sig else None
        if existing is None:
            return None
        existing.successes += 1
        existing.last_seen = now
        attempt = " ".join(str(a) for a in actions if a)
        if attempt:
            existing.winner = attempt[:160]
        if model:
            existing.winner_model = model[:80]
        data[sig] = existing.to_dict()
        self._save(data)
        return existing

    def clear(self) -> None:
        """Forget everything. ``alan lessons --clear``."""
        self._save({})


__all__ = [
    "Lesson",
    "LessonStore",
    "MAX_HINT_CHARS",
    "MAX_LESSONS",
    "MATCH_THRESHOLD",
    "render_hint",
    "signature",
    "similarity",
    "tokens",
]
