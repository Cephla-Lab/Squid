# Service Layer Synchronization Implementation Guide

## Overview

This document describes how to add robust synchronization to the service layer:
- **Command queueing** - Priority-based command serialization per service
- **Per-service locks** - Thread-safe hardware access
- **Async execution** - Non-blocking hardware calls via worker threads
- **Cancellation** - Pending commands can be cancelled by ID

**Prerequisites:** The basic service layer (BaseService, ServiceRegistry, CameraService, StageService, PeripheralService) must already exist. See `SERVICE_LAYER_ARCHITECTURE.md`.

---

## Architecture

```
┌─────────────┐         ┌─────────────────────────┐
│   Widget    │────────►│      BaseService        │
│  (Qt Main)  │ command │  ┌───────────────────┐  │
└─────────────┘         │  │  Priority Queue   │  │
       ▲                │  │    (heapq)        │  │
       │                │  └─────────┬─────────┘  │
       │ events         │            ▼            │
       │                │  ┌───────────────────┐  │
       │                │  │   Hardware Lock   │  │
       │                │  │  (threading.Lock) │  │
       │                │  └─────────┬─────────┘  │
       │                │            ▼            │
       │                │  ┌───────────────────┐  │
       │                │  │  WorkerManager    │──┼──► Hardware
       │                │  │  (ThreadPool)     │  │
       │                │  └───────────────────┘  │
       │                └─────────────────────────┘
       │                             │
       └─────────────────────────────┘
              CommandCompleted / CommandFailed events
```

**Key Principles:**
- Each service has its OWN queue and lock (no cross-service contention)
- Commands execute in priority order (CRITICAL > HIGH > NORMAL > LOW)
- Same-priority commands execute FIFO
- Sync methods remain for scripts; async methods for UI

---

## Existing Infrastructure (READ THESE FIRST)

Before writing any code, understand these existing components:

| File | What It Does | Why You Need It |
|------|--------------|-----------------|
| `squid/utils/worker_manager.py` | ThreadPoolExecutor with timeout detection | Submit async tasks here |
| `squid/utils/thread_safe_state.py` | ThreadSafeValue, ThreadSafeFlag primitives | Reference patterns |
| `squid/events.py` | EventBus pub/sub system | Publish completion events |
| `squid/services/base.py` | BaseService abstract class | Extend this |

**Read these files before starting.** The WorkerManager already handles threading, timeouts, and Qt signal marshalling.

---

## Task Breakdown

Each task is a single commit. Write tests FIRST (TDD).

---

### Task 1: Add Command Result Events

**Files to modify:**
- `squid/events.py`

**Files to read first:**
- `squid/events.py` (understand existing event pattern)

**What to do:**

Add three new event types for async command lifecycle:

```python
# squid/events.py - ADD after existing events

@dataclass
class CommandCompleted(Event):
    """Emitted when async command completes successfully."""
    command_id: str
    service_name: str
    result: Any = None


@dataclass
class CommandFailed(Event):
    """Emitted when async command fails."""
    command_id: str
    service_name: str
    error: str
    stack_trace: Optional[str] = None


@dataclass
class CommandTimeout(Event):
    """Emitted when async command times out."""
    command_id: str
    service_name: str
```

**Test file:** `tests/squid/test_events.py`

**Test to write:**
```python
def test_command_completed_event_has_required_fields():
    event = CommandCompleted(command_id="abc", service_name="StageService", result=42)
    assert event.command_id == "abc"
    assert event.service_name == "StageService"
    assert event.result == 42


def test_command_failed_event_has_error():
    event = CommandFailed(command_id="abc", service_name="StageService", error="oops")
    assert "oops" in event.error
```

**Run tests:** `pytest tests/squid/test_events.py -v`

**Commit message:** `Add CommandCompleted/CommandFailed/CommandTimeout events`

---

### Task 2: Create Command Priority and Container

**Files to create:**
- `squid/services/commands.py` (NEW FILE)

**Files to read first:**
- None (new file)

**What to do:**

Create the command container with priority support:

