from unittest.mock import patch, MagicMock

import pytest

import control.microscope
import control._def
import squid.stage.cephla
import squid.config
from control.microcontroller import Microcontroller, SimSerial
from control.microscope import _should_simulate
from squid.camera.utils import SimulatedCamera
from tests.control.test_microcontroller import get_test_micro


class TestShouldSimulate:
    """Tests for _should_simulate() per-component simulation logic."""

    def test_simulate_without_global_flag(self):
        """Simulate (True) without --simulation flag should simulate."""
        assert _should_simulate(global_simulated=False, component_override=True) is True

    def test_simulate_with_global_flag(self):
        """Simulate (True) with --simulation flag should simulate."""
        assert _should_simulate(global_simulated=True, component_override=True) is True

    def test_real_hardware_without_global_flag(self):
        """Real Hardware (False) without --simulation should use real hardware."""
        assert _should_simulate(global_simulated=False, component_override=False) is False

    def test_real_hardware_with_global_flag(self):
        """Real Hardware (False) with --simulation flag should still simulate (--simulation overrides all)."""
        assert _should_simulate(global_simulated=True, component_override=False) is True


def test_create_simulated_microscope():
    sim_scope = control.microscope.Microscope.build_from_global_config(True)
    sim_scope.close()


def test_create_simulated_microscope_with_skip_init():
    """Test that skip_init flag is accepted and doesn't cause errors."""
    sim_scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    sim_scope.close()


def test_skip_init_skips_addon_homing():
    """Test that skip_init=True actually skips homing operations in addons."""
    with patch.object(control.microscope.MicroscopeAddons, "prepare_for_use") as mock_prepare:
        sim_scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)

        # Verify prepare_for_use was called with skip_init=True
        mock_prepare.assert_called_once()
        call_kwargs = mock_prepare.call_args.kwargs
        assert call_kwargs.get("skip_init") is True, "prepare_for_use should be called with skip_init=True"

        sim_scope.close()


@patch("squid.config.get_filter_wheel_config")
def test_prepare_for_use_skips_homing_when_flag_set(mock_get_fw_config):
    """Test that MicroscopeAddons.prepare_for_use skips home() calls when skip_init=True."""
    # Mock filter wheel config
    mock_fw_config = MagicMock()
    mock_fw_config.indices = [1]
    mock_get_fw_config.return_value = mock_fw_config

    mock_filter_wheel = MagicMock()
    mock_piezo_stage = MagicMock()

    addons = control.microscope.MicroscopeAddons(
        emission_filter_wheel=mock_filter_wheel,
        piezo_stage=mock_piezo_stage,
    )

    # With skip_init=True, home() should NOT be called
    addons.prepare_for_use(skip_init=True)
    mock_filter_wheel.home.assert_not_called()
    mock_piezo_stage.home.assert_not_called()

    # With skip_init=False (default), home() SHOULD be called
    mock_filter_wheel.reset_mock()
    mock_piezo_stage.reset_mock()
    addons.prepare_for_use(skip_init=False)
    mock_filter_wheel.home.assert_called_once()
    mock_piezo_stage.home.assert_called_once()


@patch("squid.config.get_filter_wheel_config")
def test_prepare_for_use_contains_filter_wheel_homing_failure(mock_get_fw_config):
    """A filter wheel homing failure during startup must be contained, not crash
    the whole microscope build.

    The wheel is left at an unknown position, but the GUI should still come up so
    the user can re-home — a recoverable hardware hiccup must not brick startup.
    """
    from control.microcontroller import CommandAborted

    mock_fw_config = MagicMock()
    mock_fw_config.indices = [1]
    mock_get_fw_config.return_value = mock_fw_config

    mock_filter_wheel = MagicMock()
    mock_filter_wheel.home.side_effect = CommandAborted(reason="firmware reported CMD_EXECUTION_ERROR", command_id=1)

    addons = control.microscope.MicroscopeAddons(emission_filter_wheel=mock_filter_wheel)

    # Must NOT raise — the homing failure is contained so startup continues.
    addons.prepare_for_use(skip_init=False)

    mock_filter_wheel.home.assert_called_once()


def test_simulated_scope_basic_ops():
    scope = control.microscope.Microscope.build_from_global_config(True)

    scope.stage.home(x=True, y=True, z=True, theta=False, blocking=True)
    scope.stage.move_x_to(scope.stage.get_config().X_AXIS.MAX_POSITION / 2)
    scope.stage.move_y_to(scope.stage.get_config().Y_AXIS.MAX_POSITION / 2)
    scope.stage.move_z_to(scope.stage.get_config().Z_AXIS.MAX_POSITION / 2)

    scope.camera.start_streaming()
    scope.illumination_controller.turn_on_illumination()
    scope.camera.send_trigger()
    scope.camera.read_frame()
    scope.illumination_controller.turn_off_illumination()
    scope.camera.stop_streaming()


