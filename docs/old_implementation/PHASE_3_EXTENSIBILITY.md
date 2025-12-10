# Phase 3: Extensibility Foundation

**Goal**: Create infrastructure for plugin-style registration and decoupled communication.

**Impact**: Enables adding new cameras, algorithms, and features without modifying existing code.

**Estimated Effort**: 1 week

---

## Checklist

### Task 3.1: Create Registry utility
- [ ] Create test file `software/tests/squid/test_registry.py`
- [ ] Run tests (should fail)
- [ ] Create `software/squid/registry.py`
- [ ] Run tests (should pass)
- [ ] Commit: "Add Registry utility for plugin registration"

### Task 3.2: Create EventBus utility
- [ ] Create test file `software/tests/squid/test_events.py`
- [ ] Run tests (should fail)
- [ ] Create `software/squid/events.py`
- [ ] Run tests (should pass)
- [ ] Commit: "Add EventBus for decoupled communication"

### Task 3.3: Register existing cameras with Registry
- [ ] Modify `software/squid/camera/utils.py` to create camera_registry
- [ ] Add @camera_registry.register to SimulatedCamera
- [ ] Modify `software/control/camera_toupcam.py` to register ToupcamCamera
- [ ] Update get_camera() to use registry
- [ ] Run camera tests
- [ ] Commit: "Register cameras with Registry, simplify factory"

---

## Task 3.1: Create Registry utility

### Test File

**File**: `software/tests/squid/test_registry.py`

```python
"""Tests for Registry utility."""
import pytest
from squid.registry import Registry


class TestRegistry:
    """Test suite for Registry."""

    def test_register_decorator(self):
        """@registry.register decorator should register class."""
        registry = Registry[object]("test")

        @registry.register("my_impl")
        class MyImpl:
            def __init__(self, value):
                self.value = value

        assert "my_impl" in registry.available()

    def test_create_instance(self):
        """create() should instantiate registered class."""
        registry = Registry[object]("test")

        @registry.register("my_impl")
        class MyImpl:
            def __init__(self, value):
                self.value = value

        instance = registry.create("my_impl", 42)
        assert instance.value == 42

    def test_create_with_kwargs(self):
        """create() should pass kwargs to constructor."""
        registry = Registry[object]("test")

        @registry.register("configurable")
        class Configurable:
            def __init__(self, name, count=1):
                self.name = name
                self.count = count

        instance = registry.create("configurable", "test", count=5)
        assert instance.name == "test"
        assert instance.count == 5

    def test_available_lists_all(self):
        """available() should list all registered names."""
        registry = Registry[object]("test")

        @registry.register("impl_a")
        class ImplA:
            pass

        @registry.register("impl_b")
        class ImplB:
            pass

        available = registry.available()
        assert "impl_a" in available
        assert "impl_b" in available

    def test_unknown_raises_keyerror(self):
        """create() with unknown name should raise KeyError."""
        registry = Registry[object]("test")

        with pytest.raises(KeyError) as exc_info:
            registry.create("nonexistent")

        assert "nonexistent" in str(exc_info.value)
        assert "Available:" in str(exc_info.value)

    def test_register_factory(self):
        """register_factory() should register a factory function."""
        registry = Registry[str]("test")

        registry.register_factory("greeting", lambda name: f"Hello, {name}!")

        result = registry.create("greeting", "World")
        assert result == "Hello, World!"

    def test_get_class(self):
        """get_class() should return the registered class."""
        registry = Registry[object]("test")

        @registry.register("my_class")
        class MyClass:
            pass

        cls = registry.get_class("my_class")
        assert cls is MyClass

    def test_get_class_returns_none_for_factory(self):
        """get_class() should return None for factory registrations."""
        registry = Registry[object]("test")
        registry.register_factory("factory", lambda: None)

        cls = registry.get_class("factory")
        assert cls is None

    def test_registry_name_in_error(self):
        """Error message should include registry name."""
        registry = Registry[object]("camera")

        with pytest.raises(KeyError) as exc_info:
            registry.create("missing")

        assert "camera" in str(exc_info.value)
```

### Implementation File

**File**: `software/squid/registry.py`

```python
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
```

### Run Tests

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/test_registry.py -v
```

### Commit

```bash
git add software/squid/registry.py software/tests/squid/test_registry.py
git commit -m "Add Registry utility for plugin registration

Provides generic registry supporting:
- Class registration via @registry.register decorator
- Factory function registration
- Listing available implementations
- Error messages showing available options

Enables plugin-style architecture where new implementations
register themselves without modifying factory code.

