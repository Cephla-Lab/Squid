# GUI Thread Safety Implementation Plan

## Overview

**Problem:** The EventBus delivers events in the *caller's thread*. When services publish from worker threads, widget handlers run in worker threads, causing GUI crashes.

**Solution:** Create `UIEventBus` wrapper that marshals all handler calls to the Qt main thread via `QtEventDispatcher`.

## Key Decisions

1. **MovementService timer:** Use `threading.Timer` (pure Python, no Qt dependency in service layer)
2. **Image routing:** Keep StreamHandler path - controllers trigger events, StreamHandler handles actual image data via Qt signals
3. **Qt wrapper controllers:** Full replacement now - remove QtAutoFocusController and QtMultiPointController

## Files to Create

- `software/squid/qt_event_dispatcher.py` - QtEventDispatcher QObject
- `software/squid/ui_event_bus.py` - UIEventBus wrapper
- `software/squid/services/movement_service.py` - Pure Python position polling

## Files to Modify

- `software/squid/application.py` - Wire up UIEventBus
- `software/control/gui_hcs.py` - Use UIEventBus for widgets
- `software/control/gui/qt_controllers.py` - Remove (Qt wrappers replaced)
- `software/control/gui/signal_connector.py` - Remove obsolete connections
- Various widgets - Migrate from Qt signals to UIEventBus

---

## Phase 0: Event Taxonomy (Prerequisite)

**Goal:** Catalog which events are published from worker threads vs main thread, and which are consumed by UI.

### Task 0.1: Create Event Documentation

**File:** `software/docs/architecture/thread_safety/EVENT_CATALOG.md`

**Action:** Create a catalog of:
1. Events published from worker threads (need UIEventBus for widget handlers)
2. Events published from main thread (safe either way)
3. Events consumed by widgets (must be on UIEventBus)
4. Events consumed by services/controllers only (core EventBus is fine)

**Key worker-thread events (from qt_controllers.py analysis):**
- `AcquisitionStarted`, `AcquisitionFinished`, `AcquisitionProgress` - from MultiPointWorker
- `AutofocusProgress`, `AutofocusCompleted` - from autofocus worker
- `StagePositionChanged` - from movement polling timer
- `ExposureTimeChanged`, `AnalogGainChanged` - from camera callbacks

**Acceptance:** Document exists listing all 95+ events with their source thread.

---

## Phase 1: Implement QtEventDispatcher

**Goal:** Create a QObject that can execute arbitrary callables on the Qt main thread.

### Task 1.1: Create QtEventDispatcher

**File:** `software/squid/qt_event_dispatcher.py`

```python
"""Qt event dispatcher for main-thread execution.

This module provides QtEventDispatcher, a QObject that marshals arbitrary
callables to the Qt main thread via signals/slots.
"""
from typing import Callable, Any
from qtpy.QtCore import QObject, Signal, Slot, QThread
import squid.logging

_log = squid.logging.get_logger(__name__)


class QtEventDispatcher(QObject):
    """Executes callables on the Qt main thread.

    This QObject lives in the main Qt thread and provides a signal that
    can be emitted from any thread. The connected slot runs in the main
    thread, ensuring Qt widget safety.

    Usage:
        dispatcher = QtEventDispatcher()  # Create in main thread

        # From any thread:
        dispatcher.dispatch.emit(my_handler, my_event)
        # my_handler(my_event) will run in main thread
    """

    # Signal: (handler, event) - Qt handles cross-thread marshalling
    dispatch = Signal(object, object)

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self.dispatch.connect(self._on_dispatch)
        self._main_thread = QThread.currentThread()
        _log.debug(f"QtEventDispatcher created in thread {self._main_thread}")

    @Slot(object, object)
    def _on_dispatch(self, handler: Callable, event: Any) -> None:
        """Execute handler(event) in the main thread."""
        try:
            handler(event)
        except Exception as e:
            _log.exception(f"Handler {handler} raised exception for {event}: {e}")

    def is_main_thread(self) -> bool:
        """Return True if called from the Qt main thread."""
        return QThread.currentThread() is self._main_thread
```

