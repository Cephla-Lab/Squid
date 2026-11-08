import copy
import dataclasses
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import control._def
import control.microscope
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import MultiPointControllerFunctions, AcquisitionParameters, ScanPositionInformation

import tests.control.test_stubs as ts


def test_multi_point_controller_image_count_calculation():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    control._def.MERGE_CHANNELS = False
    all_configuration_names = [
        config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
    ]
    nz = 2
    nt = 3
    assert len(all_configuration_names) > 0
    all_config_count = len(all_configuration_names)

    mpc.set_NZ(nz)
    mpc.set_Nt(nt)
    mpc.set_selected_configurations(all_configuration_names[0:1])
    mpc.scanCoordinates.clear_regions()

    assert mpc.get_acquisition_image_count() == 0

    # Add a single region with 1 fov
    # NOTE: If the coordinates below aren't in the valid range for our stage, it silently fails to add regions.
    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 1, 1, 0)

    assert mpc.get_acquisition_image_count() == (nt * nz * 1 * 1)

    # Add 9 more regions with a single fov
    for i in range(1, 10):
        x_st = x_min + i
        y_st = y_min + i
        mpc.scanCoordinates.add_flexible_region(i + 2, x_st, y_st, z_mid, 1, 1, 0)

    assert mpc.get_acquisition_image_count() == (nt * nz * 10 * 1)

    # Select all the configurations
    mpc.set_selected_configurations(all_configuration_names)
    assert mpc.get_acquisition_image_count() == (nt * nz * 10 * all_config_count)

    # Add a multiple FOV region with 5 in each of x and y dirs.
    mpc.scanCoordinates.add_flexible_region(123, x_min + 11, y_min + 11, z_mid, 5, 5, 0)

    final_number_of_fov = nt * nz * (10 + 25)
    assert mpc.get_acquisition_image_count() == final_number_of_fov * all_config_count

    # When we merge, there's an extra image per fov (where we merge all the configs for that fov).
    control._def.MERGE_CHANNELS = True
    assert mpc.get_acquisition_image_count() == final_number_of_fov * (all_config_count + 1)


def test_multi_point_controller_disk_space_estimate():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    control._def.MERGE_CHANNELS = False
    all_configuration_names = [
        config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
    ]
    nz = 2
    nt = 3
    assert len(all_configuration_names) > 0
    all_config_count = len(all_configuration_names)

    mpc.set_NZ(nz)
    mpc.set_Nt(nt)
    mpc.set_selected_configurations(all_configuration_names[0:1])
    mpc.scanCoordinates.clear_regions()

    # No images -> no bytes needed (except admin bytes, which is < 200kB)
    assert mpc.get_estimated_acquisition_disk_storage() < 200 * 1024

    # Add a single region with 1 fov
    # NOTE: If the coordinates below aren't in the valid range for our stage, it silently fails to add regions.
    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 1, 1, 0)

    # Add 9 more regions with a single fov
    for i in range(1, 10):
        x_st = x_min + i
        y_st = y_min + i
        mpc.scanCoordinates.add_flexible_region(i + 2, x_st, y_st, z_mid, 1, 1, 0)

    # Select all the configurations
    mpc.set_selected_configurations(all_configuration_names)
    # Add a multiple FOV region with 5 in each of x and y dirs.
    mpc.scanCoordinates.add_flexible_region(123, x_min + 11, y_min + 11, z_mid, 5, 5, 0)

    final_number_of_fov = nt * nz * (10 + 25)
    # It is tricky to calculate the exact value here, but since we are capturing >3000 images it should at least
    # be in the multi-GB range.
    assert mpc.get_estimated_acquisition_disk_storage() > 1e9

    # When we merge, there's an extra image per fov (where we merge all the configs for that fov).
    before_size = mpc.get_estimated_acquisition_disk_storage()
    control._def.MERGE_CHANNELS = True
    after_size = mpc.get_estimated_acquisition_disk_storage()
    assert after_size > before_size