```python
# squid/services/commands.py (NEW FILE)
"""Command infrastructure for async service execution."""
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional
from uuid import uuid4


class CommandPriority(IntEnum):
    """Command priority levels. Higher value = higher priority."""
    LOW = 0
    NORMAL = 50
    HIGH = 100
    CRITICAL = 200  # For emergency stop, etc.


@dataclass
class ServiceCommand:
    """
    Container for an async service command.

    Attributes:
        command_id: Unique identifier for tracking/cancellation
        task: The callable to execute (takes no args, returns result)
        priority: Execution priority (higher = sooner)
        timeout_ms: Max execution time before timeout
        on_complete: Optional callback on success
        on_error: Optional callback on failure
        cancelled: Set True when cancelled (skip execution)
    """
    command_id: str
    task: Callable[[], Any]
    priority: CommandPriority = CommandPriority.NORMAL
    timeout_ms: int = 30000
    on_complete: Optional[Callable[[Any], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None
    cancelled: bool = False

    @staticmethod
    def generate_id() -> str:
        """Generate unique command ID."""
        return str(uuid4())

    def __lt__(self, other: "ServiceCommand") -> bool:
        """
        Compare for priority queue ordering.
        Higher priority = should come first.
        heapq is a min-heap, so invert comparison.
        """
        return self.priority > other.priority
```

**Test file:** `tests/squid/services/test_commands.py` (NEW FILE)

**Tests to write:**
```python
# tests/squid/services/test_commands.py
import pytest
from squid.services.commands import CommandPriority, ServiceCommand


def test_command_priority_ordering():
    """CRITICAL > HIGH > NORMAL > LOW"""
    assert CommandPriority.CRITICAL > CommandPriority.HIGH
    assert CommandPriority.HIGH > CommandPriority.NORMAL
    assert CommandPriority.NORMAL > CommandPriority.LOW


def test_service_command_generates_unique_ids():
    id1 = ServiceCommand.generate_id()
    id2 = ServiceCommand.generate_id()
    assert id1 != id2


def test_service_command_comparison_for_heapq():
    """Higher priority should come first (be 'less than')."""
    high = ServiceCommand(command_id="1", task=lambda: None, priority=CommandPriority.HIGH)
    low = ServiceCommand(command_id="2", task=lambda: None, priority=CommandPriority.LOW)
    # For heapq min-heap, high priority should be "less than"
    assert high < low


def test_service_command_defaults():
    cmd = ServiceCommand(command_id="test", task=lambda: 42)
    assert cmd.priority == CommandPriority.NORMAL
    assert cmd.timeout_ms == 30000
    assert cmd.cancelled is False
```

**Run tests:** `pytest tests/squid/services/test_commands.py -v`

**Commit message:** `Add CommandPriority and ServiceCommand for async execution`

---

### Task 3: Add Async Infrastructure to BaseService

**Files to modify:**
- `squid/services/base.py`

**Files to read first:**
- `squid/services/base.py` (current implementation)
- `squid/utils/worker_manager.py` (how to use WorkerManager)
- `squid/services/commands.py` (your Task 2 code)

**What to do:**

Extend BaseService with command queueing, locking, and async execution.

**ADD these imports at the top:**
```python
import heapq
import queue
import threading
from typing import Dict, TypeVar

from squid.services.commands import CommandPriority, ServiceCommand
from squid.events import CommandCompleted, CommandFailed
```

**ADD these instance variables to `__init__`:**
```python
def __init__(self, event_bus: EventBus):
    # ... existing code ...

    # Synchronization infrastructure
    self._lock = threading.Lock()  # Protects hardware access
    self._queue_lock = threading.Lock()  # Protects priority queue
    self._command_heap: list[ServiceCommand] = []  # Priority queue
    self._pending_commands: Dict[str, ServiceCommand] = {}  # For cancel lookup
    self._worker_manager: Optional["WorkerManager"] = None
    self._service_name: str = self.__class__.__name__
    self._processing = False  # Prevents concurrent queue processing
```