**Test file:** `software/tests/unit/squid/test_qt_event_dispatcher.py`

```python
"""Tests for QtEventDispatcher."""
import pytest
import threading
from unittest.mock import MagicMock
from qtpy.QtCore import QThread
from squid.qt_event_dispatcher import QtEventDispatcher


@pytest.fixture
def dispatcher(qtbot):
    """Create dispatcher in Qt main thread."""
    d = QtEventDispatcher()
    yield d


def test_dispatch_from_main_thread(dispatcher, qtbot):
    """Handler runs in main thread when emitted from main thread."""
    handler = MagicMock()
    event = {"test": "data"}

    dispatcher.dispatch.emit(handler, event)
    qtbot.wait(50)  # Allow signal processing

    handler.assert_called_once_with(event)


def test_dispatch_from_worker_thread(dispatcher, qtbot):
    """Handler runs in main thread when emitted from worker thread."""
    handler = MagicMock()
    event = {"test": "data"}
    handler_thread = None

    def capture_thread(e):
        nonlocal handler_thread
        handler_thread = QThread.currentThread()

    handler.side_effect = capture_thread

    # Emit from worker thread
    def worker():
        dispatcher.dispatch.emit(handler, event)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    qtbot.wait(100)  # Allow signal processing

    handler.assert_called_once()
    assert handler_thread is dispatcher._main_thread


def test_handler_exception_does_not_crash(dispatcher, qtbot, caplog):
    """Handler exceptions are logged but don't crash."""
    def bad_handler(e):
        raise ValueError("boom")

    dispatcher.dispatch.emit(bad_handler, {})
    qtbot.wait(50)

    assert "boom" in caplog.text
```

**Run tests:** `cd software && pytest tests/unit/squid/test_qt_event_dispatcher.py -v`

**Commit:** `feat(events): Add QtEventDispatcher for main-thread execution`

---

## Phase 2: Implement UIEventBus

**Goal:** Create a wrapper around EventBus that ensures widget handlers run on the Qt main thread.

### Task 2.1: Create UIEventBus

**File:** `software/squid/ui_event_bus.py`

```python
"""UI-aware event bus wrapper.

UIEventBus wraps the core EventBus and ensures all handler callbacks
are executed on the Qt main thread, making it safe for widget updates.
"""
from typing import Callable, Dict, Tuple, Type
import threading

from squid.events import Event, EventBus
from squid.qt_event_dispatcher import QtEventDispatcher
import squid.logging

_log = squid.logging.get_logger(__name__)


class UIEventBus:
    """Thread-safe event bus for UI components.

    This wrapper ensures all subscribed handlers are called on the Qt
    main thread, regardless of which thread publishes the event.

    Services and controllers should use the core EventBus directly.
    Widgets should use UIEventBus for thread-safe updates.

    Usage:
        core_bus = EventBus()
        dispatcher = QtEventDispatcher()
        ui_bus = UIEventBus(core_bus, dispatcher)

        # Widget subscribes via ui_bus
        ui_bus.subscribe(StagePositionChanged, self._on_position_changed)

        # Any code can publish via core_bus or ui_bus
        core_bus.publish(StagePositionChanged(x=1.0, y=2.0, z=0.5))
        # Handler runs on Qt main thread
    """

    def __init__(self, core_bus: EventBus, dispatcher: QtEventDispatcher):
        self._core_bus = core_bus
        self._dispatcher = dispatcher
        self._wrapper_map: Dict[Tuple[Type[Event], Callable], Callable] = {}
        self._lock = threading.RLock()

    def publish(self, event: Event) -> None:
        """Publish an event to the core bus.

        Events are delivered to all subscribers (both core and UI).
        UI subscribers will have their handlers run on the Qt main thread.
        """
        self._core_bus.publish(event)

    def subscribe(
        self,
        event_type: Type[Event],
        handler: Callable[[Event], None]
    ) -> None:
        """Subscribe a handler that will run on the Qt main thread.

        Args:
            event_type: The event class to subscribe to
            handler: Callback that receives the event (runs on main thread)
        """
        with self._lock:
            # Create wrapper that marshals to main thread
            def wrapper(event: Event, _handler=handler) -> None:
                if self._dispatcher.is_main_thread():
                    # Already on main thread, call directly (optimization)
                    _handler(event)
                else:
                    # Marshal to main thread via Qt signal
                    self._dispatcher.dispatch.emit(_handler, event)

            self._wrapper_map[(event_type, handler)] = wrapper
            self._core_bus.subscribe(event_type, wrapper)
            _log.debug(f"UIEventBus: subscribed {handler} to {event_type.__name__}")

    def unsubscribe(
        self,
        event_type: Type[Event],
        handler: Callable[[Event], None]
    ) -> None:
        """Unsubscribe a handler.

        Args:
            event_type: The event class to unsubscribe from
            handler: The original handler passed to subscribe()
        """
        with self._lock:
            wrapper = self._wrapper_map.pop((event_type, handler), None)

        if wrapper is not None:
            self._core_bus.unsubscribe(event_type, wrapper)
            _log.debug(f"UIEventBus: unsubscribed {handler} from {event_type.__name__}")
        else:
            _log.warning(
                f"UIEventBus: tried to unsubscribe unknown handler {handler} "
                f"from {event_type.__name__}"
            )
```

