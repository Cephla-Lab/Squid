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


@dataclass
class _TestEvent(Event):
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
    ui_bus.subscribe(_TestEvent, handler)

    ui_bus.publish(_TestEvent(value=42))
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
    ui_bus.subscribe(_TestEvent, handler)

    main_thread = QThread.currentThread()

    # Publish from worker thread
    def worker():
        core_bus.publish(_TestEvent(value=99))

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
    core_bus.subscribe(_TestEvent, handler)

    main_thread = threading.current_thread()

    # Publish from worker thread
    worker_thread_ref = [None]
    def worker():
        worker_thread_ref[0] = threading.current_thread()
        core_bus.publish(_TestEvent(value=99))

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    handler.assert_called_once()
    assert handler_threads[0] is worker_thread_ref[0]  # Handler ran in worker thread!
    assert handler_threads[0] is not main_thread


def test_unsubscribe(ui_bus, qtbot):
    """Unsubscribed handlers don't receive events."""
    handler = MagicMock()
    ui_bus.subscribe(_TestEvent, handler)
    ui_bus.unsubscribe(_TestEvent, handler)

    ui_bus.publish(_TestEvent(value=42))
    qtbot.wait(50)

    handler.assert_not_called()


def test_multiple_handlers(ui_bus, qtbot):
    """Multiple handlers all receive events on main thread."""
    handler1 = MagicMock()
    handler2 = MagicMock()

    ui_bus.subscribe(_TestEvent, handler1)
    ui_bus.subscribe(_TestEvent, handler2)

    ui_bus.publish(_TestEvent(value=42))
    qtbot.wait(50)

    handler1.assert_called_once()
    handler2.assert_called_once()


def test_handler_exception_isolated(ui_bus, qtbot, caplog):
    """One handler's exception doesn't affect others."""
    def bad_handler(e):
        raise ValueError("boom")

    good_handler = MagicMock()

    ui_bus.subscribe(_TestEvent, bad_handler)
    ui_bus.subscribe(_TestEvent, good_handler)

    ui_bus.publish(_TestEvent(value=42))
    qtbot.wait(100)

    good_handler.assert_called_once()
    assert "boom" in caplog.text
