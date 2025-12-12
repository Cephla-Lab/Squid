Here’s a cleaned‑up “architecture + plan” you can basically paste into a design doc and then refine into a detailed implementation plan.

---

## 1. Goals & Constraints

**Primary goals**

1. **Decoupled UI and business logic**

   * Widgets know only about events, not services/hardware.
   * Services/controllers expose behavior via events and commands, not direct widget calls.

2. **Thread-safe & GUI-safe**

   * Services/controllers can run on worker threads.
   * All Qt widget updates must run on the Qt main thread.
   * No GUI freezes during:

     * Live streaming.
     * Multi‑FOV / tiled acquisitions.

3. **Two planes**

   * **Control plane** (EventBus): low‑rate commands & state changes.
   * **Data plane** (StreamHandler): high‑rate image frames.

4. **Testability**

   * Core logic (services/controllers) testable without Qt.
   * UI behavior testable via Qt + a UI‑aware event layer.

---

## 2. Core Architectural Pieces

### 2.1 Core EventBus (Qt‑free)

A small, thread-safe, synchronous bus implementing the control plane:

* Responsible for:

  * Registering subscribers for event types.
  * Delivering events to all subscribers.
* Guarantees:

  * Thread-safe subscription and publication.
  * Handlers run in the **thread that calls `publish()`**.
* Non-goals:

  * Qt/thread marshalling.
  * High-frequency data streaming.

**API (conceptual)**

```python
class EventBus:
    def subscribe(self, event_type: type, handler: Callable[[Event], None]) -> None: ...
    def unsubscribe(self, event_type: type, handler: Callable[[Event], None]) -> None: ...
    def publish(self, event: Event) -> None: ...
```

**Semantics**

* `publish(event)`:

  * Copies the handler list under a lock.
  * Releases the lock.
  * Calls each handler in the caller’s thread.
* If a handler raises, it is caught and logged; it does **not** crash the thread or stop other handlers.

---

### 2.2 QtEventDispatcher (main-thread executor)

A tiny QObject that can run arbitrary callables on the Qt main thread via signals/slots.

* Lives in the **main Qt thread**.
* Exposes a `dispatch` signal that takes `(handler, event)` and invokes `handler(event)` in the thread the QObject lives in (the main thread).

**Conceptual sketch**

```python
class QtEventDispatcher(QObject):
    dispatch = Signal(object, object)  # (handler, event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dispatch.connect(self._on_dispatch)

    @Slot(object, object)
    def _on_dispatch(self, handler, event):
        handler(event)  # Runs in the main Qt thread
```

Qt’s queued connection logic takes care of cross-thread dispatch; if a worker emits the signal, the slot runs on the main thread.

---

### 2.3 UiEventBus (Qt-aware wrapper)

A wrapper that uses the **same underlying EventBus**, but ensures **widget handlers run on the Qt main thread**.

* Widgets use `UiEventBus` instead of `EventBus` directly.
* Services/controllers continue to use `EventBus` directly.

**Responsibilities**

* Delegate `publish()` straight to the core EventBus.
* Wrap handlers so that every callback is marshalled through `QtEventDispatcher`.

**Conceptual sketch**

```python
class UiEventBus:
    def __init__(self, core_bus: EventBus, dispatcher: QtEventDispatcher):
        self._core_bus = core_bus
        self._dispatcher = dispatcher
        self._wrapper_map = {}
        self._lock = threading.RLock()

    def publish(self, event: object) -> None:
        self._core_bus.publish(event)

    def subscribe(self, event_type: type, handler: Callable[[object], None]) -> None:
        with self._lock:
            def wrapper(event, _handler=handler):
                # Always go through dispatcher; Qt handles cross-thread issues
                self._dispatcher.dispatch.emit(_handler, event)

            self._wrapper_map[(event_type, handler)] = wrapper
            self._core_bus.subscribe(event_type, wrapper)

    def unsubscribe(self, event_type: type, handler: Callable[[object], None]) -> None:
        with self._lock:
            wrapper = self._wrapper_map.pop((event_type, handler), None)
        if wrapper is not None:
            self._core_bus.unsubscribe(event_type, wrapper)
```

Optional optimization: if you care about synchronous behavior when already on the main thread, add a fast path that calls the handler directly when `QThread.currentThread() is dispatcher.thread()`.

---

### 2.4 Services, Controllers, Workers

* **Services**

  * Hardware-facing, thread-safe (`RLock` around hardware access and shared state).
  * Called from main thread (via UI commands) and from worker threads (complex sequences).
  * Publish state events to **core EventBus** after state changes.
  * **Important**: publish events *after* releasing internal locks.

* **Controllers**

  * Orchestrate multi-step workflows (e.g. MultiPoint acquisition).
  * Run long-running loops on worker threads (`threading.Thread` / `QThread`).
  * Publish progress, start/finish events to the **core EventBus**.

