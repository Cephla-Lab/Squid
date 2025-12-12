# Phase 5D: Qt Controllers Refactor

## Goal

Decouple `qt_controllers.py` from direct widget connections by:
1. **Control plane** → EventBus (start/stop, progress, coordinates)
2. **Data plane** → New `AcquisitionStreamHandler` (image frames, napari updates)

This eliminates the dense signal wiring in `gui_hcs.py` and `signal_connector.py`, making widgets self-contained.

---

## Background Reading

Before starting, read these files to understand the current architecture:

| File | Why |
|------|-----|
| `squid/events.py` | Existing event definitions and EventBus implementation |
| `control/gui/qt_controllers.py` | Current Qt wrappers we're refactoring |
| `control/gui/signal_connector.py` | Current signal wiring we're eliminating |
| `control/core/display/stream_handler.py` | Existing StreamHandler pattern for live view |
| `control/core/acquisition/multi_point_controller.py` | Base controller we're modifying |
| `docs/architecture/implementation/PHASE_5C_WIDGET_CLEANUP.md` | Previous phase context |

---

## Architecture Rules

### Control Plane (EventBus)
- Lightweight dataclass events
- State changes, commands, progress updates
- Subscribers process asynchronously
- **Never** put image data here

### Data Plane (StreamHandler)
- High-frequency image frames
- Qt signals for thread-safe GUI updates
- Throttling/buffering logic lives here
- Connected via `signal.connect(slot)`

### Why Two Planes?
EventBus handlers run synchronously in sequence. 60fps image data would block all other event processing. StreamHandler uses Qt's signal queue which handles this correctly.

---

## Pre-Implementation Checklist

- [ ] Read all background files listed above
- [ ] Run existing tests: `pytest tests/unit/control -v`
- [ ] Run the app in simulation: `python main_hcs.py --simulation`
- [ ] Verify multipoint acquisition works (Wellplate Multipoint tab → Start)
- [ ] Note current behavior: napari updates, progress bar, z-plot updates

---

## Task 0: Make EventBus Thread-Safe (PREREQUISITE)

**Why this is critical:**

Widgets that subscribe to EventBus have their handlers called in the publisher's thread. If a worker thread publishes an event, the widget handler runs on that worker thread and tries to update Qt UI - this causes crashes, corruption, or undefined behavior.

**The problem:**
```python
# Worker thread publishes
class MultiPointWorker:
    def on_progress(self):
        self._event_bus.publish(AcquisitionProgress(...))  # Worker thread

# Widget subscribed
class ProgressWidget(QWidget):
    def __init__(self, event_bus):
        event_bus.subscribe(AcquisitionProgress, self._on_progress)

    def _on_progress(self, event):
        # RUNS ON WORKER THREAD - UNSAFE!
        self.progress_bar.setValue(event.current_region)  # Qt widget update from wrong thread
```

**Files to modify:**
- `squid/events.py`

**Implementation:**

Replace the current `EventBus` with a thread-safe version that always dispatches handlers on the main thread:

```python
# squid/events.py

from dataclasses import dataclass
from typing import Callable, Dict, List, Type, TypeVar, Optional
from threading import Lock
import squid.logging

_log = squid.logging.get_logger("squid.events")

# Try to import Qt, but make it optional for non-GUI usage
try:
    from qtpy.QtCore import QObject, Signal, QThread, QCoreApplication
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False
    QObject = object  # Fallback for type hints


@dataclass
class Event:
    """Base class for all events."""
    pass


E = TypeVar("E", bound=Event)


class EventBus(QObject if _QT_AVAILABLE else object):
    """
    Thread-safe event bus for decoupled communication.

    When Qt is available, handlers are ALWAYS dispatched on the main thread,
    regardless of which thread publishes the event. This makes it safe for
    widgets to subscribe and update their UI in handlers.

    When Qt is not available (e.g., headless/testing), handlers run
    synchronously in the publisher's thread.

    Example:
        bus = EventBus()

        # Subscribe (handler will always run on main thread)
        bus.subscribe(ImageCaptured, self.on_image)

        # Publish (can be called from any thread)
        bus.publish(ImageCaptured(frame=frame, info=info))

    Thread Safety:
        - subscribe/unsubscribe: Thread-safe (uses lock)
        - publish: Thread-safe (queues to main thread if needed)
        - handlers: Always run on main thread when Qt is available
    """

    # Qt signal for cross-thread dispatch (only exists if Qt available)
    if _QT_AVAILABLE:
        _dispatch_signal = Signal(object, object)  # (event, handlers)

    def __init__(self):
        if _QT_AVAILABLE:
            super().__init__()
            self._dispatch_signal.connect(self._dispatch_on_main_thread)
            self._main_thread = QThread.currentThread()

        self._subscribers: Dict[Type[Event], List[Callable]] = {}
        self._lock = Lock()
        self._debug = False

    def set_debug(self, enabled: bool) -> None:
        """Enable or disable debug mode to print all events."""
        self._debug = enabled
        if enabled:
            _log.info("EventBus debug mode enabled - all events will be logged")

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """
        Subscribe to an event type.

        The handler will always be called on the main thread (when Qt is available),
        regardless of which thread publishes the event.

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

        Can be called from any thread. Handlers will be dispatched on the
        main thread (when Qt is available).

        Args:
            event: The event to publish
        """
        if self._debug:
            _log.debug(f"[EventBus] {type(event).__name__}: {event}")

        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))

        if not handlers:
            return

        if _QT_AVAILABLE:
            # Check if we're on the main thread
            if QThread.currentThread() == self._main_thread:
                # Already on main thread, dispatch directly
                self._dispatch_handlers(event, handlers)
            else:
                # Queue for main thread dispatch via Qt signal
                self._dispatch_signal.emit(event, handlers)
        else:
            # No Qt, dispatch synchronously (for testing/headless)
            self._dispatch_handlers(event, handlers)

    def _dispatch_on_main_thread(self, event: Event, handlers: List[Callable]) -> None:
        """Called on main thread via Qt signal connection."""
        self._dispatch_handlers(event, handlers)

    def _dispatch_handlers(self, event: Event, handlers: List[Callable]) -> None:
        """Dispatch event to all handlers (must be called on main thread)."""
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
event_bus = EventBus()
```

**Key changes from current implementation:**

1. `EventBus` now inherits from `QObject` (when Qt available)
2. Has a `_dispatch_signal` Qt signal for cross-thread dispatch
3. `publish()` checks current thread and queues to main thread if needed
4. Falls back to synchronous dispatch when Qt not available (for testing)

**Testing:**

```python
# tests/unit/squid/test_events_thread_safety.py

import pytest
import threading
import time
from unittest.mock import MagicMock, patch

from squid.events import EventBus, Event
from dataclasses import dataclass


@dataclass
class TestEvent(Event):
    value: int


class TestEventBusThreadSafety:
    """Test thread-safety of EventBus."""

    def test_handler_called_on_main_thread_when_published_from_main(self, qtbot):
        """Handler should run on main thread when published from main thread."""
        from qtpy.QtCore import QThread, QCoreApplication

        bus = EventBus()
        handler_thread = None

        def handler(event):
            nonlocal handler_thread
            handler_thread = QThread.currentThread()

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(value=42))

        # Process events
        QCoreApplication.processEvents()

        assert handler_thread == QCoreApplication.instance().thread()

    def test_handler_called_on_main_thread_when_published_from_worker(self, qtbot):
        """Handler should run on main thread even when published from worker."""
        from qtpy.QtCore import QThread, QCoreApplication

        bus = EventBus()
        handler_thread = None
        event_received = threading.Event()

        def handler(event):
            nonlocal handler_thread
            handler_thread = QThread.currentThread()
            event_received.set()

        bus.subscribe(TestEvent, handler)

        # Publish from worker thread
        def worker():
            bus.publish(TestEvent(value=42))

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        worker_thread.join()

        # Process Qt events to dispatch the handler
        for _ in range(10):
            QCoreApplication.processEvents()
            if event_received.is_set():
                break
            time.sleep(0.01)

        assert event_received.is_set(), "Handler was never called"
        assert handler_thread == QCoreApplication.instance().thread()

    def test_multiple_handlers_all_called_on_main_thread(self, qtbot):
        """All handlers should run on main thread."""
        from qtpy.QtCore import QThread, QCoreApplication

        bus = EventBus()
        handler_threads = []

        def make_handler():
            def handler(event):
                handler_threads.append(QThread.currentThread())
            return handler

        # Subscribe multiple handlers
        for _ in range(3):
            bus.subscribe(TestEvent, make_handler())

        # Publish from worker
        def worker():
            bus.publish(TestEvent(value=42))

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        worker_thread.join()

        # Process events
        for _ in range(10):
            QCoreApplication.processEvents()
            if len(handler_threads) == 3:
                break
            time.sleep(0.01)

        main_thread = QCoreApplication.instance().thread()
        assert all(t == main_thread for t in handler_threads)

    def test_subscribe_unsubscribe_thread_safe(self):
        """Subscribe/unsubscribe should be thread-safe."""
        bus = EventBus()
        handlers = [MagicMock() for _ in range(100)]

        def subscriber():
            for h in handlers[:50]:
                bus.subscribe(TestEvent, h)

        def unsubscriber():
            for h in handlers[:50]:
                bus.unsubscribe(TestEvent, h)

        # Run subscribe and unsubscribe concurrently
        threads = [
            threading.Thread(target=subscriber),
            threading.Thread(target=unsubscriber),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not crash - exact state depends on timing


class TestEventBusWithoutQt:
    """Test EventBus behavior when Qt is not available."""

    def test_synchronous_dispatch_without_qt(self):
        """Without Qt, handlers run synchronously in publisher thread."""
        # This test would require mocking _QT_AVAILABLE = False
        # For now, just verify the fallback path exists
        pass


# Run with: pytest tests/unit/squid/test_events_thread_safety.py -v
```

