"""Live shutdown must drain an in-flight trigger and reject canceled callbacks."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from control._def import TriggerMode
from control.core.live_controller import LiveController


class ManualTimer:
    """Run callbacks explicitly, including one already dispatched before cancel()."""

    def __init__(self, interval, function, args=()):
        self.interval = interval
        self.function = function
        self.args = args
        self.canceled = False

    def start(self):
        pass

    def is_alive(self):
        return not self.canceled

    def cancel(self):
        self.canceled = True

    def fire(self):
        self.function(*self.args)


@pytest.fixture(params=[TriggerMode.SOFTWARE, TriggerMode.HARDWARE])
def live(request, monkeypatch):
    monkeypatch.setattr("control.core.live_controller.threading.Timer", ManualTimer)
    microcontroller = Mock()
    microcontroller.is_busy.return_value = False
    microscope = SimpleNamespace(
        low_level_drivers=SimpleNamespace(microcontroller=microcontroller),
        addons=SimpleNamespace(squid_laser_engine=None),
    )
    camera = Mock()
    camera.get_ready_for_trigger.return_value = True
    controller = LiveController(microscope, camera)
    controller.trigger_mode = request.param
    monkeypatch.setattr(controller, "turn_on_illumination", lambda: setattr(controller, "illumination_on", True))
    monkeypatch.setattr(controller, "turn_off_illumination", lambda: setattr(controller, "illumination_on", False))
    yield controller
    controller.stop_live()


def test_canceled_callback_cannot_trigger_after_stop(live):
    live.start_live()
    timer = live.timer_trigger

    live.stop_live()
    timer.fire()

    live.camera.send_trigger.assert_not_called()
    assert not live.illumination_on
    assert not live.is_live
    assert live.timer_trigger is None


def test_stop_waits_for_an_in_flight_callback_before_turning_off_illumination(live):
    entered_trigger = threading.Event()
    release_trigger = threading.Event()
    stop_attempted = threading.Event()
    stopped = threading.Event()
    errors = []

    class ObservedLock:
        """Signal when stop reaches the lock, without relying on a scheduling delay."""

        def __init__(self):
            self.lock = threading.RLock()

        def __enter__(self):
            if threading.current_thread().name == "stop-live":
                stop_attempted.set()
            self.lock.acquire()

        def __exit__(self, *exc):
            self.lock.release()

    live._live_lock = ObservedLock()

    def ready():
        entered_trigger.set()
        if not release_trigger.wait(5):
            raise TimeoutError("Test did not release the trigger")
        return True

    def run_callback():
        try:
            timer.fire()
        except BaseException as exc:
            errors.append(exc)

    def stop():
        try:
            live.stop_live()
        except BaseException as exc:
            errors.append(exc)
        finally:
            stopped.set()
            # Also wakes the test on the unfixed code, which has no lock.
            stop_attempted.set()

    live.camera.get_ready_for_trigger.side_effect = ready
    live.start_live()
    timer = live.timer_trigger
    callback = threading.Thread(target=run_callback, daemon=True)
    stopper = threading.Thread(target=stop, name="stop-live", daemon=True)
    callback.start()
    try:
        assert entered_trigger.wait(5)
        stopper.start()
        assert stop_attempted.wait(5)
        assert not stopped.is_set(), "stop_live returned while a trigger could still turn illumination on"
    finally:
        release_trigger.set()
        callback.join(5)
        if stopper.ident is not None:
            stopper.join(5)

    assert not callback.is_alive()
    assert not stopper.is_alive()
    assert not errors
    live.camera.send_trigger.assert_called_once()
    assert stopped.is_set()
    assert not live.is_live
    assert not live.illumination_on
    assert live.timer_trigger is None


@pytest.mark.parametrize("replace_timer", ["restart", "fps_change"])
def test_replaced_callback_cannot_trigger_or_replace_the_new_timer(live, replace_timer):
    live.start_live()
    old_timer = live.timer_trigger
    if replace_timer == "restart":
        live.stop_live()
        live.start_live()
    else:
        live.set_trigger_fps(20)
    new_timer = live.timer_trigger

    old_timer.fire()

    live.camera.send_trigger.assert_not_called()
    assert live.timer_trigger is new_timer
    assert not new_timer.canceled
    new_timer.fire()
    live.camera.send_trigger.assert_called_once()


@pytest.mark.parametrize(
    "camera_ready, micro_busy, interval", [(True, False, 1), (False, False, 0.01), (True, True, 1)]
)
def test_callback_preserves_trigger_and_retry_cadence(live, camera_ready, micro_busy, interval):
    live.camera.get_ready_for_trigger.return_value = camera_ready
    live.microscope.low_level_drivers.microcontroller.is_busy.return_value = micro_busy
    live.start_live()
    timer = live.timer_trigger

    timer.fire()

    assert live.camera.send_trigger.call_count == int(camera_ready and not micro_busy)
    assert live.timer_trigger is not timer
    assert live.timer_trigger.interval == pytest.approx(interval)


def test_direct_single_trigger_still_works_with_live_off(live):
    assert not live.is_live
    assert live.trigger_acquisition()
    live.camera.send_trigger.assert_called_once()
