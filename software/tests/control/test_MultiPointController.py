import copy
import dataclasses
import threading
from unittest.mock import patch

import pytest

import control._def
import control.microscope
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import MultiPointControllerFunctions, AcquisitionParameters

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


def _zarr_jsons_with_squid(mpc):
    """The _squid attributes of every OME group zarr.json of the run just finished (array zarr.json files carry none)."""
    import json
    from pathlib import Path

    out = []
    for p in (Path(mpc.base_path) / mpc.experiment_ID).rglob("zarr.json"):
        with open(p) as f:
            attrs = json.load(f).get("attributes", {}).get("_squid")
        if attrs is not None:
            out.append(attrs)
    return out


def _zarr_v3_controller(monkeypatch):
    """A simulated microscope and controller set up for a Zarr v3 multipoint run, with its tracker."""
    pytest.importorskip("tensorstore")
    control._def.MERGE_CHANNELS = False
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    return mpc, tt


def _make_zarr_writes_fail(monkeypatch, message="disk full"):
    """Every SaveZarrJob opens its store as usual (so there is a zarr.json to inspect) but its write fails."""
    import concurrent.futures
    from control.core.job_processing import SaveZarrJob

    real_submit = SaveZarrJob.submit

    def failing_submit(self):
        future, result = real_submit(self)
        if future is not None:
            future.result()
        failed = concurrent.futures.Future()
        failed.set_exception(RuntimeError(message))
        return failed, result

    monkeypatch.setattr(SaveZarrJob, "submit", failing_submit)


def test_zarr_v3_multipoint_saves_in_process_and_seals(monkeypatch):
    """A Zarr v3 multipoint run saves through the in-process runner and ends with acquisition_complete=True."""
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    mpc, tt = _zarr_v3_controller(monkeypatch)
    mpc.run_acquisition()
    assert tt.finished_event.wait(120)
    assert isinstance(mpc.multiPointWorker._job_runners[0][1], InProcessZarrRunner)
    attrs = _zarr_jsons_with_squid(mpc)
    assert attrs and all(a["acquisition_complete"] is True for a in attrs)


def test_zarr_is_sealed_with_multiprocessing_off(monkeypatch):
    """The in-process fallback used to leave the store unsealed; it must not any more."""
    monkeypatch.setattr(control._def.Acquisition, "USE_MULTIPROCESSING", False)
    mpc, tt = _zarr_v3_controller(monkeypatch)
    mpc.run_acquisition()
    assert tt.finished_event.wait(120)
    attrs = _zarr_jsons_with_squid(mpc)
    assert attrs and all(a["acquisition_complete"] is True for a in attrs)


def test_abort_mid_zarr_run_seals_store_aborted(monkeypatch):
    """Stop during a Zarr run: the run ends promptly and the stores say aborted, not complete."""
    import time

    mpc, tt = _zarr_v3_controller(monkeypatch)
    mpc.set_NZ(5)
    mpc.run_acquisition()
    assert tt.started_event.wait(30)
    deadline = time.monotonic() + 30
    while tt.image_count < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert tt.image_count >= 3
    t_abort = time.monotonic()
    mpc.request_abort_aquisition()
    assert tt.finished_event.wait(60)
    assert time.monotonic() - t_abort < 30, "abort must not wait out the job timeouts"
    attrs = _zarr_jsons_with_squid(mpc)
    assert attrs and all(a["acquisition_complete"] is False and a.get("aborted") is True for a in attrs)


def test_zarr_v3_run_leaves_the_warm_subprocess_for_tiff(monkeypatch):
    """A Zarr v3 acquisition neither consumes nor restarts the pre-warmed subprocess, and starts none of its own."""
    from control.core import job_processing

    mpc, tt = _zarr_v3_controller(monkeypatch)
    warm_before = mpc._prewarmed_job_runner
    assert warm_before is not None
    started = []  # only subprocesses started from here on count; the controller's own pre-warm is above
    monkeypatch.setattr(job_processing.JobRunner, "start", lambda self: started.append(self))
    mpc.run_acquisition()
    assert tt.finished_event.wait(120)
    assert started == [], "a Zarr v3 run must not start a save subprocess"
    assert mpc._prewarmed_job_runner is warm_before


def test_three_zarr_runs_in_one_session_all_sealed(monkeypatch):
    """Back-to-back Zarr runs in one process each get fresh writers and each store is sealed complete."""
    mpc, tt = _zarr_v3_controller(monkeypatch)
    for i in range(3):
        tt.started_event.clear()
        tt.finished_event.clear()
        mpc.start_new_experiment(f"zarr_session_{i}")
        mpc.run_acquisition()
        assert tt.finished_event.wait(120), f"run {i} did not finish"
        attrs = _zarr_jsons_with_squid(mpc)
        assert attrs and all(a["acquisition_complete"] is True for a in attrs), f"run {i}"