def test_multi_point_controller_mosaic_ram_estimate():
    """Test RAM estimation for mosaic view."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    # Store original value and enable mosaic display for testing
    original_use_napari = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

    try:
        all_configuration_names = [
            config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
        ]
        assert len(all_configuration_names) > 0

        mpc.scanCoordinates.clear_regions()

        # No regions -> 0 bytes needed
        assert mpc.get_estimated_mosaic_ram_bytes() == 0

        # Add a region with multiple FOVs to get a non-zero scan area
        # Single FOV results in zero width/height, so we need at least a grid
        x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
        y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
        z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
        # Add a 3x3 grid region to get actual scan bounds
        mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 3, 3, 0)

        # No channels selected -> 0 bytes (with warning)
        mpc.set_selected_configurations([])
        assert mpc.get_estimated_mosaic_ram_bytes() == 0

        # Select one channel -> should have non-zero RAM estimate
        mpc.set_selected_configurations(all_configuration_names[0:1])
        ram_one_channel = mpc.get_estimated_mosaic_ram_bytes()
        assert ram_one_channel > 0, f"Expected RAM > 0, got {ram_one_channel}"

        # Select all channels -> RAM should scale with channel count
        mpc.set_selected_configurations(all_configuration_names)
        ram_all_channels = mpc.get_estimated_mosaic_ram_bytes()
        assert ram_all_channels > ram_one_channel
        # RAM should scale roughly linearly with number of channels
        expected_ratio = len(all_configuration_names)
        actual_ratio = ram_all_channels / ram_one_channel
        assert abs(actual_ratio - expected_ratio) < 0.1  # Allow small rounding differences

        # Add more regions to increase scan area -> RAM should increase
        for i in range(1, 5):
            x_st = x_min + i * 1.0  # Larger spacing for bigger scan area
            y_st = y_min + i * 1.0
            mpc.scanCoordinates.add_flexible_region(i + 2, x_st, y_st, z_mid, 2, 2, 0)

        ram_larger_area = mpc.get_estimated_mosaic_ram_bytes()
        assert ram_larger_area > ram_all_channels

    finally:
        # Restore original value
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_use_napari


def test_multi_point_controller_mosaic_ram_disabled():
    """Test that RAM estimation returns 0 when mosaic display is disabled."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    # Store original value and disable mosaic display
    original_use_napari = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = False

    try:
        all_configuration_names = [
            config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
        ]

        # Add regions and select channels
        x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
        y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
        z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
        mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 5, 5, 0)
        mpc.set_selected_configurations(all_configuration_names)

        # Should return 0 when napari mosaic display is disabled
        assert mpc.get_estimated_mosaic_ram_bytes() == 0

    finally:
        # Restore original value
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_use_napari


class TestAcquisitionTracker:
    def __init__(self):
        self.started_event = threading.Event()
        self.finished_event = threading.Event()
        self.image_count = 0
        self.config_change_count = 0
        self.current_fovs_count = 0
        self.overall_progress_seen = False
        self.region_progress_seen = False

    def get_callbacks(self) -> MultiPointControllerFunctions:
        return MultiPointControllerFunctions(
            signal_acquisition_start=lambda params: self.started_event.set(),
            signal_acquisition_finished=lambda: self.finished_event.set(),
            signal_new_image=self.receive_image,
            signal_current_configuration=self.receive_config,
            signal_current_fov=self.receive_current_fov,
            signal_overall_progress=self.receive_overall_progress,
            signal_region_progress=self.receive_region_progress,
        )

    def receive_image(self, frame, info):
        self.image_count += 1

    def receive_config(self, config):
        self.config_change_count += 1

    def receive_current_fov(self, x_mm, y_mm):
        self.current_fovs_count += 1

    def receive_overall_progress(self, progress):
        self.overall_progress_seen = True

    def receive_region_progress(self, progress):
        self.region_progress_seen = True


def add_some_coordinates(mpc: MultiPointController):
    stage = mpc.stage

    min_x = stage.get_config().X_AXIS.MIN_POSITION
    min_y = stage.get_config().Y_AXIS.MIN_POSITION
    min_z = stage.get_config().Z_AXIS.MIN_POSITION

    max_x = stage.get_config().X_AXIS.MAX_POSITION
    max_y = stage.get_config().Y_AXIS.MAX_POSITION
    max_z = stage.get_config().Z_AXIS.MAX_POSITION

    mpc.scanCoordinates.add_single_fov_region(
        "region_1", center_x=min_x + 1.0, center_y=min_y + 1.0, center_z=min_z + 1.0
    )
    mpc.scanCoordinates.add_single_fov_region(
        "region_2", center_x=min_x + 0.5, center_y=min_y + 0.5, center_z=min_z + 0.1
    )
    mpc.scanCoordinates.add_flexible_region("region_grid", max_x / 2.0, max_y / 2.0, max_z / 2.0, 3, 3, 10)


def select_some_configs(mpc: MultiPointController, objective: str):
    all_config_names = [m.name for m in mpc.liveController.get_channels(objective)]
    first_two_config_names = all_config_names[:2]
    mpc.set_selected_configurations(selected_configurations_name=first_two_config_names)


