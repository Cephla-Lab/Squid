"""Tests for continuous laser AF measurement API."""

import math

import numpy as np

from squid.core.abc import CameraAcquisitionMode
from squid.core.events import EventBus
from squid.backend.controllers.autofocus.laser_auto_focus_controller import (
    LaserAutofocusController,
    LaserAFResult,
)
from squid.backend.processing.laser_spot import SpotDetectionResult


class DummyCameraService:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._acq_mode = CameraAcquisitionMode.SOFTWARE_TRIGGER
        self.triggered = False
        self.roi = None

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

    def set_region_of_interest(self, x: int, y: int, width: int, height: int) -> None:
        self.roi = (x, y, width, height)


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


class DummyObjectiveStore:
    def __init__(self, objective: str = "20x") -> None:
        self.current_objective = objective


class DummySettingsManager:
    def __init__(self) -> None:
        self.last_update = None

    def update_laser_af_settings(self, objective: str, updates: dict, crop_image=None) -> None:
        self.last_update = (objective, updates, crop_image)

    def get_laser_af_settings(self) -> dict:
        return {}


def _build_controller(
    frame: np.ndarray,
    *,
    peripheral: DummyPeripheralService | None = None,
    objective_store: DummyObjectiveStore | None = None,
    settings_manager: DummySettingsManager | None = None,
):
    camera = DummyCameraService(frame)
    stage = DummyStageService()
    peripheral = peripheral or DummyPeripheralService()
    return LaserAutofocusController(
        camera_service=camera,
        stage_service=stage,
        peripheral_service=peripheral,
        event_bus=EventBus(),
        objectiveStore=objective_store,
        laserAFSettingManager=settings_manager,
    )


def test_measure_displacement_continuous_returns_result(monkeypatch):
    frame = np.zeros((256, 256), dtype=np.uint8)
    controller = _build_controller(frame)
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"has_reference": True, "x_reference": 90.0, "pixel_to_um": 0.5}
    )

    monkeypatch.setattr(
        controller,
        "_detect_spot_in_frame",
        lambda *_args, **_kwargs: SpotDetectionResult(
            x=100.0, y=50.0, intensity=50.0, snr=2.0, background=10.0
        ),
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


def test_measure_displacement_continuous_ignores_stale_reference_when_unset(monkeypatch):
    frame = np.zeros((256, 256), dtype=np.uint8)
    controller = _build_controller(frame)
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"has_reference": False, "x_reference": -3893.7, "pixel_to_um": 0.2}
    )

    monkeypatch.setattr(
        controller,
        "_detect_spot_in_frame",
        lambda *_args, **_kwargs: SpotDetectionResult(
            x=120.0, y=50.0, intensity=50.0, snr=2.0, background=10.0
        ),
    )

    def _fake_metrics(_frame, _x, _y):
        controller._last_spot_metrics = (2.0, 50.0, 10.0)

    monkeypatch.setattr(controller, "_update_last_crop_and_metrics", _fake_metrics)

    result = controller.measure_displacement_continuous()
    assert abs(result.displacement_um) < 1e-9


def test_initialize_manual_clears_stale_reference_state():
    frame = np.zeros((32, 32), dtype=np.uint8)
    controller = _build_controller(frame)
    controller.reference_crop = np.ones((8, 8), dtype=np.float32)

    stale_config = controller.laser_af_properties.model_copy(
        update={
            "x_offset": 768.0,
            "y_offset": 904.0,
            "width": 1536,
            "height": 256,
            "x_reference": -3893.7,
            "has_reference": False,
        }
    )
    controller.initialize_manual(stale_config)

    assert controller.laser_af_properties.has_reference is False
    assert controller.laser_af_properties.x_reference is None
    assert controller.reference_crop is None


def test_initialize_manual_persists_sanitized_reference_state():
    frame = np.zeros((32, 32), dtype=np.uint8)
    objective_store = DummyObjectiveStore("20x")
    settings_manager = DummySettingsManager()
    controller = _build_controller(
        frame,
        objective_store=objective_store,
        settings_manager=settings_manager,
    )

    stale_config = controller.laser_af_properties.model_copy(
        update={
            "x_offset": 768.0,
            "y_offset": 904.0,
            "width": 1536,
            "height": 256,
            "x_reference": -3893.7,
            "has_reference": False,
        }
    )
    controller.initialize_manual(stale_config)

    assert settings_manager.last_update is not None
    _objective, updates, _crop = settings_manager.last_update
    assert updates["has_reference"] is False
    assert updates["x_reference"] is None