class TestPerComponentSimulationIntegration:
    """Integration tests verifying per-component settings affect microscope build."""

    def test_simulate_camera_without_global_flag(self, monkeypatch):
        """SIMULATE_CAMERA=True without --simulation should create simulated camera."""
        # Set per-component camera simulation (all components must be simulated for build to work
        # since we're testing without --simulation flag and real hardware isn't available in CI)
        monkeypatch.setattr(control._def, "SIMULATE_CAMERA", True)
        monkeypatch.setattr(control._def, "SIMULATE_MICROCONTROLLER", True)
        monkeypatch.setattr(control._def, "SIMULATE_SPINNING_DISK", True)
        monkeypatch.setattr(control._def, "SIMULATE_FILTER_WHEEL", True)
        monkeypatch.setattr(control._def, "SIMULATE_OBJECTIVE_CHANGER", True)
        monkeypatch.setattr(control._def, "SIMULATE_LASER_AF_CAMERA", True)

        # Build microscope WITHOUT --simulation flag (simulated=False)
        scope = control.microscope.Microscope.build_from_global_config(simulated=False)
        try:
            # Camera should be simulated due to per-component setting
            assert isinstance(
                scope.camera, SimulatedCamera
            ), f"Expected SimulatedCamera but got {type(scope.camera).__name__}"
        finally:
            scope.close()

    def test_global_simulation_overrides_per_component(self, monkeypatch):
        """--simulation flag should simulate ALL components regardless of per-component settings."""
        # Set per-component to "Real Hardware" (False) for all components
        monkeypatch.setattr(control._def, "SIMULATE_CAMERA", False)
        monkeypatch.setattr(control._def, "SIMULATE_MICROCONTROLLER", False)
        monkeypatch.setattr(control._def, "SIMULATE_SPINNING_DISK", False)
        monkeypatch.setattr(control._def, "SIMULATE_FILTER_WHEEL", False)
        monkeypatch.setattr(control._def, "SIMULATE_OBJECTIVE_CHANGER", False)
        monkeypatch.setattr(control._def, "SIMULATE_LASER_AF_CAMERA", False)

        # Build microscope WITH --simulation flag (simulated=True)
        scope = control.microscope.Microscope.build_from_global_config(simulated=True)
        try:
            # Camera should be simulated because --simulation overrides per-component
            assert isinstance(
                scope.camera, SimulatedCamera
            ), f"Expected SimulatedCamera (--simulation overrides per-component) but got {type(scope.camera).__name__}"
        finally:
            scope.close()


class TestGetImagePixelSizeUm:
    """Tests for Microscope.get_image_pixel_size_um()."""

    def test_returns_factor_times_binned_um(self):
        scope = control.microscope.Microscope.build_from_global_config(True)
        try:
            expected = scope.objective_store.get_pixel_size_factor() * scope.camera.get_pixel_size_binned_um()
            assert scope.get_image_pixel_size_um() == expected
        finally:
            scope.close()

    @pytest.mark.parametrize(
        "target, attr",
        [("objective_store", "get_pixel_size_factor"), ("camera", "get_pixel_size_binned_um")],
    )
    def test_returns_none_when_component_is_none(self, target, attr):
        scope = control.microscope.Microscope.build_from_global_config(True)
        try:
            with patch.object(getattr(scope, target), attr, return_value=None):
                assert scope.get_image_pixel_size_um() is None
        finally:
            scope.close()

    def test_returns_none_when_camera_raises_not_implemented(self):
        """Some camera drivers (e.g. DefaultCamera with an unknown model) raise
        NotImplementedError from get_pixel_size_binned_um. Callers expect None
        per the docstring."""
        scope = control.microscope.Microscope.build_from_global_config(True)
        try:
            with patch.object(scope.camera, "get_pixel_size_binned_um", side_effect=NotImplementedError):
                assert scope.get_image_pixel_size_um() is None
        finally:
            scope.close()


def test_fluidics_ini_keys_exist():
    # [SIMULATION] simulate_fluidics is parsed like the other SIMULATE_* keys. The local INI may
    # legitimately override any of these (a fluidics machine runs with RUN_FLUIDICS = True), so only
    # the keys' existence and types are asserted here.
    assert isinstance(control._def.SIMULATE_FLUIDICS, bool)
    assert isinstance(control._def.RUN_FLUIDICS, bool)
    assert isinstance(control._def.FLUIDICS_CONFIG_PATH, str) and control._def.FLUIDICS_CONFIG_PATH


def test_addons_fluidics_is_none_without_run_fluidics(monkeypatch):
    monkeypatch.setattr(control._def, "RUN_FLUIDICS", False)
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        assert scope.addons.fluidics is None
    finally:
        scope.close()