**Test file:** `software/tests/unit/squid/test_ui_event_bus.py`

```python
"""Tests for UIEventBus."""
import pytest
import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch
from qtpy.QtCore import QThread

from squid.events import Event, EventBus
from squid.qt_event_dispatcher import QtEventDispatcher
from squid.ui_event_bus import UIEventBus


@dataclass(frozen=True)
class TestEvent(Event):
    value: int


@pytest.fixture
def core_bus():
    return EventBus()


@pytest.fixture
def dispatcher(qtbot):
    return QtEventDispatcher()


@pytest.fixture
def ui_bus(core_bus, dispatcher):
    return UIEventBus(core_bus, dispatcher)


def test_publish_from_main_thread(ui_bus, qtbot):
    """Events published from main thread reach handlers."""
    handler = MagicMock()
    ui_bus.subscribe(TestEvent, handler)

    ui_bus.publish(TestEvent(value=42))
    qtbot.wait(50)

    handler.assert_called_once()
    assert handler.call_args[0][0].value == 42


def test_publish_from_worker_thread(ui_bus, core_bus, qtbot):
    """Events published from worker thread still run handler on main thread."""
    handler = MagicMock()
    handler_threads = []

    def track_thread(event):
        handler_threads.append(QThread.currentThread())

    handler.side_effect = track_thread
    ui_bus.subscribe(TestEvent, handler)

    main_thread = QThread.currentThread()

    # Publish from worker thread
    def worker():
        core_bus.publish(TestEvent(value=99))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    qtbot.wait(100)

    handler.assert_called_once()
    assert handler_threads[0] is main_thread  # Handler ran on main thread


def test_core_bus_handler_runs_in_publisher_thread(core_bus, qtbot):
    """Verify core bus handlers run in publisher thread (contrast to UIEventBus)."""
    handler = MagicMock()
    handler_threads = []

    def track_thread(event):
        handler_threads.append(threading.current_thread())

    handler.side_effect = track_thread
    core_bus.subscribe(TestEvent, handler)

    main_thread = threading.current_thread()

    # Publish from worker thread
    worker_thread_ref = [None]
    def worker():
        worker_thread_ref[0] = threading.current_thread()
        core_bus.publish(TestEvent(value=99))

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    handler.assert_called_once()
    assert handler_threads[0] is worker_thread_ref[0]  # Handler ran in worker thread!
    assert handler_threads[0] is not main_thread


def test_unsubscribe(ui_bus, qtbot):
    """Unsubscribed handlers don't receive events."""
    handler = MagicMock()
    ui_bus.subscribe(TestEvent, handler)
    ui_bus.unsubscribe(TestEvent, handler)

    ui_bus.publish(TestEvent(value=42))
    qtbot.wait(50)

    handler.assert_not_called()


def test_multiple_handlers(ui_bus, qtbot):
    """Multiple handlers all receive events on main thread."""
    handler1 = MagicMock()
    handler2 = MagicMock()

    ui_bus.subscribe(TestEvent, handler1)
    ui_bus.subscribe(TestEvent, handler2)

    ui_bus.publish(TestEvent(value=42))
    qtbot.wait(50)

    handler1.assert_called_once()
    handler2.assert_called_once()


def test_handler_exception_isolated(ui_bus, qtbot, caplog):
    """One handler's exception doesn't affect others."""
    def bad_handler(e):
        raise ValueError("boom")

    good_handler = MagicMock()

    ui_bus.subscribe(TestEvent, bad_handler)
    ui_bus.subscribe(TestEvent, good_handler)

    ui_bus.publish(TestEvent(value=42))
    qtbot.wait(100)

    good_handler.assert_called_once()
    assert "boom" in caplog.text
```