def test_multi_point_controller_basic_acquisition():
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)

    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    assert tt.overall_progress_seen
    assert tt.region_progress_seen

    assert tt.image_count == mpc.get_acquisition_image_count()
    assert tt.current_fovs_count > 0
    assert tt.config_change_count > 0


def test_multi_point_with_laser_af():
    control._def.MERGE_CHANNELS = False
    control._def.SUPPORT_LASER_AUTOFOCUS = True
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()

    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_reflection_af_flag(True)
    scope.addons.camera_focus.send_trigger()
    laser_af_ref_image = scope.addons.camera_focus.read_frame()
    assert laser_af_ref_image is not None
    mpc.laserAutoFocusController.laser_af_properties.set_reference_image(laser_af_ref_image)

    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    assert tt.overall_progress_seen
    assert tt.region_progress_seen

    assert tt.image_count == mpc.get_acquisition_image_count()
    assert tt.current_fovs_count > 0
    assert tt.config_change_count > 0


def test_multi_point_with_contrast_af():
    control._def.MERGE_CHANNELS = False

    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()

    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_af_flag(True)
    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    assert tt.overall_progress_seen
    assert tt.region_progress_seen

    assert tt.image_count == mpc.get_acquisition_image_count()
    assert tt.current_fovs_count > 0
    assert tt.config_change_count > 0


def test_acquisition_parameters_has_apply_channel_offset_default_true():
    """apply_channel_offset defaults to True for backward compatibility (TCP/MCP callers)."""
    from control.core.multi_point_utils import AcquisitionParameters, ScanPositionInformation

    sp = ScanPositionInformation(
        scan_region_coords_mm=[],
        scan_region_names=[],
        scan_region_fov_coords_mm={},
    )
    p = AcquisitionParameters(
        experiment_ID=None,
        base_path=None,
        selected_configurations=[],
        acquisition_start_time=0.0,
        scan_position_information=sp,
        NX=1,
        deltaX=0,
        NY=1,
        deltaY=0,
        NZ=1,
        deltaZ=0,
        Nt=1,
        deltat=0,
        do_autofocus=False,
        do_reflection_autofocus=False,
        use_piezo=False,
        display_resolution_scaling=1.0,
        z_stacking_config="FROM CENTER",
        z_range=(0.0, 0.0),
    )
    assert p.apply_channel_offset is True


def test_acquisition_parameters_apply_channel_offset_can_be_overridden():
    from control.core.multi_point_utils import AcquisitionParameters, ScanPositionInformation

    sp = ScanPositionInformation(
        scan_region_coords_mm=[],
        scan_region_names=[],
        scan_region_fov_coords_mm={},
    )
    p = AcquisitionParameters(
        experiment_ID=None,
        base_path=None,
        selected_configurations=[],
        acquisition_start_time=0.0,
        scan_position_information=sp,
        NX=1,
        deltaX=0,
        NY=1,
        deltaY=0,
        NZ=1,
        deltaZ=0,
        Nt=1,
        deltat=0,
        do_autofocus=False,
        do_reflection_autofocus=False,
        use_piezo=False,
        display_resolution_scaling=1.0,
        z_stacking_config="FROM CENTER",
        z_range=(0.0, 0.0),
        apply_channel_offset=False,
    )
    assert p.apply_channel_offset is False


class StubFocusMap:
    def interpolate(self, x, y, region_id=None):
        return 3.0


def test_focus_map_does_not_mutate_gui_scan_coordinates():
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)

    coords_before = copy.deepcopy(mpc.scanCoordinates.region_fov_coordinates)
    centers_before = copy.deepcopy(mpc.scanCoordinates.region_centers)

    mpc.set_focus_map(StubFocusMap())
    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    assert mpc.scanCoordinates.region_fov_coordinates == coords_before
    assert mpc.scanCoordinates.region_centers == centers_before


def test_acquisition_moves_to_per_fov_z():
    # Characterization/regression guard: the worker honors a 3-tuple coordinate's z
    # (multi_point_worker.py move_to_coordinate). Loaded-with-z plans rely on this.
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()

    captured_z_mm = []

    def record_z(frame, info):
        captured_z_mm.append(info.position.z_mm)
        tt.receive_image(frame, info)

    callbacks = tt.get_callbacks()
    callbacks.signal_new_image = record_z
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)

    stage = mpc.stage
    x = stage.get_config().X_AXIS.MIN_POSITION + 1.0
    y = stage.get_config().Y_AXIS.MIN_POSITION + 1.0
    z_target = 3.0
    # Inject regions the way a loaded CSV stores them: 3-tuple FOVs, [x, y] list centers.
    mpc.scanCoordinates.region_fov_coordinates = {"A1": [(x, y, z_target)]}
    mpc.scanCoordinates.region_centers = {"A1": [x, y]}

    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    # Every image of this single-FOV, NZ=1 acquisition was captured at the per-FOV z.
    assert captured_z_mm, "no images were captured"
    assert all(z == pytest.approx(z_target, abs=1e-3) for z in captured_z_mm)