**Integration test to verify real-world safety:**

```python
# tests/integration/test_widget_thread_safety.py

import pytest
import threading
import time

from squid.events import event_bus, Event
from dataclasses import dataclass
from qtpy.QtWidgets import QWidget, QLabel, QVBoxLayout
from qtpy.QtCore import QCoreApplication


@dataclass
class CounterUpdated(Event):
    value: int


class CounterWidget(QWidget):
    """Test widget that updates UI on event."""

    def __init__(self):
        super().__init__()
        self.label = QLabel("0")
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        self.setLayout(layout)

        self.update_count = 0
        event_bus.subscribe(CounterUpdated, self._on_counter_updated)

    def _on_counter_updated(self, event: CounterUpdated):
        # This would crash if called from wrong thread
        self.label.setText(str(event.value))
        self.update_count += 1


class TestWidgetThreadSafety:

    def test_widget_survives_events_from_worker_thread(self, qtbot):
        """Widget should safely handle events published from worker threads."""
        widget = CounterWidget()
        qtbot.addWidget(widget)
        widget.show()

        # Publish many events from worker thread
        def worker():
            for i in range(100):
                event_bus.publish(CounterUpdated(value=i))
                time.sleep(0.001)

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()

        # Process events while worker runs
        while worker_thread.is_alive() or widget.update_count < 100:
            QCoreApplication.processEvents()
            time.sleep(0.01)
            if widget.update_count >= 100:
                break

        worker_thread.join()

        # Should have received all updates without crashing
        assert widget.update_count == 100
        assert widget.label.text() == "99"
```

**Run tests:**
```bash
pytest tests/unit/squid/test_events_thread_safety.py -v
pytest tests/integration/test_widget_thread_safety.py -v
```

**Commit:** `fix(events): Make EventBus thread-safe for Qt widget handlers`

---

**IMPORTANT:** Complete Task 0 before proceeding to other tasks. All subsequent tasks assume EventBus is thread-safe.

---

## Task 1: Add New Events to EventBus

**Files to modify:**
- `squid/events.py`

**What to add:**

```python
# ============================================================================
# Multi-Point Acquisition Control Events
# ============================================================================

@dataclass
class MultiPointAcquisitionProgress(Event):
    """Overall acquisition progress.

    Published by: MultiPointController
    Consumed by: Acquisition widgets (progress bar), GUI (tab management)
    """
    current_region: int
    total_regions: int
    current_timepoint: int


@dataclass
class AcquisitionRegionFOVProgress(Event):
    """Progress within a single region.

    Published by: MultiPointController
    Consumed by: Acquisition widgets (progress bar)
    """
    current_fov: int
    total_fovs: int


@dataclass
class AcquisitionCoordinates(Event):
    """Position data captured during acquisition.

    Published by: MultiPointController (for every captured frame)
    Consumed by: SurfacePlotWidget (z-plot), FocusMapWidget

    Note: This is lightweight position data, NOT image data.
    """
    x_mm: float
    y_mm: float
    z_mm: float
    region_id: int


@dataclass
class CurrentFOVRegistered(Event):
    """FOV position to mark in navigation viewer.

    Published by: MultiPointController (after each FOV captured)
    Consumed by: NavigationViewer
    """
    x_mm: float
    y_mm: float


@dataclass
class SetDisplayTabsRequest(Event):
    """Request to configure display tabs for acquisition type.

    Published by: MultiPointController (at acquisition start)
    Consumed by: HighContentScreeningGui
    """
    configuration_names: list[str]  # Channel names
    n_z: int  # Number of z planes (affects which tab to show)


@dataclass
class StageStoppedAtPosition(Event):
    """Stage has stopped moving and settled at position.

    Published by: MovementUpdater (after motion completes)
    Consumed by: NavigationViewer (draw FOV), acquisition widgets

    Different from StagePositionChanged which fires continuously during moves.
    """
    x_mm: float
    y_mm: float
    z_mm: float
```

**Testing:**

```python
# tests/unit/squid/test_events.py - add these tests

def test_multipoint_acquisition_progress_event():
    event = MultiPointAcquisitionProgress(
        current_region=1,
        total_regions=5,
        current_timepoint=0,
    )
    assert event.current_region == 1
    assert event.total_regions == 5


def test_acquisition_coordinates_event():
    event = AcquisitionCoordinates(
        x_mm=1.5, y_mm=2.5, z_mm=0.1, region_id=0
    )
    assert event.x_mm == 1.5
    assert event.region_id == 0


def test_stage_stopped_at_position_event():
    event = StageStoppedAtPosition(x_mm=10.0, y_mm=20.0, z_mm=0.5)
    assert event.x_mm == 10.0
```

**Commit:** `feat(events): Add multipoint acquisition control events`

---

## Task 2: Create AcquisitionStreamHandler

**Files to create:**
- `control/core/display/acquisition_stream_handler.py`

**Files to modify:**
- `control/core/display/__init__.py` (add export)

**Implementation:**

