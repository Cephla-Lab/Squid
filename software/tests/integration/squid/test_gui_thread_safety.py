"""Integration-style thread-safety checks for UIEventBus."""
import threading
import time

from qtpy.QtCore import QThread

from squid.core.events import (
    EventBus,
    StagePositionChanged,
    AcquisitionProgress,
)
from squid.ui.qt_event_dispatcher import QtEventDispatcher
from squid.ui.ui_event_bus import UIEventBus
from squid.mcs.services.movement_service import MovementService
from squid.core.events import AcquisitionProgress
from squid.core.abc import Pos, StageStage


def test_ui_event_bus_runs_handler_on_main_thread_from_worker(qtbot):
    core_bus = EventBus()
    dispatcher = QtEventDispatcher()
    ui_bus = UIEventBus(core_bus, dispatcher)

    handler_threads = []
    handler_hit = threading.Event()

    def handler(evt):
        handler_threads.append(QThread.currentThread())
        handler_hit.set()

    ui_bus.subscribe(StagePositionChanged, handler)

    def worker():
        core_bus.publish(StagePositionChanged(x_mm=1.0, y_mm=2.0, z_mm=3.0))

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    qtbot.waitUntil(handler_hit.is_set, timeout=1000)
    assert handler_threads and handler_threads[0] is QThread.currentThread()


class _FakeStage:
    def __init__(self):
        self._pos = Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None)
        self._busy = False

    def get_pos(self):
        return self._pos

    def get_state(self):
        return StageStage(busy=self._busy)


def test_movement_service_events_reach_ui_on_main_thread(qtbot):
    core_bus = EventBus()
    dispatcher = QtEventDispatcher()
    ui_bus = UIEventBus(core_bus, dispatcher)

    stage = _FakeStage()
    service = MovementService(stage, None, core_bus, poll_interval_ms=10)

    handler_threads = []
    handler_hit = threading.Event()

    def on_stage(evt):
        handler_threads.append(QThread.currentThread())
        service.stop()
        handler_hit.set()

    ui_bus.subscribe(StagePositionChanged, on_stage)

    try:
        service.start()
        qtbot.waitUntil(handler_hit.is_set, timeout=1500)
    finally:
        service.stop()

    assert handler_threads and handler_threads[0] is QThread.currentThread()


def test_multipoint_progress_dispatch_on_main_thread(qtbot):
    core_bus = EventBus()
    dispatcher = QtEventDispatcher()
    ui_bus = UIEventBus(core_bus, dispatcher)

    handler_threads = []
    handler_hit = threading.Event()

    def on_progress(evt):
        handler_threads.append(QThread.currentThread())
        handler_hit.set()

    ui_bus.subscribe(AcquisitionProgress, on_progress)

    def worker():
        core_bus.publish(
            AcquisitionProgress(
                current_fov=1,
                total_fovs=2,
                current_round=1,
                total_rounds=2,
                current_channel="",
                progress_percent=10.0,
                experiment_id="integration-exp",
            )
        )

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    qtbot.waitUntil(handler_hit.is_set, timeout=1000)
    assert handler_threads and handler_threads[0] is QThread.currentThread()
