import ctypes

import numpy as np
import pytest

# The TUCam SDK shared library is loaded at import time (control/TUCam.py).
# Skip the whole module where it is not installed (e.g. CI without the SDK).
try:
    from control.TUCam import TUCAM_RAWIMG_HEADER
    from control.camera_tucsen import TucsenCamera
    from squid.abc import CameraError
except OSError as e:  # libTUCam.so.1 / TUCam.dll not present
    pytest.skip(f"TUCam SDK not available: {e}", allow_module_level=True)


def _make_header(arr: np.ndarray):
    """Build a TUCAM_RAWIMG_HEADER pointing at a real ctypes buffer holding `arr`.

    Returns (header, backing_buffer). The caller MUST keep backing_buffer alive
    for as long as header.pImgData is used — pImgData is a raw pointer into it.
    """
    raw = arr.astype(np.uint16).tobytes()
    backing = ctypes.create_string_buffer(raw, len(raw))
    header = TUCAM_RAWIMG_HEADER()
    header.usWidth = arr.shape[1]
    header.usHeight = arr.shape[0]
    header.uiImgSize = len(raw)
    header.pImgData = ctypes.cast(backing, ctypes.c_void_p)
    return header, backing


def test_convert_header_to_numpy_roundtrips_pixels():
    arr = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    header, _backing = _make_header(arr)

    out = TucsenCamera._convert_header_to_numpy(header)

    assert out.shape == (2, 3)
    assert out.dtype == np.uint16
    np.testing.assert_array_equal(out, arr)


def test_convert_header_to_numpy_copies_data():
    # The returned array must own its data (copied from device memory), so it
    # stays valid after the source buffer is dropped — it must not alias pImgData.
    arr = np.array([[7, 8], [9, 10]], dtype=np.uint16)
    header, backing = _make_header(arr)

    out = TucsenCamera._convert_header_to_numpy(header)

    # Drop the source buffer and mutate its bytes; `out` must be unaffected.
    ctypes.memset(backing, 0, len(backing))
    del backing
    np.testing.assert_array_equal(out, [[7, 8], [9, 10]])


def test_convert_header_to_numpy_rejects_short_buffer():
    arr = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    header, _backing = _make_header(arr)
    header.uiImgSize = 4  # smaller than usWidth*usHeight*2 == 12

    with pytest.raises(CameraError):
        TucsenCamera._convert_header_to_numpy(header)


def test_convert_header_to_numpy_rejects_padding():
    arr = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    header, _backing = _make_header(arr)
    header.usXPadding = 1  # per-row padding would corrupt a flat reshape

    with pytest.raises(CameraError):
        TucsenCamera._convert_header_to_numpy(header)


def test_close_waits_for_a_trigger_in_progress(monkeypatch):
    """A trigger still in send_trigger() (e.g. a confocal scan being aborted, the camera re-armed and the scan
    retried) must finish before the SDK is torn down, or it calls into a closed handle."""
    import threading
    import types

    import control.camera_tucsen as tucsen
    import squid.logging

    events = []
    monkeypatch.setattr(
        tucsen, "TUCAM_Dev_Close", lambda handle: events.append("Dev_Close") or tucsen.TUCAMRET.TUCAMRET_SUCCESS
    )
    monkeypatch.setattr(tucsen, "TUCAM_Api_Uninit", lambda: events.append("Api_Uninit"))
    camera = types.SimpleNamespace(
        _trigger_lock=threading.RLock(),
        _camera=None,
        temperature_reading_thread=None,
        stop_streaming=lambda: events.append("stop_streaming"),
        _log=squid.logging.get_logger("test"),
    )
    in_trigger = threading.Event()

    def trigger():
        with camera._trigger_lock:  # as send_trigger() holds it
            in_trigger.set()
            threading.Event().wait(0.3)
            events.append("trigger finished")

    sender = threading.Thread(target=trigger)
    sender.start()
    assert in_trigger.wait(5)
    TucsenCamera.close(camera)
    sender.join(5)

    assert events == ["trigger finished", "stop_streaming", "Dev_Close", "Api_Uninit"]