```python
# control/core/display/acquisition_stream_handler.py
"""
Data stream handler for acquisition images.

This is the DATA PLANE for multipoint acquisition - handles high-frequency
image routing with throttling for UI performance.

Control events (start/stop/progress) go through EventBus instead.
"""

from typing import TYPE_CHECKING, Optional
from qtpy.QtCore import QObject, Signal
import numpy as np

import control._def
import squid.logging

if TYPE_CHECKING:
    from control.core.acquisition import CaptureInfo
    from control.core.navigation import ObjectiveStore

_log = squid.logging.get_logger(__name__)


class AcquisitionStreamHandler(QObject):
    """Routes acquisition images to display widgets with throttling.

    Responsibilities:
    - Frame throttling (emit every Nth frame during large acquisitions)
    - Frame buffering (keep recent frames, flush at end)
    - Napari layer initialization (lazy, on first frame)
    - Layer naming (objective magnification + channel name)

    Usage:
        handler = AcquisitionStreamHandler(objective_store)
        handler.napari_layers_update.connect(napari_widget.updateLayers)

        # At acquisition start
        handler.start_acquisition(is_single_fov=False)

        # For each captured frame
        handler.on_frame_captured(frame, capture_info)

        # At acquisition end
        handler.finish_acquisition()
    """

    # Signals for image display (DATA PLANE)
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)  # frame, illumination_source
    napari_layers_init = Signal(int, int, object)  # height, width, dtype
    napari_layers_update = Signal(np.ndarray, float, float, int, str)  # frame, x_mm, y_mm, z_idx, layer_name

    def __init__(self, objective_store: "ObjectiveStore"):
        """
        Args:
            objective_store: For getting current magnification for layer names
        """
        super().__init__()
        self._objective_store = objective_store
        self._reset_state()

    def _reset_state(self) -> None:
        """Reset all state for a new acquisition."""
        self._frame_count: int = 0
        self._pending_frames: list[tuple[np.ndarray, "CaptureInfo"]] = []
        self._napari_initialized: bool = False
        self._is_single_fov: bool = False

    def start_acquisition(self, is_single_fov: bool = False) -> None:
        """Call at acquisition start to reset state.

        Args:
            is_single_fov: True for single-FOV snap (no throttling),
                          False for multi-FOV acquisition (may throttle)
        """
        _log.debug(f"AcquisitionStreamHandler.start_acquisition(is_single_fov={is_single_fov})")
        self._reset_state()
        self._is_single_fov = is_single_fov

    def finish_acquisition(self) -> None:
        """Call at acquisition end to flush any buffered frames."""
        _log.debug(f"AcquisitionStreamHandler.finish_acquisition() - flushing {len(self._pending_frames)} frames")
        for frame, info in self._pending_frames:
            self._emit_frame(frame, info)
        self._pending_frames.clear()

    def on_frame_captured(self, frame: np.ndarray, info: "CaptureInfo") -> None:
        """Process a captured frame - may throttle or buffer.

        Args:
            frame: Image data as numpy array
            info: Capture metadata (position, channel, z_index, etc.)
        """
        self._frame_count += 1

        # Determine if we should emit this frame
        emit_every_n = control._def.MULTIPOINT_DISPLAY_EVERY_NTH or 0

        # Always emit for single-FOV snaps or if display is enabled
        should_emit = self._is_single_fov or control._def.MULTIPOINT_DISPLAY_IMAGES

        # Throttle: emit every Nth frame during large acquisitions
        if not should_emit and emit_every_n > 0:
            should_emit = self._frame_count % emit_every_n == 0

        if should_emit:
            # Flush any buffered frames first (preserves order)
            for buffered_frame, buffered_info in self._pending_frames:
                self._emit_frame(buffered_frame, buffered_info)
            self._pending_frames.clear()
            self._emit_frame(frame, info)
        elif emit_every_n > 0:
            # Buffer frame for potential flush at end
            max_buffer = max(emit_every_n - 1, 1)
            self._pending_frames.append((frame, info))
            if len(self._pending_frames) > max_buffer:
                self._pending_frames.pop(0)  # Drop oldest

    def _emit_frame(self, frame: np.ndarray, info: "CaptureInfo") -> None:
        """Emit frame to all connected displays."""
        # Basic image display
        self.image_to_display.emit(frame)
        self.image_to_display_multi.emit(frame, info.configuration.illumination_source)

        # Lazy napari initialization on first frame
        if not self._napari_initialized:
            self._napari_initialized = True
            _log.debug(f"Initializing napari layers: shape={frame.shape}, dtype={frame.dtype}")
            self.napari_layers_init.emit(frame.shape[0], frame.shape[1], frame.dtype)

        # Build layer name: "20x BF"
        try:
            mag = int(self._objective_store.get_current_objective_info()["magnification"])
        except (KeyError, TypeError):
            mag = 1  # Fallback
        layer_name = f"{mag}x {info.configuration.name}"

        self.napari_layers_update.emit(
            frame,
            info.position.x_mm,
            info.position.y_mm,
            info.z_index,
            layer_name,
        )
```

**Update `__init__.py`:**

```python
# control/core/display/__init__.py - add to exports
from control.core.display.acquisition_stream_handler import AcquisitionStreamHandler
```

**Testing:**

```python
# tests/unit/control/core/display/test_acquisition_stream_handler.py

import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from qtpy.QtCore import QObject

from control.core.display.acquisition_stream_handler import AcquisitionStreamHandler


@pytest.fixture
def mock_objective_store():
    store = MagicMock()
    store.get_current_objective_info.return_value = {"magnification": 20}
    return store


@pytest.fixture
def mock_capture_info():
    info = MagicMock()
    info.configuration.name = "BF"
    info.configuration.illumination_source = 0
    info.position.x_mm = 1.0
    info.position.y_mm = 2.0
    info.z_index = 0
    return info


@pytest.fixture
def stream_handler(mock_objective_store):
    return AcquisitionStreamHandler(mock_objective_store)


class TestAcquisitionStreamHandler:
    def test_init_resets_state(self, stream_handler):
        assert stream_handler._frame_count == 0
        assert stream_handler._pending_frames == []
        assert stream_handler._napari_initialized == False

    def test_start_acquisition_resets_state(self, stream_handler):
        stream_handler._frame_count = 10
        stream_handler._napari_initialized = True

        stream_handler.start_acquisition(is_single_fov=True)

        assert stream_handler._frame_count == 0
        assert stream_handler._is_single_fov == True
        assert stream_handler._napari_initialized == False

    def test_single_fov_always_emits(self, stream_handler, mock_capture_info):
        """Single-FOV snaps should always emit, no throttling."""
        stream_handler.start_acquisition(is_single_fov=True)

        emitted = []
        stream_handler.image_to_display.connect(lambda img: emitted.append(img))

        frame = np.zeros((100, 100), dtype=np.uint16)
        stream_handler.on_frame_captured(frame, mock_capture_info)

        assert len(emitted) == 1

    @patch('control._def.MULTIPOINT_DISPLAY_EVERY_NTH', 3)
    @patch('control._def.MULTIPOINT_DISPLAY_IMAGES', False)
    def test_throttling_emits_every_nth(self, stream_handler, mock_capture_info):
        """Should emit every Nth frame when throttling enabled."""
        stream_handler.start_acquisition(is_single_fov=False)

        emitted = []
        stream_handler.image_to_display.connect(lambda img: emitted.append(img))

        frame = np.zeros((100, 100), dtype=np.uint16)
        for _ in range(9):
            stream_handler.on_frame_captured(frame, mock_capture_info)

        # With emit_every_n=3, frames 3, 6, 9 should emit
        assert len(emitted) == 3

    @patch('control._def.MULTIPOINT_DISPLAY_EVERY_NTH', 3)
    @patch('control._def.MULTIPOINT_DISPLAY_IMAGES', False)
    def test_finish_flushes_buffered_frames(self, stream_handler, mock_capture_info):
        """Buffered frames should be flushed at acquisition end."""
        stream_handler.start_acquisition(is_single_fov=False)

        emitted = []
        stream_handler.image_to_display.connect(lambda img: emitted.append(img))

        frame = np.zeros((100, 100), dtype=np.uint16)
        # Capture 4 frames: 3 emits, 4 is buffered
        for _ in range(4):
            stream_handler.on_frame_captured(frame, mock_capture_info)

        assert len(emitted) == 1  # Only frame 3 emitted so far

        stream_handler.finish_acquisition()

        assert len(emitted) == 2  # Frame 4 now flushed

    def test_napari_init_emitted_once(self, stream_handler, mock_capture_info):
        """napari_layers_init should only emit on first frame."""
        stream_handler.start_acquisition(is_single_fov=True)

        init_calls = []
        stream_handler.napari_layers_init.connect(
            lambda h, w, d: init_calls.append((h, w, d))
        )

        frame = np.zeros((100, 200), dtype=np.uint16)
        stream_handler.on_frame_captured(frame, mock_capture_info)
        stream_handler.on_frame_captured(frame, mock_capture_info)

        assert len(init_calls) == 1
        assert init_calls[0] == (100, 200, np.uint16)

    def test_layer_name_includes_magnification(self, stream_handler, mock_capture_info):
        """Layer name should be '{mag}x {channel}'."""
        stream_handler.start_acquisition(is_single_fov=True)

        layer_names = []
        stream_handler.napari_layers_update.connect(
            lambda f, x, y, z, name: layer_names.append(name)
        )

        frame = np.zeros((100, 100), dtype=np.uint16)
        stream_handler.on_frame_captured(frame, mock_capture_info)

        assert layer_names[0] == "20x BF"
```

**Run tests:** `pytest tests/unit/control/core/display/test_acquisition_stream_handler.py -v`

**Commit:** `feat(display): Add AcquisitionStreamHandler for acquisition image routing`

---

## Task 3: Refactor MultiPointController to Use EventBus

**Files to modify:**
- `control/core/acquisition/multi_point_controller.py`

