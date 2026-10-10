"""Tests for the append-only event timeline (Phase 2, section 4.4).

The log is the part of Phase 2 that must be trustworthy before anything is
allowed to write to it. These tests are therefore about *guarantees* rather
than features: a row cannot be rewritten, the only deletion is age-bounded,
and a digest reports what was recorded rather than what would be nice.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from alan.core.timeline import TimelineEvent, TimelineStore


def _iso(days_ago: float = 0.0) -> str:
    return (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat(
        timespec="seconds"
    )


class TimelineStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tl_")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "timeline.db"
        self.store = TimelineStore(self.path)
        self.addCleanup(self.store.close)

    def test_append_then_recent_returns_newest_first(self) -> None:
        self.store.append(TimelineEvent(kind="a", ts=_iso(2)))
        self.store.append(TimelineEvent(kind="b", ts=_iso(1)))
        self.store.append(TimelineEvent(kind="c", ts=_iso(0)))

        recent = self.store.recent(limit=10)
        self.assertEqual([e.kind for e in recent], ["c", "b", "a"])
        self.assertEqual(self.store.count(), 3)

    def test_every_field_round_trips(self) -> None:
        event = TimelineEvent(
            kind="window.focus",
            ts=_iso(),
            app="code",
            title="pricing.md - Alan",
            url="",
            summary="editing pricing notes",
            entities=("pricing", "stripe"),
            project="Alan",
        )
        self.store.append(event)

        got = self.store.recent(limit=1)[0]
        self.assertEqual(got.kind, "window.focus")
        self.assertEqual(got.app, "code")
        self.assertEqual(got.title, "pricing.md - Alan")
        self.assertEqual(got.summary, "editing pricing notes")
        self.assertEqual(got.entities, ("pricing", "stripe"))
        self.assertEqual(got.project, "Alan")

    def test_rows_are_immutable_at_the_database_level(self) -> None:
        """The guarantee, not the convention.

        A log any future refactor can UPDATE is not an audit trail. This is
        asserted against the database itself so a new write path cannot quietly
        reintroduce mutation.
        """
        row_id = self.store.append(TimelineEvent(kind="observed", ts=_iso()))

        with self.assertRaises(sqlite3.Error) as ctx:
            self.store._db.execute(
                "UPDATE events SET kind = ? WHERE id = ?", ("rewritten", row_id)
            )
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(self.store.recent(limit=1)[0].kind, "observed")

    def test_purge_is_the_only_deletion_path_and_is_age_bounded(self) -> None:
        old, fresh = _iso(30), _iso(1)
        self.store.append(TimelineEvent(kind="old", ts=old))
        self.store.append(TimelineEvent(kind="fresh", ts=fresh))

        purged = self.store.purge_older_than(14)
        self.assertEqual(purged, 1, "only the over-retention row goes")
        remaining = self.store.recent(limit=10)
        self.assertEqual([e.kind for e in remaining], ["fresh"])

    def test_purge_keeps_everything_when_nothing_is_expired(self) -> None:
        for i in range(3):
            self.store.append(TimelineEvent(kind=f"e{i}", ts=_iso(i)))
        self.assertEqual(self.store.purge_older_than(365), 0)
        self.assertEqual(self.store.count(), 3)

    def test_backfilled_events_keep_their_own_timestamp(self) -> None:
        """The time it happened, not the time it was written.

        Without this, importing yesterday's observations would stamp them all
        as today and the digest would report the wrong day entirely.
        """
        happened = _iso(1)
        self.store.append(TimelineEvent(kind="late", ts=happened))
        self.assertEqual(self.store.recent(limit=1)[0].ts, happened)

    def test_recent_can_filter_by_kind(self) -> None:
        self.store.append(TimelineEvent(kind="a", ts=_iso()))
        self.store.append(TimelineEvent(kind="b", ts=_iso()))
        self.assertEqual(len(self.store.recent(limit=10, kind="a")), 1)

    def test_extend_appends_in_one_batch(self) -> None:
        n = self.store.extend(
            TimelineEvent(kind=f"e{i}", ts=_iso()) for i in range(4)
        )
        self.assertEqual(n, 4)
        self.assertEqual(self.store.count(), 4)
        self.assertEqual(self.store.extend([]), 0)

    def test_days_lists_distinct_calendar_days_newest_first(self) -> None:
        self.store.append(TimelineEvent(kind="a", ts=_iso(2)))
        self.store.append(TimelineEvent(kind="b", ts=_iso(0)))
        self.store.append(TimelineEvent(kind="c", ts=_iso(0)))
        days = self.store.days()
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0], _iso(0)[:10])


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tl_digest_")
        self.addCleanup(self._tmp.cleanup)
        self.store = TimelineStore(Path(self._tmp.name) / "tl.db")
        self.addCleanup(self.store.close)

    def test_empty_store_digests_to_an_explicit_nothing(self) -> None:
        digest = self.store.digest()
        self.assertTrue(digest.empty)
        self.assertIn("nothing recorded", digest.render())

    def test_digest_prefers_the_project_over_the_app(self) -> None:
        """A project is a fact when known; an app name is only a fallback.

        Inventing a project name would make the digest read as more certain
        than the data supports, which is worse than naming the app.
        """
        base = _iso()
        for app, project in (("code", "Alan"), ("code", "Alan"), ("slack", "")):
            self.store.append(
                TimelineEvent(
                    kind="window.focus", ts=base, app=app, project=project
                )
            )

        digest = self.store.digest(day=base[:10])
        subjects = [g.subject for g in digest.groups]
        self.assertIn("Alan", subjects)
        self.assertIn("slack", subjects)

    def test_digest_counts_observations_and_reports_a_span(self) -> None:
        self.store.append(TimelineEvent(kind="k", ts=_iso()))
        self.store.append(TimelineEvent(kind="k", ts=_iso()))
        digest = self.store.digest()
        self.assertEqual(digest.total, 2)
        self.assertEqual(digest.groups[0].observations, 2)
        self.assertTrue(digest.span, "an active span should be reported")

    def test_digest_defaults_to_the_newest_day(self) -> None:
        self.store.append(TimelineEvent(kind="old", ts=_iso(2)))
        self.store.append(TimelineEvent(kind="new", ts=_iso()))
        digest = self.store.digest()
        self.assertEqual(digest.day, _iso()[:10])
        self.assertEqual(digest.total, 1, "only the newest day is summarised")

    def test_digest_groups_by_kind_when_there_is_no_app_or_project(self) -> None:
        self.store.append(TimelineEvent(kind="daemon.heartbeat", ts=_iso()))
        digest = self.store.digest()
        self.assertEqual(digest.groups[0].subject, "daemon.heartbeat")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
