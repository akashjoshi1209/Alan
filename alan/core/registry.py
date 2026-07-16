"""Component registry for Alan."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, TypeVar

from alan.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class ComponentMetadata:
    """Metadata for a registered component."""

    name: str
    component_type: Type
    version: str = "1.0.0"
    description: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    config_schema: Optional[Dict] = None
    enabled: bool = True


class Registry:
    """Registry for components with discovery and metadata."""

    def __init__(self, name: str = "default"):
        self.name = name
        self._components: Dict[str, Any] = {}
        self._metadata: Dict[str, ComponentMetadata] = {}
        self._type_index: Dict[Type, List[str]] = {}
        self._tag_index: Dict[str, List[str]] = {}

    def register(
        self,
        name: str,
        component: Any,
        component_type: Optional[Type] = None,
        version: str = "1.0.0",
        description: str = "",
        tags: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        config_schema: Optional[Dict] = None,
        enabled: bool = True,
    ) -> None:
        """Register a component."""
        if name in self._components:
            raise ValueError(f"Component '{name}' already registered")

        self._components[name] = component

        if component_type is None:
            component_type = type(component)

        metadata = ComponentMetadata(
            name=name,
            component_type=component_type,
            version=version,
            description=description,
            tags=tags or [],
            dependencies=dependencies or [],
            config_schema=config_schema,
            enabled=enabled,
        )

        self._metadata[name] = metadata

        # Update type index
        if component_type not in self._type_index:
            self._type_index[component_type] = []
        self._type_index[component_type].append(name)

        # Update tag index
        for tag in metadata.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            self._tag_index[tag].append(name)

        logger.debug("Component registered", name=name, type=component_type.__name__)

    def unregister(self, name: str) -> Optional[Any]:
        """Unregister a component."""
        component = self._components.pop(name, None)
        if component is None:
            return None

        metadata = self._metadata.pop(name, None)
        if metadata:
            # Update type index
            if metadata.component_type in self._type_index:
                self._type_index[metadata.component_type].remove(name)
                if not self._type_index[metadata.component_type]:
                    del self._type_index[metadata.component_type]

            # Update tag index
            for tag in metadata.tags:
                if tag in self._tag_index:
                    self._tag_index[tag].remove(name)
                    if not self._tag_index[tag]:
                        del self._tag_index[tag]

        logger.debug("Component unregistered", name=name)
        return component

    def get(self, name: str) -> Optional[Any]:
        """Get a component by name."""
        return self._components.get(name)

    def get_metadata(self, name: str) -> Optional[ComponentMetadata]:
        """Get component metadata."""
        return self._metadata.get(name)

    def get_by_type(self, component_type: Type[T]) -> List[T]:
        """Get all components of a specific type."""
        names = self._type_index.get(component_type, [])
        return [self._components[name] for name in names if name in self._components]

    def get_by_tag(self, tag: str) -> List[Any]:
        """Get all components with a specific tag."""
        names = self._tag_index.get(tag, [])
        return [self._components[name] for name in names if name in self._components]

    def list_names(self) -> List[str]:
        """List all registered component names."""
        return list(self._components.keys())

    def list_metadata(self) -> List[ComponentMetadata]:
        """List all component metadata."""
        return list(self._metadata.values())

    def list_by_type(self, component_type: Type) -> List[str]:
        """List component names by type."""
        return self._type_index.get(component_type, [])

    def list_by_tag(self, tag: str) -> List[str]:
        """List component names by tag."""
        return self._tag_index.get(tag, [])

    def get_all_tags(self) -> List[str]:
        """Get all registered tags."""
        return list(self._tag_index.keys())

    def is_registered(self, name: str) -> bool:
        """Check if a component is registered."""
        return name in self._components

    def enable(self, name: str) -> bool:
        """Enable a component."""
        if name in self._metadata:
            self._metadata[name].enabled = True
            return True
        return False

    def disable(self, name: str) -> bool:
        """Disable a component."""
        if name in self._metadata:
            self._metadata[name].enabled = False
            return True
        return False

    def is_enabled(self, name: str) -> bool:
        """Check if a component is enabled."""
        metadata = self._metadata.get(name)
        return metadata.enabled if metadata else False

    def get_enabled(self) -> List[str]:
        """Get names of all enabled components."""
        return [name for name, meta in self._metadata.items() if meta.enabled]

    def clear(self) -> None:
        """Clear all components."""
        self._components.clear()
        self._metadata.clear()
        self._type_index.clear()
        self._tag_index.clear()

    def __len__(self) -> int:
        return len(self._components)

    def __contains__(self, name: str) -> bool:
        return name in self._components


# Global registries
_registries: Dict[str, Registry] = {}


def get_registry(name: str = "default") -> Registry:
    """Get a registry by name."""
    if name not in _registries:
        _registries[name] = Registry(name)
    return _registries[name]


def create_registry(name: str) -> Registry:
    """Create a new registry."""
    if name in _registries:
        raise ValueError(f"Registry '{name}' already exists")
    registry = Registry(name)
    _registries[name] = registry
    return registry


def remove_registry(name: str) -> bool:
    """Remove a registry."""
    if name in _registries:
        _registries[name].clear()
        del _registries[name]
        return True
    return False


def get_all_registries() -> Dict[str, Registry]:
    """Get all registries."""
    return _registries.copy()