**What to change:**

The controller currently uses a `callbacks` object with function pointers. We'll add EventBus publishing alongside (for backward compatibility during migration).

```python
# In MultiPointController.__init__, add parameters:

def __init__(
    self,
    # ... existing params ...
    # NEW: Optional EventBus for control events
    event_bus: Optional["EventBus"] = None,
    # NEW: Optional stream handler for image data
    acquisition_stream_handler: Optional["AcquisitionStreamHandler"] = None,
):
    self._event_bus = event_bus
    self._acquisition_stream_handler = acquisition_stream_handler
    # ... rest of init ...
```

```python
# Modify _signal_acquisition_start (or equivalent method):

def _on_acquisition_started(self, parameters: AcquisitionParameters):
    # DATA PLANE: Prepare stream handler
    if self._acquisition_stream_handler:
        self._acquisition_stream_handler.start_acquisition(
            is_single_fov=self.run_acquisition_current_fov
        )

    # CONTROL PLANE: Publish events
    if self._event_bus:
        from squid.events import AcquisitionStarted, SetDisplayTabsRequest
        import time

        self._event_bus.publish(AcquisitionStarted(
            experiment_id=getattr(parameters, 'experiment_id', ''),
            timestamp=time.time(),
        ))

        config_names = [c.name for c in self.selected_configurations]
        n_z = self.NZ if not self.run_acquisition_current_fov else 2
        self._event_bus.publish(SetDisplayTabsRequest(
            configuration_names=config_names,
            n_z=n_z,
        ))

    # LEGACY: Call callback if provided (backward compat)
    if self._callbacks and self._callbacks.signal_acquisition_start:
        self._callbacks.signal_acquisition_start(parameters)
```

```python
# Modify image capture handling:

def _on_image_captured(self, frame: CameraFrame, info: CaptureInfo):
    # DATA PLANE: Route to stream handler (handles throttling)
    if self._acquisition_stream_handler:
        self._acquisition_stream_handler.on_frame_captured(frame.frame, info)

    # CONTROL PLANE: Always publish coordinates (lightweight)
    if self._event_bus:
        from squid.events import AcquisitionCoordinates
        self._event_bus.publish(AcquisitionCoordinates(
            x_mm=info.position.x_mm,
            y_mm=info.position.y_mm,
            z_mm=info.position.z_mm,
            region_id=info.region_id,
        ))

    # LEGACY: Call callback if provided
    if self._callbacks and self._callbacks.signal_new_image:
        self._callbacks.signal_new_image(frame, info)
```

```python
# Modify acquisition finish:

def _on_acquisition_finished(self):
    # DATA PLANE: Flush buffered frames
    if self._acquisition_stream_handler:
        self._acquisition_stream_handler.finish_acquisition()

    # CONTROL PLANE: Publish events
    if self._event_bus:
        from squid.events import AcquisitionFinished, CurrentFOVRegistered

        self._event_bus.publish(AcquisitionFinished(success=True))

        # Get final position
        if self._stage_service:
            pos = self._stage_service.get_position()
        else:
            pos = self.stage.get_pos()
        self._event_bus.publish(CurrentFOVRegistered(
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
        ))

    # LEGACY callback
    if self._callbacks and self._callbacks.signal_acquisition_finished:
        self._callbacks.signal_acquisition_finished()
```

```python
# Modify progress updates:

def _on_overall_progress(self, progress: OverallProgressUpdate):
    if self._event_bus:
        from squid.events import MultiPointAcquisitionProgress
        self._event_bus.publish(MultiPointAcquisitionProgress(
            current_region=progress.current_region,
            total_regions=progress.total_regions,
            current_timepoint=progress.current_timepoint,
        ))

    # LEGACY callback
    if self._callbacks and self._callbacks.signal_overall_progress:
        self._callbacks.signal_overall_progress(progress)


def _on_region_progress(self, progress: RegionProgressUpdate):
    if self._event_bus:
        from squid.events import AcquisitionRegionFOVProgress
        self._event_bus.publish(AcquisitionRegionFOVProgress(
            current_fov=progress.current_fov,
            total_fovs=progress.region_fovs,
        ))

    # LEGACY callback
    if self._callbacks and self._callbacks.signal_region_progress:
        self._callbacks.signal_region_progress(progress)
```

**Testing:**

Add to existing controller tests:

```python
# tests/unit/control/core/acquisition/test_multi_point_controller.py

def test_publishes_acquisition_started_event(mock_event_bus, controller):
    controller._event_bus = mock_event_bus
    controller._on_acquisition_started(mock_parameters)

    calls = [c for c in mock_event_bus.publish.call_args_list
             if isinstance(c[0][0], AcquisitionStarted)]
    assert len(calls) == 1


def test_publishes_coordinates_on_image_capture(mock_event_bus, controller):
    controller._event_bus = mock_event_bus
    controller._on_image_captured(mock_frame, mock_info)

    calls = [c for c in mock_event_bus.publish.call_args_list
             if isinstance(c[0][0], AcquisitionCoordinates)]
    assert len(calls) == 1
    assert calls[0][0][0].x_mm == mock_info.position.x_mm


def test_routes_images_to_stream_handler(mock_stream_handler, controller):
    controller._acquisition_stream_handler = mock_stream_handler
    controller._on_image_captured(mock_frame, mock_info)

    mock_stream_handler.on_frame_captured.assert_called_once()
```

**Commit:** `refactor(acquisition): Add EventBus and StreamHandler support to MultiPointController`

---

## Task 4: Simplify QtMultiPointController

**Files to modify:**
- `control/gui/qt_controllers.py`

**What to change:**

Remove the callback implementations and signal emissions that are now handled by EventBus/StreamHandler:

```python
class QtMultiPointController(MultiPointController, QObject):
    """Qt wrapper for MultiPointController.

    After refactor:
    - Control events (progress, coordinates) → EventBus (handled by parent)
    - Image data → AcquisitionStreamHandler (passed to parent)
    - This class just provides QObject inheritance for Qt integration

    DEPRECATED signals (kept for backward compat during migration):
    - acquisition_finished → Use AcquisitionFinished event
    - signal_acquisition_start → Use AcquisitionStarted event
    - signal_acquisition_progress → Use MultiPointAcquisitionProgress event
    - signal_region_progress → Use AcquisitionRegionFOVProgress event
    - signal_coordinates → Use AcquisitionCoordinates event
    - signal_register_current_fov → Use CurrentFOVRegistered event
    - signal_set_display_tabs → Use SetDisplayTabsRequest event
    - signal_current_configuration → Use MicroscopeModeChanged event

    Image signals REMOVED (use AcquisitionStreamHandler instead):
    - image_to_display
    - image_to_display_multi
    - napari_layers_init
    - napari_layers_update
    """

    # DEPRECATED: Keep these temporarily for backward compatibility
    # Will be removed once all widgets subscribe to EventBus
    acquisition_finished = Signal()
    signal_acquisition_start = Signal()
    signal_acquisition_progress = Signal(int, int, int)
    signal_region_progress = Signal(int, int)
    signal_coordinates = Signal(float, float, float, int)
    signal_register_current_fov = Signal(float, float)
    signal_set_display_tabs = Signal(list, int)
    signal_current_configuration = Signal(object)  # ChannelMode

    def __init__(
        self,
        microscope: Microscope,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        channel_configuration_manager: ChannelConfigurationManager,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
        fluidics: Optional[Any] = None,
        # Service-based parameters
        camera_service: Optional["CameraService"] = None,
        stage_service: Optional["StageService"] = None,
        peripheral_service: Optional["PeripheralService"] = None,
        piezo_service: Optional["PiezoService"] = None,
        event_bus: Optional["EventBus"] = None,
        # NEW: Stream handler for image data
        acquisition_stream_handler: Optional["AcquisitionStreamHandler"] = None,
    ):
        QObject.__init__(self)

        # Create callbacks that emit Qt signals (backward compat)
        # These will be removed once widgets use EventBus directly
        callbacks = MultiPointControllerFunctions(
            signal_acquisition_start=self._legacy_acquisition_start,
            signal_acquisition_finished=self._legacy_acquisition_finished,
            signal_new_image=None,  # Handled by stream handler now
            signal_current_configuration=self._legacy_current_configuration,
            signal_current_fov=self._legacy_current_fov,
            signal_overall_progress=self._legacy_overall_progress,
            signal_region_progress=self._legacy_region_progress,
        )

        MultiPointController.__init__(
            self,
            microscope=microscope,
            live_controller=live_controller,
            autofocus_controller=autofocus_controller,
            objective_store=objective_store,
            channel_configuration_manager=channel_configuration_manager,
            callbacks=callbacks,
            scan_coordinates=scan_coordinates,
            laser_autofocus_controller=laser_autofocus_controller,
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            piezo_service=piezo_service,
            event_bus=event_bus,
            acquisition_stream_handler=acquisition_stream_handler,
        )

    # LEGACY callback methods - emit Qt signals for backward compat
    # TODO: Remove these once all widgets use EventBus

    def _legacy_acquisition_start(self, parameters):
        config_names = [c.name for c in self.selected_configurations]
        n_z = self.NZ if not self.run_acquisition_current_fov else 2
        self.signal_set_display_tabs.emit(config_names, n_z)
        self.signal_acquisition_start.emit()

    def _legacy_acquisition_finished(self):
        self.acquisition_finished.emit()
        if self._stage_service:
            pos = self._stage_service.get_position()
        else:
            pos = self.stage.get_pos()
        self.signal_register_current_fov.emit(pos.x_mm, pos.y_mm)

    def _legacy_current_configuration(self, channel_mode):
        self.signal_current_configuration.emit(channel_mode)

    def _legacy_current_fov(self, x_mm, y_mm):
        self.signal_register_current_fov.emit(x_mm, y_mm)

    def _legacy_overall_progress(self, progress):
        self.signal_acquisition_progress.emit(
            progress.current_region,
            progress.total_regions,
            progress.current_timepoint,
        )

    def _legacy_region_progress(self, progress):
        self.signal_region_progress.emit(
            progress.current_fov,
            progress.region_fovs,
        )
```