def test_engine_has_no_fluidics_coupling():
    from dataclasses import fields

    assert "use_fluidics" not in {f.name for f in fields(AcquisitionParameters)}
    assert not hasattr(MultiPointController, "set_use_fluidics")


def test_start_new_experiment_without_timestamp_uses_the_folder_verbatim(tmp_path):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    mpc.set_base_path(str(tmp_path))

    mpc.start_new_experiment("R01_image", add_timestamp=False)

    assert mpc.experiment_ID == "R01_image"
    assert (tmp_path / "R01_image" / "acquisition parameters.json").exists()
    with pytest.raises(FileExistsError):
        mpc.start_new_experiment("R01_image", add_timestamp=False)


def test_start_new_experiment_default_still_appends_a_timestamp(tmp_path):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    mpc.set_base_path(str(tmp_path))

    mpc.start_new_experiment("my exp")

    assert mpc.experiment_ID.startswith("my_exp_")
    assert len(mpc.experiment_ID) > len("my_exp_")


class CountingTracker(TestAcquisitionTracker):
    def __init__(self):
        super().__init__()
        self.finished_count = 0

    def get_callbacks(self) -> MultiPointControllerFunctions:
        callbacks = super().get_callbacks()

        def finished():
            self.finished_count += 1
            self.finished_event.set()

        return dataclasses.replace(callbacks, signal_acquisition_finished=finished)


def _controller_with_tracker():
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = CountingTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    return scope, tt, mpc


def test_completed_run_reports_end_reason_and_image_count():
    scope, tt, mpc = _controller_with_tracker()
    assert mpc.last_end_reason is None

    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert tt.finished_count == 1
    assert mpc.last_end_reason == "completed"
    assert mpc.last_image_count == tt.image_count > 0


def test_saving_settings_changed_after_startup_apply_to_the_next_acquisition(tmp_path, monkeypatch):
    """The saving subprocess is started ahead of the acquisition, with its own copy of control._def
    from that moment, so the settings have to reach it with the acquisition."""
    import tifffile

    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.INDIVIDUAL_IMAGES)
    monkeypatch.setattr(control._def, "TIFF_COMPRESSION_LEVEL", 0)
    scope, tt, mpc = _controller_with_tracker()
    # One image is enough.
    mpc.scanCoordinates.remove_region("region_2")
    mpc.scanCoordinates.remove_region("region_grid")
    mpc.selected_configurations = mpc.selected_configurations[:1]
    mpc.set_base_path(str(tmp_path))
    mpc.start_new_experiment("acquisition", add_timestamp=False)

    # What saving the Preferences dialog does.
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.MULTI_PAGE_TIFF)
    monkeypatch.setattr(control._def, "TIFF_COMPRESSION_LEVEL", 6)

    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    stacks = list((tmp_path / "acquisition").rglob("*_stack.tiff"))
    assert stacks, "Nothing was saved as MULTI_PAGE_TIFF"
    with tifffile.TiffFile(stacks[0]) as tif:
        assert tif.pages[0].compression == tifffile.COMPRESSION.ADOBE_DEFLATE


def test_user_abort_reports_user_abort():
    scope, tt, mpc = _controller_with_tracker()
    mpc.run_acquisition()
    mpc.request_abort_aquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert tt.finished_count == 1
    assert mpc.last_end_reason == "user_abort"


def test_validation_failure_reports_failed_to_start_exactly_once():
    scope, tt, mpc = _controller_with_tracker()
    mpc.laserAutoFocusController.laser_af_properties.has_reference = False
    mpc.set_reflection_af_flag(True)

    mpc.run_acquisition()

    assert tt.finished_event.is_set()
    assert tt.finished_count == 1
    assert mpc.last_end_reason == "failed_to_start"
    assert not mpc.acquisition_in_progress()


def test_focus_map_without_scan_bounds_reports_failed_to_start_and_restores_the_camera():
    scope, tt, mpc = _controller_with_tracker()
    mpc.set_gen_focus_map_flag(True)
    mpc.scanCoordinates.get_scan_bounds = lambda: None  # the focus-map early return
    callbacks_before = scope.camera.get_callbacks_enabled()

    mpc.run_acquisition()

    assert tt.finished_count == 1
    assert mpc.last_end_reason == "failed_to_start"
    assert scope.camera.get_callbacks_enabled() == callbacks_before
    assert not mpc.acquisition_in_progress()


