import time

import pytest

import squid.config
import squid.camera.utils
from squid.abc import CameraAcquisitionMode


def test_clamp_precise_framerate_tenths():
    from control.camera_toupcam import clamp_precise_framerate_tenths

    # fps -> tenths, clamped to [min,max] in tenths
    assert clamp_precise_framerate_tenths(11.5, min_tenths=10, max_tenths=600) == 115
    assert clamp_precise_framerate_tenths(0.1, min_tenths=10, max_tenths=600) == 10  # below min
    assert clamp_precise_framerate_tenths(999.0, min_tenths=10, max_tenths=600) == 600  # above max


def _sim_camera():
    cfg = squid.config.get_camera_config().model_copy(update={"rotate_image_angle": None, "flip": None})
    return squid.camera.utils.get_camera(cfg, simulated=True)


def test_set_frame_rate_returns_achievable_and_is_clamped():
    cam = _sim_camera()
    cam.set_exposure_time(10)  # 10 ms -> max ~100 fps (exposure-limited)
    # Requesting more than achievable returns the achievable max.
    achievable = cam.set_frame_rate(10_000.0)
    assert achievable <= 1000.0 / cam.get_total_frame_time() + 1e-6
    assert achievable > 0


def test_simulated_camera_honors_frame_rate():
    cam = _sim_camera()
    cam.set_exposure_time(1)  # 1 ms exposure -> would free-run very fast
    cam.set_frame_rate(20.0)  # but cap to 20 fps
    cam.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
    received = []
    cam.add_frame_callback(lambda f: received.append(f.timestamp))
    cam.start_streaming()
    time.sleep(1.0)
    cam.stop_streaming()
    # ~20 fps over ~1s -> well under 40, comfortably over 5 (loose bounds for CI).
    assert 5 <= len(received) <= 40


def _bare_toupcam(strobe_time_us, exposure_ms):
    """A ToupcamCamera with just the attributes set_frame_rate/_continuous_max_framerate touch.

    Built without __init__ (no hardware) so the readout-vs-exposure rate math can be
    tested against the exact values the real ITR3CMOS26000KMA produces (strobe_time_us=35666).
    """
    from control.camera_toupcam import ToupcamCamera, StrobeInfo

    cam = object.__new__(ToupcamCamera)
    cam._log = squid.logging.get_logger("test_toupcam_fps")
    cam._strobe_info = StrobeInfo(strobe_time_us=float(strobe_time_us), trigger_delay_us=15666.0)
    cam._exposure_time = float(exposure_ms)
    cam._current_mode_key = lambda: (3104, 2084, 2)  # 2x2 binning, 16-bit; no SDK behind a bare instance
    cam._raw_camera_stream_started = False  # stopped unless a test says otherwise
    return cam


def test_continuous_max_framerate_is_readout_limited_not_triggered_frame_time():
    # ITR3CMOS26000KMA: readout period 35.666 ms => ~28 fps free-run, exposure (20ms) < readout.
    # The BUG returned 1000/get_total_frame_time() = 1000/(20 + (35666+15666)/1000) = ~14 fps.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)
    fps = cam._continuous_max_framerate()
    assert 27.5 < fps < 28.5, fps  # readout-limited ~28, NOT the halved ~14


def test_continuous_max_framerate_is_exposure_limited_for_long_exposure():
    # Exposure (100 ms) longer than readout (35.666 ms) => continuous rate is exposure-limited.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=100.0)
    fps = cam._continuous_max_framerate()
    assert 9.5 < fps < 10.5, fps  # 1000/100


class _FakeToupcamSdk:
    """Just enough of the toupcam SDK object for set_frame_rate.

    ``readable=False`` mimics a stopped pull-mode stream: on the ITR3CMOS26000KMA the
    MIN/MAX_PRECISE_FRAMERATE *reads* fail with E_UNEXPECTED while the camera is stopped,
    but the PRECISE_FRAMERATE *write* is accepted and takes effect on the next Start.
    """

    E_UNEXPECTED = -2147418113

    def __init__(self, min_tenths=19, max_tenths=280, readable=True, writable=True):
        import control.toupcam as toupcam

        self._toupcam = toupcam
        self.min_tenths, self.max_tenths = min_tenths, max_tenths
        self.readable, self.writable = readable, writable
        self.puts = []

    def get_Option(self, opt):
        if not self.readable:
            raise self._toupcam.HRESULTException(self.E_UNEXPECTED)
        if opt == self._toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE:
            return self.max_tenths
        if opt == self._toupcam.TOUPCAM_OPTION_MIN_PRECISE_FRAMERATE:
            return self.min_tenths
        raise AssertionError(f"unexpected get_Option({opt})")

    def put_Option(self, opt, value):
        if not self.writable:
            raise self._toupcam.HRESULTException(self.E_UNEXPECTED)
        assert opt == self._toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE
        self.puts.append(value)


