import time

import squid.config
import squid.camera.utils
from squid.abc import CameraAcquisitionMode


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