def test_worker_construction_failure_reports_failed_to_start_once_and_reraises(monkeypatch):
    import control.core.multi_point_controller as mpc_module

    class BrokenWorker:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(mpc_module, "MultiPointWorker", BrokenWorker)
    scope, tt, mpc = _controller_with_tracker()

    with pytest.raises(RuntimeError, match="boom"):
        mpc.run_acquisition()

    assert tt.finished_count == 1
    assert mpc.last_end_reason == "failed_to_start"


def test_acquisition_yaml_has_region_fovs_and_the_protocol_section(tmp_path):
    import yaml

    scope, tt, mpc = _controller_with_tracker()
    mpc.set_base_path(str(tmp_path))
    mpc.start_new_experiment("R01_image", add_timestamp=False)
    mpc.protocol_info = {"name": "demo", "round": "R01", "step": "image", "run_name": "liver"}

    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    with open(tmp_path / "R01_image" / "acquisition.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    regions = {r["name"]: r for r in data["wellplate_scan"]["regions"]}
    expected = [list(fov) for fov in mpc.scanCoordinates.region_fov_coordinates["region_1"]]
    assert regions["region_1"]["fovs"] == expected
    assert len(regions["region_grid"]["fovs"]) == 9
    assert data["protocol"] == {"name": "demo", "round": "R01", "step": "image", "run_name": "liver"}
    assert "fluidics" not in data
    assert mpc.protocol_info is None


def test_protocol_info_is_consumed_even_when_the_run_fails_to_start(tmp_path):
    import yaml

    scope, tt, mpc = _controller_with_tracker()
    mpc.set_base_path(str(tmp_path))
    mpc.start_new_experiment("R01_image", add_timestamp=False)
    mpc.protocol_info = {"name": "demo", "round": "R01"}
    mpc.laserAutoFocusController.laser_af_properties.has_reference = False
    mpc.set_reflection_af_flag(True)
    mpc.run_acquisition()  # validation failure
    assert mpc.last_end_reason == "failed_to_start" and mpc.protocol_info is None

    mpc.set_reflection_af_flag(False)
    tt.finished_event.clear()
    mpc.start_new_experiment("R02_image", add_timestamp=False)
    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)
    with open(tmp_path / "R02_image" / "acquisition.yaml", encoding="utf-8") as f:
        assert "protocol" not in yaml.safe_load(f)


# --- MCU-idle pre-flight -----------------------------------------------------
# Starting an acquisition must survive a stop_live() timeout and wait for the MCU to
# go idle before the worker moves the stage (see LiveController.trigger_acquisition).


def _stop_live_like_wedged_mcu(live_controller):
    """Mimic the real failure: is_live goes False, then the illumination-off wait raises."""

    def stop_live():
        live_controller.is_live = False
        raise TimeoutError("illumination-off wait timed out")

    return stop_live


def test_run_acquisition_aborts_cleanly_when_microcontroller_stays_busy():
    scope, tt, mpc = _controller_with_tracker()

    mpc.liveController.is_live = True
    with patch.object(
        mpc.liveController, "stop_live", side_effect=_stop_live_like_wedged_mcu(mpc.liveController)
    ), patch.object(
        mpc.microcontroller, "wait_till_operation_is_completed", side_effect=TimeoutError("busy")
    ) as wait_mock:
        assert mpc.run_acquisition() is False  # must not raise, and must say it did not start

    wait_mock.assert_called_once()
    assert mpc.thread is None
    assert tt.finished_event.wait(5)
    assert not tt.started_event.is_set()
    assert mpc.last_end_reason == "failed_to_start"


def test_run_acquisition_continues_when_stop_live_times_out_but_mcu_recovers():
    scope, tt, mpc = _controller_with_tracker()

    mpc.liveController.is_live = True
    with patch.object(
        mpc.liveController, "stop_live", side_effect=_stop_live_like_wedged_mcu(mpc.liveController)
    ), patch.object(
        mpc.liveController, "start_live"
    ):  # keep post-acquisition resume from really starting live
        assert mpc.run_acquisition() is True
        assert tt.started_event.wait(5)
        assert tt.finished_event.wait(30)
        mpc.thread.join(10)

    assert tt.image_count == mpc.get_acquisition_image_count()


# --- Live after an acquisition -----------------------------------------------
# Live is off once an acquisition ends, whether or not it was on before. A machine that
# wants live back sets resume_live_after_acquisition = True in its INI.


def _controller_with_live_on():
    scope, tt, mpc = _controller_with_tracker()
    mpc.liveController.start_live()
    assert mpc.liveController.is_live
    return scope, tt, mpc


