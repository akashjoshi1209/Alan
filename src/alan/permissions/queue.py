"""The approval queue: permission requests that could not be answered inline.

Interactive chat answers a confirmation on the spot -- the console prompt is the
confirmer. A scheduled task has no one watching the console, and the two failure
modes both have to be avoided:

* **fail silently** -- the unattended agent just does the risky thing, which is
  how Grok Bot's runaway re-apply loop happened;
* **forget** -- the approval is dropped when a card expires or the process
  restarts, and the task retries forever with nobody ever seeing the prompt.

The queue takes the middle path. A request that needs a human under an
unattended session becomes a *record*: it sits in ``pending()``, the task that
needed it stops and says so (``waiting_approval``), and nothing proceeds until
somebody decides. Decisions are per-request fingerprints:

* **approve** is remembered -- the next run of the same task, hitting the same
  action, passes without asking again. That is what makes an approval useful
  rather than a one-off interrupt.
* **reject** is *not* remembered as a permanent veto; instead the queue's
  ``on_decide`` callback pauses the task. Re-running it is an explicit operator
  action (``/schedule resume``), so a rejection can never turn into either a
  silent veto forever or a nagging retry loop.

Nothing here writes to disk by itself: approvals are live state of a running
process, while *tasks* persist in the schedule store. A restart loses pending
approvals, and the task's own recorded state (``waiting_approval`` in
``schedule.json``) is what surfaces the fact after the restart.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..core.events import EventBus, Events
from ..interfaces.types import PermissionRequest, new_id, utcnow

#: How long a run's summary of a pending approval stays in history.
HISTORY_LIMIT = 100


def fingerprint(request: PermissionRequest) -> str:
    """Stable identity of *the action being asked about*.

    Same capability + same arguments (order-insensitive) -> same fingerprint, so
    re-running a task that wants to do the exact same thing matches the
    approval it already got. Arguments are redacted before hashing the same way
    everywhere else, so the queue never keeps a secret the rest of Alan drops.
    """
    try:
        args = json.dumps(request.arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        args = repr(sorted(request.arguments.items()))
    material = f"{request.capability}\n{args}"
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class Approval:
    """One pending or decided permission request."""

    id: str
    capability: str
    risk: str
    agent: str
    reason: str
    arguments: Dict[str, Any]
    source: str = "scheduler"
    task_id: str = ""
    session_id: str = ""
    fingerprint: str = ""
    status: str = "pending"  # pending | approved | rejected
    created_at: str = ""
    decided_at: str = ""

    def summary(self) -> str:
        """One line for /activity."""
        mark = {"pending": "?", "approved": "+", "rejected": "-"}[self.status]
        origin = f"{self.source}:{self.task_id}" if self.task_id else self.source
        return (
            f"[{mark}] {self.id} {self.capability} ({self.risk}) "
            f"from {origin} -- {self.reason or 'needs your approval'}"
        )


class ApprovalQueue:
    """Thread-safe store of unattended permission requests.

    ``on_decide`` is wired by the runtime to the scheduler, so approving or
    rejecting a request immediately re-queues (or pauses) the task that asked.
    """

    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        on_decide: Optional[Callable[[Approval], None]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._by_id: Dict[str, Approval] = {}
        self._order: List[str] = []
        self._history: List[Approval] = []
        #: fingerprint -> Approval for every *approved* request. Approvals are
        #: cached; rejections deliberately are not (see module docstring).
        self._approved: Dict[str, Approval] = {}
        self._bus = bus
        self._on_decide = on_decide

    def set_on_decide(self, callback: Optional[Callable[[Approval], None]]) -> None:
        """Wire the reaction to a decision (the scheduler attaches late)."""
        self._on_decide = callback

    # -- submission -------------------------------------------------------

    def confirmer(self, request: PermissionRequest, *, task_id: str = "") -> bool:
        """A confirmer for unattended sessions: remember, then decline.

        Returns ``True`` only when this exact action was already approved.
        Otherwise it files an Approval (deduplicated if one is already pending
        for the same fingerprint) and returns ``False`` so the caller stops at
        the gate -- the task records ``waiting_approval`` and surfaces it.
        """
        fp = fingerprint(request)
        with self._lock:
            cached = self._approved.get(fp)
        if cached is not None:
            return True

        approval = self.submit(request, source="scheduler", task_id=task_id)
        if approval.status == "approved":  # approved between check and submit
            return True
        return False

    def submit(
        self,
        request: PermissionRequest,
        *,
        source: str = "scheduler",
        task_id: str = "",
    ) -> Approval:
        """File a request; dedupe an identical pending one."""
        fp = fingerprint(request)
        with self._lock:
            for approval in self._pending_list():
                if approval.fingerprint == fp:
                    return approval
            approval = Approval(
                id=new_id("apr_"),
                capability=request.capability,
                risk=str(getattr(request.risk, "name", request.risk)),
                agent=request.agent,
                reason=request.reason,
                arguments=dict(request.arguments),
                source=source,
                task_id=task_id,
                session_id=request.session_id,
                fingerprint=fp,
                status="pending",
                created_at=utcnow().isoformat(),
            )
            self._by_id[approval.id] = approval
            self._order.append(approval.id)
        self._emit(Events.APPROVAL_SUBMITTED, approval)
        return approval

    # -- decisions --------------------------------------------------------

    def decide(self, approval_id: str, approve: bool) -> Optional[Approval]:
        """Approve or reject a pending request. ``None`` if unknown/decided."""
        with self._lock:
            approval = self._by_id.get(approval_id)
            if approval is None or approval.status != "pending":
                return None
            approval.status = "approved" if approve else "rejected"
            approval.decided_at = utcnow().isoformat()
            if approve:
                self._approved[approval.fingerprint] = approval
                # Anything else pending for the same action is settled too --
                # approving one is approving the identical request.
                for twin in self._pending_list():
                    if twin.fingerprint == approval.fingerprint:
                        twin.status = "approved"
                        twin.decided_at = approval.decided_at
                        self._retire(twin)
                        self._approved[twin.fingerprint] = twin
            self._retire(approval)
        self._emit(Events.APPROVAL_DECIDED, approval)
        if self._on_decide is not None:
            try:
                self._on_decide(approval)
            except Exception:  # pragma: no cover - a callback must not kill decide
                pass
        return approval

    def decide_by_task(self, task_id: str, approve: bool) -> Optional[Approval]:
        """Decide the newest pending approval raised by ``task_id``."""
        with self._lock:
            for approval_id in reversed(self._order):
                approval = self._by_id.get(approval_id)
                if (
                    approval is not None
                    and approval.task_id == task_id
                    and approval.status == "pending"
                ):
                    target = approval.id
                    break
            else:
                return None
        return self.decide(target, approve)

    # -- views ------------------------------------------------------------

    def pending(self) -> List[Approval]:
        """Pending approvals, oldest first."""
        with self._lock:
            return list(self._pending_list())

    def pending_for(self, task_id: str) -> List[Approval]:
        """Pending approvals raised by one task."""
        with self._lock:
            return [a for a in self._pending_list() if a.task_id == task_id]

    def get(self, approval_id: str) -> Optional[Approval]:
        with self._lock:
            return self._by_id.get(approval_id)

    def history(self, limit: int = 20) -> List[Approval]:
        """Most recent decided approvals, newest first."""
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def clear(self) -> int:
        """Drop every decided approval (pending ones stay). Returns count."""
        with self._lock:
            dropped = len(self._history)
            self._history.clear()
            return dropped

    # -- internals --------------------------------------------------------

    def _pending_list(self) -> List[Approval]:
        """Pending approvals in order. Caller must hold the lock."""
        return [self._by_id[i] for i in self._order if self._by_id[i].status == "pending"]

    def _retire(self, approval: Approval) -> None:
        """Move a decided approval out of the pending order. Lock held."""
        if approval.id in self._order:
            self._order.remove(approval.id)
        self._history.append(approval)
        if len(self._history) > HISTORY_LIMIT:
            del self._history[: len(self._history) - HISTORY_LIMIT]

    def _emit(self, name: str, approval: Approval) -> None:
        if self._bus is None:
            return
        try:
            self._bus.emit(
                name,
                approval_id=approval.id,
                capability=approval.capability,
                risk=approval.risk,
                task_id=approval.task_id,
                status=approval.status,
                source=approval.source,
            )
        except Exception:  # pragma: no cover - an event must not break a decision
            pass