**Run tests:** `cd software && pytest tests/unit/squid/test_ui_event_bus.py -v`

**Commit:** `feat(events): Add UIEventBus for thread-safe widget updates`

---

## Phase 3: Wire Application Composition

**Goal:** Create and wire UIEventBus in ApplicationContext.

### Task 3.1: Update ApplicationContext

**File:** `software/squid/application.py`

**Changes:**

1. Import new classes at top:
```python
from squid.qt_event_dispatcher import QtEventDispatcher
from squid.ui_event_bus import UIEventBus
```

2. Add to `__init__`:
```python
# After QApplication exists, create Qt dispatcher
self._qt_dispatcher: Optional[QtEventDispatcher] = None
self._ui_event_bus: Optional[UIEventBus] = None
```

3. Add new method:
```python
def create_ui_event_bus(self) -> UIEventBus:
    """Create UIEventBus for widget subscriptions.

    Must be called from Qt main thread after QApplication is created.
    Returns the UIEventBus that widgets should use for subscriptions.
    """
    if self._ui_event_bus is None:
        self._qt_dispatcher = QtEventDispatcher()
        self._ui_event_bus = UIEventBus(event_bus, self._qt_dispatcher)
        _log.info("Created UIEventBus for thread-safe widget updates")
    return self._ui_event_bus

@property
def ui_event_bus(self) -> Optional[UIEventBus]:
    """Get the UIEventBus, or None if not yet created."""
    return self._ui_event_bus
```

4. Update ServiceRegistry to expose ui_event_bus:
```python
class ServiceRegistry:
    def __init__(self, event_bus: EventBus, ui_event_bus: Optional[UIEventBus] = None):
        self._event_bus = event_bus
        self._ui_event_bus = ui_event_bus

    @property
    def ui_event_bus(self) -> Optional[UIEventBus]:
        return self._ui_event_bus
```

**Test:** Verify ApplicationContext can create UIEventBus:
```python
def test_application_context_creates_ui_event_bus(qtbot):
    ctx = ApplicationContext(simulation=True)
    ui_bus = ctx.create_ui_event_bus()
    assert ui_bus is not None
    assert ctx.ui_event_bus is ui_bus
```

**Commit:** `feat(app): Wire UIEventBus in ApplicationContext`

---

## Phase 4: Update Widget Base Classes

**Goal:** Update EventBusWidget/Frame/Dialog to use UIEventBus.

### Task 4.1: Update Widget Base Classes

**File:** `software/control/widgets/base.py`

**Changes:**