**Commit:** `refactor(qt_controllers): Simplify QtMultiPointController, delegate to EventBus/StreamHandler`

---

## Task 5: Update MovementUpdater to Publish Events

**Files to modify:**
- `control/gui/qt_controllers.py`

**What to change:**

```python
class MovementUpdater(QObject):
    """Polls stage/piezo position and emits updates.

    After refactor:
    - StageStoppedAtPosition → EventBus (for decoupled listeners)
    - PiezoPositionChanged → EventBus
    - position signal → Kept as Qt signal (high-frequency UI updates)
    """

    # Qt signals for high-frequency updates (keep these)
    position_after_move = Signal(squid.abc.Pos)  # DEPRECATED: use StageStoppedAtPosition event
    position = Signal(squid.abc.Pos)
    piezo_z_um = Signal(float)  # DEPRECATED: use PiezoPositionChanged event

    def __init__(
        self,
        stage: AbstractStage,
        piezo: Optional[PiezoStage],
        event_bus: Optional["EventBus"] = None,  # NEW
        movement_threshold_mm: float = 0.0001,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.stage = stage
        self.piezo = piezo
        self._event_bus = event_bus
        self.movement_threshold_mm = movement_threshold_mm
        self.previous_pos: Optional[squid.abc.Pos] = None
        self.previous_piezo_pos: Optional[float] = None
        self.sent_after_stopped = False

    def do_update(self):
        # Piezo updates
        if self.piezo:
            current_piezo = self.piezo.position
            if self.previous_piezo_pos != current_piezo:
                self.previous_piezo_pos = current_piezo

                # Qt signal (backward compat)
                self.piezo_z_um.emit(current_piezo)

                # EventBus (new way)
                if self._event_bus:
                    from squid.events import PiezoPositionChanged
                    self._event_bus.publish(PiezoPositionChanged(
                        position_um=current_piezo
                    ))

        # Stage position updates
        pos = self.stage.get_pos()

        if not self.previous_pos:
            self.previous_pos = pos
            return

        abs_delta_x = abs(self.previous_pos.x_mm - pos.x_mm)
        abs_delta_y = abs(self.previous_pos.y_mm - pos.y_mm)

        is_stopped = (
            abs_delta_x < self.movement_threshold_mm
            and abs_delta_y < self.movement_threshold_mm
            and not self.stage.get_state().busy
        )

        if is_stopped and not self.sent_after_stopped:
            self.sent_after_stopped = True

            # Qt signal (backward compat)
            self.position_after_move.emit(pos)

            # EventBus (new way)
            if self._event_bus:
                from squid.events import StageStoppedAtPosition
                self._event_bus.publish(StageStoppedAtPosition(
                    x_mm=pos.x_mm,
                    y_mm=pos.y_mm,
                    z_mm=pos.z_mm,
                ))
        elif not is_stopped:
            self.sent_after_stopped = False

        # High-frequency position signal (keep as Qt)
        self.position.emit(pos)
        self.previous_pos = pos
```

**Testing:**

```python
# tests/unit/control/gui/test_movement_updater.py

def test_publishes_stage_stopped_event(mock_event_bus, mock_stage):
    updater = MovementUpdater(mock_stage, None, event_bus=mock_event_bus)

    # Simulate stopped stage
    mock_stage.get_pos.return_value = Pos(1.0, 2.0, 0.5)
    mock_stage.get_state.return_value.busy = False

    updater.do_update()  # First call sets previous
    updater.do_update()  # Second call detects stopped

    calls = [c for c in mock_event_bus.publish.call_args_list
             if isinstance(c[0][0], StageStoppedAtPosition)]
    assert len(calls) == 1
    assert calls[0][0][0].x_mm == 1.0


def test_publishes_piezo_position_event(mock_event_bus, mock_stage, mock_piezo):
    updater = MovementUpdater(mock_stage, mock_piezo, event_bus=mock_event_bus)

    mock_piezo.position = 50.0
    updater.do_update()

    calls = [c for c in mock_event_bus.publish.call_args_list
             if isinstance(c[0][0], PiezoPositionChanged)]
    assert len(calls) == 1
    assert calls[0][0][0].position_um == 50.0
```

**Commit:** `refactor(movement): Add EventBus publishing to MovementUpdater`

---

## Task 6: Wire Up in gui_hcs.py

**Files to modify:**
- `control/gui_hcs.py`

**What to change:**

```python
# In load_objects():

# Create acquisition stream handler (DATA PLANE)
from control.core.display import AcquisitionStreamHandler
self.acquisitionStreamHandler = AcquisitionStreamHandler(self.objectiveStore)

# Create multipoint controller with both planes
self.multipointController = QtMultiPointController(
    self.microscope,
    self.liveController,
    self.autofocusController,
    self.objectiveStore,
    self.channelConfigurationManager,
    scan_coordinates=self.scanCoordinates,
    laser_autofocus_controller=self.laserAutofocusController,
    fluidics=self.fluidics,
    camera_service=self._services.get("camera"),
    stage_service=self._services.get("stage"),
    peripheral_service=self._services.get("peripheral"),
    piezo_service=self._services.get("piezo"),
    event_bus=self._event_bus,
    acquisition_stream_handler=self.acquisitionStreamHandler,  # NEW
)
```

```python
# In setup_movement_updater():

self.movement_updater = MovementUpdater(
    stage=self.stage,
    piezo=self.piezo,
    event_bus=self._event_bus,  # NEW
)
```

```python
# In make_connections() or makeNapariConnections():

# Connect acquisition stream handler to napari widgets (DATA PLANE)
if USE_NAPARI_FOR_LIVE_VIEW and not self.live_only_mode:
    self.acquisitionStreamHandler.image_to_display.connect(
        lambda img: self.napariLiveWidget.updateLiveLayer(img, from_autofocus=False)
    )

if USE_NAPARI_FOR_MULTIPOINT and not self.live_only_mode:
    self.acquisitionStreamHandler.napari_layers_init.connect(
        self.napariMultiChannelWidget.initLayers
    )
    self.acquisitionStreamHandler.napari_layers_update.connect(
        self.napariMultiChannelWidget.updateLayers
    )

if USE_NAPARI_FOR_MOSAIC_DISPLAY and not self.live_only_mode:
    self.acquisitionStreamHandler.napari_layers_update.connect(
        self.napariMosaicDisplayWidget.updateMosaic
    )
```

