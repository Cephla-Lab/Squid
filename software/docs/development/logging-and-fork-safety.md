# Logging and Fork Safety

The squid logging system provides structured logging with support for GUI display, headless operation, and forked subprocesses.

## The Problem

On Linux, Python's `multiprocessing` defaults to `fork`, which clones the entire process including all objects. Qt objects (QObject, signals, QTimer) contain C++ pointers that become invalid in the child process. If a logging handler uses Qt signals, any log message in the subprocess will crash.

```
Main Process                    Forked Subprocess
┌─────────────────────┐         ┌─────────────────────┐
│ QtLoggingHandler    │  fork   │ QtLoggingHandler    │
│  └─ QObject (valid) │ ─────→  │  └─ QObject (INVALID)│
│  └─ signal (valid)  │         │  └─ signal (CRASH!) │
└─────────────────────┘         └─────────────────────┘
```

macOS uses `spawn` by default (starts fresh process), so this crash only manifests on Linux.

## The Solution: BufferingHandler

The `BufferingHandler` in `squid/logging.py` uses a standard Python `queue.Queue` instead of Qt signals:

```
Main Process                    Forked Subprocess
┌─────────────────────┐         ┌─────────────────────┐
│ BufferingHandler    │  fork   │ BufferingHandler    │
│  └─ queue.Queue     │ ─────→  │  └─ queue.Queue     │
│   (fork-compatible) │         │     (isolated copy) │
└─────────────────────┘         └─────────────────────┘
         │                                 │
         │ poll via QTimer                 │ messages accumulate
         ↓                                 │ (bounded, no consumer)
    GUI displays                           └─→ no crash
```

### Key Properties

| Property | Description |
|----------|-------------|
| Fork-compatible | Uses `queue.Queue` which survives fork as isolated copy |
| Thread-safe | `queue.Queue` handles concurrent access; `dropped_count` protected by lock |
| Headless-safe | No Qt dependencies in core handler |
| Bounded | Max 1000 messages prevents memory growth in subprocess |
| Observable | `dropped_count` property tracks overflow accurately under contention |

## Usage

### GUI Application

```python
import logging
from squid.logging import BufferingHandler, get_logger

# Create and attach handler
handler = BufferingHandler(min_level=logging.WARNING)
get_logger().addHandler(handler)

# Connect to widget (starts QTimer polling)
warning_widget.connect_handler(handler)

# Later, disconnect and cleanup
warning_widget.disconnect_handler()
get_logger().removeHandler(handler)
```

### Headless Script

```python
import logging
from squid.logging import BufferingHandler, get_logger

handler = BufferingHandler(min_level=logging.WARNING)
get_logger().addHandler(handler)

# ... run operations ...

# Check for warnings/errors
for level, logger_name, message in handler.get_pending():
    print(f"[{logging.getLevelName(level)}] {logger_name}: {message}")

# Check if any messages were dropped
if handler.dropped_count > 0:
    print(f"Warning: {handler.dropped_count} messages dropped (buffer full)")
```

## Implementation Details

### WarningErrorWidget Polling

The `WarningErrorWidget` polls the handler every 100ms via QTimer:

```python
class WarningErrorWidget(QWidget):
    POLL_INTERVAL_MS = 100

    def connect_handler(self, handler: BufferingHandler):
        self.disconnect_handler()  # Clean up any existing timer
        self._handler = handler
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_messages)
        self._poll_timer.start(self.POLL_INTERVAL_MS)

    def _poll_messages(self):
        if self._handler is None:
            return
        try:
            for level, logger_name, message in self._handler.get_pending():
                self.add_message(level, logger_name, message)
        except Exception as e:
            # Qt silently swallows timer exceptions - log explicitly
            squid.logging.get_logger(__name__).error(f"Poll error: {e}")
```

### Buffer Overflow Handling

When the buffer is full (1000 messages), new messages are dropped and counted:

```python
def emit(self, record):
    try:
        msg = self.format(record)
        self._queue.put_nowait((record.levelno, record.name, msg))
    except queue.Full:
        # dropped_count is protected by a lock in the real implementation
        with self._dropped_count_lock:
            self._dropped_count += 1  # Track, don't block
    except Exception:
        self.handleError(record)
```

This prevents:
- Blocking in the logging thread
- Unbounded memory growth in subprocesses with no consumer

## Common Pitfalls

### 1. Qt Objects in Forked Processes

**Wrong:**
```python
class BadHandler(logging.Handler):
    def __init__(self):
        self.signal = pyqtSignal(str)  # C++ object, invalid after fork
```

**Right:**
```python
class GoodHandler(logging.Handler):
    def __init__(self):
        self._queue = queue.Queue()  # Python object, fork-safe
```

### 2. Qt Silently Swallows Exceptions

**Wrong:**
```python
def _poll_messages(self):
    for msg in self._handler.get_pending():
        self.process(msg)  # Exception here is silently lost
```

**Right:**
```python
def _poll_messages(self):
    try:
        for msg in self._handler.get_pending():
            self.process(msg)
    except Exception as e:
        logger.error(f"Poll error: {e}", exc_info=True)
```

### 3. GUI Thread Requirement

`connect_handler()` and `disconnect_handler()` must be called from the GUI thread because they create/destroy QTimer objects, which are not thread-safe.

## Testing

Tests are in:
- `tests/squid/test_logging.py` - BufferingHandler unit tests
- `tests/control/test_widgets.py` - Widget integration tests

Key test scenarios:
- Message buffering at configured level
- Buffer overflow and dropped_count tracking
- Multi-threaded emit safety
- Double-connect handling (timer cleanup)
- Headless operation (no Qt)

## Related

- [Acquisition Backpressure](acquisition-backpressure.md) - Similar cross-process coordination
- PR #467 - Original WarningErrorWidget implementation
- PR #487 - Fork-safety fix