Part of extensibility improvements - see docs/EXTENSIBILITY.md Section 5.
"
```

---

## Task 3.2: Create EventBus utility

### Test File

**File**: `software/tests/squid/test_events.py`

```python
"""Tests for EventBus utility."""
import pytest
from dataclasses import dataclass
from squid.events import Event, EventBus


@dataclass
class TestEvent(Event):
    """Test event for unit tests."""
    message: str


@dataclass
class OtherEvent(Event):
    """Another test event."""
    value: int


class TestEventBus:
    """Test suite for EventBus."""

    def test_subscribe_and_publish(self):
        """Subscribers should receive published events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="hello"))

        assert len(received) == 1
        assert received[0].message == "hello"

    def test_multiple_subscribers(self):
        """Multiple subscribers should all receive events."""
        bus = EventBus()
        received_a = []
        received_b = []

        bus.subscribe(TestEvent, lambda e: received_a.append(e))
        bus.subscribe(TestEvent, lambda e: received_b.append(e))

        bus.publish(TestEvent(message="test"))

        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_different_event_types(self):
        """Subscribers only receive their event type."""
        bus = EventBus()
        test_events = []
        other_events = []

        bus.subscribe(TestEvent, lambda e: test_events.append(e))
        bus.subscribe(OtherEvent, lambda e: other_events.append(e))

        bus.publish(TestEvent(message="test"))
        bus.publish(OtherEvent(value=42))

        assert len(test_events) == 1
        assert len(other_events) == 1
        assert test_events[0].message == "test"
        assert other_events[0].value == 42

    def test_unsubscribe(self):
        """Unsubscribed handlers should not receive events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="first"))

        bus.unsubscribe(TestEvent, handler)
        bus.publish(TestEvent(message="second"))

        assert len(received) == 1
        assert received[0].message == "first"

    def test_handler_exception_doesnt_crash(self):
        """Exception in handler should not crash bus."""
        bus = EventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("handler error")

        def good_handler(event):
            received.append(event)

        bus.subscribe(TestEvent, bad_handler)
        bus.subscribe(TestEvent, good_handler)

        # Should not raise
        bus.publish(TestEvent(message="test"))

        # Good handler should still receive event
        assert len(received) == 1

    def test_clear(self):
        """clear() should remove all subscriptions."""
        bus = EventBus()
        received = []

        bus.subscribe(TestEvent, lambda e: received.append(e))
        bus.clear()
        bus.publish(TestEvent(message="test"))

        assert len(received) == 0
