"""Toupcam's readiness check must never call into the SDK: it runs on the live trigger thread
and used to race a settings change mid-reconfiguration (HRESULT E_UNEXPECTED on the bench).
The base-class lock gate itself is tested in tests/squid/test_camera.py."""

import threading
import time
from unittest.mock import MagicMock

import pytest

import squid.logging
import control.camera_toupcam as camera_toupcam


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
    assert _toupcam(trigger_sent, trigger_age_s).get_ready_for_trigger() is ready


def test_toupcam_stop_streaming_forgets_the_in_flight_trigger():
    cam = _toupcam(trigger_sent=True, trigger_age_s=0.1)
    cam._camera = MagicMock()

    cam.stop_streaming()

    assert cam.get_ready_for_trigger()
