"""
Generic registry for plugin-style implementations.

Allows implementations to self-register, making it easy to add
new cameras, autofocus algorithms, etc. without modifying factory code.

Usage:
    # Define registry
    camera_registry = Registry[AbstractCamera]("camera")

    # Register implementations
    @camera_registry.register("toupcam")
    class ToupcamCamera(AbstractCamera):
        ...

    # Or register factory function
    camera_registry.register_factory("simulated", lambda cfg: SimulatedCamera(cfg))

    # Create instance by name
    camera = camera_registry.create("toupcam", config)

    # List available implementations
    print(camera_registry.available())  # ["toupcam", "simulated", ...]
"""
from typing import TypeVar, Generic, Dict, Type, Callable, Optional, List, Any

T = TypeVar('T')


class Registry(Generic[T]):
    """
    Generic registry for plugin implementations.

    Supports both class registration (via decorator) and factory
    function registration for more complex instantiation.
    """

    def __init__(self, name: str):
        """
        Initialize registry.

        Args:
            name: Human-readable name for error messages (e.g., "camera", "autofocus")
        """
        self.name = name
        self._implementations: Dict[str, Type[T]] = {}
        self._factories: Dict[str, Callable[..., T]] = {}

    def register(self, name: str):
        """
        Decorator to register a class.

        Args:
            name: Name to register under

        Returns:
            Decorator function

        Example:
            @camera_registry.register("toupcam")
            class ToupcamCamera(AbstractCamera):
                ...
        """
        def decorator(cls: Type[T]) -> Type[T]:
            self._implementations[name] = cls
            return cls
        return decorator

    def register_factory(self, name: str, factory: Callable[..., T]) -> None:
        """
        Register a factory function.

        Args:
            name: Name to register under
            factory: Function that creates instances

        Example:
            camera_registry.register_factory(
                "simulated",
                lambda cfg: SimulatedCamera(cfg)
            )
        """
        self._factories[name] = factory

    def create(self, name: str, *args: Any, **kwargs: Any) -> T:
        """
        Create an instance by name.

        Args:
            name: Registered name
            *args: Positional arguments for constructor/factory
            **kwargs: Keyword arguments for constructor/factory

        Returns:
            New instance

        Raises:
            KeyError: If name not registered
        """
        if name in self._factories:
            return self._factories[name](*args, **kwargs)
        if name in self._implementations:
            return self._implementations[name](*args, **kwargs)
        raise KeyError(
            f"Unknown {self.name}: '{name}'. "
            f"Available: {self.available()}"
        )

    def available(self) -> List[str]:
        """
        List available implementations.

        Returns:
            Sorted list of registered names
        """
        return sorted(set(self._implementations.keys()) | set(self._factories.keys()))

    def get_class(self, name: str) -> Optional[Type[T]]:
        """
        Get the class for a name.

        Args:
            name: Registered name

        Returns:
            Class if registered as class, None if factory or not found
        """
        return self._implementations.get(name)

    def is_registered(self, name: str) -> bool:
        """Check if a name is registered."""
        return name in self._implementations or name in self._factories
