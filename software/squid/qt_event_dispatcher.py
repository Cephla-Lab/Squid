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