**ADD these methods:**
```python
def _get_worker_manager(self) -> "WorkerManager":
    """Lazy-init shared WorkerManager."""
    if self._worker_manager is None:
        from squid.utils.worker_manager import WorkerManager
        self._worker_manager = WorkerManager(max_workers=2)
    return self._worker_manager


def submit_async(
    self,
    task: Callable[[], Any],
    priority: CommandPriority = CommandPriority.NORMAL,
    timeout_ms: int = 30000,
    on_complete: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> str:
    """
    Submit command for async execution in worker thread.

    Args:
        task: Callable that performs the work (no args, returns result)
        priority: Execution priority (CRITICAL executes first)
        timeout_ms: Max execution time
        on_complete: Called with result on success
        on_error: Called with exception on failure

    Returns:
        command_id for tracking/cancellation
    """
    cmd = ServiceCommand(
        command_id=ServiceCommand.generate_id(),
        task=task,
        priority=priority,
        timeout_ms=timeout_ms,
        on_complete=on_complete,
        on_error=on_error,
    )
    with self._queue_lock:
        heapq.heappush(self._command_heap, cmd)
        self._pending_commands[cmd.command_id] = cmd

    self._process_next_command()
    return cmd.command_id


def cancel(self, command_id: str) -> bool:
    """
    Cancel a pending command by ID.

    Returns True if command was found and cancelled.
    Has no effect on already-executing commands.
    """
    with self._queue_lock:
        cmd = self._pending_commands.get(command_id)
        if cmd and not cmd.cancelled:
            cmd.cancelled = True
            del self._pending_commands[command_id]
            self._log.debug(f"Cancelled command {command_id}")
            return True
    return False


def cancel_all(self) -> None:
    """Cancel all pending commands."""
    with self._queue_lock:
        for cmd in self._pending_commands.values():
            cmd.cancelled = True
        self._pending_commands.clear()
        self._command_heap.clear()


def _process_next_command(self) -> None:
    """Process next command from priority queue."""
    with self._queue_lock:
        if self._processing:
            return  # Already processing a command
        self._processing = True

        # Find next non-cancelled command
        cmd = None
        while self._command_heap:
            candidate = heapq.heappop(self._command_heap)
            if not candidate.cancelled:
                cmd = candidate
                break

        if cmd is None:
            self._processing = False
            return

        # Remove from pending (now executing)
        self._pending_commands.pop(cmd.command_id, None)

    def wrapped_task():
        with self._lock:  # Serialize hardware access
            return cmd.task()

    def on_done(result):
        if cmd.on_complete:
            try:
                cmd.on_complete(result)
            except Exception as e:
                self._log.error(f"on_complete callback failed: {e}")
        self.publish(CommandCompleted(
            command_id=cmd.command_id,
            service_name=self._service_name,
            result=result,
        ))
        with self._queue_lock:
            self._processing = False
        self._process_next_command()

    def on_fail(error):
        if cmd.on_error:
            try:
                cmd.on_error(error)
            except Exception as e:
                self._log.error(f"on_error callback failed: {e}")
        self.publish(CommandFailed(
            command_id=cmd.command_id,
            service_name=self._service_name,
            error=str(error),
        ))
        with self._queue_lock:
            self._processing = False
        self._process_next_command()

    self._get_worker_manager().submit(
        task_name=cmd.command_id,
        task=wrapped_task,
        timeout_ms=cmd.timeout_ms,
        on_complete=on_done,
        on_error=on_fail,
    )


def execute_sync(self, task: Callable[[], T]) -> T:
    """
    Execute task synchronously with lock protection.
    Use this in scripts where blocking is acceptable.
    """
    with self._lock:
        return task()
```

**UPDATE `shutdown` method:**
```python
def shutdown(self) -> None:
    """Clean shutdown of service."""
    self.cancel_all()

    if self._worker_manager:
        self._worker_manager.shutdown()

    for event_type, handler in self._subscriptions:
        self._event_bus.unsubscribe(event_type, handler)
    self._subscriptions.clear()
```