def test_run_fluidics_builds_an_uninitialized_simulated_service(monkeypatch):
    pytest.importorskip("fluidics")
    from control.fluidics_system import FluidicsService

    # The import guard in control.microscope ran at import time with RUN_FLUIDICS=False, so bind the class
    # explicitly; SIMULATE_FLUIDICS is honoured through _should_simulate like the other components.
    monkeypatch.setattr(control._def, "RUN_FLUIDICS", True)
    monkeypatch.setattr(control._def, "SIMULATE_FLUIDICS", True)
    monkeypatch.setattr(control.microscope, "FluidicsService", FluidicsService, raising=False)
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        service = scope.addons.fluidics
        assert isinstance(service, FluidicsService)
        assert service.simulated is True
        assert service.initialized is False
        assert service.default_config_path == control._def.FLUIDICS_CONFIG_PATH
    finally:
        scope.close()


def test_microscope_close_closes_the_fluidics_service():
    class FakeService:
        closed = 0

        def close(self):
            self.closed += 1
            return []

    scope = control.microscope.Microscope.build_from_global_config(True)
    fake = FakeService()
    scope.addons.fluidics = fake
    scope.close()
    scope.close()  # second close is a no-op
    assert fake.closed == 1


# ---------------------------------------------------------------- the pixel-size seam (spec B §4.4-4.5)


def _saved_calibration_for(objective_name: str, pixel_size_um: float):
    """A valid pixel_size record for the simulated camera and `objective_name`, as a saved config."""
    import numpy as np

    from control.models.objective_calibration_config import ImageTransform, build_pixel_record, merge_pixel_records
    from control.objective_calibration_hardware import camera_key, image_transform
    from squid.objective_calibration.engine import PixelSizeSummary

    camera_config = squid.config.get_camera_config()
    rotate_deg, flip = image_transform(camera_config)
    summary = PixelSizeSummary(
        objective=objective_name,
        cycles=3,
        pixel_size_um=pixel_size_um,
        std_pixel_size_um=0.0003,
        matrix_um_per_px=pixel_size_um * np.eye(2),
        rotation_deg=0.1,
        flip=np.eye(2),
        orientation_matches_mosaic=True,
        anisotropy=1.0,
        fit_residual_um=0.3,
    )
    record = build_pixel_record(
        summary,
        measured_at="2026-09-28T10:00:00",
        camera_key=camera_key(camera_config),
        unbinned_sensor_pixel_um=SimulatedCamera.PIXEL_SIZE_UM,
        binned_sensor_pixel_um=SimulatedCamera.PIXEL_SIZE_UM,
        binning=1,
        image_transform=ImageTransform(rotate_deg=rotate_deg, flip=flip),
        tube_lens_mm=control._def.TUBE_LENS_MM,
        declared=control._def.get_declared(objective_name),
    )
    return merge_pixel_records(None, {objective_name: record})


def test_the_microscope_hands_the_saved_calibration_and_camera_to_the_objective_store(monkeypatch):
    from control.core.config.repository import ConfigRepository

    name = control._def.DEFAULT_OBJECTIVE
    nominal = control.microscope.ObjectiveStore.calculate_pixel_size_factor(
        control._def.OBJECTIVES[name], control._def.TUBE_LENS_MM
    )
    measured = nominal * SimulatedCamera.PIXEL_SIZE_UM * 1.03  # 3% off nominal, in um/px at 1x
    monkeypatch.setattr(
        ConfigRepository, "get_objective_calibration", lambda self: _saved_calibration_for(name, measured)
    )
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        store = scope.objective_store
        assert store.pixel_size_source(name) == "calibrated"
        assert store.get_pixel_size_factor() == pytest.approx(nominal * 1.03)
        # the test ini runs the camera at 2x2 binning: the factor is binning-free, the image pixel is not
        binned_um = scope.camera.get_pixel_size_binned_um()
        assert scope.get_image_pixel_size_um() == pytest.approx(nominal * 1.03 * binned_um)
        assert store.pixel_matrix(name) is not None
        assert scope.objective_calibration_error == ""
    finally:
        scope.close()


def test_an_unreadable_calibration_file_leaves_the_microscope_nominal_and_reports_it(monkeypatch):
    from control.core.config.repository import ConfigRepository
    from control.models.objective_calibration_config import ObjectiveCalibrationFileError

    def unreadable(self):
        raise ObjectiveCalibrationFileError("objective_calibration.yaml cannot be read (bad)")

    monkeypatch.setattr(ConfigRepository, "get_objective_calibration", unreadable)
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        name = control._def.DEFAULT_OBJECTIVE
        assert scope.objective_store.pixel_size_source(name) == "nominal"
        assert "cannot be read" in scope.objective_calibration_error
    finally:
        scope.close()


def test_a_simulated_microscope_reads_the_simulation_calibration_never_the_machines(monkeypatch):
    from control.core.config.repository import ConfigRepository

    read_from = []
    monkeypatch.setattr(
        ConfigRepository, "get_objective_calibration", lambda self: read_from.append(self.machine_configs_path)
    )
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        assert len(read_from) == 1
        assert read_from[0].parts[-3:] == ("cache", "simulation", "machine_configs")
    finally:
        scope.close()
