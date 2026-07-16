"""Lifecycle management for Alan components."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from alan.core.registry import Registry
from alan.core.events import EventBus
from alan.logging import get_logger

logger = get_logger(__name__)


class LifecyclePhase(Enum):
    """Component lifecycle phases."""

    CREATED = "created"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class LifecycleComponent(ABC):
    """Base class for lifecycle-managed components."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Component name."""
        pass

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the component."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start the component."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the component."""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the component."""
        pass

    def get_dependencies(self) -> List[str]:
        """Get component dependencies."""
        return []


@dataclass
class ComponentState:
    """State of a managed component."""

    name: str
    phase: LifecyclePhase = LifecyclePhase.CREATED
    error: Optional[str] = None
    dependencies: List[str] = dataclass.field(default_factory=list)


class LifecycleManager:
    """Manages component lifecycle with dependency ordering."""

    def __init__(self, registry: Registry, event_bus: EventBus):
        self.registry = registry
        self.event_bus = event_bus
        self._states: Dict[str, ComponentState] = {}
        self._startup_order: List[str] = []
        self._running = False

    def register_component(
        self,
        name: str,
        dependencies: Optional[List[str]] = None,
    ) -> None:
        """Register a component for lifecycle management."""
        component = self.registry.get(name)
        if not component:
            raise ValueError(f"Component '{name}' not found in registry")

        if not isinstance(component, LifecycleComponent):
            raise TypeError(
                f"Component '{name}' does not implement LifecycleComponent"
            )

        state = ComponentState(
            name=name,
            dependencies=dependencies or component.get_dependencies(),
        )
        self._states[name] = state
        self._recalculate_startup_order()

    def _recalculate_startup_order(self) -> None:
        """Calculate startup order based on dependencies."""
        # Topological sort
        visited = set()
        temp = set()
        order = []

        def visit(name: str) -> None:
            if name in temp:
                raise ValueError(f"Circular dependency detected involving {name}")
            if name in visited:
                return

            temp.add(name)
            state = self._states.get(name)
            if state:
                for dep in state.dependencies:
                    if dep in self._states:
                        visit(dep)
            temp.remove(name)
            visited.add(name)
            order.append(name)

        for name in self._states:
            if name not in visited:
                visit(name)

        self._startup_order = order
        logger.debug("Startup order calculated", order=order)

    def get_startup_order(self) -> List[str]:
        """Get the startup order."""
        return self._startup_order

    def get_shutdown_order(self) -> List[str]:
        """Get the shutdown order (reverse of startup)."""
        return list(reversed(self._startup_order))

    async def initialize_all(self) -> None:
        """Initialize all registered components."""
        logger.info("Initializing components", count=len(self._startup_order))

        for name in self._startup_order:
            await self._initialize_component(name)

        logger.info("All components initialized")

    async def start_all(self) -> None:
        """Start all registered components."""
        logger.info("Starting components", count=len(self._startup_order))
        self._running = True

        for name in self._startup_order:
            await self._start_component(name)

        logger.info("All components started")

    async def stop_all(self) -> None:
        """Stop all components."""
        logger.info("Stopping components")
        self._running = False

        for name in self.get_shutdown_order():
            await self._stop_component(name)

        logger.info("All components stopped")

    async def shutdown_all(self) -> None:
        """Shutdown all components."""
        logger.info("Shutting down components")

        for name in self.get_shutdown_order():
            await self._shutdown_component(name)

        logger.info("All components shut down")

    async def _initialize_component(self, name: str) -> None:
        """Initialize a single component."""
        state = self._states[name]
        component = self.registry.get(name)

        if not component:
            raise ValueError(f"Component '{name}' not found")

        state.phase = LifecyclePhase.INITIALIZING
        try:
            await component.initialize()
            state.phase = LifecyclePhase.INITIALIZED
            logger.debug("Component initialized", name=name)

            # Emit event
            await self.event_bus.publish(
                event_name="component.initialized",
                data={"component": name},
            )
        except Exception as e:
            state.phase = LifecyclePhase.ERROR
            state.error = str(e)
            logger.exception("Component initialization failed", name=name, error=str(e))
            raise

    async def _start_component(self, name: str) -> None:
        """Start a single component."""
        state = self._states[name]
        component = self.registry.get(name)

        state.phase = LifecyclePhase.STARTING
        try:
            await component.start()
            state.phase = LifecyclePhase.RUNNING
            logger.debug("Component started", name=name)

            await self.event_bus.publish(
                event_name="component.started",
                data={"component": name},
            )
        except Exception as e:
            state.phase = LifecyclePhase.ERROR
            state.error = str(e)
            logger.exception("Component start failed", name=name, error=str(e))
            raise

    async def _stop_component(self, name: str) -> None:
        """Stop a single component."""
        state = self._states[name]
        component = self.registry.get(name)

        state.phase = LifecyclePhase.STOPPING
        try:
            await component.stop()
            state.phase = LifecyclePhase.STOPPED
            logger.debug("Component stopped", name=name)

            await self.event_bus.publish(
                event_name="component.stopped",
                data={"component": name},
            )
        except Exception as e:
            state.phase = LifecyclePhase.ERROR
            state.error = str(e)
            logger.exception("Component stop failed", name=name, error=str(e))
            raise

    async def _shutdown_component(self, name: str) -> None:
        """Shutdown a single component."""
        state = self._states[name]
        component = self.registry.get(name)

        try:
            await component.shutdown()
            logger.debug("Component shutdown", name=name)

            await self.event_bus.publish(
                event_name="component.shutdown",
                data={"component": name},
            )
        except Exception as e:
            logger.exception("Component shutdown failed", name=name, error=str(e))
            # Don't raise, continue shutting down others

    def get_state(self, name: str) -> Optional[ComponentState]:
        """Get component state."""
        return self._states.get(name)

    def get_all_states(self) -> Dict[str, ComponentState]:
        """Get all component states."""
        return self._states.copy()

    def is_running(self, name: str) -> bool:
        """Check if component is running."""
        state = self._states.get(name)
        return state.phase == LifecyclePhase.RUNNING if state else False

    def is_healthy(self) -> bool:
        """Check if all components are healthy."""
        return all(
            s.phase in (LifecyclePhase.RUNNING, LifecyclePhase.INITIALIZED)
            for s in self._states.values()
        )