**Test file:** `tests/squid/services/test_base_service_async.py` (NEW FILE)

**Tests to write:**
```python
# tests/squid/services/test_base_service_async.py
import threading
import time
import pytest
from squid.events import EventBus, CommandCompleted, CommandFailed
from squid.services.base import BaseService
from squid.services.commands import CommandPriority


class TestService(BaseService):
    """Concrete implementation for testing."""
    pass


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def service(event_bus):
    svc = TestService(event_bus)
    yield svc
    svc.shutdown()


def test_submit_async_returns_command_id(service):
    cmd_id = service.submit_async(lambda: 42)
    assert cmd_id is not None
    assert len(cmd_id) > 0


def test_commands_execute_in_order(service):
    results = []
    done = threading.Event()

    def make_task(n):
        def task():
            results.append(n)
            if n == 3:
                done.set()
            return n
        return task

    service.submit_async(make_task(1))
    service.submit_async(make_task(2))
    service.submit_async(make_task(3))

    done.wait(timeout=2.0)
    assert results == [1, 2, 3]


def test_high_priority_executes_first(service):
    results = []
    done = threading.Event()

    # Block with slow task
    service.submit_async(lambda: (time.sleep(0.1), results.append("slow"))[1])
    # Queue while blocked
    service.submit_async(lambda: results.append("normal"), priority=CommandPriority.NORMAL)
    service.submit_async(lambda: results.append("high"), priority=CommandPriority.HIGH)
    service.submit_async(lambda: (results.append("critical"), done.set())[1], priority=CommandPriority.CRITICAL)

    done.wait(timeout=2.0)
    time.sleep(0.3)  # Let all complete
    # critical and high should be before normal
    assert results.index("critical") < results.index("normal")
    assert results.index("high") < results.index("normal")


def test_cancel_pending_command(service):
    results = []
    done = threading.Event()

    # Block service
    service.submit_async(lambda: (time.sleep(0.2), results.append("blocking"), done.set())[2])
    # Queue command then cancel
    cmd_id = service.submit_async(lambda: results.append("cancelled"))
    cancelled = service.cancel(cmd_id)

    done.wait(timeout=2.0)
    time.sleep(0.1)
    assert cancelled is True
    assert "cancelled" not in results


def test_cancel_returns_false_for_unknown_id(service):
    result = service.cancel("nonexistent-id")
    assert result is False


def test_cancel_all_clears_queue(service):
    results = []
    done = threading.Event()

    service.submit_async(lambda: (time.sleep(0.1), results.append(1), done.set())[2])
    service.submit_async(lambda: results.append(2))
    service.submit_async(lambda: results.append(3))
    service.cancel_all()

    done.wait(timeout=2.0)
    time.sleep(0.1)
    assert results == [1]  # Only first (already executing)


def test_lock_serializes_execution(service):
    """Commands should not overlap execution."""
    execution_log = []
    done = threading.Event()

    def timed_task(name):
        def task():
            start = time.time()
            time.sleep(0.05)
            end = time.time()
            execution_log.append((name, start, end))
            if name == "b":
                done.set()
            return name
        return task

    service.submit_async(timed_task("a"))
    service.submit_async(timed_task("b"))

    done.wait(timeout=2.0)
    # Verify no overlap
    a_start, a_end = execution_log[0][1], execution_log[0][2]
    b_start, b_end = execution_log[1][1], execution_log[1][2]
    assert a_end <= b_start, "Tasks should not overlap"


def test_on_complete_callback_invoked(service):
    callback_results = []
    done = threading.Event()

    service.submit_async(
        lambda: 42,
        on_complete=lambda r: (callback_results.append(r), done.set()),
    )

    done.wait(timeout=2.0)
    assert callback_results == [42]


def test_command_completed_event_published(service, event_bus):
    events = []
    event_bus.subscribe(CommandCompleted, lambda e: events.append(e))
    done = threading.Event()

    service.submit_async(
        lambda: "result",
        on_complete=lambda _: done.set(),
    )

    done.wait(timeout=2.0)
    time.sleep(0.05)  # Let event propagate
    assert len(events) == 1
    assert events[0].service_name == "TestService"
    assert events[0].result == "result"


def test_command_failed_event_on_error(service, event_bus):
    events = []
    event_bus.subscribe(CommandFailed, lambda e: events.append(e))
    done = threading.Event()

    service.submit_async(
        lambda: (_ for _ in ()).throw(RuntimeError("test error")),
        on_error=lambda _: done.set(),
    )

    done.wait(timeout=2.0)
    time.sleep(0.05)
    assert len(events) == 1
    assert "test error" in events[0].error


def test_execute_sync_blocks_and_returns(service):
    result = service.execute_sync(lambda: 42)
    assert result == 42
```