**Testing:**

Manual testing checklist:
- [ ] Run `python main_hcs.py --simulation`
- [ ] Go to Wellplate Multipoint tab
- [ ] Start acquisition
- [ ] Verify: napari live view updates with images
- [ ] Verify: napari multichannel view shows layers
- [ ] Verify: napari mosaic view updates
- [ ] Verify: progress bar updates
- [ ] Verify: z-plot updates with coordinates
- [ ] Verify: navigation viewer shows FOV markers

**Commit:** `feat(gui): Wire AcquisitionStreamHandler and EventBus in gui_hcs.py`

---

## Task 7: Update Widgets to Subscribe to EventBus

This is the final step - update widgets to subscribe to events instead of connecting to Qt signals.

**Files to modify (one at a time):**

### 7a. SurfacePlotWidget (z-plot)

**File:** `control/widgets/display/surface_plot.py`

```python
class SurfacePlotWidget(QWidget):
    def __init__(self, event_bus: Optional[EventBus] = None, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus

        if self._event_bus:
            from squid.events import (
                AcquisitionCoordinates,
                MultiPointAcquisitionProgress,
                AcquisitionFinished,
            )
            self._event_bus.subscribe(AcquisitionCoordinates, self._on_coordinates)
            self._event_bus.subscribe(MultiPointAcquisitionProgress, self._on_progress)
            self._event_bus.subscribe(AcquisitionFinished, self._on_finished)

        # ... rest of init ...

    def _on_coordinates(self, event: "AcquisitionCoordinates"):
        self.add_point(event.x_mm, event.y_mm, event.z_mm, event.region_id)

    def _on_progress(self, event: "MultiPointAcquisitionProgress"):
        if event.current_region > 1:
            self.plot()
        self.clear()

    def _on_finished(self, event: "AcquisitionFinished"):
        self.plot()
```

**Remove from signal_connector.py:**
```python
# DELETE these lines from connect_plot_signals():
gui.multipointController.signal_coordinates.connect(gui.zPlotWidget.add_point)
gui.multipointController.signal_acquisition_progress.connect(plot_after_each_region)
gui.multipointController.acquisition_finished.connect(gui.zPlotWidget.plot)
```

**Commit:** `refactor(widgets): SurfacePlotWidget subscribes to EventBus`

### 7b. NavigationViewer

**File:** `control/core/core.py` (NavigationViewer class)

```python
class NavigationViewer:
    def __init__(
        self,
        objective_store,
        camera,
        sample,
        event_bus: Optional[EventBus] = None,
    ):
        self._event_bus = event_bus

        if self._event_bus:
            from squid.events import StageStoppedAtPosition, CurrentFOVRegistered
            self._event_bus.subscribe(StageStoppedAtPosition, self._on_stage_stopped)
            self._event_bus.subscribe(CurrentFOVRegistered, self._on_fov_registered)

        # ... rest of init ...

    def _on_stage_stopped(self, event: "StageStoppedAtPosition"):
        from squid.abc import Pos
        self.draw_fov_current_location(Pos(event.x_mm, event.y_mm, event.z_mm))

    def _on_fov_registered(self, event: "CurrentFOVRegistered"):
        self.register_fov(event.x_mm, event.y_mm)
```

**Remove from signal_connector.py:**
```python
# DELETE these lines from connect_navigation_signals():
gui.movement_updater.position_after_move.connect(gui.navigationViewer.draw_fov_current_location)
gui.multipointController.signal_register_current_fov.connect(gui.navigationViewer.register_fov)
```

**Commit:** `refactor(navigation): NavigationViewer subscribes to EventBus`

### 7c. Continue for other widgets...

Follow the same pattern for:
- `PiezoWidget` → `PiezoPositionChanged`
- `LiveControlWidget` → `MicroscopeModeChanged`
- Acquisition widgets → `SetDisplayTabsRequest`

---

## Task 8: Clean Up Deprecated Code

Once all widgets use EventBus:

**Files to modify:**
- `control/gui/qt_controllers.py` - Remove deprecated signals and legacy callbacks
- `control/gui/signal_connector.py` - Remove connections that are now handled by EventBus subscriptions
- `control/core/acquisition/multi_point_controller.py` - Remove legacy callback support

**Commit:** `chore: Remove deprecated Qt signals and legacy callbacks`

---

## Testing Checklist

### Unit Tests
- [ ] `pytest tests/unit/squid/test_events.py -v`
- [ ] `pytest tests/unit/control/core/display/test_acquisition_stream_handler.py -v`
- [ ] `pytest tests/unit/control/core/acquisition/test_multi_point_controller.py -v`
- [ ] `pytest tests/unit/control/gui/test_movement_updater.py -v`

### Integration Tests
- [ ] `pytest tests/integration -v`

### Manual Tests
- [ ] Start app: `python main_hcs.py --simulation`
- [ ] Wellplate Multipoint acquisition:
  - [ ] Images appear in live view
  - [ ] Multichannel view populates
  - [ ] Mosaic view builds
  - [ ] Progress bar updates
  - [ ] Z-plot shows coordinates
  - [ ] Navigation viewer shows FOV markers
- [ ] Flexible Multipoint acquisition (same checks)
- [ ] Single-FOV snap (should show immediately, no throttling)
- [ ] Stage movement updates navigation viewer
- [ ] Piezo movement updates piezo widget

---

## Rollback Plan

If issues arise:

1. The legacy Qt signals are preserved during migration
2. Widgets can fall back to signal connections if EventBus subscription fails
3. `AcquisitionStreamHandler` is additive - old image routing still works

To rollback a specific widget:
1. Remove EventBus subscription from widget `__init__`
2. Re-add signal connection in `signal_connector.py`
3. Commit with `revert: ...` message

---

## Summary

| Component | Before | After |
|-----------|--------|-------|
| Acquisition start/stop | Qt signals | `AcquisitionStarted`/`Finished` events |
| Progress updates | Qt signals | `MultiPointAcquisitionProgress` event |
| Coordinates | Qt signals | `AcquisitionCoordinates` event |
| Image frames | Qt signals on controller | `AcquisitionStreamHandler` Qt signals |
| Throttling logic | In `QtMultiPointController` | In `AcquisitionStreamHandler` |
| Widget wiring | `signal_connector.py` | Widget `__init__` subscriptions |
| Stage position | Qt signals | `StageStoppedAtPosition` event |

---

## Appendix A: Complete Signal Audit

This is a complete audit of every signal connection in `signal_connector.py` and `gui_hcs.py`.

### Legend

| Category | Description | Action |
|----------|-------------|--------|
| **EventBus** | Control plane - state changes, commands | Move to EventBus subscription |
| **StreamHandler** | Data plane - image frames | Keep as Qt signal on StreamHandler |
| **Local Qt** | UI-internal signals (clicks, tab changes) | Keep as Qt signal |
| **Existing Event** | Already has an event defined | Use existing event |

---

### `connect_acquisition_signals` (signal_connector.py:23-44)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `flexibleMultiPointWidget.signal_acquisition_started` → `gui.toggleAcquisitionStart` | EventBus | `AcquisitionStateChanged` | Widget already publishes; GUI subscribes |
| `wellplateMultiPointWidget.signal_acquisition_started` → `gui.toggleAcquisitionStart` | EventBus | `AcquisitionStateChanged` | Same pattern |
| `wellplateMultiPointWidget.signal_toggle_live_scan_grid` → `gui.toggle_live_scan_grid` | EventBus | NEW: `LiveScanGridToggled` | Or keep local if only one consumer |
| `multiPointWithFluidicsWidget.signal_acquisition_started` → `gui.toggleAcquisitionStart` | EventBus | `AcquisitionStateChanged` | Same pattern |
| `fluidicsWidget.fluidics_initialized_signal` → `multiPointWithFluidicsWidget.init_fluidics` | EventBus | NEW: `FluidicsInitialized` | Peripheral state |

**After migration:** This entire function can be deleted. Widgets subscribe to `AcquisitionStateChanged` in their `__init__`.

---

### `connect_profile_signals` (signal_connector.py:47-62)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `profileWidget.signal_profile_changed` → `liveControlWidget.refresh_mode_list` | EventBus | NEW: `ProfileChanged` | Config change |
| `profileWidget.signal_profile_changed` → lambda (select mode) | EventBus | `ProfileChanged` | Same event, different handler |
| `objectivesWidget.signal_objective_changed` → lambda (select mode) | Existing Event | `ObjectiveChanged` | Already exists |