```

### Implementation File

**File**: `software/squid/events.py`

```python
"""
Event bus for decoupled component communication.

Provides a simple publish/subscribe mechanism that allows components
to communicate without direct references to each other.

Usage:
    from squid.events import Event, EventBus, event_bus

    # Define event types as dataclasses
    @dataclass
    class ImageCaptured(Event):
        frame: CameraFrame
        info: CaptureInfo

    # Subscribe to events
    event_bus.subscribe(ImageCaptured, lambda e: display(e.frame))

    # Publish events
    event_bus.publish(ImageCaptured(frame=frame, info=info))
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Type, TypeVar
from threading import Lock
import squid.logging

_log = squid.logging.get_logger("squid.events")


@dataclass
class Event:
    """Base class for all events."""
    pass


E = TypeVar('E', bound=Event)


class EventBus:
    """
    Simple event bus for decoupled communication.

    Thread-safe publish/subscribe mechanism. Handler exceptions
    are logged but do not crash the bus.

    Example:
        bus = EventBus()

        # Subscribe
        bus.subscribe(ImageCaptured, self.on_image)

        # Publish
        bus.publish(ImageCaptured(frame=frame, info=info))

        # Unsubscribe
        bus.unsubscribe(ImageCaptured, self.on_image)
    """

    def __init__(self):
        self._subscribers: Dict[Type[Event], List[Callable]] = {}
        self._lock = Lock()

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: The event class to subscribe to
            handler: Function called with event when published
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """
        Unsubscribe from an event type.

        Args:
            event_type: The event class to unsubscribe from
            handler: The handler to remove
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                except ValueError:
                    pass  # Handler not in list

    def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribers.

        Args:
            event: The event to publish
        """
        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                _log.exception(f"Handler {handler} failed for event {event}: {e}")

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscribers.clear()


# Global event bus instance
# Can be replaced with dependency injection if needed
event_bus = EventBus()


# Common event types

@dataclass
class AcquisitionStarted(Event):
    """Emitted when acquisition begins."""
    experiment_id: str
    timestamp: float


@dataclass
class AcquisitionFinished(Event):
    """Emitted when acquisition completes."""
    success: bool
    error: Exception = None


@dataclass
class ImageCaptured(Event):
    """Emitted when an image is captured."""
    frame_id: int
    # Note: Don't include heavy objects like frame data
    # Use frame_id to look up from a cache instead


@dataclass
class StageMovedTo(Event):
    """Emitted when stage moves to a position."""
    x_mm: float
    y_mm: float
    z_mm: float


@dataclass
class FocusChanged(Event):
    """Emitted when focus changes."""
    z_mm: float
    source: str  # "autofocus", "manual", "focus_map"
```

### Run Tests

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/test_events.py -v
```

### Commit

```bash
git add software/squid/events.py software/tests/squid/test_events.py
git commit -m "Add EventBus for decoupled communication

Provides publish/subscribe mechanism for component communication:
- Thread-safe subscribe/unsubscribe/publish
- Exception isolation (handler errors don't crash bus)
- Common event types (AcquisitionStarted, ImageCaptured, etc.)
- Global event_bus instance for convenience

Enables adding new features without modifying existing code.

Part of extensibility improvements - see docs/EXTENSIBILITY.md Section 6.
"
```

---

## Task 3.3: Register existing cameras with Registry

### Files to Modify

1. `software/squid/camera/utils.py`
2. `software/control/camera_toupcam.py`
3. Other camera files (optional)

### Changes to squid/camera/utils.py

Add registry and register SimulatedCamera:

```python
# At top of file, add:
from squid.registry import Registry
from squid.abc import AbstractCamera

# Create camera registry
camera_registry = Registry[AbstractCamera]("camera")

# ... existing code ...

# Change SimulatedCamera to use decorator:
@camera_registry.register("simulated")
class SimulatedCamera(AbstractCamera):
    # ... existing implementation ...

# Update get_camera function:
def get_camera(
    config: CameraConfig,
    simulated: bool = False,
    hw_trigger_fn: Optional[Callable[[Optional[float]], bool]] = None,
    hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]] = None,
) -> AbstractCamera:
    """
    Create a camera instance based on configuration.

    Uses the camera registry for extensible camera support.
    """
    if simulated:
        return camera_registry.create(
            "simulated",
            config,
            hw_trigger_fn=hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
        )

    camera_name = config.camera_type.value.lower()

    # Try to import and register camera on demand
    _ensure_camera_registered(camera_name, config)

    try:
        camera = camera_registry.create(
            camera_name,
            config,
            hw_trigger_fn=hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
        )
        _open_if_needed(camera)
        return camera
    except KeyError:
        _log.warning(f"Camera '{camera_name}' not in registry, using default")
        return camera_registry.create(
            "default",
            config,
            hw_trigger_fn=hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
        )


def _ensure_camera_registered(camera_name: str, config: CameraConfig):
    """Import camera module to trigger registration."""
    if camera_registry.is_registered(camera_name):
        return

    # Map camera names to modules
    module_map = {
        "toupcam": "control.camera_toupcam",
        "flir": "control.camera_flir",
        "hamamatsu": "control.camera_hamamatsu",
        "ids": "control.camera_ids",
        "tucsen": "control.camera_tucsen",
        "photometrics": "control.camera_photometrics",
        "andor": "control.camera_andor",
        "tis": "control.camera_TIS",
    }

    module_name = module_map.get(camera_name)
    if module_name:
        try:
            __import__(module_name)
        except ImportError as e:
            _log.warning(f"Failed to import {module_name}: {e}")


def _open_if_needed(camera):
    """Open camera if it has an open() method."""
    try:
        camera.open()
    except AttributeError:
        pass
```

### Changes to control/camera_toupcam.py

Add registration:

```python
# At top of file, after imports:
from squid.camera.utils import camera_registry

# Add decorator to class:
@camera_registry.register("toupcam")
class ToupcamCamera(AbstractCamera):
    # ... existing implementation ...
```

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/test_camera.py -v
```

### Commit

```bash
git add software/squid/camera/utils.py software/control/camera_toupcam.py
git commit -m "Register cameras with Registry, simplify factory

- Add camera_registry to squid/camera/utils.py
- Register SimulatedCamera with @camera_registry.register
- Register ToupcamCamera with @camera_registry.register
- Update get_camera() to use registry instead of if/elif chain
- Add _ensure_camera_registered() for lazy loading

New cameras can now self-register without modifying get_camera().

Part of extensibility improvements - see docs/EXTENSIBILITY.md Section 5.
"
```

---

## Phase 3 Complete

After completing all tasks:

1. Run full test suite:
```bash
pytest --tb=short -v
```

2. Verify camera creation still works:
```bash
python -c "
from squid.camera.utils import camera_registry, get_camera
from squid.config import CameraConfig, CameraVariant
print('Available cameras:', camera_registry.available())
config = CameraConfig(camera_type=CameraVariant.TOUPCAM)
camera = get_camera(config, simulated=True)
print('Created:', type(camera).__name__)
"
```