**Run tests:** `pytest tests/squid/services/test_base_service_async.py -v`

**Commit message:** `Add async command execution to BaseService with priority queue and cancellation`

---

### Task 4: Add Async Methods to StageService

**Files to modify:**
- `squid/services/stage_service.py`

**Files to read first:**
- `squid/services/stage_service.py` (current implementation)
- `squid/abc.py` (Stage ABC, especially `stop()` method if it exists)

**What to do:**

Add async versions of stage methods. Keep sync methods for backwards compatibility.

**ADD import:**
```python
from squid.services.commands import CommandPriority
```

**ADD these methods:**
```python
def move_x_async(
    self,
    distance_mm: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """
    Non-blocking X move. Returns command_id for cancellation.

    Publishes StagePositionChanged event on completion.
    """
    def task():
        self._stage.move_x(distance_mm, blocking=True)
        return self._stage.get_pos()

    def on_done(pos):
        self._publish_position()
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=60000,
        on_complete=on_done,
    )


def move_y_async(
    self,
    distance_mm: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking Y move."""
    def task():
        self._stage.move_y(distance_mm, blocking=True)
        return self._stage.get_pos()

    def on_done(pos):
        self._publish_position()
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=60000,
        on_complete=on_done,
    )


def move_z_async(
    self,
    distance_mm: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking Z move."""
    def task():
        self._stage.move_z(distance_mm, blocking=True)
        return self._stage.get_pos()

    def on_done(pos):
        self._publish_position()
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=60000,
        on_complete=on_done,
    )


def move_to_async(
    self,
    x_mm: float,
    y_mm: float,
    z_mm: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking move to absolute position."""
    def task():
        self._stage.move_x_to(x_mm, blocking=True)
        self._stage.move_y_to(y_mm, blocking=True)
        self._stage.move_z_to(z_mm, blocking=True)
        return self._stage.get_pos()

    def on_done(pos):
        self._publish_position()
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=120000,  # Longer timeout for multi-axis
        on_complete=on_done,
    )


def home_async(
    self,
    x: bool = False,
    y: bool = False,
    z: bool = False,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking home operation."""
    def task():
        self._stage.home(x, y, z)
        return self._stage.get_pos()

    def on_done(pos):
        self._publish_position()
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=300000,  # Homing can take a long time
        on_complete=on_done,
    )


def emergency_stop(self) -> str:
    """
    High-priority stop that jumps the queue.

    Use this when user needs to stop motion immediately.
    """
    def task():
        # If stage has stop() method, call it
        if hasattr(self._stage, 'stop'):
            self._stage.stop()
        return self._stage.get_pos()

    return self.submit_async(
        task,
        priority=CommandPriority.CRITICAL,
        timeout_ms=5000,
    )
```

**Test file:** `tests/squid/services/test_stage_service.py`

**ADD these tests:**
```python
def test_move_x_async_returns_command_id(stage_service):
    cmd_id = stage_service.move_x_async(1.0)
    assert cmd_id is not None


def test_move_x_async_publishes_position_event(stage_service, event_bus):
    events = []
    event_bus.subscribe(StagePositionChanged, lambda e: events.append(e))
    done = threading.Event()

    stage_service.move_x_async(1.0, on_complete=done.set)
    done.wait(timeout=2.0)
    time.sleep(0.05)

    assert len(events) >= 1


def test_emergency_stop_has_critical_priority(stage_service):
    # Queue normal moves
    stage_service.move_x_async(1.0)
    stage_service.move_y_async(1.0)
    # Emergency stop should jump queue
    cmd_id = stage_service.emergency_stop()
    assert cmd_id is not None
```