def test_live_is_off_after_an_acquisition_that_started_with_live_on():
    scope, tt, mpc = _controller_with_live_on()

    assert mpc.run_acquisition() is True
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert mpc.last_end_reason == "completed"
    assert not mpc.liveController.is_live


def test_live_is_off_after_an_acquisition_that_fails_to_start_with_live_on():
    scope, tt, mpc = _controller_with_live_on()
    mpc.set_gen_focus_map_flag(True)
    mpc.scanCoordinates.get_scan_bounds = lambda: None  # the focus-map early return, after live is stopped

    assert mpc.run_acquisition() is False

    assert mpc.last_end_reason == "failed_to_start"
    assert not mpc.liveController.is_live


def test_live_comes_back_after_an_acquisition_when_the_ini_asks_for_it(monkeypatch):
    monkeypatch.setattr(control._def, "RESUME_LIVE_AFTER_ACQUISITION", True)
    scope, tt, mpc = _controller_with_live_on()
    try:
        assert mpc.run_acquisition() is True
        assert tt.finished_event.wait(30)
        mpc.thread.join(10)

        assert mpc.liveController.is_live
    finally:
        mpc.liveController.stop_live()


def _record_run_events(mpc):
    """Wrap the run's callbacks to record (event, live on?) in the order they fire."""
    events = []
    callbacks = mpc.callbacks

    def start(params):
        events.append(("start", mpc.liveController.is_live))
        callbacks.signal_acquisition_start(params)

    def finished():
        events.append(("finished", mpc.liveController.is_live))
        callbacks.signal_acquisition_finished()

    mpc.callbacks = dataclasses.replace(
        callbacks,
        signal_acquisition_preparing=lambda: events.append(("preparing", mpc.liveController.is_live)),
        signal_acquisition_start=start,
        signal_acquisition_finished=finished,
    )
    return events


def test_preparing_is_reported_before_the_run_stops_live():
    """The GUI locks Live and Snap on this. A TCP run prepares on the server's thread (stopping live, autofocusing
    for a focus map) while the GUI still takes clicks, so it must come before the run touches live or the camera."""
    scope, tt, mpc = _controller_with_live_on()
    events = _record_run_events(mpc)

    assert mpc.run_acquisition() is True
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert events == [("preparing", True), ("start", False), ("finished", False)]


def test_a_start_that_fails_after_preparing_still_reports_finished():
    """Finished is what unlocks Live and Snap, so a start that fails once preparing was sent must still send it."""
    scope, tt, mpc = _controller_with_live_on()
    events = _record_run_events(mpc)
    mpc.set_gen_focus_map_flag(True)
    mpc.scanCoordinates.get_scan_bounds = lambda: None  # the focus-map early return, after live is stopped

    assert mpc.run_acquisition() is False

    assert events == [("preparing", True), ("finished", False)]


def test_start_blocker_refuses_an_acquisition_before_anything_moves():
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        mpc.validate_acquisition_settings = lambda *_: True
        mpc.start_blocker = lambda: "the Z lift is moving"
        start = scope.stage.get_pos()
        assert mpc.run_acquisition() is False
        assert mpc.last_end_reason == "failed_to_start"
        assert scope.stage.get_pos() == start
    finally:
        scope.close()


def test_finish_is_reported_when_the_stage_stops_answering_mid_run():
    """A dead stage controller fails the worker's next move and then the move back to the start position. The run
    must still report its end, or the GUI stays in "acquiring" with no acquisition left to abort."""
    import squid.exceptions

    scope, tt, mpc = _controller_with_tracker()
    real_move_x_to = scope.stage.move_x_to
    calls = []

    def move_x_to(x_mm, *args, **kwargs):
        calls.append(x_mm)
        if len(calls) > 2:
            raise squid.exceptions.SquidTimeout("stage stopped answering")
        return real_move_x_to(x_mm, *args, **kwargs)

    with patch.object(scope.stage, "move_x_to", side_effect=move_x_to):
        mpc.run_acquisition()
        assert tt.finished_event.wait(30)
        mpc.thread.join(10)

    assert len(calls) > 3, "the move back to the start position was not attempted"
    assert tt.finished_count == 1
    assert mpc.last_end_reason == "error"
    assert not mpc.acquisition_in_progress()


