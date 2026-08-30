"""Unit tests for core kernel."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from alan.core import (
    AlanKernel,
    KernelState,
    KernelConfig,
    get_kernel,
    set_kernel,
    kernel_context,
)
from alan.core.events import EventBus, Event
from alan.core.container import Container
from alan.core.registry import Registry
from alan.core.signals import SignalHandler
from alan.core.state import ApplicationState, StateScope


class TestKernelConfig:
    """Test kernel configuration."""

    def test_default_config(self):
        """Test default kernel config."""
        config = KernelConfig()
        assert config.config_path is None
        assert config.environment == "development"
        assert config.auto_reload is False
        assert config.components == []


class TestAlanKernel:
    """Test Alan kernel."""

    @pytest.fixture
    def kernel(self):
        """Create kernel instance."""
        return AlanKernel(environment="testing")

    @pytest.mark.asyncio
    async def test_kernel_initialization(self, kernel):
        """Test kernel initialization."""
        assert kernel.state == KernelState.STOPPED
        assert kernel.config.environment == "testing"
        assert kernel.settings is None

    @pytest.mark.asyncio
    async def test_kernel_start_stop(self, kernel):
        """Test kernel start and stop."""
        await kernel.start()
        assert kernel.state == KernelState.RUNNING
        assert kernel.settings is not None
        assert kernel.container is not None
        assert kernel.event_bus is not None
        assert kernel.registry is not None
        assert kernel.lifecycle is not None
        assert kernel.signals is not None
        assert kernel.app_state is not None

        await kernel.stop()
        assert kernel.state == KernelState.STOPPED

    @pytest.mark.asyncio
    async def test_kernel_startup_tasks(self, kernel):
        """Test kernel startup tasks."""
        task_called = False

        async def startup_task():
            nonlocal task_called
            task_called = True

        kernel.register_startup_task(startup_task)
        await kernel.start()

        assert task_called is True
        await kernel.stop()

    @pytest.mark.asyncio
    async def test_kernel_shutdown_tasks(self, kernel):
        """Test kernel shutdown tasks."""
        task_called = False

        async def shutdown_task():
            nonlocal task_called
            task_called = True

        kernel.register_shutdown_task(shutdown_task)
        await kernel.start()
        await kernel.stop()

        assert task_called is True

    @pytest.mark.asyncio
    async def test_kernel_component_registration(self, kernel):
        """Test component registration."""
        component = MagicMock()
        kernel.register_component("test_component", component)

        retrieved = kernel.get_component("test_component")
        assert retrieved is component

    @pytest.mark.asyncio
    async def test_kernel_uptime(self, kernel):
        """Test kernel uptime tracking."""
        assert kernel.uptime == 0.0

        await kernel.start()
        uptime1 = kernel.uptime
        assert uptime1 >= 0

        import asyncio
        await asyncio.sleep(0.01)
        uptime2 = kernel.uptime
        assert uptime2 > uptime1

        await kernel.stop()

    @pytest.mark.asyncio
    async def test_kernel_health_check(self, kernel):
        """Test kernel health check."""
        assert kernel.is_healthy is False

        await kernel.start()
        assert kernel.is_healthy is True

        await kernel.stop()
        assert kernel.is_healthy is False


class TestKernelSingleton:
    """Test kernel singleton management."""

    def test_get_kernel_creates_instance(self):
        """Test get_kernel creates instance."""
        kernel = get_kernel(environment="testing")
        assert kernel is not None

        # Second call should return same instance
        kernel2 = get_kernel()
        assert kernel is kernel2

    def test_set_kernel(self):
        """Test set_kernel."""
        custom_kernel = AlanKernel(environment="testing")
        set_kernel(custom_kernel)

        retrieved = get_kernel()
        assert retrieved is custom_kernel


class TestKernelContextManager:
    """Test kernel context manager."""

    @pytest.mark.asyncio
    async def test_kernel_context(self):
        """Test kernel context manager."""
        async with kernel_context(environment="testing") as kernel:
            assert kernel.state == KernelState.RUNNING
            assert kernel.is_healthy

        assert kernel.state == KernelState.STOPPED

    @pytest.mark.asyncio
    async def test_kernel_context_error_handling(self):
        """Test kernel context manager handles errors."""
        async with kernel_context(environment="testing") as kernel:
            assert kernel.is_healthy

        # Should still stop on error
        assert kernel.state == KernelState.STOPPED


class TestEventBus:
    """Test EventBus."""

    @pytest.mark.asyncio
    async def test_event_bus_start_stop(self):
        """Test event bus start/stop."""
        bus = EventBus()
        await bus.start()
        assert bus.is_running
        await bus.stop()
        assert not bus.is_running

    @pytest.mark.asyncio
    async def test_event_subscribe_emit(self):
        """Test event subscription and emission."""
        bus = EventBus()
        await bus.start()

        received_events = []

        def handler(event):
            received_events.append(event)

        bus.subscribe("test.event", handler)

        event = Event(type="test.event", data={"key": "value"})
        await bus.emit(event)

        assert len(received_events) == 1
        assert received_events[0].type == "test.event"
        assert received_events[0].data == {"key": "value"}

        await bus.stop()

    @pytest.mark.asyncio
    async def test_event_unsubscribe(self):
        """Test event unsubscription."""
        bus = EventBus()
        await bus.start()

        received = []

        def handler(event):
            received.append(event)

        handler_id = bus.subscribe("test.event", handler)
        bus.unsubscribe(handler_id)

        event = Event(type="test.event", data={})
        await bus.emit(event)

        assert len(received) == 0
        await bus.stop()

    @pytest.mark.asyncio
    async def test_event_wildcard_subscription(self):
        """Test wildcard event subscription."""
        bus = EventBus()
        await bus.start()

        received = []

        def handler(event):
            received.append(event)

        bus.subscribe_all(handler)

        await bus.emit(Event(type="event.a", data={}))
        await bus.emit(Event(type="event.b", data={}))

        assert len(received) == 2
        await bus.stop()

    @pytest.mark.asyncio
    async def test_event_history(self):
        """Test event history."""
        bus = EventBus()
        await bus.start()

        for i in range(5):
            await bus.emit(Event(type=f"event.{i}", data={}))

        history = bus.get_history(limit=3)
        assert len(history) == 3

        all_history = bus.get_history()
        assert len(all_history) == 5

        await bus.stop()


class TestContainer:
    """Test DI Container."""

    def test_register_singleton(self):
        """Test singleton registration."""
        container = Container()

        class Service:
            pass

        container.register_singleton(Service, Service)
        instance1 = container.resolve(Service)
        instance2 = container.resolve(Service)
        assert instance1 is instance2

    def test_register_transient(self):
        """Test transient registration."""
        container = Container()

        class Service:
            pass

        container.register_transient(Service, Service)
        instance1 = container.resolve(Service)
        instance2 = container.resolve(Service)
        assert instance1 is not instance2

    def test_register_factory(self):
        """Test factory registration."""
        container = Container()

        def create_service():
            return {"created": True}

        container.register_factory(dict, create_service, singleton=True)
        result = container.resolve(dict)
        assert result == {"created": True}

    def test_register_instance(self):
        """Test instance registration."""
        container = Container()
        instance = {"key": "value"}
        container.register_instance(dict, instance)

        resolved = container.resolve(dict)
        assert resolved is instance

    def test_is_registered(self):
        """Test registration check."""
        container = Container()
        assert not container.is_registered(str)

        container.register_instance(str, "test")
        assert container.is_registered(str)

    def test_child_scope(self):
        """Test child scope creation."""
        container = Container()
        container.register_instance(str, "parent")

        child = container.create_scope()
        child.register_instance(str, "child")

        assert child.resolve(str) == "child"
        assert container.resolve(str) == "parent"


class TestRegistry:
    """Test Component Registry."""

    def test_register_component(self):
        """Test component registration."""
        registry = Registry()

        class Component:
            pass

        comp = Component()
        registry.register("test", comp, version="1.0.0", tags=["test"])

        assert registry.is_registered("test")
        assert registry.get("test") is comp

    def test_unregister_component(self):
        """Test component unregistration."""
        registry = Registry()
        comp = MagicMock()

        registry.register("test", comp)
        assert registry.is_registered("test")

        unregistered = registry.unregister("test")
        assert unregistered is comp
        assert not registry.is_registered("test")

    def test_get_by_type(self):
        """Test getting components by type."""
        registry = Registry()

        class Base:
            pass

        class Derived(Base):
            pass

        d1 = Derived()
        d2 = Derived()
        registry.register("d1", d1, component_type=Base)
        registry.register("d2", d2, component_type=Base)

        components = registry.get_by_type(Base)
        assert len(components) == 2
        assert d1 in components
        assert d2 in components

    def test_get_by_tag(self):
        """Test getting components by tag."""
        registry = Registry()

        comp1 = MagicMock()
        comp2 = MagicMock()
        registry.register("c1", comp1, tags=["api"])
        registry.register("c2", comp2, tags=["api", "internal"])
        registry.register("c3", MagicMock(), tags=["internal"])

        api_components = registry.get_by_tag("api")
        assert len(api_components) == 2

    def test_enable_disable(self):
        """Test component enable/disable."""
        registry = Registry()
        comp = MagicMock()

        registry.register("test", comp)
        assert registry.is_enabled("test")

        registry.disable("test")
        assert not registry.is_enabled("test")

        registry.enable("test")
        assert registry.is_enabled("test")


class TestSignalHandler:
    """Test Signal Handler."""

    @pytest.mark.asyncio
    async def test_signal_registration(self):
        """Test signal handler registration."""
        kernel = MagicMock()
        handler = SignalHandler(kernel)

        def custom_handler(signum, frame):
            pass

        handler.add_handler(signal.SIGTERM, custom_handler)
        handler.start()
        handler.stop()


class TestApplicationState:
    """Test Application State."""

    @pytest.mark.asyncio
    async def test_set_get_state(self):
        """Test setting and getting state."""
        state = ApplicationState()

        await state.set("key1", "value1")
        assert await state.get("key1") == "value1"

        await state.set("key2", {"nested": "value"})
        assert await state.get("key2") == {"nested": "value"}

    @pytest.mark.asyncio
    async def test_state_scopes(self):
        """Test state scopes."""
        state = ApplicationState()

        await state.set("global", "value", scope=StateScope.GLOBAL)
        await state.set("user", "value", scope=StateScope.USER)
        await state.set("session", "value", scope=StateScope.SESSION)

        assert await state.get("global") == "value"
        assert await state.get("user") == "value"
        assert await state.get("session") == "value"

    @pytest.mark.asyncio
    async def test_state_ttl(self):
        """State TTL expiration."""
        state = ApplicationState()

        await state.set("temp", "value", ttl=0.01)
        assert await state.get("temp") == "value"

        import asyncio
        await asyncio.sleep(0.02)
        assert await state.get("temp") is None

    @pytest.mark.asyncio
    async def test_state_delete(self):
        """Test state deletion."""
        state = ApplicationState()

        await state.set("key", "value")
        assert await state.exists("key")

        await state.delete("key")
        assert not await state.exists("key")

    @pytest.mark.asyncio
    async def test_state_clear(self):
        """Test state clearing."""
        state = ApplicationState()

        await state.set("a", 1, scope=StateScope.GLOBAL)
        await state.set("b", 2, scope=StateScope.USER)

        await state.clear(StateScope.USER)
        assert await state.get("a") == 1
        assert await state.get("b") is None

        await state.clear()
        assert await state.get("a") is None

    @pytest.mark.asyncio
    async def test_state_listeners(self):
        """Test state change listeners."""
        state = ApplicationState()
        changes = []

        def listener(key, new_val, old_val):
            changes.append((key, new_val, old_val))

        state.add_listener("watched", listener)

        await state.set("watched", "new")
        await state.set("watched", "newer")
        await state.set("other", "value")  # Should not trigger

        assert len(changes) == 2
        assert changes[0] == ("watched", "new", None)
        assert changes[1] == ("watched", "newer", "new")

    @pytest.mark.asyncio
    async def test_atomic_operations(self):
        """Test atomic state operations."""
        state = ApplicationState()

        # Increment
        await state.set("counter", 0)
        result = await state.increment("counter", 5)
        assert result == 5
        assert await state.get("counter") == 5

        # Append
        await state.set("items", [])
        result = await state.append("items", "item1")
        assert result == ["item1"]
        await state.append("items", "item2")
        assert await state.get("items") == ["item1", "item2"]

        # Setdefault
        result = await state.setdefault("new_key", "default")
        assert result == "default"
        assert await state.get("new_key") == "default"