**Run tests:** `pytest tests/squid/services/test_stage_service.py -v`

**Commit message:** `Add async move methods to StageService with priority and emergency_stop`

---

### Task 5: Add Async Methods to CameraService

**Files to modify:**
- `squid/services/camera_service.py`

**What to do:**

Add async versions of camera settings methods.

**ADD import:**
```python
from squid.services.commands import CommandPriority
```

**ADD these methods:**
```python
def set_exposure_time_async(
    self,
    exposure_time_ms: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking exposure time change."""
    def task():
        limits = self._camera.get_exposure_limits()
        clamped = max(limits[0], min(limits[1], exposure_time_ms))
        self._camera.set_exposure_time(clamped)
        return clamped

    def on_done(actual_ms):
        self.publish(ExposureTimeChanged(exposure_time_ms=actual_ms))
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=5000,
        on_complete=on_done,
    )


def set_analog_gain_async(
    self,
    gain: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking analog gain change."""
    def task():
        limits = self._camera.get_analog_gain_limits()
        clamped = max(limits[0], min(limits[1], gain))
        self._camera.set_analog_gain(clamped)
        return clamped

    def on_done(actual):
        self.publish(AnalogGainChanged(gain=actual))
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=5000,
        on_complete=on_done,
    )
```

**Test file:** `tests/squid/services/test_camera_service.py`

**ADD these tests:**
```python
def test_set_exposure_time_async_returns_command_id(camera_service):
    cmd_id = camera_service.set_exposure_time_async(10.0)
    assert cmd_id is not None


def test_set_exposure_time_async_publishes_event(camera_service, event_bus):
    events = []
    event_bus.subscribe(ExposureTimeChanged, lambda e: events.append(e))
    done = threading.Event()

    camera_service.set_exposure_time_async(10.0, on_complete=done.set)
    done.wait(timeout=2.0)
    time.sleep(0.05)

    assert len(events) >= 1
```

**Run tests:** `pytest tests/squid/services/test_camera_service.py -v`

**Commit message:** `Add async exposure and gain methods to CameraService`

---

### Task 6: Add Async Methods to PeripheralService

**Files to modify:**
- `squid/services/peripheral_service.py`

**What to do:**

Add async version of DAC control.

**ADD import:**
```python
from squid.services.commands import CommandPriority
```

**ADD this method:**
```python
def set_dac_async(
    self,
    channel: int,
    percentage: float,
    priority: CommandPriority = CommandPriority.NORMAL,
    on_complete: Optional[Callable[[], None]] = None,
) -> str:
    """Non-blocking DAC set."""
    def task():
        clamped = max(0.0, min(100.0, percentage))
        value = round(clamped * 65535 / 100)
        self._microcontroller.analog_write_onboard_DAC(channel, value)
        return clamped

    def on_done(actual):
        self.publish(DACValueChanged(channel=channel, value=actual))
        if on_complete:
            on_complete()

    return self.submit_async(
        task,
        priority=priority,
        timeout_ms=1000,
        on_complete=on_done,
    )
```

**Test file:** `tests/squid/services/test_peripheral_service.py`

**ADD this test:**
```python
def test_set_dac_async_returns_command_id(peripheral_service):
    cmd_id = peripheral_service.set_dac_async(0, 50.0)
    assert cmd_id is not None
```

**Run tests:** `pytest tests/squid/services/test_peripheral_service.py -v`

**Commit message:** `Add async DAC method to PeripheralService`

---

### Task 7: Update NavigationWidget to Use Async Methods

**Files to modify:**
- `control/widgets/stage.py`

**Files to read first:**
- `control/widgets/stage.py` (current NavigationWidget implementation)

**What to do:**

Update button handlers to use async methods and disable buttons during moves.