* **Workers (threads)**

  * Run acquisition loops, autofocus sweeps, tiled scans, etc.
  * Never touch Qt widgets directly.
  * Interact with UI via events only.

---

### 2.5 Widgets

* Depend only on `UiEventBus` (and possibly the data-plane API for viewing frames).
* Subscribe to events via `UiEventBus`, ensuring GUI safety.

**Example**

```python
class StageWidget(QWidget):
    def __init__(self, event_bus: UiEventBus, parent=None):
        super().__init__(parent)
        self._bus = event_bus
        self._bus.subscribe(StagePositionChanged, self._on_stage_changed)

    def _on_stage_changed(self, event: StagePositionChanged):
        # Guaranteed to run on Qt main thread
        self.x_label.setText(f"{event.x:.1f}")
        self.y_label.setText(f"{event.y:.1f}")

    def closeEvent(self, event):
        self._bus.unsubscribe(StagePositionChanged, self._on_stage_changed)
        super().closeEvent(event)
```

Widgets are responsible for unsubscribing when destroyed, to avoid leakiness (or a central lifecycle manager can handle this).

---

### 2.6 Data Plane (StreamHandler)

* Completely separate from EventBus.
* Handles high-rate image frames (e.g. 30–60 fps).
* Typically:

  * Camera worker thread pushes frames into a queue.
  * A viewer component on the main thread dequeues and renders at a suitable rate (possibly with dropping/decimation).
* Control-plane events (e.g., `ExposureChanged`, `AcquisitionProgress`) can reference stream identifiers but never carry image frame payloads.

---

## 3. Threading Model

**Main Qt thread**

* Runs Qt event loop.
* Owns:

  * All widgets.
  * `QtEventDispatcher`.
  * `UiEventBus` instance.
* Executes:

  * All widget event handlers (via `UiEventBus`).
  * Rendering & lightweight UI computations.

**Worker threads**

* Acquisition loops, autofocus, tiling, etc.
* Call services and controllers.
* Publish events to the **core EventBus**.
* Push frame data to the data plane.

**Service locking pattern**

* Acquire lock → talk to hardware / update internal state → release lock → **then** publish events.
* Avoid holding locks while publishing.

---

## 4. Event Types & Flows

### 4.1 Control-plane event types

Divide conceptually (even if it’s just one `Event` base class):

* **Commands (down)**

  * `SetExposureCommand(exposure_ms)`
  * `MoveStageCommand(x, y, z)`
  * `StartAcquisitionCommand(config)`
* **State / status (up)**

  * `ExposureChanged(exposure_ms)`
  * `StagePositionChanged(x, y, z)`
  * `PiezoPositionChanged(z)`
  * `AcquisitionStarted(config)`
  * `AcquisitionProgress(done_tiles, total_tiles)`
  * `AcquisitionFinished(success, error=None)`

Commands can either:

* Be published on the same EventBus and handled by services, or
* Be implemented as direct method calls on controllers/services from widgets.

(You can standardize later; the architecture works for either.)

### 4.2 Example: Multi-FOV tiled acquisition

1. User configures and hits “Start”:

   * Widget:

     * Publishes `StartAcquisitionCommand` to bus (or calls controller method).
2. Controller starts worker:

   * Worker thread begins loop over tiles/FOVs.
   * Immediately publishes `AcquisitionStarted(config)` to core bus.
3. Worker does tiles:

   * After each tile or at some interval: `AcquisitionProgress(tile_idx, total_tiles)`.
   * On completion or error: `AcquisitionFinished(success, error)`.
4. UI side:

   * Widgets subscribe via `UiEventBus`:

     * `AcquisitionStarted` → disable controls, reset progress.
     * `AcquisitionProgress` → update progress bar.
     * `AcquisitionFinished` → enable controls, show summary.

Because UI subscribers use `UiEventBus`, all of these handler calls are on the Qt main thread, regardless of where the events were published from.

---

## 5. Performance & Responsiveness Guidelines

To keep the GUI snappy during streaming/tiled acquisitions:

1. **No frames on EventBus**
   All frame data stays on the data plane.

2. **Throttle progress/state events**

   * Avoid event storms from worker threads:

     * Don’t emit `AcquisitionProgress` per frame; aim for ~5–10 Hz or per tile/FOV.
     * For continuous movements, publish position at meaningful intervals, not every tiny step.

3. **Keep UI handlers lightweight**

   * Handlers should:

     * Update labels, progress bars, small internal variables.
     * Possibly schedule heavier work elsewhere.
   * No heavy computation, blocking I/O, or long loops inside event handlers on the UI thread.

4. **Ensure no deadlocks**

   * Never let a worker thread hold a lock and indirectly wait for the main thread while the main thread is blocked waiting on that lock.
   * Releasing service locks before publishing events avoids classic UI ↔ worker deadlock patterns.

