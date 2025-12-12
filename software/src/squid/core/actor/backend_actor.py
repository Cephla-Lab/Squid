"""Backend Actor for processing commands on a dedicated thread.

The BackendActor owns a priority queue and processes commands sequentially
on a single backend thread. This ensures:
1. All controller logic runs on a known thread
2. Commands are processed in priority order (STOP > NORMAL)
3. No race conditions between command handlers
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

import squid.core.logging
from squid.core.events import Event
from squid.core.actor.thread_assertions import set_backend_thread, clear_backend_thread

_log = squid.core.logging.get_logger(__name__)


# Priority levels - higher numbers = higher priority
class Priority:
    """Command priority levels."""

    NORMAL = 50
    HIGH = 75
    STOP = 100  # Stop commands always processed first


@dataclass(order=True)
class CommandEnvelope:
    """Wrapper for commands with priority and metadata.

    The ordering is by (-priority, timestamp) so higher priority
    commands are processed first, and within same priority, FIFO.
    """

    sort_key: tuple = field(init=False, repr=False)
    priority: int
    timestamp: float
    command: Event = field(compare=False)

    def __post_init__(self):
        # Negative priority so higher priority sorts first
        self.sort_key = (-self.priority, self.timestamp)


class PriorityCommandQueue:
    """Thread-safe priority queue for commands.

    Higher priority commands are dequeued first. Within the same
    priority level, commands are processed FIFO.
    """

    def __init__(self):
        self._queue: queue.PriorityQueue[CommandEnvelope] = queue.PriorityQueue()
        self._lock = threading.Lock()

    def put(self, command: Event, priority: int = Priority.NORMAL) -> None:
        """Enqueue a command with given priority."""
        envelope = CommandEnvelope(
            priority=priority,
            timestamp=time.time(),
            command=command,
        )
        self._queue.put(envelope)

    def get(self, timeout: Optional[float] = None) -> CommandEnvelope:
        """Dequeue the highest priority command.

        Raises queue.Empty if timeout expires.
        """
        return self._queue.get(timeout=timeout)

    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()

    def qsize(self) -> int:
        """Return approximate queue size."""
        return self._queue.qsize()


# Type alias for command handlers
CommandHandler = Callable[[Event], None]


class BackendActor:
    """Actor that processes commands on a dedicated backend thread.

    The BackendActor:
    1. Owns a priority command queue
    2. Runs a background thread that processes commands
    3. Dispatches commands to registered handlers
    4. Optionally spawns compute/IO work to a thread pool

    Usage:
        actor = BackendActor()
        actor.register_handler(StartLiveCommand, live_controller.handle_start_live)
        actor.start()

        # Commands are enqueued and processed on backend thread
        actor.enqueue(StartLiveCommand(...))

        # Shutdown
        actor.stop()
    """

    def __init__(
        self,
        worker_pool_size: int = 4,
    ):
        """Initialize the BackendActor.

        Args:
            worker_pool_size: Size of thread pool for compute/IO tasks
        """
        self._command_queue = PriorityCommandQueue()
        self._handlers: Dict[Type[Event], List[CommandHandler]] = {}
        self._worker_pool: Optional[ThreadPoolExecutor] = None
        self._worker_pool_size = worker_pool_size

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def register_handler(
        self, command_type: Type[Event], handler: CommandHandler
    ) -> None:
        """Register a handler for a command type.

        Multiple handlers can be registered for the same command type.
        They will be called in registration order.

        Args:
            command_type: The command class to handle
            handler: Function to call when command is received
        """
        with self._lock:
            if command_type not in self._handlers:
                self._handlers[command_type] = []
            self._handlers[command_type].append(handler)
            _log.debug(f"Registered handler for {command_type.__name__}")

    def unregister_handler(
        self, command_type: Type[Event], handler: CommandHandler
    ) -> None:
        """Unregister a handler for a command type.

        Args:
            command_type: The command class
            handler: The handler function to remove
        """
        with self._lock:
            if command_type in self._handlers:
                try:
                    self._handlers[command_type].remove(handler)
                except ValueError:
                    pass  # Handler not found

    def start(self) -> None:
        """Start the backend actor thread."""
        with self._lock:
            if self._running:
                _log.warning("BackendActor already running")
                return

            self._running = True
            self._worker_pool = ThreadPoolExecutor(
                max_workers=self._worker_pool_size,
                thread_name_prefix="BackendWorker",
            )
            self._thread = threading.Thread(
                target=self._run_loop,
                name="BackendActor",
                daemon=True,
            )
            self._thread.start()
            _log.info("BackendActor started")

    def stop(self, timeout_s: float = 2.0) -> None:
        """Stop the backend actor thread gracefully.

        Args:
            timeout_s: Timeout for thread join
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        # Wait for thread to finish
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            if self._thread.is_alive():
                _log.warning("BackendActor thread did not stop in time")
            self._thread = None

        # Shutdown worker pool
        if self._worker_pool is not None:
            self._worker_pool.shutdown(wait=True, cancel_futures=True)
            self._worker_pool = None

        _log.info("BackendActor stopped")

    @property
    def is_running(self) -> bool:
        """Check if the actor is running."""
        return self._running

    def enqueue(self, command: Event, priority: int = Priority.NORMAL) -> None:
        """Enqueue a command for processing.

        Args:
            command: The command event to process
            priority: Command priority (default NORMAL)
        """
        # Auto-start if not already running to avoid dropping commands during startup
        if not self._running:
            _log.info("BackendActor not running; auto-starting to process command")
            self.start()
        self._command_queue.put(command, priority)

    def submit_work(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Submit compute/IO work to the worker pool.

        Use this for long-running operations that shouldn't block
        the command processing loop.

        Args:
            fn: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments
        """
        if self._worker_pool is None:
            _log.warning("Worker pool not available, running synchronously")
            fn(*args, **kwargs)
            return
        self._worker_pool.submit(fn, *args, **kwargs)

    def _run_loop(self) -> None:
        """Main processing loop - runs on backend thread."""
        # Set this thread as the backend thread for assertions
        set_backend_thread(threading.current_thread())
        _log.debug("BackendActor loop started")

        try:
            while self._running:
                try:
                    envelope = self._command_queue.get(timeout=0.1)
                    self._dispatch_command(envelope.command)
                except queue.Empty:
                    continue
                except Exception as e:
                    _log.exception(f"Error in BackendActor loop: {e}")
        finally:
            clear_backend_thread()
            _log.debug("BackendActor loop exited")

    def _dispatch_command(self, command: Event) -> None:
        """Dispatch a command to registered handlers.

        Args:
            command: The command to dispatch
        """
        command_type = type(command)
        handlers = self._handlers.get(command_type, [])

        if not handlers:
            _log.debug(f"No handlers for {command_type.__name__}")
            return

        for handler in handlers:
            try:
                handler(command)
            except Exception as e:
                _log.exception(
                    f"Error in handler for {command_type.__name__}: {e}"
                )

    def drain(self, timeout_s: float = 1.0) -> int:
        """Process all pending commands synchronously.

        Useful for testing. Processes commands until queue is empty
        or timeout expires.

        Args:
            timeout_s: Maximum time to spend draining

        Returns:
            Number of commands processed
        """
        count = 0
        deadline = time.time() + timeout_s
        # Provide backend thread context so assertion helpers work in drain-mode tests
        backend_thread = threading.current_thread()
        set_backend_thread(backend_thread)

        try:
            while time.time() < deadline:
                try:
                    envelope = self._command_queue.get(timeout=0.01)
                    self._dispatch_command(envelope.command)
                    count += 1
                except queue.Empty:
                    break
        finally:
            clear_backend_thread()

        return count