**Example pattern for one button:**
```python
def move_x_forward(self):
    """Handle X+ button click."""
    # Disable button during move
    self.btn_moveX_forward.setEnabled(False)

    def on_complete():
        # Re-enable button (runs on Qt main thread via event)
        self.btn_moveX_forward.setEnabled(True)

    self._stage_service.move_x_async(
        distance_mm=self.entry_deltaX.value(),
        on_complete=on_complete,
    )
```

**Apply this pattern to all move buttons.**

**Test:** Manual testing - click buttons, verify they disable during move and re-enable after.

**Commit message:** `Update NavigationWidget to use async stage methods`

---

### Task 8: Update CameraSettingsWidget to Use Async Methods

**Files to modify:**
- `control/widgets/camera.py`

**What to do:**

Update exposure/gain controls to use async methods.

**Example:**
```python
def set_exposure_time(self, value):
    """Handle exposure time spinbox change."""
    self.entry_exposureTime.setEnabled(False)

    def on_complete():
        self.entry_exposureTime.setEnabled(True)

    self._camera_service.set_exposure_time_async(
        exposure_time_ms=value,
        on_complete=on_complete,
    )
```

**Commit message:** `Update CameraSettingsWidget to use async camera methods`

---

### Task 9: Update DACControWidget to Use Async Methods

**Files to modify:**
- `control/widgets/hardware.py`

**What to do:**

Update DAC controls to use async methods.

**Commit message:** `Update DACControWidget to use async peripheral methods`

---

## Testing Strategy

**Unit tests:** Each service and the base class have their own test file.

**Run all service tests:**
```bash
pytest tests/squid/services/ -v
```

**Integration tests:** Create `tests/squid/services/test_async_integration.py`:
```python
def test_concurrent_services_dont_block_each_other():
    """Stage and camera commands should execute in parallel."""
    stage_done = threading.Event()
    camera_done = threading.Event()

    stage_service.move_x_async(1.0, on_complete=stage_done.set)
    camera_service.set_exposure_time_async(10.0, on_complete=camera_done.set)

    assert stage_done.wait(2.0), "Stage command timed out"
    assert camera_done.wait(2.0), "Camera command timed out"
```

**Manual testing checklist:**
- [ ] Click stage move button - should disable during move
- [ ] Queue multiple moves - should execute in order
- [ ] Emergency stop - should execute immediately
- [ ] Cancel pending move - should not execute
- [ ] Shutdown during moves - should not hang

---

## File Summary

| File | Action |
|------|--------|
| `squid/events.py` | ADD CommandCompleted, CommandFailed, CommandTimeout |
| `squid/services/commands.py` | CREATE CommandPriority, ServiceCommand |
| `squid/services/base.py` | EXTEND with async infrastructure |
| `squid/services/stage_service.py` | ADD async move methods |
| `squid/services/camera_service.py` | ADD async settings methods |
| `squid/services/peripheral_service.py` | ADD async DAC method |
| `control/widgets/stage.py` | UPDATE to use async methods |
| `control/widgets/camera.py` | UPDATE to use async methods |
| `control/widgets/hardware.py` | UPDATE to use async methods |
| `tests/squid/services/test_commands.py` | CREATE |
| `tests/squid/services/test_base_service_async.py` | CREATE |

---

## Commit Order

1. `Add CommandCompleted/CommandFailed/CommandTimeout events`
2. `Add CommandPriority and ServiceCommand for async execution`
3. `Add async command execution to BaseService with priority queue and cancellation`
4. `Add async move methods to StageService with priority and emergency_stop`
5. `Add async exposure and gain methods to CameraService`
6. `Add async DAC method to PeripheralService`
7. `Update NavigationWidget to use async stage methods`
8. `Update CameraSettingsWidget to use async camera methods`
9. `Update DACControWidget to use async peripheral methods`

---

## Principles Applied

- **DRY**: Async infrastructure lives in BaseService, not duplicated per service
- **YAGNI**: Only adding async methods that are actually needed by widgets
- **TDD**: Each task starts with writing tests
