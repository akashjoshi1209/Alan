"""The approval bridge: Alan's queue, an operator's phone (Phase 4).

A scheduled task that stops at the permission gate files an Approval and waits
for a human to decide. That human was always the console -- fine while the
console is open, which is not the point of an always-on assistant. This module
adds the second, reachable surface: Telegram.

It is a *long-poll* bridge, and deliberately not a webhook:

* ``getUpdates`` reaches out of Alan on a schedule; Telegram never dials back
  in. No webhook endpoint, no open port, no server to expose -- the stick
  keeps its offline-first posture.
* The only input that matters is a decision. ``/approve <id>`` and
  ``/reject <id>`` from the configured chat become the same calls the chat
  loop's ``/approve`` and ``/reject`` make, with the same id-suffix matching
  and the same replies (both route through ``decide_approval``).
* Fail-closed: ``chat_id`` in settings is the *only* chat that may decide.
  Every other sender is ignored entirely -- no action, no echo, no reply. A
  token that leaks to the wrong chat still leaks nothing but silence.

The token never appears in a message, a log line or a repr.

The bridge is built with the runtime but only *started* by a chat session, for
the same reason as the scheduler: one-shot commands must not spawn pollers.
Only a chat with a human watching is allowed to own a long-poll thread.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .core.events import Event, EventBus, Events

#: Telegram treats any poll longer than this as an error.
_MAX_LONG_POLL = 50.0
#: An extra network budget on top of the poll wait and message reads.
_HTTP_SLACK = 5.0
#: A poll that answered instantly was a fake or a dead connection, not a
#: long-poll; never spin faster than this even on an empty answer.
_MIN_CALL_GAP = 0.8

#: The bridge's reply to unknown input or /help. Kept short: Telegram shows it
#: as-is.
_HELP_TEXT = (
    "alan approvals\n"
    "/approve <id>  allow an action (a few trailing chars of the id are enough)\n"
    "/reject <id>   refuse it\n"
    "/activity      approvals + tasks\n"
    "only the configured chat may decide"
)


class TelegramAPIError(RuntimeError):
    """Telegram answered but refused the call (bad token, poll conflict, ...)."""


class TelegramTransport:
    """The protocol the bridge talks to; tests inject a fake.

    ``get_updates(offset, poll_seconds)`` blocks up to ``poll_seconds`` and
    returns the raw ``result`` list of updates (each ``update_id``, optionally
    a ``message`` with ``chat.id`` and ``text``). ``send_message(chat_id,
    text)`` posts one message.
    """

    def get_updates(self, offset: int, poll_seconds: float) -> list[dict]:
        raise NotImplementedError

    def send_message(self, chat_id: str, text: str) -> bool:
        raise NotImplementedError


class _TelegramHTTP(TelegramTransport):
    """The real transport: requests against ``api.telegram.org``.

    ``requests`` is imported lazily so a runtime that never configures Telegram
    pays nothing for it, and a machine without it still builds.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._session: Any = None

    def _post(self, method: str, payload: dict, timeout: float) -> dict:
        import requests

        if self._session is None:
            self._session = requests.Session()
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        response = self._session.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise TelegramAPIError(
                data.get("description") or f"telegram: {method} not ok"
            )
        return data

    def get_updates(self, offset: int, poll_seconds: float) -> list[dict]:
        data = self._post(
            "getUpdates",
            {
                "offset": offset,
                "timeout": poll_seconds,
                "allowed_updates": ["message"],
            },
            timeout=poll_seconds + _HTTP_SLACK,
        )
        return data.get("result") or []

    def send_message(self, chat_id: str, text: str) -> bool:
        self._post(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=_HTTP_SLACK + 5.0,
        )
        return True


