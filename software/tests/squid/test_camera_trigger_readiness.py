"""get_ready_for_trigger() stands down while a settings change holds the camera's trigger lock.

The live trigger timer and the acquisition worker call it from their own threads; a driver's
check that talked to the SDK mid-reconfiguration raised inside the SDK (seen on a Toupcam while
changing binning during live view).
"""

import threading
import time

import pytest

import squid.logging
import control.camera_toupcam as camera_toupcam
from tests.tools import get_test_camera


def _hold_pause_streaming(camera, release: threading.Event, paused: threading.Event):
    with camera._pause_streaming():
        paused.set()
        release.wait(timeout=5)


def test_not_ready_while_a_settings_change_pauses_streaming_on_another_thread():
    camera = get_test_camera()
    camera.start_streaming()
    assert camera.get_ready_for_trigger()

    release, paused = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold_pause_streaming, args=(camera, release, paused), daemon=True)
    holder.start()
    assert paused.wait(timeout=5)
    try:
        assert not camera.get_ready_for_trigger()
    finally:
        release.set()
        holder.join(timeout=5)

    assert camera.get_ready_for_trigger()
    camera.stop_streaming()


def test_ready_check_is_reentrant_from_the_trigger_path():
    """send_trigger holds the same lock; drivers call the readiness check from inside it."""
    camera = get_test_camera()
    camera.start_streaming()
    with camera._trigger_lock:
        assert camera.get_ready_for_trigger()
    camera.stop_streaming()


# ─── Toupcam: readiness never touches the SDK ───────────────────────────────


def _toupcam(trigger_sent: bool, trigger_age_s: float):
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._camera = None  # any SDK call would fail loudly
    cam._trigger_lock = threading.RLock()
    cam._exposure_time = 20.0
    cam._trigger_sent = trigger_sent
    cam._last_trigger_timestamp = time.time() - trigger_age_s
    cam._log = squid.logging.get_logger("test_toupcam_readiness")
    return cam


@pytest.mark.parametrize(
    "trigger_sent, trigger_age_s, ready",
    [
        (False, 0.0, True),
        (True, 0.1, False),  # previous trigger still in flight
        (True, 60.0, True),  # in flight far longer than any exposure: assumed lost
    ],
)
def test_toupcam_readiness_uses_only_stored_state(trigger_sent, trigger_age_s, ready):
    cam = _toupcam(trigger_sent, trigger_age_s)

    assert cam.get_ready_for_trigger() is ready


def test_toupcam_stop_streaming_forgets_the_in_flight_trigger():
    cam = _toupcam(trigger_sent=True, trigger_age_s=0.1)

    class _SDK:
        def Stop(self):
            pass

    cam._camera = _SDK()
    cam.stop_streaming()

    assert cam.get_ready_for_trigger()