1. Update imports:
```python
from typing import Callable, List, Optional, Tuple, Type, Union
from squid.events import Event, EventBus
from squid.ui_event_bus import UIEventBus
```

2. Update constructor signature and subscriptions:
```python
class EventBusWidget(QWidget):
    """Base widget with EventBus integration.

    Accepts either EventBus or UIEventBus. If UIEventBus is provided,
    handlers are guaranteed to run on the Qt main thread.
    """

    def __init__(
        self,
        event_bus: Union[EventBus, UIEventBus],
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self._event_bus = event_bus
        self._subscriptions: List[Tuple[Type[Event], Callable]] = []

    def _subscribe(
        self,
        event_type: Type[Event],
        handler: Callable[[Event], None]
    ) -> None:
        """Subscribe to an event type with automatic cleanup tracking."""
        self._event_bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def _publish(self, event: Event) -> None:
        """Publish an event."""
        self._event_bus.publish(event)

    def _cleanup_subscriptions(self) -> None:
        """Unsubscribe all tracked subscriptions."""
        for event_type, handler in self._subscriptions:
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

    def closeEvent(self, event) -> None:
        self._cleanup_subscriptions()
        super().closeEvent(event)
```

3. Apply same changes to `EventBusFrame` and `EventBusDialog`.

**Test:** Existing widget tests should pass (API is compatible).

**Commit:** `refactor(widgets): Support UIEventBus in widget base classes`

---

## Phase 5: Update GUI Initialization

**Goal:** Pass UIEventBus to widgets instead of raw EventBus.

### Task 5.1: Update HighContentScreeningGui

**File:** `software/control/gui_hcs.py`

**Changes:**

1. Create UIEventBus early in `__init__`:
```python
def __init__(self, microscope, services, ...):
    # ... existing code ...

    # Create UIEventBus for widget subscriptions
    # Must be done in main thread after QApplication exists
    self._ui_event_bus = services.ui_event_bus
    if self._ui_event_bus is None:
        # Fallback: create from global event_bus
        from squid.qt_event_dispatcher import QtEventDispatcher
        from squid.ui_event_bus import UIEventBus
        self._qt_dispatcher = QtEventDispatcher()
        self._ui_event_bus = UIEventBus(event_bus, self._qt_dispatcher)
```

2. Update widget_factory.py to use `gui._ui_event_bus` instead of `event_bus`.

**Test:** Run GUI in simulation mode, verify no crashes on worker-thread events.

**Commit:** `feat(gui): Use UIEventBus for widget subscriptions`

---

## Phase 6: Replace Qt Wrapper Controllers

**Goal:** Remove QtAutoFocusController, QtMultiPointController, MovementUpdater and use pure EventBus.

### Task 6.1: Add Events for Controller Outputs

**File:** `software/squid/events.py`

**Add events that Qt controllers currently emit as signals:**

```python
# Movement events (from MovementUpdater)
@dataclass(frozen=True)
class StageMovementStopped(Event):
    """Emitted when stage stops moving (debounced)."""
    position: "Pos"

@dataclass(frozen=True)
class PiezoPositionChanged(Event):
    """Emitted when piezo position changes."""
    position_um: float

# Autofocus events (already exist - verify)
# AutofocusProgress, AutofocusCompleted

# Multipoint events (most already exist - verify)
# AcquisitionStarted, AcquisitionFinished, AcquisitionProgress
# Add if missing:
@dataclass(frozen=True)
class ImageCapturedForDisplay(Event):
    """Internal event for display update during acquisition."""
    # Note: Don't put actual image data here - use separate channel
    pass
```

**Commit:** `feat(events): Add events for controller outputs`

### Task 6.2: Create MovementService

**File:** `software/squid/services/movement_service.py`

Replace MovementUpdater with a service that publishes events:

