"""Core kernel module for Alan."""

from alan.core.kernel import AlanKernel, KernelConfig, KernelState, get_kernel, set_kernel, kernel_context
from alan.core.lifecycle import (
    LifecycleManager,
    LifecycleComponent,
    LifecyclePhase,
    ComponentState,
    LifecycleHook,
    StartupHook,
    ShutdownHook,
    HealthCheck,
    HealthChecker,
)
from alan.core.events import EventBus, Event, EventHandler, get_event_bus, set_event_bus, publish, subscribe, subscribe_async
from alan.core.container import Container, ServiceDescriptor, get_container, set_container, inject
from alan.core.registry import Registry, ComponentMetadata, get_registry, create_registry, remove_registry
from alan.core.signals import SignalHandler, SignalInfo, get_signal_handler, set_signal_handler
from alan.core.state import (
    ApplicationState,
    StateScope,
    StateEntry,
    ConversationState,
    SessionState,
    get_app_state,
    set_app_state,
)

__all__ = [
    # Kernel
    "AlanKernel",
    "KernelConfig",
    "KernelState",
    "get_kernel",
    "set_kernel",
    "kernel_context",
    # Lifecycle
    "LifecycleManager",
    "LifecycleComponent",
    "LifecyclePhase",
    "ComponentState",
    "LifecycleHook",
    "StartupHook",
    "ShutdownHook",
    "HealthCheck",
    "HealthChecker",
    # Events
    "EventBus",
    "Event",
    "EventHandler",
    "get_event_bus",
    "set_event_bus",
    "publish",
    "subscribe",
    "subscribe_async",
    # Container
    "Container",
    "ServiceDescriptor",
    "get_container",
    "set_container",
    "inject",
    # Registry
    "Registry",
    "ComponentMetadata",
    "get_registry",
    "create_registry",
    "remove_registry",
    # Signals
    "SignalHandler",
    "SignalInfo",
    "get_signal_handler",
    "set_signal_handler",
    # State
    "ApplicationState",
    "StateScope",
    "StateEntry",
    "ConversationState",
    "SessionState",
    "get_app_state",
    "set_app_state",
]