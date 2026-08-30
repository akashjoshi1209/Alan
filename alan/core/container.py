"""Dependency injection container for Alan."""

from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import isclass
from typing import Any, Dict, Generic, Optional, Type, TypeVar, get_type_hints

from alan.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class ServiceDescriptor:
    """Describes a service registration."""

    service_type: Type
    implementation: Optional[Type] = None
    factory: Optional[Callable] = None
    instance: Optional[Any] = None
    singleton: bool = True
    dependencies: Dict[str, Any] = field(default_factory=dict)


class Container:
    """Dependency injection container."""

    def __init__(self, parent: Optional["Container"] = None):
        self._services: Dict[Type, ServiceDescriptor] = {}
        self._instances: Dict[Type, Any] = {}
        self._parent = parent
        self._resolving: set = set()

    def register(
        self,
        service_type: Type[T],
        implementation: Optional[Type[T]] = None,
        *,
        factory: Optional[Callable[..., T]] = None,
        instance: Optional[T] = None,
        singleton: bool = True,
        **dependencies,
    ) -> "Container":
        """Register a service."""
        if sum(x is not None for x in (implementation, factory, instance)) != 1:
            raise ValueError(
                "Exactly one of implementation, factory, or instance must be provided"
            )

        descriptor = ServiceDescriptor(
            service_type=service_type,
            implementation=implementation,
            factory=factory,
            instance=instance,
            singleton=singleton,
            dependencies=dependencies,
        )

        self._services[service_type] = descriptor
        logger.debug(
            "Registered service",
            service=service_type.__name__,
            singleton=singleton,
        )
        return self

    def register_singleton(self, service_type: Type[T], implementation: Type[T]) -> "Container":
        """Register a singleton service."""
        return self.register(service_type, implementation, singleton=True)

    def register_transient(self, service_type: Type[T], implementation: Type[T]) -> "Container":
        """Register a transient service (new instance each time)."""
        return self.register(service_type, implementation, singleton=False)

    def register_factory(
        self, service_type: Type[T], factory: Callable[..., T], singleton: bool = True
    ) -> "Container":
        """Register a factory function."""
        return self.register(service_type, factory=factory, singleton=singleton)

    def register_instance(self, service_type: Type[T], instance: T) -> "Container":
        """Register an existing instance."""
        return self.register(service_type, instance=instance, singleton=True)

    def is_registered(self, service_type: Type) -> bool:
        """Check if a service is registered."""
        if service_type in self._services:
            return True
        if self._parent:
            return self._parent.is_registered(service_type)
        return False

    def resolve(self, service_type: Type[T]) -> T:
        """Resolve a service."""
        # Check cache for singletons
        if service_type in self._instances:
            return self._instances[service_type]

        # Check if currently resolving (circular dependency)
        if service_type in self._resolving:
            raise RuntimeError(f"Circular dependency detected for {service_type.__name__}")

        # Get descriptor
        descriptor = self._services.get(service_type)
        if not descriptor:
            if self._parent:
                return self._parent.resolve(service_type)
            raise KeyError(f"Service {service_type.__name__} not registered")

        self._resolving.add(service_type)
        try:
            instance = self._create_instance(descriptor)
        finally:
            self._resolving.discard(service_type)

        # Cache singleton
        if descriptor.singleton:
            self._instances[service_type] = instance

        return instance

    def _create_instance(self, descriptor: ServiceDescriptor) -> Any:
        """Create an instance from descriptor."""
        if descriptor.instance is not None:
            return descriptor.instance

        if descriptor.factory:
            # Resolve factory dependencies
            kwargs = self._resolve_dependencies(descriptor.dependencies)
            return descriptor.factory(**kwargs)

        if descriptor.implementation:
            # Auto-resolve constructor dependencies
            return self._auto_resolve(descriptor.implementation)

        raise RuntimeError("Invalid service descriptor")

    def _auto_resolve(self, implementation: Type) -> Any:
        """Auto-resolve constructor dependencies."""
        hints = get_type_hints(implementation.__init__)
        kwargs = {}

        for param_name, param_type in hints.items():
            if param_name == "return":
                continue
            if param_name in ("self", "cls"):
                continue
            try:
                kwargs[param_name] = self.resolve(param_type)
            except KeyError:
                # Parameter not registered, will use default or raise
                pass

        return implementation(**kwargs)

    def _resolve_dependencies(self, dependencies: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve explicit dependencies."""
        resolved = {}
        for name, dep in dependencies.items():
            if isclass(dep):
                resolved[name] = self.resolve(dep)
            elif callable(dep):
                resolved[name] = dep()
            else:
                resolved[name] = dep
        return resolved

    def get(self, service_type: Type[T], default: Any = None) -> Optional[T]:
        """Get a service or return default."""
        try:
            return self.resolve(service_type)
        except KeyError:
            return default

    def create_scope(self) -> "Container":
        """Create a child scope."""
        return Container(parent=self)

    def clear(self) -> None:
        """Clear all registrations and instances."""
        self._services.clear()
        self._instances.clear()

    def get_registrations(self) -> Dict[Type, ServiceDescriptor]:
        """Get all registrations."""
        return self._services.copy()


# Global container
_container: Optional[Container] = None


def get_container() -> Container:
    """Get the global container."""
    global _container
    if _container is None:
        _container = Container()
    return _container


def set_container(container: Container) -> None:
    """Set the global container."""
    global _container
    _container = container


def inject(**dependencies) -> Callable:
    """Decorator for dependency injection."""

    def decorator(func: Callable) -> Callable:
        container = get_container()

        def wrapper(*args, **kwargs):
            # Resolve missing dependencies
            for name, dep_type in dependencies.items():
                if name not in kwargs:
                    kwargs[name] = container.resolve(dep_type)
            return func(*args, **kwargs)

        return wrapper

    return decorator