```python
"""Movement monitoring service.

Polls stage/piezo position and publishes events on movement changes.
"""
import threading
from typing import Optional

from squid.abc import AbstractStage, Pos
from squid.services.base import BaseService
from squid.events import (
    Event, EventBus,
    StagePositionChanged, StageMovementStopped, PiezoPositionChanged
)
from control.peripherals.piezo import PiezoStage
import squid.logging

_log = squid.logging.get_logger(__name__)


class MovementService(BaseService):
    """Monitors stage/piezo movement and publishes position events."""

    def __init__(
        self,
        stage: AbstractStage,
        piezo: Optional[PiezoStage],
        event_bus: EventBus,
        poll_interval_ms: int = 100,
        movement_threshold_mm: float = 0.0001,
    ):
        super().__init__(event_bus)
        self._stage = stage
        self._piezo = piezo
        self._poll_interval_ms = poll_interval_ms
        self._movement_threshold_mm = movement_threshold_mm

        self._previous_pos: Optional[Pos] = None
        self._previous_piezo_pos: Optional[float] = None
        self._sent_stopped = False

        self._running = False
        self._timer: Optional[threading.Timer] = None

    def start(self) -> None:
        """Start position polling."""
        self._running = True
        self._schedule_poll()

    def stop(self) -> None:
        """Stop position polling."""
        self._running = False
        if self._timer:
            self._timer.cancel()

    def _schedule_poll(self) -> None:
        if self._running:
            self._timer = threading.Timer(
                self._poll_interval_ms / 1000.0,
                self._do_poll
            )
            self._timer.daemon = True
            self._timer.start()

    def _do_poll(self) -> None:
        try:
            self._poll_once()
        finally:
            self._schedule_poll()

    def _poll_once(self) -> None:
        # Poll piezo
        if self._piezo:
            current_piezo = self._piezo.position
            if self._previous_piezo_pos != current_piezo:
                self._previous_piezo_pos = current_piezo
                self.publish(PiezoPositionChanged(position_um=current_piezo))

        # Poll stage
        pos = self._stage.get_pos()
        if self._previous_pos is None:
            self._previous_pos = pos
            return

        dx = abs(self._previous_pos.x_mm - pos.x_mm)
        dy = abs(self._previous_pos.y_mm - pos.y_mm)
        is_moving = (
            dx > self._movement_threshold_mm or
            dy > self._movement_threshold_mm or
            self._stage.get_state().busy
        )

        if not is_moving and not self._sent_stopped:
            self._sent_stopped = True
            self.publish(StageMovementStopped(position=pos))
        elif is_moving:
            self._sent_stopped = False

        # Always publish position updates
        self.publish(StagePositionChanged(
            x_mm=pos.x_mm, y_mm=pos.y_mm, z_mm=pos.z_mm
        ))

        self._previous_pos = pos
```

**Commit:** `feat(services): Add MovementService to replace MovementUpdater`

### Task 6.3: Refactor AutoFocusController

**File:** `software/control/core/autofocus/autofocus_controller.py`

Remove Qt dependency, use pure EventBus:

1. Remove callback parameters from constructor
2. Publish events instead of calling callbacks:
```python
# Before:
self._on_finished_callback()
self._on_image_callback(image)

# After:
self._event_bus.publish(AutofocusCompleted(success=True))
self._event_bus.publish(ImageCapturedForDisplay())  # Image via StreamHandler
```

**File:** `software/control/gui/qt_controllers.py`

Remove `QtAutoFocusController` class entirely. Update gui_hcs.py to use plain `AutoFocusController`.

**Commit:** `refactor(autofocus): Remove Qt dependency, use EventBus`

### Task 6.4: Refactor MultiPointController

Similar to Task 6.3 - larger change, break into sub-tasks:

1. Remove signal callbacks from MultiPointControllerFunctions
2. Publish events instead:
   - `AcquisitionStarted`, `AcquisitionFinished`, `AcquisitionProgress`
   - **Images continue through StreamHandler** (Qt signals for data plane - this is correct)
   - Controller just calls `streamHandler.on_new_frame()` or similar - no EventBus for image data