def test_a_failed_hardware_restore_does_not_skip_the_software_state(tmp_path):
    """Restoring the mode fails (e.g. the controller is gone). The steps that touch no device - the .done file, the
    camera's callback flag - must still happen, and the end of the run must still be reported."""
    import squid.exceptions

    scope, tt, mpc = _controller_with_tracker()
    mpc.scanCoordinates.remove_region("region_grid")
    mpc.set_base_path(str(tmp_path))
    mpc.start_new_experiment("acquisition", add_timestamp=False)
    scope.camera.enable_callbacks(False)  # run_acquisition() turns them on; the restore must turn them back off

    real_set_mode = mpc.liveController.set_microscope_mode

    def set_mode(*args, **kwargs):
        if mpc.multiPointWorker is not None and mpc.multiPointWorker.end_reason is not None:
            raise squid.exceptions.SquidTimeout("controller stopped answering")  # only the post-run restore
        return real_set_mode(*args, **kwargs)

    with patch.object(mpc.liveController, "set_microscope_mode", side_effect=set_mode):
        mpc.run_acquisition()
        assert tt.finished_event.wait(30)
        mpc.thread.join(10)

    assert tt.finished_count == 1
    assert mpc.last_end_reason == "completed"
    assert (tmp_path / "acquisition" / ".done").exists()
    assert scope.camera.get_callbacks_enabled() is False


@pytest.mark.parametrize("name", ["FROM BOTTOM", "FROM CENTER", "FROM TOP"])
def test_set_z_stacking_config_takes_the_config_name(name):
    from control.core.multi_point_controller import MultiPointController

    fake = SimpleNamespace(z_stacking_config="FROM BOTTOM", _log=MagicMock())
    MultiPointController.set_z_stacking_config(fake, name)
    assert fake.z_stacking_config == name


def test_set_z_stacking_config_rejects_an_unknown_name():
    from control.core.multi_point_controller import MultiPointController

    fake = SimpleNamespace(z_stacking_config="FROM BOTTOM", _log=MagicMock())
    with pytest.raises(ValueError, match="z_stacking_config"):
        MultiPointController.set_z_stacking_config(fake, "FROM NOWHERE")
    assert fake.z_stacking_config == "FROM BOTTOM"


# --- Set Range relative to the laser AF reference plane -------------------------------------------------------------


def _relative_validation_controller(
    *, laser_af=True, relative=(-5.0, 15.0), absolute=None, use_piezo=True, config="FROM BOTTOM", delta_z_mm=0.002
):
    """A bare controller with just the fields validate_acquisition_settings reads (a 300 um piezo scope)."""
    from control.piezo import PiezoStage

    c = MultiPointController.__new__(MultiPointController)
    c._log = MagicMock()
    c.do_reflection_af = laser_af
    c.use_piezo = use_piezo
    c.piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": 300})
    c.laserAutoFocusController = MagicMock()
    c.laserAutoFocusController.laser_af_properties.has_reference = True
    c.NZ = 3
    c.deltaZ = delta_z_mm
    c.z_stacking_config = config
    c.piezo_z_range_um = absolute
    c.laser_af_relative_range_um = relative
    return c


def _last_error(c):
    return c._log.error.call_args.args[0]


def test_start_accepts_laser_af_with_a_relative_range():
    assert _relative_validation_controller().validate_acquisition_settings() is True


def test_start_refuses_a_relative_range_with_laser_af_off():
    c = _relative_validation_controller(laser_af=False)
    assert c.validate_acquisition_settings() is False
    assert "needs laser AF" in _last_error(c)


def test_start_still_refuses_an_absolute_piezo_range_with_laser_af_on():
    c = _relative_validation_controller(relative=None, absolute=(20.0, 26.0))
    assert c.validate_acquisition_settings() is False
    assert "absolute piezo Z-range can't run with laser AF" in _last_error(c)


def test_start_refuses_relative_with_absolute_range_from_top_reversed_too_long_or_stepper_on_piezo_scope():
    both = _relative_validation_controller(absolute=(20.0, 26.0))
    assert both.validate_acquisition_settings() is False

    from_top = _relative_validation_controller(config="FROM TOP")
    assert from_top.validate_acquisition_settings() is False
    assert "A relative Z-range runs from Z-min up (From Bottom)" in _last_error(from_top)

    reversed_range = _relative_validation_controller(relative=(15.0, -5.0))
    assert reversed_range.validate_acquisition_settings() is False

    too_long = _relative_validation_controller(relative=(0.0, 400.0))  # 400 um on a 300 um piezo
    assert too_long.validate_acquisition_settings() is False

    stepper = _relative_validation_controller(use_piezo=False)
    assert stepper.validate_acquisition_settings() is False


@pytest.mark.parametrize("delta_z_mm", [0.0, -0.001, float("nan"), float("inf")])
def test_start_refuses_a_relative_range_with_invalid_spacing(delta_z_mm):
    c = _relative_validation_controller(delta_z_mm=delta_z_mm)
    assert c.validate_acquisition_settings() is False


