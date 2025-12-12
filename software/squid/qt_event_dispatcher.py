"""Qt event dispatcher for main-thread execution.

This module provides QtEventDispatcher, a QObject that marshals arbitrary
callables to the Qt main thread via signals/slots.
"""
import threading
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
        self._qt_main_thread = QThread.currentThread()
        # Also store Python's main thread for reliable cross-thread detection
        # QThread.currentThread() can return the main thread for non-Qt threads
        self._python_main_thread = threading.main_thread()
        _log.debug(f"QtEventDispatcher created in thread {self._qt_main_thread}")

    @Slot(object, object)
    def _on_dispatch(self, handler: Callable, event: Any) -> None:
        """Execute handler(event) in the main thread."""
        _log.debug(f"QtEventDispatcher: executing {handler.__name__ if hasattr(handler, '__name__') else handler} for {type(event).__name__}")
        try:
            handler(event)
        except Exception as e:
            _log.exception(f"Handler {handler} raised exception for {event}: {e}")
        _log.debug(f"QtEventDispatcher: completed {type(event).__name__}")

    def is_main_thread(self) -> bool:
        """Return True if called from the Qt main thread.

        Uses both Python's threading module and Qt's QThread to reliably detect
        if we're on the main thread. This handles the case where QThread.currentThread()
        returns the main thread for non-Qt Python threads (threading.Thread), which
        would otherwise cause handlers to be called on the wrong thread.
        """
        # Check Python threading first - this is reliable for Python threads
        if threading.current_thread() is not self._python_main_thread:
            return False
        # Also verify Qt agrees (handles QThread workers)
        return QThread.currentThread() is self._qt_main_thread