3. Remove `QtMultiPointController`
4. Update gui_hcs.py

**Key insight:** The `image_to_display` Qt signal in QtMultiPointController is actually correct architecture - high-frequency image data should NOT go through EventBus. The controller should route images to StreamHandler, which handles the Qt signal emission. Only control events (start/stop/progress) go through EventBus.

**Commit:** `refactor(multipoint): Remove Qt dependency, use EventBus`

---

## Phase 7: Migrate Widget Signal Connections

**Goal:** Replace signal_connector.py connections with EventBus subscriptions.

### Task 7.1: Audit signal_connector.py

Each function in signal_connector.py needs analysis:

| Function | Signals | Migration Strategy |
|----------|---------|-------------------|
| `connect_acquisition_signals` | signal_acquisition_started | Subscribe to AcquisitionStarted/Finished |
| `connect_profile_signals` | signal_profile_changed | Create ProfileChanged event |
| `connect_live_control_signals` | signal_newExposureTime, signal_newAnalogGain | Already have ExposureTimeChanged |
| `connect_navigation_signals` | signal_coordinates_clicked, position_after_move | Subscribe to StageMovementStopped |
| `connect_tab_signals` | currentChanged | Qt signal, keep as-is |
| `connect_wellplate_signals` | signalWellplateSettings | Create WellplateFormatChanged event |
| `connect_display_signals` | image_to_display | Keep StreamHandler signals |
| `connect_laser_autofocus_signals` | Various | Migrate to events |
| `connect_confocal_signals` | signal_toggle_confocal_widefield | Create events |
| `connect_plot_signals` | signal_coordinates | Already events |

### Task 7.2: Add Missing Events

**File:** `software/squid/events.py`

```python
@dataclass(frozen=True)
class ProfileChanged(Event):
    """Emitted when user changes the active profile."""
    profile_name: str

@dataclass(frozen=True)
class WellplateFormatChanged(Event):
    """Emitted when wellplate format changes."""
    format: str
    rows: int
    cols: int
    # ... other wellplate settings
```

**Commit:** `feat(events): Add events for profile and wellplate changes`

### Task 7.3: Update Widgets to Subscribe

For each widget, replace Qt signal connections with EventBus subscriptions.

**Example - NavigationViewer:**

Before (in signal_connector.py):
```python
gui.movement_updater.position_after_move.connect(
    gui.navigationViewer.draw_fov_current_location
)
```

After (in NavigationViewer.__init__):
```python
self._subscribe(StageMovementStopped, self._on_stage_stopped)

def _on_stage_stopped(self, event: StageMovementStopped) -> None:
    self.draw_fov_current_location(event.position)
```

**Commit per widget group:** `refactor(widgets/navigation): Use EventBus subscriptions`

---

## Phase 8: Remove Obsolete Code

### Task 8.1: Remove qt_controllers.py

Once all migrations complete:
1. Remove `MovementUpdater` (replaced by MovementService)
2. Remove `QtAutoFocusController` (replaced by plain AutoFocusController + events)
3. Remove `QtMultiPointController` (replaced by plain MultiPointController + events)

**Commit:** `refactor(gui): Remove obsolete Qt wrapper controllers`

### Task 8.2: Simplify signal_connector.py

Remove functions that are now obsolete. Keep only:
- Tab change signals (Qt-specific)
- StreamHandler image signals (data plane, not control plane)

**Commit:** `refactor(gui): Remove obsolete signal connections`

---

## Phase 9: Testing & Validation

### Task 9.1: Unit Tests

Ensure all new code has tests:
- [ ] `test_qt_event_dispatcher.py` - Cross-thread dispatch
- [ ] `test_ui_event_bus.py` - Wrapper behavior
- [ ] `test_movement_service.py` - Position polling
- [ ] Widget tests - Subscription cleanup

### Task 9.2: Integration Tests