class _FakeGenicamCamera:
    """Just enough of an Aries (GenICam) TucsenCamera for the region, readout-time and link-mode code."""

    def __init__(self, params):
        import contextlib
        import threading

        import control.camera_tucsen as tucsen
        import squid.logging

        self.params = dict(params)
        self.writes = []
        self._model_properties = TucsenCamera._get_model_properties(tucsen.TucsenCameraModel.ARIES_6510)
        self._camera_mode = tucsen.ModeAries.HDR
        self._binning = (1, 1)
        self._camera = None
        self._log = squid.logging.get_logger("test")
        self._pause_streaming = contextlib.nullcontext
        self._update_internal_settings = lambda: TucsenCamera._calculate_strobe_delay(self)
        self.acquisition_mode = tucsen.CameraAcquisitionMode.SOFTWARE_TRIGGER
        self.strobe_delays_ms = []
        self._hw_set_strobe_delay_ms_fn = self.strobe_delays_ms.append
        self._exposure_time_ms = 10.0
        self._trigger_sent = threading.Event()

    def _get_genicam_parameter(self, name):
        if name not in self.params:
            raise CameraError(f"Failed to get GenICam parameter attributes for '{name}'")
        return {"name": name, "value": self.params[name]}

    def _set_genicam_parameter(self, name, value, param_type):
        self.writes.append((name, value))
        self.params[name] = value
        if name == "BinningSelector":  # the Aries bins after readout; Height is in binned rows
            self.params["Height"] = 3200 >> value
        return True

    def _raw_set_binning_genicam(self, binning_value):
        return TucsenCamera._raw_set_binning_genicam(self, binning_value)

    def get_region_of_interest(self):
        return TucsenCamera.get_region_of_interest(self)

    def get_acquisition_mode(self):
        return self.acquisition_mode

    def get_strobe_time(self):
        return TucsenCamera.get_strobe_time(self)

    def set_exposure_time(self, exposure_time_ms):
        return TucsenCamera.set_exposure_time(self, exposure_time_ms)


_FULL_FRAME = {"OffsetX": 0, "OffsetY": 0, "Width": 3200, "Height": 3200, "TriggerInputDelay": 0}


def test_aries_region_is_set_through_width_height_and_offsets():
    """The MultiROI* nodes are read-only on the Aries, so writing them left the full frame in place."""
    camera = _FakeGenicamCamera(_FULL_FRAME)

    TucsenCamera.set_region_of_interest(camera, 0, 1200, 3200, 800)

    assert TucsenCamera.get_region_of_interest(camera) == (0, 1200, 3200, 800)
    assert not any(name.startswith("MultiROI") for name, _ in camera.writes)
    # Offsets are cleared before the size changes, so a larger region never runs past the sensor edge.
    assert camera.writes[:2] == [("OffsetX", 0), ("OffsetY", 0)]


def test_aries_region_change_reapplies_exposure_and_strobe_delay_in_hardware_trigger():
    """The camera exposure (exposure + readout) and the MCU strobe delay are derived from the readout time, which
    follows the region height: a region change must push both, or illumination can start before every row is
    exposing."""
    import control.camera_tucsen as tucsen

    camera = _FakeGenicamCamera({**_FULL_FRAME, "RollingSpeed": 3.75})
    camera.acquisition_mode = tucsen.CameraAcquisitionMode.HARDWARE_TRIGGER
    TucsenCamera.set_region_of_interest(camera, 0, 1200, 3200, 800)
    assert camera.strobe_delays_ms[-1] == pytest.approx(3.0)
    assert camera.params["ExposureTime"] == 13000  # 10 ms + 3 ms readout, in us

    TucsenCamera.set_region_of_interest(camera, 0, 0, 3200, 3200)

    assert camera.strobe_delays_ms[-1] == pytest.approx(12.0)
    assert camera.params["ExposureTime"] == 22000  # 10 ms + 12 ms readout
    assert camera._exposure_time_ms == 10.0  # the user's exposure is unchanged


