import numpy as np

import squid.camera.utils
import squid.config
from squid.abc import CameraAcquisitionMode
from squid.config import CameraPixelFormat
from squid.camera import hot_pixel_capture as cap
from squid.camera import hot_pixels as hp


def _sim_camera():
    config = squid.config.get_camera_config().model_copy(
        update={"rotate_image_angle": None, "flip": None, "default_pixel_format": CameraPixelFormat.MONO12}
    )
    camera = squid.camera.utils.get_camera(config, simulated=True)
    camera.set_pixel_format(CameraPixelFormat.MONO12)
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    camera.start_streaming()
    return camera


def test_capture_dark_stack_shapes_and_count():
    camera = _sim_camera()
    try:
        stack = cap.capture_dark_stack(camera, exposure_ms=1.0, n_frames=5, warmup_frames=1)
    finally:
        camera.stop_streaming()
    assert stack is not None
    assert stack.n_frames == 5
    h, w = camera.get_resolution()[1], camera.get_resolution()[0]
    assert stack.mean.shape == stack.min_proj.shape == stack.max_proj.shape
    assert stack.mean.shape == (h, w)
    # min projection never exceeds max projection anywhere
    assert np.all(stack.min_proj <= stack.max_proj)


def test_capture_dark_stack_stops_when_requested():
    camera = _sim_camera()
    try:
        stack = cap.capture_dark_stack(camera, exposure_ms=1.0, n_frames=100, warmup_frames=0, should_stop=lambda: True)
    finally:
        camera.stop_streaming()
    assert stack is None


def test_settle_temperature_converges_on_simulated_camera():
    camera = _sim_camera()
    # Simulated get_temperature returns the setpoint immediately -> settles in stable_reads polls.
    settled, last = cap.settle_temperature(
        camera, target_c=-10.0, tolerance_c=1.0, stable_reads=3, sleep_fn=lambda s: None, now_fn=lambda: 0.0
    )
    camera.stop_streaming()
    assert settled is True
    assert last == -10.0


def test_settle_temperature_no_tec_returns_false():
    class NoTecCamera:
        def set_temperature(self, t):
            raise NotImplementedError("no TEC")

    settled, last = cap.settle_temperature(NoTecCamera(), target_c=-10.0, sleep_fn=lambda s: None)
    assert settled is False
    assert last is None


def test_settle_temperature_times_out():
    class StuckCamera:
        def set_temperature(self, t):
            pass

        def get_temperature(self):
            return 25.0  # never reaches target

    fake_time = {"t": 0.0}

    def now():
        return fake_time["t"]

    def sleep(s):
        fake_time["t"] += s

    settled, last = cap.settle_temperature(
        StuckCamera(), target_c=-10.0, timeout_s=10.0, poll_interval_s=2.0, sleep_fn=sleep, now_fn=now
    )
    assert settled is False
    assert last == 25.0


def test_run_sweep_ambient_produces_one_result_per_exposure():
    camera = _sim_camera()
    try:
        results = cap.run_sweep(
            camera,
            exposures_ms=[1.0, 2.0],
            temperatures_c=None,
            n_frames=3,
            thresholds=hp.DefectThresholds(),
            pixel_format=CameraPixelFormat.MONO12,
        )
    finally:
        camera.stop_streaming()
    assert len(results) == 2
    assert [r.exposure_ms for r in results] == [1.0, 2.0]
    assert all(r.temperature_c is None for r in results)


def test_run_sweep_temperature_grid():
    camera = _sim_camera()
    progress = []
    try:
        results = cap.run_sweep(
            camera,
            exposures_ms=[1.0, 2.0],
            temperatures_c=[-10.0, 0.0],
            n_frames=2,
            thresholds=hp.DefectThresholds(),
            pixel_format=CameraPixelFormat.MONO12,
            on_progress=lambda t, e: progress.append((t, e)),
            settle_kwargs={"sleep_fn": lambda s: None, "stable_reads": 1},
        )
    finally:
        camera.stop_streaming()
    assert len(results) == 4  # 2 temps x 2 exposures
    assert progress[0] == (-10.0, 1.0)


def test_run_sweep_stops_midway():
    camera = _sim_camera()
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1  # allow first condition, then stop

    try:
        results = cap.run_sweep(
            camera,
            exposures_ms=[1.0, 2.0, 3.0],
            temperatures_c=None,
            n_frames=2,
            thresholds=hp.DefectThresholds(),
            pixel_format=CameraPixelFormat.MONO12,
            should_stop=should_stop,
        )
    finally:
        camera.stop_streaming()
    assert len(results) < 3


def test_gui_module_imports_without_qapplication():
    # Importing the GUI module must not construct a QApplication or open a window.
    import importlib

    from PyQt5.QtWidgets import QApplication

    mod = importlib.import_module("tools.hot_pixel_test_gui")
    assert QApplication.instance() is None
    args = mod.parse_args(["--camera", "toupcam", "--simulated"])
    assert args.camera == "toupcam"
    assert args.simulated is True
