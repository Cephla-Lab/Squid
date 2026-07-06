import time

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


def test_set_frame_rate_fallback_returns_continuous_max_when_option_unavailable():
    # On this camera get_Option(MAX_PRECISE_FRAMERATE) raises E_UNEXPECTED, so set_frame_rate
    # must fall back to the readout/exposure-limited continuous max (~28 fps), NOT ~14 fps.
    import control.toupcam as toupcam

    cam = _bare_toupcam(strobe_time_us=35666.0, exposure_ms=20.0)

    class _FailingOptionCam:
        def get_Option(self, opt):
            raise toupcam.HRESULTException(-2147418113)  # E_UNEXPECTED, as seen on the real device

    cam._camera = _FailingOptionCam()
    # Request 30 fps; camera can't reach it, so we get its true continuous max (~28), not ~14.
    achievable = cam.set_frame_rate(30.0)
    assert 27.5 < achievable < 28.5, achievable


# --- Frame-rate capability (settable vs not) ------------------------------------------


def test_simulated_camera_reports_frame_rate_settable():
    # The simulated (and ToupTek) cameras can command a rate; only cameras like Hamamatsu can't.
    assert _sim_camera().can_set_frame_rate() is True


def test_base_estimate_frame_rate_is_sequential():
    # Base default assumes sequential timing: 1000 / (exposure + strobe).
    cam = _sim_camera()
    cam.set_exposure_time(10)
    expected = 1000.0 / (10.0 + cam.get_strobe_time())
    assert abs(cam.estimate_frame_rate(10.0) - expected) < 1e-6


def test_hamamatsu_frame_rate_not_settable_and_overlap_estimate():
    # Hamamatsu's DCAM library isn't present off-instrument; this runs only where it imports.
    import pytest

    try:
        import control.camera_hamamatsu as ham
    except (ImportError, OSError):
        pytest.skip("Hamamatsu DCAM library not available on this platform")

    from types import SimpleNamespace

    from control.dcamapi4 import DCAM_IDPROP

    cam = object.__new__(ham.HamamatsuCamera)
    assert cam.can_set_frame_rate() is False

    # Per-property stub: INTERNAL_FRAMERATE (authoritative free-run rate) vs INTERNAL_LINEINTERVAL
    # (10 us/line, used by the formula). line_interval 10 us over 2000 rows -> 20 ms readout.
    def _prop(pid):
        if pid == DCAM_IDPROP.INTERNALFRAMERATE:
            return 42.0
        return 0.00001

    cam._camera = SimpleNamespace(prop_getvalue=_prop)
    cam._capabilities = SimpleNamespace(binning_to_resolution={(1, 1): (2000, 2000)})
    cam._exposure_time_ms = 5.0

    # estimate_frame_rate (widget preview at a hypothetical exposure) uses the overlap formula.
    assert abs(cam.estimate_frame_rate(5.0) - 50.0) < 1e-6  # readout(20) > exposure(5) -> 1000/20
    assert abs(cam.estimate_frame_rate(40.0) - 25.0) < 1e-6  # exposure(40) > readout(20) -> 1000/40

    # set_frame_rate reports the camera's authoritative INTERNAL_FRAMERATE, not the formula.
    assert abs(cam.set_frame_rate(999.0) - 42.0) < 1e-6

    # ...falling back to the formula estimate when the authoritative read fails.
    def _prop_fr_unreadable(pid):
        if pid == DCAM_IDPROP.INTERNALFRAMERATE:
            return False  # DCAM read failure sentinel
        return 0.00001

    cam._camera = SimpleNamespace(prop_getvalue=_prop_fr_unreadable)
    assert abs(cam.set_frame_rate(999.0) - 50.0) < 1e-6  # exposure 5ms < readout 20ms -> 1000/20