@pytest.mark.parametrize("hardware_trigger", [True, False])
def test_binning_change_uses_the_new_binning_and_reapplies_exposure(hardware_trigger):
    """The readout time was computed before self._binning was updated (48 ms instead of 12 going 4x4 -> 1x1 on an
    Aries), and a binning change never pushed the camera exposure or the MCU strobe delay."""
    import control.camera_tucsen as tucsen

    camera = _FakeGenicamCamera({**_FULL_FRAME, "RollingSpeed": 3.75})
    if hardware_trigger:
        camera.acquisition_mode = tucsen.CameraAcquisitionMode.HARDWARE_TRIGGER

    for binning in ((4, 4), (1, 1), (2, 2)):
        TucsenCamera.set_binning(camera, *binning)

        assert camera._binning == binning
        # Binning happens after readout: all 3200 rows are still read, 12 ms at 3.75 us per row.
        assert camera._strobe_delay_ms == pytest.approx(12.0)
        if hardware_trigger:
            assert camera.strobe_delays_ms[-1] == pytest.approx(12.0)
            assert camera.params["ExposureTime"] == 22000  # 10 ms + 12 ms readout, in us
        else:
            assert camera.strobe_delays_ms == []
            assert camera.params["ExposureTime"] == 10000


def test_binning_change_reapplies_exposure_before_streaming_resumes():
    """Streaming (and with it live triggers) must not resume with the new binning but the previous exposure and MCU
    strobe delay: both are pushed inside the same pause as the binning change."""
    import contextlib

    import control.camera_tucsen as tucsen

    camera = _FakeGenicamCamera({**_FULL_FRAME, "RollingSpeed": 3.75})
    camera.acquisition_mode = tucsen.CameraAcquisitionMode.HARDWARE_TRIGGER
    events = []
    depth = [0]

    @contextlib.contextmanager
    def pause_streaming():  # stops and restarts streaming only at the outermost level, like AbstractCamera's
        depth[0] += 1
        if depth[0] == 1:
            events.append("stop streaming")
        try:
            yield
        finally:
            depth[0] -= 1
            if depth[0] == 0:
                events.append("start streaming")

    camera._pause_streaming = pause_streaming
    set_genicam = camera._set_genicam_parameter
    camera._set_genicam_parameter = lambda name, value, t: (events.append(name), set_genicam(name, value, t))[1]
    camera._hw_set_strobe_delay_ms_fn = lambda ms: events.append("MCU strobe delay")

    TucsenCamera.set_binning(camera, 2, 2)

    assert events[0] == "stop streaming" and events[-1] == "start streaming"
    assert events.count("stop streaming") == 1 and events.count("start streaming") == 1
    assert events.index("BinningSelector") < events.index("ExposureTime") < events.index("start streaming")
    assert events.index("MCU strobe delay") < events.index("start streaming")


def test_failed_binning_change_keeps_the_previous_binning():
    camera = _FakeGenicamCamera({**_FULL_FRAME, "RollingSpeed": 3.75})

    def fail(binning_value):
        raise CameraError("Cannot set camera binning.")

    camera._raw_set_binning_genicam = fail
    with pytest.raises(CameraError):
        TucsenCamera.set_binning(camera, 2, 2)

    assert camera._binning == (1, 1)


def test_aries_readout_time_comes_from_the_camera_row_time():
    camera = _FakeGenicamCamera({**_FULL_FRAME, "RollingSpeed": 3.75})

    TucsenCamera._calculate_strobe_delay(camera)

    assert camera._strobe_delay_ms == pytest.approx(12.0)  # 3200 rows x 3.75 us


def test_aries_readout_time_follows_the_region_height():
    camera = _FakeGenicamCamera({**_FULL_FRAME, "Height": 800, "RollingSpeed": 3.75})

    TucsenCamera._calculate_strobe_delay(camera)

    assert camera._strobe_delay_ms == pytest.approx(3.0)


def test_aries_readout_time_falls_back_to_the_datasheet_row_time():
    camera = _FakeGenicamCamera(_FULL_FRAME)  # no RollingSpeed node

    TucsenCamera._calculate_strobe_delay(camera)

    assert camera._strobe_delay_ms == pytest.approx(12.0)  # HDR: 83 fps over 3200 rows


@pytest.mark.parametrize("mode, level", [("SingleGigE", "WARNING"), ("DoubleGigE", "INFO")])
def test_aries_link_mode_is_logged(caplog, mode, level):
    import logging

    camera = _FakeGenicamCamera({"GigEMode": mode})
    camera._log.propagate = True

    with caplog.at_level(logging.INFO):
        TucsenCamera._log_link_mode(camera)

    assert [r.levelname for r in caplog.records if "GigEMode" in r.getMessage()] == [level]