class TelegramBridge:
    """Long-poll approval bridge: one daemon thread while started.

    Outbound notifications are event-driven: the bridge subscribes to
    ``approval.submitted`` / ``approval.decided`` and announces approvals on the
    bus to the configured chat. Inbound decisions are read by the poll thread
    and routed through ``decide`` exactly like the chat loop's ``/approve``.
    """

    #: Docs for the operator, shown by /help.
    HELP = _HELP_TEXT

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        decide: Callable[[str, bool], str],
        activity: Optional[Callable[[], str]] = None,
        bus: Optional[EventBus] = None,
        transport: Optional[TelegramTransport] = None,
        long_poll: float = _MAX_LONG_POLL,
        retry_seconds: float = 3.0,
        notify_tasks: bool = True,
    ) -> None:
        if not token or not chat_id:
            raise ValueError("a token and a chat id are both required")
        self.chat_id = str(chat_id)
        self._transport = transport or _TelegramHTTP(token)
        self._decide = decide
        self._activity = activity
        self._long_poll = min(max(1.0, float(long_poll)), _MAX_LONG_POLL)
        self._retry = max(0.5, float(retry_seconds))
        self.notify_tasks = bool(notify_tasks)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Outbound sends are serialised: the poll thread and the bus callback
        # can fire concurrently.
        self._send_lock = threading.Lock()
        self.poll_errors = 0
        self.last_error = ""

        self._unsubs: list[Callable[[], None]] = []
        if bus is not None:
            self._unsubs.append(
                bus.subscribe(Events.APPROVAL_SUBMITTED, self._on_submitted)
            )
            self._unsubs.append(
                bus.subscribe(Events.APPROVAL_DECIDED, self._on_decided)
            )
            if self.notify_tasks:
                self._unsubs.append(bus.subscribe("task.*", self._on_task))

    # -- lifecycle ---------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the poll thread. Idempotent; True once running."""
        if self.started:
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            name=f"alan-telegram-{self.chat_id[:4]}",
            target=self._run,
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def close(self) -> None:
        """Stop the loop if running and detach from the event bus."""
        self.stop()
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # pragma: no cover - never break shutdown
                pass
        self._unsubs.clear()

    def status(self) -> dict[str, Any]:
        note = "bridge ready" if not self.last_error else f"last poll error: {self.last_error}"
        return {
            "chat_id": self.chat_id,
            "running": self.started,
            "poll_errors": self.poll_errors,
            "note": note,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid; no token ever
        return f"<TelegramBridge chat={self.chat_id} {'running' if self.started else 'idle'}>"

    # -- inbound: the poll thread -----------------------------------------

    def _run(self) -> None:
        offset = 0
        while not self._stop.is_set():
            called_at = time.monotonic()
            try:
                updates = self._transport.get_updates(offset, self._long_poll)
            except TelegramAPIError as exc:
                # The common case is 409 "terminated by other getUpdates
                # request" -- two pollers (e.g. a second `alan chat`) is a
                # conflict, not an outage. Count it, wait, and keep going so
                # one false start never kills the bridge.
                self._degrade(exc)
                if self._stop.wait(self._retry):
                    break
                continue
            except Exception as exc:  # noqa: BLE001 - a dead network must not kill the loop
                self._degrade(exc)
                if self._stop.wait(self._retry):
                    break
                continue

            if updates:
                for update in updates:
                    self._handle_update(update)
                    offset = max(offset, int(update.get("update_id", 0)) + 1)

            # A healthy long-poll blocked for `long_poll` seconds already, so
            # an empty batch that came back instantly was a fake transport or a
            # dead connection; never spin on it.
            elapsed = time.monotonic() - called_at
            if not updates and elapsed < _MIN_CALL_GAP:
                if self._stop.wait(_MIN_CALL_GAP - elapsed):
                    break

    def _degrade(self, exc: Exception) -> None:
        self.poll_errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"

    def _handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return  # edited_message, channel_post, ... are outside scope
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        if str(chat.get("id", "")) != self.chat_id:
            return  # fail-closed: not our operator, no action, no reply
        text = str(message.get("text", "") or "").strip()
        if text:
            self._handle_command(self.chat_id, text)

    def _handle_command(self, chat_id: str, text: str) -> None:
        lower = text.lower()
        if lower.startswith("/approve") or lower.startswith("/reject"):
            approve = lower.startswith("/approve")
            marker = "/approve " if approve else "/reject "
            target = text[len(marker):].strip()
            if not target:
                self._send(chat_id, "usage: /approve <id> or /reject <id>")
                return
            try:
                reply = self._decide(target, approve)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                reply = f"could not decide: {type(exc).__name__}: {exc}"
            self._send(chat_id, str(reply)[:400])
            return
        if lower.startswith("/activity"):
            if self._activity is None:
                self._send(chat_id, "the activity view is unavailable on this bridge")
                return
            try:
                reply = self._activity()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                reply = f"could not read activity: {type(exc).__name__}: {exc}"
            self._send(chat_id, str(reply)[:3500])
            return
        if lower.startswith("/start") or lower.startswith("/help"):
            self._send(chat_id, self.HELP)
            return
        # Authorized chat, unrecognised text: a hint, never an action.
        self._send(chat_id, self.HELP)

    def _send(self, chat_id: str, text: str) -> bool:
        with self._send_lock:
            try:
                return bool(self._transport.send_message(chat_id, text))
            except Exception:  # noqa: BLE001 - a failed ping must not kill the loop
                return False

    # -- outbound: bus events ---------------------------------------------

    def _on_submitted(self, event: Event) -> None:
        if not self.started:
            return
        fields = event.fields or {}
        aid = str(fields.get("approval_id", "?"))
        text = (
            f"\u2753 approval {aid} "
            f"({fields.get('capability', '?')}, {fields.get('risk', '?')})"
        )
        task = fields.get("task_id")
        if task:
            text += f" from scheduler task {task}"
        text += f"\n/approve {aid} to allow it, /reject {aid} to refuse"
        self._send(self.chat_id, text)

    def _on_decided(self, event: Event) -> None:
        if not self.started:
            return
        fields = event.fields or {}
        approved = fields.get("status") == "approved"
        mark = "\u2705" if approved else "\u26d4"
        verb = "approved" if approved else "rejected"
        aid = str(fields.get("approval_id", "?"))
        text = f"{mark} {aid} {verb} ({fields.get('capability', '?')})"
        task = fields.get("task_id")
        if task:
            text += (
                f" -- task {task} will run again"
                if approved
                else f" -- task {task} paused"
            )
        self._send(self.chat_id, text)

    def _render_task_event(self, event: Event) -> Optional[str]:
        """One phone line for a task event, or None to skip it.

        Three shapes, mirroring what the chat feed prints locally; a task that
        is *waiting* for approval is deliberately absent here -- the approval
        announcement already carries the id and the /approve instruction, and
        two messages for the same stop is noise.
        """
        fields = event.fields or {}
        task_id = str(fields.get("task_id", ""))
        if event.name == Events.TASK_STARTED:
            request = str(fields.get("request", ""))[:80]
            return f"\u25b6 task {task_id} started: {request}"
        if event.name == Events.TASK_FINISHED:
            mark = "done" if fields.get("ok") else "finished"
            summary = str(fields.get("summary", ""))[:120]
            return f"\u2705 task {task_id} {mark}: {summary}"
        if event.name == Events.TASK_FAILED:
            error = str(fields.get("error") or fields.get("summary", ""))[:120]
            return f"\u26d4 task {task_id} FAILED: {error}"
        return None

    def _on_task(self, event: Event) -> None:
        if not self.started:
            return
        line = self._render_task_event(event)
        if line is None:
            return
        self._send(self.chat_id, line)