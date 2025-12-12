# Coding Standards for Squid Refactoring

This document defines the patterns, conventions, and principles for the architecture refactoring. Read this before working on any phase.

---

## Table of Contents

1. [Core Principles](#core-principles)
2. [File Organization](#file-organization)
3. [Event Naming Conventions](#event-naming-conventions)
4. [Service Pattern](#service-pattern)
5. [Controller Pattern](#controller-pattern)
6. [Widget Pattern](#widget-pattern)
7. [Thread Safety Rules](#thread-safety-rules)
8. [Import Guidelines](#import-guidelines)
9. [Error Handling](#error-handling)

---

## Core Principles

### DRY (Don't Repeat Yourself)
- Extract common patterns into base classes or utilities
- Use `BaseService` for all services
- Use shared state dataclasses
- Reference `CODING_STANDARDS.md` instead of duplicating patterns

### YAGNI (You Aren't Gonna Need It)
- Only implement what's required for the current task
- Don't add "nice to have" features
- Don't over-engineer for hypothetical future needs
- Delete code that isn't used

### TDD (Test-Driven Development)
- Write the test first
- Write minimum code to pass the test
- Refactor while keeping tests green
- Run tests after every change

### Frequent Commits
- Commit after each logical subtask
- Use conventional commit format
- Keep commits small and reviewable
- Don't batch unrelated changes

---

## File Organization

### Directory Structure

```
squid/
├── abc.py                    # Hardware protocols (Protocols, not ABCs)
├── events.py                 # All event definitions (commands + state)
├── application.py            # DI container (ApplicationContext)
├── registry.py               # Plugin registry
├── exceptions.py             # Custom exceptions
├── logging.py                # Logging setup
├── config/                   # Pydantic configuration models
├── services/                 # Hardware services (thread-safe wrappers)
│   ├── base.py               # BaseService ABC
│   ├── camera_service.py
│   ├── stage_service.py
│   ├── peripheral_service.py
│   ├── illumination_service.py
│   └── filter_wheel_service.py
├── controllers/              # NEW: Domain controllers
│   ├── __init__.py
│   ├── microscope_mode_controller.py
│   └── peripherals_controller.py
└── utils/
    ├── safe_callback.py
    ├── thread_safe_state.py
    └── worker_manager.py

control/
├── gui_hcs.py                # Main window
├── widgets/                  # All Qt widgets (pure presentation)
├── core/
│   ├── display/              # LiveController, StreamHandler
│   ├── acquisition/          # MultiPointController, MultiPointWorker
│   ├── autofocus/            # AutoFocusController, LaserAFController
│   ├── navigation/           # ScanCoordinates, FocusMap, ObjectiveStore
│   ├── configuration/        # ConfigurationManager, ChannelConfigurationManager
│   └── tracking/             # TrackingController
└── peripherals/              # Hardware drivers
```

### Where to Put New Code

| Code Type | Location |
|-----------|----------|
| New event | `squid/events.py` |
| New hardware protocol | `squid/abc.py` |
| New hardware service | `squid/services/{name}_service.py` |
| New domain controller | `squid/controllers/{name}_controller.py` |
| New widget | `control/widgets/{category}/{name}.py` |
| New hardware driver | `control/peripherals/{category}/{name}.py` |

---

## Event Naming Conventions

Events are the communication mechanism between layers. There are two types:

### Command Events (GUI → Controller/Service)

**Format:** `{Action}{Target}Command`

**Purpose:** Request a change or action

**Examples:**
```python
@dataclass(frozen=True)
class SetExposureTimeCommand(Event):
    """Request camera exposure time change."""
    exposure_time_ms: float

@dataclass(frozen=True)
class StartLiveCommand(Event):
    """Request to start live camera preview."""
    configuration: str | None = None

@dataclass(frozen=True)
class MoveStageCommand(Event):
    """Request relative stage movement."""
    axis: str  # 'x', 'y', or 'z'
    distance_mm: float
```

### State Events (Controller/Service → GUI)

**Format:** `{Target}{State}Changed` or `{Action}Completed`

**Purpose:** Notify that state has changed

**Examples:**
```python
@dataclass(frozen=True)
class ExposureTimeChanged(Event):
    """Camera exposure time was changed."""
    exposure_time_ms: float

@dataclass(frozen=True)
class LiveStateChanged(Event):
    """Live view state changed."""
    is_live: bool
    configuration: str | None = None

@dataclass(frozen=True)
class AutofocusCompleted(Event):
    """Autofocus finished."""
    success: bool
    z_position: float | None
    score: float | None
    error: str | None = None
```

### Event Rules

1. **All events must be frozen dataclasses:**
   ```python
   @dataclass(frozen=True)
   class MyEvent(Event):
       field: type
   ```

2. **Events are immutable** - never modify after creation

3. **Events should be small** - only include necessary data

4. **Use Optional for optional fields:**
   ```python
   @dataclass(frozen=True)
   class MyEvent(Event):
       required_field: str
       optional_field: str | None = None
   ```

---

## Service Pattern

Services are **thread-safe hardware wrappers**. They:
- Subscribe to command events
- Call hardware methods (with locking)
- Publish state change events
- Do NOT contain orchestration logic

### Service Template

```python
# squid/services/example_service.py

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from squid.events import (
    Event,
    SetExampleCommand,
    ExampleChanged,
)
from squid.services.base import BaseService

if TYPE_CHECKING:
    from squid.abc import AbstractExample
    from squid.events import EventBus


class ExampleService(BaseService):
    """Thread-safe example hardware operations.

    Subscribes to: SetExampleCommand
    Publishes: ExampleChanged
    """

    def __init__(self, hardware: AbstractExample, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self._hardware = hardware
        self._lock = threading.RLock()

        # Subscribe to commands
        self.subscribe(SetExampleCommand, self._on_set_example)

    def _on_set_example(self, cmd: SetExampleCommand) -> None:
        """Handle SetExampleCommand."""
        with self._lock:
            # Validate
            validated_value = self._validate(cmd.value)
            # Call hardware
            self._hardware.set_value(validated_value)
        # Publish state change (outside lock)
        self.publish(ExampleChanged(value=validated_value))

    def _validate(self, value: float) -> float:
        """Validate and clamp value to hardware limits."""
        min_val, max_val = self._hardware.get_limits()
        return max(min_val, min(max_val, value))

    # Direct access methods for controllers
    def get_value(self) -> float:
        """Get current value. Thread-safe."""
        with self._lock:
            return self._hardware.get_value()

    def set_value(self, value: float) -> None:
        """Set value directly. Thread-safe. Publishes event."""
        with self._lock:
            validated = self._validate(value)
            self._hardware.set_value(validated)
        self.publish(ExampleChanged(value=validated))
```

### Service Rules

1. **Always use `threading.RLock()`** for hardware access
2. **Publish events outside the lock** to avoid deadlocks
3. **Validate inputs** before calling hardware
4. **Log warnings** for unsupported operations (don't raise)
5. **Provide direct methods** for controller use (not just event handlers)

---

## Controller Pattern

Controllers **orchestrate workflows** and **own state**. They:
- Subscribe to command events
- Coordinate multiple services
- Manage state machines
- Publish state change events

### Controller Template

```python
# squid/controllers/example_controller.py

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from squid.events import (
    Event,
    StartExampleCommand,
    StopExampleCommand,
    ExampleStateChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services import CameraService, StageService


@dataclass
class ExampleState:
    """State managed by ExampleController."""
    is_running: bool = False
    current_value: float = 0.0
    error: str | None = None


class ExampleController:
    """Orchestrates example workflow.

    Coordinates: CameraService, StageService
    Subscribes to: StartExampleCommand, StopExampleCommand
    Publishes: ExampleStateChanged
    """

    def __init__(
        self,
        camera_service: CameraService,
        stage_service: StageService,
        event_bus: EventBus,
    ) -> None:
        self._camera = camera_service
        self._stage = stage_service
        self._bus = event_bus

        self._state = ExampleState()
        self._lock = threading.RLock()

        # Subscribe to commands
        self._bus.subscribe(StartExampleCommand, self._on_start)
        self._bus.subscribe(StopExampleCommand, self._on_stop)

    @property
    def state(self) -> ExampleState:
        """Current state (read-only)."""
        with self._lock:
            return self._state

    def _on_start(self, cmd: StartExampleCommand) -> None:
        """Handle StartExampleCommand."""
        with self._lock:
            if self._state.is_running:
                return  # Already running
            self._state = replace(self._state, is_running=True, error=None)

        # Coordinate services
        self._camera.start_streaming(self._on_frame)

        # Publish state change
        self._bus.publish(ExampleStateChanged(
            is_running=True,
            current_value=self._state.current_value,
        ))

    def _on_stop(self, cmd: StopExampleCommand) -> None:
        """Handle StopExampleCommand."""
        with self._lock:
            if not self._state.is_running:
                return  # Not running
            self._state = replace(self._state, is_running=False)

        # Stop services
        self._camera.stop_streaming()

        # Publish state change
        self._bus.publish(ExampleStateChanged(
            is_running=False,
            current_value=self._state.current_value,
        ))

    def _on_frame(self, frame) -> None:
        """Callback from camera service."""
        # Process frame...
        pass

    # Methods for other controllers to call
    def stop_for_other_operation(self) -> None:
        """Stop if running, for coordination with other controllers."""
        if self._state.is_running:
            self._on_stop(StopExampleCommand())
```

### Controller Rules

1. **Use `@dataclass` for state** - immutable, use `replace()` to update
2. **Own the state** - only this controller modifies its state
3. **Coordinate services** - call service methods, don't access hardware directly
4. **Use events for GUI communication** - don't call widgets
5. **Provide coordination methods** - for other controllers to call

---

## Widget Pattern

Widgets are **pure presentation**. They:
- Subscribe to state events
- Render state to UI
- Publish command events on user interaction
- Do NOT call services or controllers directly

### Widget Template

```python
# control/widgets/example/example_widget.py

from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QWidget, QVBoxLayout, QSpinBox, QPushButton

from squid.events import (
    SetExampleCommand,
    ExampleChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus


class ExampleWidget(QWidget):
    """Widget for controlling example feature.

    Subscribes to: ExampleChanged
    Publishes: SetExampleCommand
    """

    def __init__(self, event_bus: EventBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bus = event_bus

        self._setup_ui()
        self._connect_events()

    def _setup_ui(self) -> None:
        """Create UI elements."""
        layout = QVBoxLayout(self)

        # Value spinbox
        self._value_spinbox = QSpinBox()
        self._value_spinbox.setRange(0, 100)
        self._value_spinbox.valueChanged.connect(self._on_value_input)
        layout.addWidget(self._value_spinbox)

        # Action button
        self._action_button = QPushButton("Do Action")
        self._action_button.clicked.connect(self._on_action_clicked)
        layout.addWidget(self._action_button)

    def _connect_events(self) -> None:
        """Subscribe to state events."""
        self._bus.subscribe(ExampleChanged, self._on_example_changed)

    # User input → publish command
    def _on_value_input(self, value: int) -> None:
        """User changed value spinbox."""
        self._bus.publish(SetExampleCommand(value=float(value)))

    def _on_action_clicked(self) -> None:
        """User clicked action button."""
        self._bus.publish(StartExampleCommand())

    # State event → update UI
    def _on_example_changed(self, event: ExampleChanged) -> None:
        """State changed, update UI."""
        # Block signals to prevent feedback loop
        self._value_spinbox.blockSignals(True)
        self._value_spinbox.setValue(int(event.value))
        self._value_spinbox.blockSignals(False)
```

### Widget Rules

1. **Only communicate via EventBus** - no service/controller/hardware calls
2. **Use `blockSignals(True)` when updating from events** - prevents feedback loops
3. **Keep logic minimal** - validation belongs in services
4. **Don't store duplicate state** - subscribe to events for current state

### Anti-Patterns to Avoid

```python
# BAD: Direct hardware access
def _on_button_clicked(self):
    self.camera.set_exposure_time(100)  # NO!

# BAD: Direct service call
def _on_button_clicked(self):
    self.camera_service.set_exposure_time(100)  # NO!

# BAD: Direct controller call
def _on_button_clicked(self):
    self.live_controller.start_live()  # NO!

# GOOD: Publish command event
def _on_button_clicked(self):
    self._bus.publish(SetExposureTimeCommand(exposure_time_ms=100))  # YES!
```

---

## Thread Safety Rules

### Rule 1: Services Lock Hardware Access

```python
class CameraService(BaseService):
    def __init__(self, camera, event_bus):
        super().__init__(event_bus)
        self._camera = camera
        self._lock = threading.RLock()  # REQUIRED

    def set_exposure(self, ms: float) -> None:
        with self._lock:  # REQUIRED
            self._camera.set_exposure_time(ms)
```

### Rule 2: Never Block EventBus Handlers

EventBus handlers run synchronously. Long operations block all events.

```python
# BAD: Blocking operation in handler
def _on_start_acquisition(self, cmd):
    for i in range(1000):
        self._stage.move_to(...)  # Blocks everything!
        self._camera.capture()

# GOOD: Spawn worker thread
def _on_start_acquisition(self, cmd):
    threading.Thread(
        target=self._run_acquisition,
        daemon=True
    ).start()

def _run_acquisition(self):
    for i in range(1000):
        self._stage.move_to(...)
        self._camera.capture()
```

### Rule 3: Use ThreadSafeValue for Shared State

```python
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

# Thread-safe value wrapper
self._current_z = ThreadSafeValue(0.0)
self._current_z.set(1.5)
z = self._current_z.get()

# Thread-safe flag
self._stop_requested = ThreadSafeFlag()
self._stop_requested.set()
if self._stop_requested.is_set():
    return
```

### Rule 4: Publish Events Outside Locks

```python
# BAD: Publishing inside lock can deadlock
def _on_command(self, cmd):
    with self._lock:
        self._hardware.do_thing()
        self.publish(ThingDone())  # If handler tries to acquire same lock = deadlock

# GOOD: Publish after releasing lock
def _on_command(self, cmd):
    with self._lock:
        self._hardware.do_thing()
        result = self._hardware.get_result()
    self.publish(ThingDone(result=result))  # Safe
```

---

## Import Guidelines

### Use TYPE_CHECKING for Circular Imports

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.abc import AbstractCamera
    from squid.events import EventBus
    from squid.services import CameraService


class MyController:
    def __init__(
        self,
        camera: AbstractCamera,  # OK - only used for type hint
        event_bus: EventBus,
        camera_service: CameraService,
    ) -> None:
        ...
```

### Prefer Absolute Imports

```python
# GOOD: Absolute imports
from squid.events import SetExposureTimeCommand
from squid.services.camera_service import CameraService
from control.core.display.live_controller import LiveController

# BAD: Relative imports (harder to understand)
from ..events import SetExposureTimeCommand
from .camera_service import CameraService
```

### Import Order

```python
# 1. Standard library
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

# 2. Third-party
import numpy as np
from qtpy.QtWidgets import QWidget

# 3. Local - squid package
from squid.events import Event, SetExposureTimeCommand
from squid.services.base import BaseService

# 4. Local - control package
from control.core.display.live_controller import LiveController

# 5. TYPE_CHECKING imports (at top, after from __future__)
if TYPE_CHECKING:
    from squid.abc import AbstractCamera
```

---

## Error Handling

### Services: Log Warnings, Don't Raise

```python
def _on_set_gain(self, cmd: SetAnalogGainCommand) -> None:
    gain_range = self._camera.get_gain_range()
    if gain_range is None:
        # Log warning, don't crash
        self._log.warning("Camera does not support analog gain")
        return

    clamped = max(gain_range.min_gain, min(gain_range.max_gain, cmd.gain))
    with self._lock:
        self._camera.set_analog_gain(clamped)
    self.publish(AnalogGainChanged(gain=clamped))
```

### Controllers: Catch and Report

```python
def _run_acquisition(self) -> None:
    try:
        self._do_acquisition()
        self._bus.publish(AcquisitionFinished(success=True))
    except Exception as e:
        self._log.exception(f"Acquisition failed: {e}")
        self._bus.publish(AcquisitionFinished(success=False, error=str(e)))
```

### Use safe_callback for Callbacks

```python
from squid.utils.safe_callback import safe_callback

def _on_frame(self, frame):
    result = safe_callback(self._process_frame, frame)
    if not result.success:
        self._log.error(f"Frame processing failed: {result.error}")
```