**File:** `software/tests/integration/squid/test_gui_thread_safety.py`

```python
"""Integration tests for GUI thread safety."""
import pytest
import threading
from qtpy.QtCore import QThread

from squid.events import event_bus, StagePositionChanged
from squid.application import ApplicationContext


def test_widget_receives_worker_event_on_main_thread(qtbot):
    """Widget handlers run on main thread even when event is from worker."""
    ctx = ApplicationContext(simulation=True)
    ui_bus = ctx.create_ui_event_bus()

    handler_threads = []
    def handler(event):
        handler_threads.append(QThread.currentThread())

    ui_bus.subscribe(StagePositionChanged, handler)
    main_thread = QThread.currentThread()

    # Publish from worker thread (simulating service)
    def worker():
        event_bus.publish(StagePositionChanged(x_mm=1.0, y_mm=2.0, z_mm=0.0))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    qtbot.wait(100)

    assert len(handler_threads) == 1
    assert handler_threads[0] is main_thread
```

### Task 9.3: Manual Testing

Run full GUI and verify:
1. Start acquisition - no crashes
2. Position updates display correctly
3. Live view works
4. Autofocus completes without crash
5. All widgets update smoothly

---

## Commit Strategy

Follow TDD - write tests first, then implementation.

**Commit order:**
1. `feat(events): Add QtEventDispatcher for main-thread execution`
2. `feat(events): Add UIEventBus for thread-safe widget updates`
3. `feat(app): Wire UIEventBus in ApplicationContext`
4. `refactor(widgets): Support UIEventBus in widget base classes`
5. `feat(gui): Use UIEventBus for widget subscriptions`
6. `feat(events): Add events for controller outputs`
7. `feat(services): Add MovementService to replace MovementUpdater`
8. `refactor(autofocus): Remove Qt dependency, use EventBus`
9. `refactor(multipoint): Remove Qt dependency, use EventBus`
10. `feat(events): Add events for profile and wellplate changes`
11. `refactor(widgets/*): Use EventBus subscriptions` (per widget group)
12. `refactor(gui): Remove obsolete Qt wrapper controllers`
13. `refactor(gui): Remove obsolete signal connections`
14. `docs: Update architecture documentation`

---

## File Summary

**New Files:**
- `software/squid/qt_event_dispatcher.py`
- `software/squid/ui_event_bus.py`
- `software/squid/services/movement_service.py`
- `software/tests/unit/squid/test_qt_event_dispatcher.py`
- `software/tests/unit/squid/test_ui_event_bus.py`
- `software/tests/unit/squid/services/test_movement_service.py`
- `software/tests/integration/squid/test_gui_thread_safety.py`
- `software/docs/architecture/thread_safety/EVENT_CATALOG.md`

**Modified Files:**
- `software/squid/events.py` - Add new events
- `software/squid/application.py` - Wire UIEventBus
- `software/squid/services/__init__.py` - Export MovementService
- `software/control/widgets/base.py` - Support UIEventBus
- `software/control/gui_hcs.py` - Use UIEventBus
- `software/control/gui/widget_factory.py` - Pass UIEventBus
- `software/control/gui/signal_connector.py` - Remove obsolete connections
- `software/control/gui/qt_controllers.py` - Remove (or simplify)
- `software/control/core/autofocus/autofocus_controller.py` - Remove callbacks
- `software/control/core/acquisition/multi_point_controller.py` - Remove callbacks
- Various widgets in `software/control/widgets/` - Add subscriptions

---

## Success Criteria

- [ ] All widget handlers run on Qt main thread
- [ ] No Qt crashes from worker-thread GUI access
- [ ] StreamHandler data plane unchanged (still uses Qt signals)
- [ ] All existing tests pass
- [ ] New unit tests for QtEventDispatcher, UIEventBus
- [ ] Integration test proves thread safety
- [ ] GUI works in simulation mode
- [ ] GUI works with real hardware
