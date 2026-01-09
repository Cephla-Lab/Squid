"""Tests for continuous laser AF measurement API."""

import math

import numpy as np

from squid.core.abc import CameraAcquisitionMode
from squid.core.events import EventBus
from squid.backend.controllers.autofocus.laser_auto_focus_controller import (
    LaserAutofocusController,
    LaserAFResult,
)


class DummyCameraService:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._acq_mode = CameraAcquisitionMode.SOFTWARE_TRIGGER
        self.triggered = False

    def get_acquisition_mode(self):
        return self._acq_mode

    def send_trigger(self, illumination_time=None):  # noqa: ARG002
        self.triggered = True

    def read_frame(self):
        return self._frame

    def enable_callbacks(self, enabled: bool) -> None:  # noqa: ARG002
        return None

    def get_exposure_time(self) -> float:
        return 10.0


class DummyStageService:
    pass


class DummyPeripheralService:
    def __init__(self, *, raise_on_use: bool = False) -> None:
        self._raise_on_use = raise_on_use

    def turn_on_af_laser(self, wait_for_completion: bool = True) -> None:  # noqa: ARG002
        if self._raise_on_use:
            raise AssertionError("turn_on_af_laser called unexpectedly")

    def turn_off_af_laser(self, wait_for_completion: bool = True) -> None:  # noqa: ARG002
        if self._raise_on_use:
            raise AssertionError("turn_off_af_laser called unexpectedly")


def _build_controller(frame: np.ndarray, *, peripheral: DummyPeripheralService | None = None):
    camera = DummyCameraService(frame)
    stage = DummyStageService()
    peripheral = peripheral or DummyPeripheralService()
    return LaserAutofocusController(
        camera_service=camera,
        stage_service=stage,
        peripheral_service=peripheral,
        event_bus=EventBus(),
    )


def test_measure_displacement_continuous_returns_result(monkeypatch):
    frame = np.zeros((256, 256), dtype=np.uint8)
    controller = _build_controller(frame)
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"x_reference": 90.0, "pixel_to_um": 0.5}
    )

    monkeypatch.setattr(
        controller,
        "_detect_spot_in_frame",
        lambda *_args, **_kwargs: (100.0, 50.0),
    )

    def _fake_metrics(_frame, _x, _y):
        controller._last_spot_metrics = (2.0, 50.0, 10.0)

    monkeypatch.setattr(controller, "_update_last_crop_and_metrics", _fake_metrics)

    result = controller.measure_displacement_continuous()
    assert isinstance(result, LaserAFResult)
    assert result.displacement_um == 5.0
    assert result.spot_snr == 2.0
    assert result.spot_intensity == 50.0
    assert result.correlation is None
    assert result.spot_x_px == 100.0
    assert result.spot_y_px == 50.0


def test_measure_displacement_continuous_blocked_returns_nan():
    frame = np.zeros((32, 32), dtype=np.uint8)
    controller = _build_controller(frame)

    assert controller._measurement_lock.acquire(blocking=False)
    try:
        result = controller.measure_displacement_continuous()
    finally:
        controller._measurement_lock.release()

    assert math.isnan(result.displacement_um)


def test_measure_displacement_blocked_returns_nan():
    frame = np.zeros((32, 32), dtype=np.uint8)
    peripheral = DummyPeripheralService(raise_on_use=True)
    controller = _build_controller(frame, peripheral=peripheral)

    assert controller._measurement_lock.acquire(blocking=False)
    try:
        result = controller.measure_displacement()
    finally:
        controller._measurement_lock.release()

    assert math.isnan(result)
