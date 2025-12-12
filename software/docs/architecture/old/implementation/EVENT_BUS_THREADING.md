# EventBus Threading Problem

## The Problem

The EventBus is thread-safe for data structure access but **not Qt-safe for handler dispatch**. When an event is published from a worker thread, subscriber handlers execute on that worker thread—not the main Qt thread. If a widget subscribes to such an event, its handler will attempt GUI updates from the wrong thread, causing crashes or corruption.

## How the EventBus Currently Works

```python
def publish(self, event: Event) -> None:
    with self._lock:
        handlers = list(self._subscribers.get(type(event), []))

    for handler in handlers:
        handler(event)  # Runs in the CALLER'S thread
```

The lock protects the subscriber dictionary from concurrent modification. But `handler(event)` executes synchronously in whatever thread called `publish()`.

## Where Events Are Published From Worker Threads

### 1. MultiPointWorker (acquisition loop)

The acquisition worker runs in a `threading.Thread`:

```python
# control/core/acquisition/multi_point_controller.py:641
self.thread = Thread(target=self.multiPointWorker.run, ...)
```

Inside `run()`, it publishes events directly:

```python
# control/core/acquisition/multi_point_worker.py:357
self._event_bus.publish(AcquisitionStarted(...))  # Worker thread!
```

### 2. Services called from workers

Services publish state events after operations. When called from a worker thread, the publish happens on that thread:

```python
# Worker thread calls:
self._piezo_service.move_to(position_um)

# Inside PiezoService.move_to():
def move_to(self, position_um: float) -> float:
    with self._lock:
        self._piezo.move_to(clamped)
        self._state = replace(self._state, position_um=actual)

    self.publish(PiezoPositionChanged(position_um=actual))  # Still on worker thread!
    return actual
```

## Events Published From Worker Threads

| Source | Events | Called From |
|--------|--------|-------------|
| `MultiPointWorker` | `AcquisitionStarted`, `AcquisitionFinished`, `AcquisitionProgress` | `Thread.run()` |
| `PiezoService` | `PiezoPositionChanged` | Worker via `move_to()` |
| `StageService` | `StagePositionChanged` | Worker via `move_*()` |
| `CameraService` | Various | Worker via service methods |

## Why It Hasn't Crashed (Yet)

Most event publishers currently run on the main thread:

- **Widgets** publish commands from Qt signal handlers → main thread
- **MovementUpdater** uses QTimer → main thread
- **Command handlers** in services respond to widget commands → main thread

The worker-thread publishes (`AcquisitionStarted`, etc.) haven't caused visible crashes because:
1. Few widgets currently subscribe to these specific events
2. The subscription handlers may not touch Qt widgets directly
3. Race conditions are timing-dependent—they may manifest only under load

## The Latent Bug

Phase 5C introduced widgets that subscribe directly to EventBus events. If any of these widgets subscribe to events that can be published from worker threads, their handlers will run on the worker thread:

```python
# Widget subscribing to an event
class SomeWidget(QWidget):
    def __init__(self, event_bus):
        self._event_bus = event_bus
        self._event_bus.subscribe(PiezoPositionChanged, self._on_piezo_changed)

    def _on_piezo_changed(self, event):
        self.label.setText(f"Z: {event.position_um}")  # CRASH: Wrong thread!
```

## Why Qt Signals Don't Have This Problem

Qt signals use "queued connections" for cross-thread communication. When a signal is emitted from thread A and connected to a slot in thread B, Qt automatically posts the call to thread B's event queue:

```python
class Worker(QObject):
    finished = Signal(int)

# Connection across threads uses queued connection automatically
worker.finished.connect(widget.on_finished)  # Safe: Qt handles thread crossing
```

## The QtMultiPointController Pattern

The current code avoids this problem using Qt wrapper classes:

```python
class QtMultiPointController(MultiPointController, QObject):
    acquisition_finished = Signal(bool, object)  # Qt signal

    def __init__(self, ...):
        # Register callback that emits Qt signal
        self.set_acquisition_finished_callback(self._signal_acquisition_finished_fn)

    def _signal_acquisition_finished_fn(self, success, error):
        self.acquisition_finished.emit(success, error)  # Qt handles thread crossing
```

The worker thread calls the callback, which emits a Qt signal. Qt marshals the signal to the main thread. Widgets connect to the Qt signal, not the EventBus.

This works but defeats the purpose of the EventBus—widgets need a reference to the Qt wrapper, coupling them to it.

## Solution Options

### Option A: Thread-Safe EventBus

Make EventBus inherit from QObject and use a Qt signal internally to marshal all handler dispatch to the main thread:

```python
class EventBus(QObject):
    _dispatch_signal = Signal(object, object)  # (event, handlers)

    def __init__(self):
        super().__init__()
        self._dispatch_signal.connect(self._dispatch_on_main_thread)
        self._main_thread = QThread.currentThread()

    def publish(self, event):
        handlers = list(self._subscribers.get(type(event), []))

        if QThread.currentThread() == self._main_thread:
            # Already on main thread - dispatch directly
            for handler in handlers:
                self._safe_call(handler, event)
        else:
            # Worker thread - queue to main thread via Qt signal
            self._dispatch_signal.emit(event, handlers)

    def _dispatch_on_main_thread(self, event, handlers):
        for handler in handlers:
            self._safe_call(handler, event)
```

**Pros**: Drop-in replacement, same API, widgets can subscribe safely
**Cons**: EventBus now depends on Qt, handlers always run on main thread (even if you wanted worker-thread execution)

### Option B: Keep Qt Wrappers

Keep the current pattern where Qt wrapper classes bridge worker threads to GUI:

```
Worker Thread → callback → QtWrapper emits Signal → Main Thread → Widget slot
```

**Pros**: Explicit about thread boundaries, no EventBus changes
**Cons**: Widgets coupled to Qt wrappers, EventBus can't be used for worker→widget communication

### Option C: Explicit Thread Dispatch

Add a parameter to control dispatch behavior:

```python
event_bus.subscribe(SomeEvent, handler, dispatch_on_main_thread=True)
```

**Pros**: Flexible, explicit
**Cons**: Easy to forget, more complex API

## Recommendation

**Option A (Thread-Safe EventBus)** is recommended because:

1. It's a drop-in replacement—existing code works unchanged
2. Widgets can safely subscribe to any event without knowing where it's published from
3. Falls back to synchronous dispatch when Qt unavailable (headless testing)
4. Aligns with the architecture goal: widgets subscribe to events, services/controllers publish

The main trade-off is that all handlers run on the main thread, but this is correct for GUI applications—handlers that need worker-thread execution are rare and can spawn their own threads.