def test_zarr_write_error_ends_the_run_as_error(monkeypatch, caplog):
    """A failed Zarr write must end the run as an error and seal the store incomplete, not finish quietly."""
    import logging

    mpc, tt = _zarr_v3_controller(monkeypatch)
    _make_zarr_writes_fail(monkeypatch)
    with caplog.at_level(logging.ERROR):
        mpc.run_acquisition()
        assert tt.finished_event.wait(120)
    assert mpc.multiPointWorker._abort_cause == "error"
    assert any("disk full" in r.getMessage() for r in caplog.records)
    attrs = _zarr_jsons_with_squid(mpc)
    assert attrs and all(a["acquisition_complete"] is False for a in attrs)


def test_dead_tiff_subprocess_aborts_acquisition(caplog):
    """A save subprocess that dies with jobs pending must abort the run, not let it finish with data missing."""
    import logging
    import queue
    from unittest.mock import MagicMock
    from control.core.job_processing import SaveImageJob
    from control.core.multi_point_worker import MultiPointWorker

    worker = MultiPointWorker.__new__(MultiPointWorker)  # only _summarize_runner_outputs' own fields are needed
    worker._log = logging.getLogger("squid.MultiPointWorker")
    worker._acquisition_error_count = 0
    worker._slack_notifier = None
    runner = MagicMock()
    runner.output_queue.return_value = MagicMock(get_nowait=MagicMock(side_effect=queue.Empty))
    runner.is_alive.return_value = False
    runner.has_pending.return_value = True
    runner.exitcode = -6
    worker._job_runners = [(SaveImageJob, runner)]
    worker._abort_due_to_error = MagicMock()
    with caplog.at_level(logging.ERROR):
        worker._summarize_runner_outputs()
    worker._abort_due_to_error.assert_called_once()
    assert any("save subprocess exited" in r.getMessage() for r in caplog.records)


def test_close_shuts_down_in_process_runner_instead_of_terminating_it():
    """close() on an abnormal shutdown must seal an in-process runner's stores, not treat it as a process."""
    import logging
    from unittest.mock import MagicMock
    from control.core.in_process_zarr_runner import InProcessZarrRunner
    from control.core.job_processing import SaveZarrJob
    from control.core.multi_point_controller import MultiPointController

    mpc = MultiPointController.__new__(MultiPointController)  # close() only needs the fields set below
    mpc._log = logging.getLogger("squid.MultiPointController")
    mpc._prewarmed_job_runner = None
    mpc._prewarmed_bp_values = None
    mpc._memory_monitor = None
    mpc.thread = None
    runner = MagicMock(spec=InProcessZarrRunner)
    runner.is_alive.return_value = True
    runner.terminate = MagicMock()  # not part of InProcessZarrRunner; present only to prove it is not called
    worker = MagicMock()
    worker._job_runners = [(SaveZarrJob, runner)]
    mpc.multiPointWorker = worker
    mpc.close()
    runner.shutdown.assert_called_once_with(timeout_s=MultiPointController._PROCESS_TERMINATE_TIMEOUT_S, aborted=True)
    runner.terminate.assert_not_called()


def test_zarr_write_error_surfacing_at_the_end_ends_the_run_as_error(monkeypatch, caplog):
    """A failed Zarr write that only _finish_jobs' final drain sees must end the run as an error, store incomplete.

    Why this test exists: the per-FOV poll already aborts on a failure it sees; this one checks the failure that only
    the final drain sees, by holding every result back from the per-FOV poll (drain_all=False).
    """
    import logging
    from control.core.multi_point_worker import MultiPointWorker, SummarizeResult

    real_summarize = MultiPointWorker._summarize_runner_outputs

    def final_drain_only(self, drain_all=False):
        if not drain_all:  # the per-FOV poll in the acquisition loop sees nothing
            return SummarizeResult(none_failed=True, had_results=False)
        return real_summarize(self, drain_all=drain_all)

    mpc, tt = _zarr_v3_controller(monkeypatch)
    _make_zarr_writes_fail(monkeypatch)
    monkeypatch.setattr(MultiPointWorker, "_summarize_runner_outputs", final_drain_only)
    with caplog.at_level(logging.ERROR):
        mpc.run_acquisition()
        assert tt.finished_event.wait(120)
    assert mpc.multiPointWorker._abort_cause == "error"
    assert any("A save job failed, aborting acquisition" in r.getMessage() for r in caplog.records)
    attrs = _zarr_jsons_with_squid(mpc)
    assert attrs and all(a["acquisition_complete"] is False for a in attrs)