**After migration:** Delete function. `LiveControlWidget` subscribes to `ProfileChanged` and `ObjectiveChanged`.

---

### `connect_live_control_signals` (signal_connector.py:65-75)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `liveControlWidget.signal_newExposureTime` → `cameraSettingWidget.set_exposure_time` | Existing Event | `SetExposureTimeCommand` | Widget should publish command |
| `liveControlWidget.signal_newAnalogGain` → `cameraSettingWidget.set_analog_gain` | Existing Event | `SetAnalogGainCommand` | Widget should publish command |
| `liveControlWidget.signal_start_live` → `gui.onStartLive` | Existing Event | `StartLiveCommand` | Already exists |

**After migration:** Delete function. `CameraSettingsWidget` subscribes to `ExposureTimeChanged`/`AnalogGainChanged`. Commands go through service.

---

### `connect_navigation_signals` (signal_connector.py:78-106)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `navigationViewer.signal_coordinates_clicked` → `gui.move_from_click_mm` | Existing Event | `MoveStageToCommand` | User click → command |
| `objectivesWidget.signal_objective_changed` → `navigationViewer.redraw_fov` | Existing Event | `ObjectiveChanged` | |
| `cameraSettingWidget.signal_binning_changed` → `navigationViewer.redraw_fov` | Existing Event | `BinningChanged` | |
| `objectivesWidget.signal_objective_changed` → `flexibleMultiPointWidget.update_fov_positions` | Existing Event | `ObjectiveChanged` | |
| `movement_updater.position_after_move` → `navigationViewer.draw_fov_current_location` | EventBus | `StageStoppedAtPosition` | In Phase 5D |
| `multipointController.signal_register_current_fov` → `navigationViewer.register_fov` | EventBus | `CurrentFOVRegistered` | In Phase 5D |
| `multipointController.signal_current_configuration` → `liveControlWidget.update_ui_for_mode` | Existing Event | `MicroscopeModeChanged` | |
| `movement_updater.piezo_z_um` → `piezoWidget.update_displacement_um_display` | Existing Event | `PiezoPositionChanged` | In Phase 5D |
| `multipointController.signal_set_display_tabs` → `gui.setAcquisitionDisplayTabs` | EventBus | `SetDisplayTabsRequest` | In Phase 5D |

**After migration:** Delete function. Each widget subscribes to relevant events.

---

### `connect_tab_signals` (signal_connector.py:109-113)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `recordTabWidget.currentChanged` → `gui.onTabChanged` | **Local Qt** | N/A | Pure Qt UI, keep |
| `imageDisplayTabs.currentChanged` → `gui.onDisplayTabChanged` | **Local Qt** | N/A | Pure Qt UI, keep |

**After migration:** Keep this function - these are Qt widget internal signals.

---

### `connect_wellplate_signals` (signal_connector.py:116-138)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `wellplateFormatWidget.signalWellplateSettings` → `navigationViewer.update_wellplate_settings` | EventBus | NEW: `WellplateSettingsChanged` | |
| `wellplateFormatWidget.signalWellplateSettings` → `scanCoordinates.update_wellplate_settings` | EventBus | `WellplateSettingsChanged` | Same event |
| `wellplateFormatWidget.signalWellplateSettings` → `wellSelectionWidget.onWellplateChanged` | EventBus | `WellplateSettingsChanged` | Same event |
| `wellplateFormatWidget.signalWellplateSettings` → lambda (gui.onWellplateChanged) | EventBus | `WellplateSettingsChanged` | Same event |
| `wellSelectionWidget.signal_wellSelectedPos` → `gui.move_to_mm` | Existing Event | `MoveStageToCommand` | User action → command |
| `wellSelectionWidget.signal_wellSelected` → `wellplateMultiPointWidget.update_well_coordinates` | EventBus | NEW: `WellSelected` | |
| `objectivesWidget.signal_objective_changed` → `wellplateMultiPointWidget.update_coordinates` | Existing Event | `ObjectiveChanged` | |

**After migration:** Delete function. Widgets subscribe to `WellplateSettingsChanged`, `WellSelected`, `ObjectiveChanged`.

---

### `connect_display_signals` (signal_connector.py:141-193)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `multipointController.signal_current_configuration` → `napariLiveWidget.update_ui_for_mode` | Existing Event | `MicroscopeModeChanged` | |
| `autofocusController.image_to_display` → napariLiveWidget | **StreamHandler** | N/A | Image data - keep Qt |
| `streamHandler.image_to_display` → napariLiveWidget | **StreamHandler** | N/A | Image data - keep Qt |
| `multipointController.image_to_display` → napariLiveWidget | **StreamHandler** | N/A | Move to AcquisitionStreamHandler |
| `napariLiveWidget.signal_coordinates_clicked` → `gui.move_from_click_image` | **Local Qt** | N/A | User click, keep local |
| `liveControlWidget.signal_live_configuration` → `napariLiveWidget.set_live_configuration` | EventBus | `MicroscopeModeChanged` or `LiveConfigurationChanged` | |
| `napariLiveWidget.signal_newExposureTime` → cameraSettingWidget | Existing Event | `SetExposureTimeCommand` | |
| `napariLiveWidget.signal_newAnalogGain` → cameraSettingWidget | Existing Event | `SetAnalogGainCommand` | |
| `napariLiveWidget.signal_autoLevelSetting` → imageDisplayWindow | EventBus | NEW: `SetAutoLevelCommand` | |
| Non-napari: `imageDisplay.image_to_display` → imageDisplayWindow | **StreamHandler** | N/A | Image data |
| `imageDisplayWindow.image_click_coordinates` → move_from_click_image | **Local Qt** | N/A | User click |

**After migration:** Keep image connections on StreamHandler. Move config/mode signals to EventBus.

---

### `connect_laser_autofocus_signals` (signal_connector.py:196-255)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `profileWidget.signal_profile_changed` → slot_settings_changed_laser_af | EventBus | `ProfileChanged` | |
| `objectivesWidget.signal_objective_changed` → slot_settings_changed_laser_af | Existing Event | `ObjectiveChanged` | |
| `laserAutofocusSettingWidget.signal_newExposureTime` → cameraSettingWidget_focus | Existing Event | `SetExposureTimeCommand` | For focus camera |
| `laserAutofocusSettingWidget.signal_newAnalogGain` → cameraSettingWidget_focus | Existing Event | `SetAnalogGainCommand` | For focus camera |
| `laserAutofocusSettingWidget.signal_apply_settings` → laserAutofocusControlWidget | Existing Event | `LaserAFPropertiesChanged` | |
| `laserAutofocusSettingWidget.signal_laser_spot_location` → imageDisplayWindow_focus | EventBus | NEW: `LaserAFSpotLocationChanged` | |
| `laserAutofocusController.signal_cross_correlation` → laserAutofocusSettingWidget | EventBus | NEW: `LaserAFCrossCorrelationResult` | |
| `streamHandler_focus_camera.signal_new_frame_received` → liveController_focus | **StreamHandler** | N/A | Frame callback |
| `streamHandler_focus_camera.image_to_display` → imageDisplayWindow_focus | **StreamHandler** | N/A | Image data |
| `streamHandler_focus_camera.image_to_display` → displacementMeasurementController | **StreamHandler** | N/A | Image data |
| `displacementMeasurementController.signal_plots` → waveformDisplay | EventBus | NEW: `DisplacementMeasurementPlots` | |
| `displacementMeasurementController.signal_readings` → displacementMeasurementWidget | EventBus | NEW: `DisplacementMeasurementReadings` | |
| `laserAutofocusController.image_to_display` → imageDisplayWindow_focus | **StreamHandler** | N/A | Image data |
| `laserAutofocusController.signal_piezo_position_update` → piezoWidget | Existing Event | `PiezoPositionChanged` | |

**After migration:** Keep image/frame signals on StreamHandler. Move state/result signals to EventBus.

---

