"""Core kernel for Alan."""

import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from alan.config import get_settings, Settings
from alan.core.container import Container
from alan.core.events import EventBus
from alan.core.lifecycle import LifecycleManager
from alan.core.registry import ComponentRegistry
from alan.core.signals import SignalHandler
from alan.core.state import ApplicationState
from alan.logging import get_logger, setup_logging

logger = get_logger(__name__)


class KernelState(Enum):
    """Kernel lifecycle states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class KernelConfig:
    """Kernel configuration."""

    config_path: Optional[Path] = None
    environment: str = "development"
    auto_reload: bool = False
    components: List[str] = field(default_factory=list)


class AlanKernel:
    """Main kernel for the Alan operating system."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        environment: str = "development",
    ):
        self.config = KernelConfig(
            config_path=config_path,
            environment=environment,
        )
        self.state = KernelState.STOPPED
        self._start_time: Optional[datetime] = None
        self._shutdown_event = asyncio.Event()

        # Core components
        self.settings: Optional[Settings] = None
        self.container: Optional[Container] = None
        self.event_bus: Optional[EventBus] = None
        self.lifecycle: Optional[LifecycleManager] = None
        self.registry: Optional[ComponentRegistry] = None
        self.signals: Optional[SignalHandler] = None
        self.app_state: Optional[ApplicationState] = None

        # Component tracking
        self._started_components: Set[str] = set()
        self._startup_tasks: List[Callable] = []
        self._shutdown_tasks: List[Callable] = []

    async def start(self) -> None:
        """Start the kernel and all components."""
        if self.state != KernelState.STOPPED:
            raise RuntimeError(f"Kernel already in state: {self.state.value}")

        self.state = KernelState.STARTING
        self._start_time = datetime.utcnow()

        try:
            # Initialize logging first
            setup_logging()
            logger.info("Starting Alan kernel", environment=self.config.environment)

            # Load configuration
            self.settings = get_settings()
            logger.debug("Configuration loaded")

            # Initialize core components
            await self._initialize_core()

            # Initialize registered components
            await self._initialize_components()

            # Run startup tasks
            await self._run_startup_tasks()

            # Start signal handling
            self.signals.start()

            self.state = KernelState.RUNNING
            logger.info(
                "Alan kernel started successfully",
                startup_time_ms=(datetime.utcnow() - self._start_time).total_seconds() * 1000,
            )

        except Exception as e:
            self.state = KernelState.ERROR
            logger.exception("Failed to start kernel", error=str(e))
            await self._cleanup_on_error()
            raise

    async def stop(self) -> None:
        """Stop the kernel gracefully."""
        if self.state in (KernelState.STOPPED, KernelState.STOPPING):
            return

        self.state = KernelState.STOPPING
        logger.info("Stopping Alan kernel")

        try:
            # Stop signal handling
            if self.signals:
                self.signals.stop()

            # Run shutdown tasks
            await self._run_shutdown_tasks()

            # Stop components in reverse order
            await self._stop_components()

            # Stop core components
            await self._stop_core()

            self.state = KernelState.STOPPED
            logger.info("Alan kernel stopped")

        except Exception as e:
            self.state = KernelState.ERROR
            logger.exception("Error during shutdown", error=str(e))
            raise
        finally:
            self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    async def restart(self) -> None:
        """Restart the kernel."""
        await self.stop()
        await self.start()

    async def _initialize_core(self) -> None:
        """Initialize core infrastructure components."""
        # Event bus
        self.event_bus = EventBus()
        await self.event_bus.start()

        # Dependency injection container
        self.container = Container()
        self.container.register_instance(Settings, self.settings)
        self.container.register_instance(AlanKernel, self)
        self.container.register_instance(EventBus, self.event_bus)

        # Component registry
        self.registry = ComponentRegistry(self.container)

        # Lifecycle manager
        self.lifecycle = LifecycleManager(self.registry, self.event_bus)

        # Signal handler
        self.signals = SignalHandler(self)

        # Application state
        self.app_state = ApplicationState()

        logger.debug("Core components initialized")

    async def _initialize_components(self) -> None:
        """Initialize registered components."""
        if not self.registry:
            return

        # Initialize components in dependency order
        for component_name in self.registry.get_startup_order():
            component = self.registry.get(component_name)
            if component and hasattr(component, "initialize"):
                try:
                    await component.initialize()
                    self._started_components.add(component_name)
                    logger.debug("Component initialized", component=component_name)
                except Exception as e:
                    logger.exception(
                        "Failed to initialize component",
                        component=component_name,
                        error=str(e),
                    )
                    raise

    async def _stop_components(self) -> None:
        """Stop components in reverse order."""
        if not self.registry:
            return

        for component_name in reversed(self.registry.get_startup_order()):
            component = self.registry.get(component_name)
            if component and hasattr(component, "shutdown"):
                try:
                    await component.shutdown()
                    logger.debug("Component stopped", component=component_name)
                except Exception as e:
                    logger.exception(
                        "Error stopping component",
                        component=component_name,
                        error=str(e),
                    )

    async def _stop_core(self) -> None:
        """Stop core components."""
        if self.event_bus:
            await self.event_bus.stop()

        if self.container:
            await self.container.close()

        logger.debug("Core components stopped")

    async def _run_startup_tasks(self) -> None:
        """Run registered startup tasks."""
        for task in self._startup_tasks:
            try:
                if asyncio.iscoroutinefunction(task):
                    await task()
                else:
                    task()
            except Exception as e:
                logger.exception("Startup task failed", error=str(e))
                raise

    async def _run_shutdown_tasks(self) -> None:
        """Run registered shutdown tasks."""
        for task in reversed(self._shutdown_tasks):
            try:
                if asyncio.iscoroutinefunction(task):
                    await task()
                else:
                    task()
            except Exception as e:
                logger.exception("Shutdown task failed", error=str(e))

    async def _cleanup_on_error(self) -> None:
        """Cleanup on startup error."""
        await self._stop_components()
        await self._stop_core()

    def register_startup_task(self, task: Callable) -> None:
        """Register a task to run on startup."""
        self._startup_tasks.append(task)

    def register_shutdown_task(self, task: Callable) -> None:
        """Register a task to run on shutdown."""
        self._shutdown_tasks.append(task)

    def register_component(self, name: str, component: Any) -> None:
        """Register a component with the kernel."""
        if self.registry:
            self.registry.register(name, component)

    def get_component(self, name: str) -> Optional[Any]:
        """Get a registered component."""
        if self.registry:
            return self.registry.get(name)
        return None

    @property
    def uptime(self) -> float:
        """Get kernel uptime in seconds."""
        if self._start_time:
            return (datetime.utcnow() - self._start_time).total_seconds()
        return 0.0

    @property
    def is_running(self) -> bool:
        return self.state == KernelState.RUNNING

    @property
    def is_healthy(self) -> bool:
        return self.state == KernelState.RUNNING


# Global kernel instance
_kernel: Optional[AlanKernel] = None


def get_kernel(
    config_path: Optional[Path] = None,
    environment: str = "development",
) -> AlanKernel:
    """Get or create the global kernel instance."""
    global _kernel
    if _kernel is None:
        _kernel = AlanKernel(config_path, environment)
    return _kernel


def set_kernel(kernel: AlanKernel) -> None:
    """Set the global kernel instance (mainly for testing)."""
    global _kernel
    _kernel = kernel


@asynccontextmanager
async def kernel_context(
    config_path: Optional[Path] = None,
    environment: str = "development",
):
    """Context manager for kernel lifecycle."""
    kernel = AlanKernel(config_path, environment)
    try:
        await kernel.start()
        yield kernel
    finally:
        await kernel.stop()