"""Signal handling for Alan kernel."""

import asyncio
import signal
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from alan.core.kernel import AlanKernel
from alan.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SignalInfo:
    """Information about a received signal."""

    signum: int
    name: str
    timestamp: float
    frame: Optional[object] = None


class SignalHandler:
    """Handles OS signals for graceful shutdown."""

    def __init__(self, kernel: AlanKernel):
        self._kernel = kernel
        self._handlers: Dict[int, List[Callable]] = {}
        self._default_handlers: Dict[int, Callable] = {}
        self._received_signals: List[SignalInfo] = []
        self._shutdown_requested = False
        self._shutdown_event = asyncio.Event()

    def start(self) -> None:
        """Register signal handlers."""
        if hasattr(signal, "SIGTERM"):
            self._register(signal.SIGTERM, self._handle_shutdown_signal)
        if hasattr(signal, "SIGINT"):
            self._register(signal.SIGINT, self._handle_shutdown_signal)
        if hasattr(signal, "SIGHUP"):
            self._register(signal.SIGHUP, self._handle_reload_signal)
        if hasattr(signal, "SIGUSR1"):
            self._register(signal.SIGUSR1, self._handle_usr1_signal)
        if hasattr(signal, "SIGUSR2"):
            self._register(signal.SIGUSR2, self._handle_usr2_signal)

        logger.debug("Signal handlers registered")

    def stop(self) -> None:
        """Restore default signal handlers."""
        for signum, handler in self._default_handlers.items():
            try:
                signal.signal(signum, handler)
            except Exception:
                pass
        self._handlers.clear()
        self._default_handlers.clear()
        logger.debug("Signal handlers restored")

    def _register(self, signum: int, handler: Callable) -> None:
        """Register a signal handler."""
        try:
            self._default_handlers[signum] = signal.signal(signum, handler)
            if signum not in self._handlers:
                self._handlers[signum] = []
            self._handlers[signum].append(handler)
        except Exception as e:
            logger.warning("Failed to register signal handler", signum=signum, error=str(e))

    def add_handler(self, signum: int, handler: Callable) -> None:
        """Add a custom signal handler."""
        if signum not in self._handlers:
            self._register(signum, self._dispatch)
        self._handlers[signum].append(handler)

    def remove_handler(self, signum: int, handler: Callable) -> bool:
        """Remove a custom signal handler."""
        if signum in self._handlers:
            try:
                self._handlers[signum].remove(handler)
                return True
            except ValueError:
                pass
        return False

    def _dispatch(self, signum: int, frame: Optional[object]) -> None:
        """Dispatch signal to registered handlers."""
        info = SignalInfo(
            signum=signum,
            name=signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum),
            timestamp=asyncio.get_event_loop().time(),
            frame=frame,
        )
        self._received_signals.append(info)

        handlers = self._handlers.get(signum, [])
        for handler in handlers:
            try:
                handler(signum, frame)
            except Exception as e:
                logger.error("Signal handler error", signum=signum, error=str(e))

    def _handle_shutdown_signal(self, signum: int, frame: Optional[object]) -> None:
        """Handle shutdown signals (SIGTERM, SIGINT)."""
        logger.info("Shutdown signal received", signal=signum)
        self._shutdown_requested = True
        self._shutdown_event.set()

        # Schedule kernel shutdown
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._kernel.stop())
        else:
            # If no event loop running, we can't do async shutdown
            pass

    def _handle_reload_signal(self, signum: int, frame: Optional[object]) -> None:
        """Handle reload signal (SIGHUP)."""
        logger.info("Reload signal received", signal=signum)
        # Trigger config reload
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._reload_config())

    async def _reload_config(self) -> None:
        """Reload configuration."""
        try:
            # This would trigger config reload in the kernel
            logger.info("Configuration reload triggered")
        except Exception as e:
            logger.error("Config reload failed", error=str(e))

    def _handle_usr1_signal(self, signum: int, frame: Optional[object]) -> None:
        """Handle USR1 signal (custom)."""
        logger.info("USR1 signal received", signal=signum)
        # Custom action - e.g., dump state, trigger GC
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._dump_state())

    async def _dump_state(self) -> None:
        """Dump application state for debugging."""
        logger.info("State dump requested")
        # This would dump kernel/component state

    def _handle_usr2_signal(self, signum: int, frame: Optional[object]) -> None:
        """Handle USR2 signal (custom)."""
        logger.info("USR2 signal received", signal=signum)
        # Custom action - e.g., toggle debug mode

    def wait_for_shutdown(self) -> asyncio.Event:
        """Get event that fires when shutdown is requested."""
        return self._shutdown_event

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown was requested."""
        return self._shutdown_requested

    def get_received_signals(self) -> List[SignalInfo]:
        """Get list of received signals."""
        return self._received_signals.copy()

    def clear_received_signals(self) -> None:
        """Clear received signals history."""
        self._received_signals.clear()


# Global signal handler
_signal_handler: Optional[SignalHandler] = None


def get_signal_handler() -> Optional[SignalHandler]:
    """Get the global signal handler."""
    return _signal_handler


def set_signal_handler(handler: SignalHandler) -> None:
    """Set the global signal handler."""
    global _signal_handler
    _signal_handler = handler