### `connect_confocal_signals` (signal_connector.py:258-270)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `spinningDiskConfocalWidget.signal_toggle_confocal_widefield` → channelConfigurationManager | Existing Event | `SetSpinningDiskPositionCommand` | |
| `spinningDiskConfocalWidget.signal_toggle_confocal_widefield` → lambda (select mode) | EventBus | `SpinningDiskStateChanged` | |

**After migration:** Delete function. Use existing spinning disk events.

---

### `connect_plot_signals` (signal_connector.py:273-285)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `multipointController.signal_coordinates` → `zPlotWidget.add_point` | EventBus | `AcquisitionCoordinates` | In Phase 5D |
| `multipointController.signal_acquisition_progress` → plot_after_each_region | EventBus | `MultiPointAcquisitionProgress` | In Phase 5D |
| `multipointController.acquisition_finished` → `zPlotWidget.plot` | Existing Event | `AcquisitionFinished` | |

**After migration:** Delete function. `SurfacePlotWidget` subscribes to events.

---

### `connect_well_selector_button` (signal_connector.py:288-293)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `imageDisplayWindow.btn_well_selector.clicked` → lambda | **Local Qt** | N/A | Button click, keep |

**After migration:** Keep - pure UI button click.

---

### `connect_slide_position_controller` (signal_connector.py:296-326)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `stageUtils.signal_loading_position_reached` → disable buttons (3x) | EventBus | NEW: `StageLoadingPositionReached` | |
| `stageUtils.signal_scanning_position_reached` → enable buttons (3x) | EventBus | NEW: `StageScanningPositionReached` | |
| `stageUtils.signal_scanning_position_reached` → navigationViewer.clear_slide | EventBus | `StageScanningPositionReached` | Same event |

**After migration:** Delete function. Widgets subscribe to position events.

---

### `gui_hcs.py make_connections()` (lines 726-749)

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `streamHandler.signal_new_frame_received` → `liveController.on_new_frame` | **StreamHandler** | N/A | Frame callback, keep |
| `streamHandler.packet_image_to_write` → `imageSaver.enqueue` | **StreamHandler** | N/A | Image data, keep |

**After migration:** Keep - core data plane connections.

---

### `makeNapariConnections()` (gui_hcs.py:761-967)

Most are duplicates of above. Key additions:

| Signal → Slot | Category | Event | Notes |
|---------------|----------|-------|-------|
| `multipointController.napari_layers_init` → napariMultiChannelWidget | **StreamHandler** | N/A | Move to AcquisitionStreamHandler |
| `multipointController.napari_layers_update` → napariMultiChannelWidget | **StreamHandler** | N/A | Move to AcquisitionStreamHandler |
| `multipointController.napari_layers_update` → napariMosaicDisplayWidget | **StreamHandler** | N/A | Move to AcquisitionStreamHandler |
| `flexibleMultiPointWidget.signal_acquisition_channels` → initChannels | EventBus | NEW: `AcquisitionChannelsConfigured` | |
| `flexibleMultiPointWidget.signal_acquisition_shape` → initLayersShape | EventBus | NEW: `AcquisitionShapeConfigured` | |
| `wellplateMultiPointWidget.signal_acquisition_channels` → initChannels | EventBus | `AcquisitionChannelsConfigured` | Same event |
| `wellplateMultiPointWidget.signal_acquisition_shape` → initLayersShape | EventBus | `AcquisitionShapeConfigured` | Same event |
| `wellplateMultiPointWidget.signal_manual_shape_mode` → enable_shape_drawing | EventBus | NEW: `ManualShapeModeChanged` | |
| `napariMosaicDisplayWidget.signal_shape_drawn` → update_manual_shape | EventBus | NEW: `ManualShapeDrawn` | |
| `napariMosaicDisplayWidget.signal_coordinates_clicked` → move_from_click_mm | **Local Qt** | N/A | User click |
| `napariMosaicDisplayWidget.signal_clear_viewer` → navigationViewer.clear_slide | EventBus | NEW: `ClearViewerRequested` | Or keep local |

---

## Appendix B: Can signal_connector.py Be Deleted?

**Yes, eventually.** After full migration:

| Function | Delete? | Reason |
|----------|---------|--------|
| `connect_acquisition_signals` | ✅ Yes | Widgets subscribe to `AcquisitionStateChanged` |
| `connect_profile_signals` | ✅ Yes | Widgets subscribe to `ProfileChanged`, `ObjectiveChanged` |
| `connect_live_control_signals` | ✅ Yes | Commands go through EventBus |
| `connect_navigation_signals` | ✅ Yes | Widgets subscribe to events |
| `connect_tab_signals` | ❌ No | Keep - pure Qt UI signals |
| `connect_wellplate_signals` | ✅ Yes | Widgets subscribe to `WellplateSettingsChanged` etc. |
| `connect_display_signals` | ⚠️ Partial | Keep StreamHandler image connections |
| `connect_laser_autofocus_signals` | ⚠️ Partial | Keep StreamHandler connections |
| `connect_confocal_signals` | ✅ Yes | Use existing events |
| `connect_plot_signals` | ✅ Yes | Widgets subscribe to events |
| `connect_well_selector_button` | ❌ No | Keep - button click |
| `connect_slide_position_controller` | ✅ Yes | Widgets subscribe to position events |

**Final state:** `signal_connector.py` shrinks to:
1. `connect_tab_signals` - Qt tab change signals
2. `connect_well_selector_button` - button click
3. StreamHandler image connections (or move these to a `connect_data_plane` function)

---

## Appendix C: New Events Needed (Complete List)

Events already defined in `squid/events.py`:
- `AcquisitionStarted`, `AcquisitionFinished`, `AcquisitionStateChanged`
- `AcquisitionProgress`, `AcquisitionRegionProgress`
- `ObjectiveChanged`, `BinningChanged`, `PixelSizeChanged`
- `MicroscopeModeChanged`
- `PiezoPositionChanged`
- `SetExposureTimeCommand`, `SetAnalogGainCommand`
- `MoveStageToCommand`
- `LaserAFPropertiesChanged`
- `SpinningDiskStateChanged`

**New events to add:**

```python
# Profile/Configuration
@dataclass
class ProfileChanged(Event):
    """Microscope profile configuration changed."""
    profile_name: str

# Wellplate
@dataclass
class WellplateSettingsChanged(Event):
    """Wellplate format/settings changed."""
    format: str
    rows: int
    columns: int

@dataclass
class WellSelected(Event):
    """Well(s) selected in wellplate widget."""
    wells: list[tuple[int, int]]  # List of (row, col)

# Stage positions
@dataclass
class StageLoadingPositionReached(Event):
    """Stage reached loading position."""
    pass

@dataclass
class StageScanningPositionReached(Event):
    """Stage reached scanning position."""
    pass

# Live scan grid
@dataclass
class LiveScanGridToggled(Event):
    """Live scan grid overlay toggled."""
    enabled: bool

# Fluidics
@dataclass
class FluidicsInitialized(Event):
    """Fluidics system initialized."""
    success: bool

# Display
@dataclass
class SetAutoLevelCommand(Event):
    """Command to set auto-level mode."""
    enabled: bool

# Laser AF
@dataclass
class LaserAFSpotLocationChanged(Event):
    """Laser AF spot location updated."""
    x: float
    y: float

@dataclass
class LaserAFCrossCorrelationResult(Event):
    """Cross-correlation measurement result."""
    correlation: float
    displacement_um: float

# Displacement measurement
@dataclass
class DisplacementMeasurementPlots(Event):
    """Displacement measurement plot data."""
    data: dict  # Plot data

@dataclass
class DisplacementMeasurementReadings(Event):
    """Displacement measurement readings."""
    readings: dict  # Measurement values

# Acquisition configuration
@dataclass
class AcquisitionChannelsConfigured(Event):
    """Channels configured for acquisition."""
    channels: list[str]

@dataclass
class AcquisitionShapeConfigured(Event):
    """Acquisition shape/grid configured."""
    rows: int
    columns: int
    z_planes: int

# Manual shape drawing
@dataclass
class ManualShapeModeChanged(Event):
    """Manual shape drawing mode toggled."""
    enabled: bool

@dataclass
class ManualShapeDrawn(Event):
    """Manual shape drawn in viewer."""
    shapes_mm: list  # Shape coordinates in mm

# Viewer
@dataclass
class ClearViewerRequested(Event):
    """Request to clear viewer/overlay."""
    pass
```
