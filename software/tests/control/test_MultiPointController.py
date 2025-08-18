import pytest
import threading

import tests.control.gui_test_stubs as gts
import control._def
import control.microscope
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import MultiPointControllerFunctions, AcquisitionParameters


def test_multi_point_controller_image_count_calculation(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)

    control._def.MERGE_CHANNELS = False
    all_configuration_names = [
        config.name
        for config in mpc.channelConfigurationManager.get_configurations(mpc.objectiveStore.current_objective)
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


def test_multi_point_controller_disk_space_estimate(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)

    control._def.MERGE_CHANNELS = False
    all_configuration_names = [
        config.name
        for config in mpc.channelConfigurationManager.get_configurations(mpc.objectiveStore.current_objective)
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
    all_config_names = [m.name for m in mpc.channelConfigurationManager.get_configurations(objective)]
    first_two_config_names = all_config_names[:2]
    mpc.set_selected_configurations(selected_configurations_name=first_two_config_names)


def test_multi_point_controller_basic_acquisition(qtbot):
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = gts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

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

    mpc = gts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

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


@pytest.mark.skip(reason="We still need to pull QT usage out of AutofocusController and AutofocusWorker.")
def test_multi_point_with_contrast_af():
    control._def.MERGE_CHANNELS = False

    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()

    mpc = gts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

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