def test_set_frame_rate_without_cached_range_bounds_by_readout_and_still_writes():
    # Range unreadable (stream stopped) and nothing cached (mode changed since the last
    # stream start): bound the request by the readout/exposure-limited continuous max
    # (~28 fps, NOT the ~14 fps of the triggered frame time), write that, and report it.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)
    cam._camera = _FakeToupcamSdk(readable=False)
    achievable = cam.set_frame_rate(30.0)
    assert 27.5 < achievable < 28.5, achievable
    assert cam._camera.puts == [280]
    # A request below the readout max is written as-is (the camera clamps to its own minimum).
    assert cam.set_frame_rate(10.0) == 10.0
    assert cam._camera.puts == [280, 100]


def test_set_frame_rate_range_unreadable_and_unwritable_falls_back_to_continuous_max():
    # A model with no PRECISE_FRAMERATE support at all: no write goes through, report the
    # free-run maximum so the caller downsamples in software.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)
    cam._camera = _FakeToupcamSdk(readable=False, writable=False)
    achievable = cam.set_frame_rate(10.0)
    assert 27.5 < achievable < 28.5, achievable


def test_mode_change_invalidates_cached_range():
    # The cached (min, max) belongs to one (resolution, bit depth); a mode change must drop it
    # so a stopped-stream set_frame_rate() can't clamp the new mode to the old maximum
    # (28 fps at 2x2 16-bit vs 63 fps at 3x3 on the ITR3CMOS26000KMA).
    from control.camera_toupcam import ToupcamCamera

    cam = _bare_toupcam(strobe_time_us=15821.0, exposure_ms=2.0)  # 3x3 readout: ~63 fps
    cam._camera = _FakeToupcamSdk(min_tenths=19, max_tenths=280, readable=True)
    cam._refresh_precise_framerate_range()  # cached (19, 280) for the 2x2 mode key
    cam._camera.readable = False  # stream stopped
    cam._current_mode_key = lambda: (2064, 1386, 2)  # switched to 3x3
    # What _update_internal_settings does on a mode change:
    if cam._current_mode_key() != cam._precise_framerate_mode_key:
        cam._precise_framerate_range_tenths = None
    assert cam.set_frame_rate(1000.0) == pytest.approx(1000.0 / 15.821, abs=0.1)
    assert cam._camera.puts[-1] == 632


def test_set_frame_rate_applies_hint_while_streaming():
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)
    cam._camera = _FakeToupcamSdk()
    assert cam.set_frame_rate(10.0) == 10.0
    assert cam._camera.puts == [100]
    assert cam._precise_framerate_range_tenths == (19, 280)


def test_set_frame_rate_uses_cached_range_while_stopped():
    # The bug seen on hardware: record() calls set_frame_rate right after the frame-shape probe
    # stopped the stream, the range read raised E_UNEXPECTED, and the hint was silently dropped
    # (camera then free-ran at 28 fps).  With the range cached from the last read the write
    # must still go through and the returned rate must be the requested one.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)
    cam._camera = _FakeToupcamSdk(readable=True)
    cam._refresh_precise_framerate_range()  # populated while "streaming"
    cam._camera.readable = False  # stream stopped
    assert cam.set_frame_rate(10.0) == 10.0
    assert cam._camera.puts == [100]


def test_set_frame_rate_clamps_to_range():
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=1.0)
    cam._camera = _FakeToupcamSdk(min_tenths=19, max_tenths=280)
    assert cam.set_frame_rate(1.0) == 1.9  # below MIN -> MIN
    assert cam.set_frame_rate(100.0) == 28.0  # above MAX -> MAX (== readout-limited max here)
    assert cam._camera.puts == [19, 280]


def test_set_frame_rate_is_bounded_by_exposure():
    # MAX_PRECISE_FRAMERATE ignores exposure: at 100 ms exposure the camera still reports 28 fps
    # max but delivers 10 fps.  The returned rate sizes the recording, so it must be 10, not 28.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=100.0)
    cam._camera = _FakeToupcamSdk()
    assert cam.set_frame_rate(30.0) == 10.0
    assert cam._camera.puts == [280]


def test_set_frame_rate_write_failure_falls_back_to_continuous_max():
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)
    cam._camera = _FakeToupcamSdk(writable=False)
    achievable = cam.set_frame_rate(10.0)
    assert 27.5 < achievable < 28.5, achievable