---

## 6. High-Level Implementation Plan

Use this as your “phase breakdown” to drive the more detailed plan.

### Step 0 – Event taxonomy and usage

* Enumerate key events you already use or need:

  * Hardware state changes (stage, piezo, camera settings).
  * Acquisition lifecycle and progress.
  * Diagnostics/alerts if desired (e.g., `ErrorOccurred`).
* Mark which ones:

  * Are published from worker threads vs main thread.
  * Are consumed by UI vs core logic.

### Step 1 – Solidify the Core EventBus

* Implement/verify:

  * `subscribe`, `unsubscribe`, `publish` with an internal `RLock`.
  * Copy handler list under lock; call after releasing lock.
  * Exception handling inside `publish` loop.
* Add minimal tests:

  * Multi-threaded subscribe/publish without races.
  * Subscribers modifying subscriptions during `publish`.
  * Exceptions in one handler don’t prevent others from running.

### Step 2 – Implement QtEventDispatcher

* Create `QtEventDispatcher` QObject with `dispatch` signal and `_on_dispatch` slot.
* Instantiate it after `QApplication` is created, on the main thread.
* Assert (or ensure) that `dispatcher.thread()` is the main Qt thread.

### Step 3 – Implement UiEventBus

* Implement `UiEventBus` wrapping `EventBus` + `QtEventDispatcher`.
* Responsibilities:

  * `publish` → delegates to core bus.
  * `subscribe`:

    * Creates wrapper calling `dispatcher.dispatch.emit(handler, event)`.
    * Registers wrapper with core bus; stores mapping for unsubscribe.
  * `unsubscribe`:

    * Looks up wrapper and unsubscribes from core bus.
* Add basic tests:

  * When publishing from main thread, UI handlers run.
  * When publishing from worker thread, UI handlers run on main thread (can be tested via `QThread.currentThread()` introspection or a custom flag).

### Step 4 – Wire application composition

* In your “composition root” (app startup):

  ```python
  core_bus = EventBus()
  qt_dispatcher = QtEventDispatcher()
  ui_bus = UiEventBus(core_bus, qt_dispatcher)

  # Services/controllers get core_bus
  stage_service = StageService(core_bus, ...)
  multipoint_controller = MultiPointController(core_bus, ...)

  # Widgets get ui_bus
  stage_widget = StageWidget(ui_bus, ...)
  acquisition_widget = AcquisitionWidget(ui_bus, ...)
  ```

* Ensure there’s only **one** instance of each bus to keep a single logical control plane.

### Step 5 – Migrate UI subscriptions to UiEventBus

* For each widget:

  * Replace direct references to services/controllers where possible with subscriptions to events.
  * Subscribe via `UiEventBus` rather than directly to core EventBus.
  * Ensure `closeEvent` or equivalent unsubscribes appropriately (or implement a widget-lifecycle-based manager).

### Step 6 – Replace Qt wrapper controllers

* For each `QtXxxController` pattern currently used to bridge worker threads to UI:

  * Identify what signals it exposes (e.g., `acquisition_finished`, `progress_changed`).
  * Replace those Qt signals with events on the core EventBus:

    * `AcquisitionStarted`, `AcquisitionProgress`, `AcquisitionFinished`.
  * Update widgets to listen for those events via `UiEventBus` instead of connecting to Qt signals.
* Gradually remove unnecessary wrapper controllers as they become redundant.

### Step 7 – Audit event frequency and move data off control plane

* Confirm:

  * No image frames go through EventBus.
  * Progress/state events are at manageable rates.
* If necessary:

  * Add simple throttling logic for noisy signals (e.g., publish progress only if `time_since_last > 100 ms` or progress changed by at least X%).

### Step 8 – Testing & validation

* **Unit tests**:

  * EventBus multi-thread behavior.
  * UiEventBus handler marshalling.
* **Integration tests**:

  * Start a fake acquisition controller in a worker thread, publish events, verify widgets update and the UI stays responsive.
* **Stress tests**:

  * Simulate high-rate progress events (within reasonable bounds) and monitor UI responsiveness.
  * Tiled acquisition mock with many tiles and ensure the main thread isn’t starved.

---

## 7. Future Extensions

The architecture leaves room for:

* Alternate frontends:

  * CLI or web UI could use the same **EventBus** without Qt, or with different “dispatchers”.
* Alternate dispatch strategies:

  * A `BackgroundEventBus` or different dispatcher for expensive non-UI subscribers.
* Logging/metrics:

  * Subscribe a logging component to the core EventBus for audit trails, without impacting UI.

---

This gives you a coherent story you can expand into more detailed tasks (individual tickets / TODOs) for each step: implementing the buses, wiring them up, migrating widgets and controllers, and tuning event rates.
