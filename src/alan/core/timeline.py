"""Append-only local event timeline (Phase 2, roadmap section 4.4).

The log the rest of Phase 2 writes into. It is deliberately dumb about *how*
an event was observed -- sensing and screen understanding arrive later, behind
their own gates -- and strict about *what a log is*: rows are immutable, every
append is stamped, and the only way data leaves is an explicit, age-bounded
purge.

Immutability is enforced by the database rather than by convention. A log that
any future refactor can silently UPDATE is not an audit trail, and principle 3
of the roadmap ("audit everything") is worth nothing if the record can be
rewritten after the fact. ``UPDATE`` is blocked outright; ``DELETE`` is
reachable only through :meth:`TimelineStore.purge_older_than`, which is the
retention policy from section 4.3 made concrete -- explicit, bounded and
call it what it is, rather than a silent delete hiding behind a helper.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    app TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project);

-- Rows are immutable. Rewriting an observation after it was recorded is the
-- one thing an audit log must not permit, so the database refuses it even
-- from code that means well.
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'timeline events are append-only');
END;
"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class TimelineEvent:
    """One observation. Every field is optional but ``kind`` and ``ts``.

    ``entities`` and ``project`` stay empty until something can fill them in
    honestly: a timeline that invents a project name is worse than one that
    says nothing, because the digest is read as fact.
    """

    kind: str
    ts: str = ""
    app: str = ""
    title: str = ""
    url: str = ""
    summary: str = ""
    entities: tuple[str, ...] = field(default_factory=tuple)
    project: str = ""

    def to_row(self) -> tuple:
        return (
            self.ts or _now_iso(),
            self.kind,
            self.app,
            self.title,
            self.url,
            self.summary,
            ",".join(self.entities),
            self.project,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TimelineEvent":
        entities = tuple(e for e in (row["entities"] or "").split(",") if e)
        return cls(
            ts=row["ts"],
            kind=row["kind"],
            app=row["app"],
            title=row["title"],
            url=row["url"],
            summary=row["summary"],
            entities=entities,
            project=row["project"],
        )


class TimelineStore:
    """SQLite-backed append-only event log.

    One row per observation, ordered by insertion. ``ts`` is carried by the
    event (or filled at append time) rather than by a database default, so a
    backfilled event keeps the time it actually happened instead of the time
    it was written.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Cross-thread by design: the daemon's loop thread appends while a CLI
        # process reads, so the connection is explicitly not thread-bound.
        # ``check_same_thread=False`` alone would merely trade the exception
        # for a data race -- two threads inside one sqlite3 connection is
        # undefined -- so every method below takes this guard.
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._guard = threading.RLock()
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._db.commit()

    # -- writes -----------------------------------------------------------

    def append(self, event: TimelineEvent) -> int:
        """Append one event; returns its row id."""
        with self._guard:
            cur = self._db.execute(
                "INSERT INTO events (ts, kind, app, title, url, summary, entities, project) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                event.to_row(),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    def extend(self, events: Iterable[TimelineEvent]) -> int:
        """Append several events in one transaction; returns the count."""
        rows = [event.to_row() for event in events]
        if not rows:
            return 0
        with self._guard:
            self._db.executemany(
                "INSERT INTO events (ts, kind, app, title, url, summary, entities, project) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._db.commit()
        return len(rows)

    def purge_older_than(self, days: float) -> int:
        """Delete events older than ``days``. The only deletion path.

        Bounded by age on purpose: section 4.3 asks for short raw retention
        with derived summaries kept longer, and a purge that cannot express
        "older than" is one that eventually gets called with no bound at all.
        """
        cutoff = (
            datetime.now().astimezone() - timedelta(days=float(days))
        ).isoformat(timespec="seconds")
        with self._guard:
            cur = self._db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._db.commit()
        return int(cur.rowcount or 0)

    # -- reads ------------------------------------------------------------

    def count(self) -> int:
        with self._guard:
            row = self._db.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"])

    def recent(self, limit: int = 50, *, kind: str = "") -> list[TimelineEvent]:
        """Newest-first events, optionally of one kind."""
        with self._guard:
            if kind:
                rows = self._db.execute(
                    "SELECT * FROM events WHERE kind = ? ORDER BY id DESC LIMIT ?",
                    (kind, int(limit)),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
        return [TimelineEvent.from_row(row) for row in rows]

    def days(self) -> list[str]:
        """Distinct calendar days present, newest first."""
        with self._guard:
            rows = self._db.execute(
                "SELECT DISTINCT substr(ts, 1, 10) AS day FROM events ORDER BY day DESC"
            ).fetchall()
        return [row["day"] for row in rows]

    def digest(self, day: Optional[str] = None) -> "Digest":
        """Summarise one day: what was touched, and for how long.

        "How long" is counted as *spans*, not minutes: events are sparse
        observations, so the honest reading of "worked on X for a while" is
        "X appears across N observations spanning this much wall clock". A
        minute count would be arithmetic on data that was never sampled
        densely enough to support it.
        """
        with self._guard:
            if day is None:
                available = self.days()
                if not available:
                    return Digest(day="", groups=(), span="", total=0)
                day = available[0]

            rows = self._db.execute(
                "SELECT * FROM events WHERE substr(ts, 1, 10) = ? ORDER BY ts ASC",
                (day,),
            ).fetchall()
        events = [TimelineEvent.from_row(row) for row in rows]

        by_subject: dict[str, list[TimelineEvent]] = defaultdict(list)
        for event in events:
            # A project, when one is known; otherwise the app. Naming the app
            # is a fact, naming a project would be a guess.
            by_subject[event.project or event.app or event.kind].append(event)

        groups: list[DigestGroup] = []
        for subject, group in sorted(
            by_subject.items(), key=lambda item: (-len(item[1]), item[0])
        ):
            first, last = group[0].ts, group[-1].ts
            groups.append(
                DigestGroup(
                    subject=subject,
                    observations=len(group),
                    first=first,
                    last=last,
                    kinds=tuple(sorted({e.kind for e in group})),
                    titles=tuple(
                        dict.fromkeys(e.title for e in group if e.title)
                    ),
                )
            )

        span = ""
        if events:
            span = _span_between(events[0].ts, events[-1].ts)
        return Digest(day=day, groups=tuple(groups), span=span, total=len(events))

    def close(self) -> None:
        try:
            with self._guard:
                self._db.close()
        except Exception:  # noqa: BLE001 - closing must not mask a real error
            pass


def _span_between(start: str, end: str) -> str:
    """Human span between two ISO timestamps, or "" if unparseable."""
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
    except ValueError:
        return ""
    seconds = int((b - a).total_seconds())
    if seconds < 0:
        return ""
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


@dataclass(frozen=True)
class DigestGroup:
    subject: str
    observations: int
    first: str
    last: str
    kinds: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Digest:
    day: str
    groups: tuple[DigestGroup, ...]
    span: str
    total: int

    @property
    def empty(self) -> bool:
        return self.total == 0

    def render(self) -> str:
        if self.empty:
            return f"{self.day or 'today'}: nothing recorded"
        lines = [f"{self.day} - {self.total} observation(s), active {self.span}"]
        for group in self.groups:
            lines.append(
                f"  {group.subject}: {group.observations} observation(s) "
                f"[{', '.join(group.kinds)}]"
            )
            for title in group.titles[:3]:
                lines.append(f"      - {title}")
        return "\n".join(lines)
