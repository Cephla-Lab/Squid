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
