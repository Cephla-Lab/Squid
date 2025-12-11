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


def test_is_main_thread_returns_true_on_main_thread(dispatcher):
    """is_main_thread returns True when called from main thread."""
    assert dispatcher.is_main_thread() is True


def test_is_main_thread_returns_false_on_worker_thread(dispatcher, qtbot):
    """is_main_thread returns False when called from worker thread."""
    result = None

    def worker():
        nonlocal result
        result = dispatcher.is_main_thread()

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert result is False


def test_multiple_dispatches(dispatcher, qtbot):
    """Multiple dispatches all execute in order."""
    results = []

    def handler(e):
        results.append(e)

    for i in range(5):
        dispatcher.dispatch.emit(handler, i)

    qtbot.wait(100)

    assert results == [0, 1, 2, 3, 4]
