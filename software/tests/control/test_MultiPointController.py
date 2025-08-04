import threading

import tests.control.gui_test_stubs as gts
import control._def
import control.microscope
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

def test_multi_point_controller_basic_acquisition():
    scope = control.microscope.Microscope.build_from_global_config(True)
    start_pos = scope.stage.get_pos()

    max_x = scope.stage.get_config().X_AXIS.MAX_POSITION
    max_y = scope.stage.get_config().Y_AXIS.MAX_POSITION
    max_z = scope.stage.get_config().Z_AXIS.MAX_POSITION

    started_event = threading.Event()
    def mark_started(params: AcquisitionParameters):
        started_event.set()

    finished_event = threading.Event()
    def mark_finished():
        finished_event.set()

    image_count = 0
    def count_images(frame, info):
        nonlocal image_count
        image_count += 1

    config_change_count = 0
    def count_configuration_changes(config):
        nonlocal config_change_count
        config_change_count += 1

    current_fovs_count = 0
    def count_current_fovs(x_mm, y_mm):
        nonlocal current_fovs_count
        current_fovs_count += 1

    overall_progress_seen = False
    def mark_overall_progress(progress):
        nonlocal overall_progress_seen
        overall_progress_seen = True

    region_progress_seen = False
    def mark_region_progress(progress):
        nonlocal region_progress_seen
        region_progress_seen = True


    test_mpc_callbacks = MultiPointControllerFunctions(
        signal_acquisition_start=mark_started,
        signal_acquisition_finished=mark_finished,
        signal_new_image=count_images,
        signal_current_configuration=count_configuration_changes,
        signal_current_fov=count_current_fovs,
        signal_overall_progress=mark_overall_progress,
        signal_region_progress=mark_region_progress
    )

    mpc = gts.get_test_multi_point_controller(microscope=scope, callbacks=test_mpc_callbacks)
    mpc.scanCoordinates.add_single_fov_region("region_1", center_x=start_pos.x_mm, center_y=start_pos.y_mm, center_z=start_pos.z_mm)
    mpc.scanCoordinates.add_single_fov_region("region_2", center_x=start_pos.x_mm + 0.5, center_y=start_pos.y_mm + 0.5, center_z=start_pos.z_mm + 0.1)
    mpc.scanCoordinates.add_flexible_region("region_grid", max_x / 2.0, max_y / 2.0, max_z / 2.0, 3, 3, 10)

    all_config_names = [m.name for m in mpc.channelConfigurationManager.get_configurations(scope.objective_store.current_objective)]
    first_two_config_names = all_config_names[:2]

    mpc.set_selected_configurations(selected_configurations_name=first_two_config_names)

    mpc.run_acquisition()

    timeout_s = 5

    assert started_event.wait(timeout_s)
    assert finished_event.wait(timeout_s)

    assert overall_progress_seen
    assert region_progress_seen

    assert image_count == mpc.get_acquisition_image_count()
    assert current_fovs_count > 0
    assert config_change_count > 0