def test_refresh_range_never_raises_and_only_caches_in_continuous_mode():
    # V4: a failing ROI / pixel-format lookup inside the refresh must not escape into
    # _start_raw_camera_stream (it would desync the streaming flag from the SDK stream).
    from squid.abc import CameraAcquisitionMode

    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=2.0)
    cam._camera = _FakeToupcamSdk()

    def boom():
        raise RuntimeError("get_Roi failed")

    cam._current_mode_key = boom
    assert cam._refresh_precise_framerate_range() is None  # swallowed, nothing cached
    cam._current_mode_key = lambda: (3104, 2084, 2)

    # B4: the post-start refresh runs only for a CONTINUOUS (free-run) stream; a
    # trigger-mode stream (z-stack phase) must not overwrite the free-run range.
    class _StartingSdk(_FakeToupcamSdk):
        def StartPullModeWithCallback(self, cb, ctx):
            pass

    cam._camera = _StartingSdk(min_tenths=48, max_tenths=1085)
    cam._raw_camera_stream_started = False
    cam.get_acquisition_mode = lambda: CameraAcquisitionMode.SOFTWARE_TRIGGER
    cam._start_raw_camera_stream()
    assert cam._raw_camera_stream_started is True
    assert cam._precise_framerate_range_tenths is None
    cam.get_acquisition_mode = lambda: CameraAcquisitionMode.CONTINUOUS
    cam._start_raw_camera_stream()
    assert cam._precise_framerate_range_tenths == (48, 1085)


def test_set_frame_rate_skips_live_write_when_value_already_set_and_records_live_writes():
    # While streaming, a write corrupts the frame in flight (hardware ROI), so the driver reads
    # PRECISE_FRAMERATE back first and only writes when the camera does not already have the
    # value; a live write that does happen is timestamped for ContinuousFrameSource.
    import time as _time

    class _Sdk(_FakeToupcamSdk):
        def __init__(self, current):
            super().__init__()
            self.current = current

        def get_Option(self, opt):
            import control.toupcam as toupcam

            if opt == toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE:
                return self.current
            return super().get_Option(opt)

        def put_Option(self, opt, value):
            super().put_Option(opt, value)
            self.current = value

    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=2.0)
    cam._camera = _Sdk(current=100)
    cam._raw_camera_stream_started = True
    assert cam.set_frame_rate(10.0) == 10.0
    assert cam._camera.puts == []  # already at 100 tenths: no live write
    assert cam.frame_rate_hint_live_write_ts is None
    t0 = _time.time()
    assert cam.set_frame_rate(20.0) == 20.0
    assert cam._camera.puts == [200]
    assert cam.frame_rate_hint_live_write_ts is not None and cam.frame_rate_hint_live_write_ts >= t0
    # Stopped: always write, never timestamp.
    cam._raw_camera_stream_started = False
    ts_before = cam.frame_rate_hint_live_write_ts
    assert cam.set_frame_rate(15.0) == 15.0
    assert cam._camera.puts == [200, 150]
    assert cam.frame_rate_hint_live_write_ts == ts_before


def test_get_max_frame_rate_is_a_pure_readout_and_exposure_query():
    # 2x2 16-bit readout 35.666 ms: 28 fps at short exposures, exposure-limited beyond.
    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=2.0)
    cam._camera = _FakeToupcamSdk()
    assert 27.5 < cam.get_max_frame_rate() < 28.5
    assert cam.get_max_frame_rate(100.0) == pytest.approx(10.0)
    assert cam.get_max_frame_rate(2.0) == pytest.approx(cam.get_max_frame_rate())
    assert cam._camera.puts == []  # never writes
    # Bounded by the cached SDK maximum for the current mode when it is lower.
    cam._precise_framerate_range_tenths = (19, 250)
    cam._precise_framerate_mode_key = cam._current_mode_key()
    assert cam.get_max_frame_rate(2.0) == pytest.approx(25.0)
    # A cache for another mode is ignored.
    cam._precise_framerate_mode_key = (1, 1, 1)
    assert 27.5 < cam.get_max_frame_rate(2.0) < 28.5


def test_simulated_camera_max_frame_rate_matches_its_set_frame_rate_clamp():
    cam = _sim_camera()
    cam.set_exposure_time(10)
    assert cam.get_max_frame_rate() == pytest.approx(cam.set_frame_rate(10_000.0))
    assert cam.get_max_frame_rate(97.0) == pytest.approx(1000.0 / (97.0 + cam.get_strobe_time()))