def test_start_refuses_relative_range_with_per_region_offsets():
    c = _relative_validation_controller()
    assert c.validate_acquisition_settings({"A1": 3.0}) is False
    # Per-region offsets come only from the GUI's focus map panel, so the advice names its checkbox; per-region
    # Z-ranges don't exist in the GUI.
    assert (
        _last_error(c) == "Per-region laser AF offsets and a relative Z-range don't combine: uncheck Laser AF Offset "
        "in the focus map panel, or clear the Z-range and use From Bottom."
    )
    assert c.validate_acquisition_settings({}) is True


def _relative_run_controller(tmp_path):
    """A real controller with a 2 um step, range (-5, 15) -> 11 planes, NZ left at 3, 2 FOVs x 2 channels."""
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    mpc.set_base_path(str(tmp_path))
    limits = mpc.stage.get_config()
    x0, y0, z0 = limits.X_AXIS.MIN_POSITION + 1.0, limits.Y_AXIS.MIN_POSITION + 1.0, limits.Z_AXIS.MIN_POSITION + 1.0
    mpc.scanCoordinates.add_single_fov_region("r1", center_x=x0, center_y=y0, center_z=z0)
    mpc.scanCoordinates.add_single_fov_region("r2", center_x=x0 + 0.5, center_y=y0 + 0.5, center_z=z0)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_NZ(3)
    mpc.set_Nt(1)
    mpc.set_deltaZ(2.0)  # um
    mpc.z_stacking_config = "FROM BOTTOM"
    mpc.set_laser_af_relative_range_um((-5.0, 15.0))
    mpc.start_new_experiment("relative_run")
    mpc.timestamp_acquisition_started = 0.0
    return scope, mpc


def test_image_count_estimate_uses_the_derived_nz(tmp_path):
    scope, mpc = _relative_run_controller(tmp_path)
    try:
        assert mpc.effective_nz() == 11
        assert mpc.get_acquisition_image_count() == 44  # 11 planes x 2 FOVs x 2 channels, not 12
    finally:
        scope.close()


def test_parameters_json_and_run_state_use_the_derived_nz(tmp_path):
    scope, mpc = _relative_run_controller(tmp_path)
    try:
        params = json.loads((tmp_path / mpc.experiment_ID / "acquisition parameters.json").read_text())
        assert params["Nz"] == 11
        assert params["laser AF relative z-range (um)"] == [-5.0, 15.0]

        mpc.validate_acquisition_settings = lambda *_: True
        with patch("control.core.multi_point_controller.MultiPointWorker"), patch(
            "control.core.multi_point_controller.squid.acquisition_state.RunStateWriter.start"
        ) as start:
            mpc.run_acquisition()
        assert start.call_args.kwargs["expected"]["z"] == 11
    finally:
        scope.close()


def test_build_params_uses_the_derived_nz(tmp_path):
    scope, mpc = _relative_run_controller(tmp_path)
    try:
        sp = ScanPositionInformation(scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={})
        params = mpc.build_params(sp)
        assert params.NZ == 11
        assert params.laser_af_relative_range_um == (-5.0, 15.0)
        assert mpc.NZ == 3  # the field itself stays what was set
    finally:
        scope.close()


def test_saved_yaml_and_parameters_record_the_relative_range(tmp_path):
    from control.acquisition_yaml_loader import parse_acquisition_yaml
    from control.core.multi_point_controller import _save_acquisition_yaml

    scope, mpc = _relative_run_controller(tmp_path)
    try:
        mpc.set_reflection_af_flag(True)
        sp = ScanPositionInformation(scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={})
        params = dataclasses.replace(mpc.build_params(sp), z_range=(1.0, 1.0))
        out = tmp_path / "saved"
        out.mkdir()
        _save_acquisition_yaml(params, str(out))
        data = parse_acquisition_yaml(str(out))
        assert data.laser_af_relative_range_um == (-5.0, 15.0)
        assert data.nz == 11
    finally:
        scope.close()


def test_absolute_and_plain_runs_keep_the_nz_they_were_given(tmp_path):
    scope, mpc = _relative_run_controller(tmp_path)
    try:
        mpc.set_laser_af_relative_range_um(None)
        assert mpc.effective_nz() == 3
        assert mpc.get_acquisition_image_count() == 3 * 2 * 2
        mpc.set_piezo_z_range_um((20.0, 26.0))  # absolute mode: Nz is what the widget derived and set
        assert mpc.effective_nz() == 3
    finally:
        scope.close()
