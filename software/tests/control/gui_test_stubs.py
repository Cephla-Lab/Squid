import pathlib

import control.microscope
import control.core.core
import control.core.objective_store
import control.microcontroller
import control.lighting
import squid.abc

import control._def
from control.microscope import Microscope
from control.core.multi_point_controller import MultiPointController
from tests.tools import get_repo_root, get_test_piezo_stage


def get_test_live_controller(
    microscope: control.microscope.Microscope, starting_objective
) -> control.core.core.LiveController:
    controller = control.core.core.LiveController(microscope=microscope)

    controller.set_microscope_mode(
        microscope.configuration_manager.channel_manager.get_configurations(objective=starting_objective)[0]
    )
    return controller


def get_test_configuration_manager_path() -> pathlib.Path:
    return get_repo_root() / "acquisition_configurations"


def get_test_configuration_manager() -> control.core.core.ConfigurationManager:
    channel_manager = control.core.core.ChannelConfigurationManager()
    laser_af_manager = control.core.core.LaserAFSettingManager()
    return control.core.core.ConfigurationManager(
        channel_manager=channel_manager,
        laser_af_manager=laser_af_manager,
        base_config_path=get_test_configuration_manager_path(),
    )


def get_test_illumination_controller(
    microcontroller: control.microcontroller.Microcontroller,
) -> control.lighting.IlluminationController:
    return control.lighting.IlluminationController(
        microcontroller=microcontroller,
        intensity_control_mode=control.lighting.IntensityControlMode.Software,
        shutter_control_mode=control.lighting.ShutterControlMode.Software,
    )


def get_test_autofocus_controller(
    camera,
    stage: squid.abc.AbstractStage,
    live_controller: control.core.core.LiveController,
    microcontroller: control.microcontroller.Microcontroller,
):
    return control.core.core.AutoFocusController(
        camera=camera, stage=stage, liveController=live_controller, microcontroller=microcontroller, nl5=None
    )


def get_test_scan_coordinates(
    objective_store: control.core.objective_store.ObjectiveStore,
    navigation_viewer: control.core.core.NavigationViewer,
    stage: squid.abc.AbstractStage,
):
    return control.core.core.ScanCoordinates(objective_store, navigation_viewer, stage)


def get_test_objective_store():
    return control.core.objective_store.ObjectiveStore(
        objectives_dict=control._def.OBJECTIVES, default_objective=control._def.DEFAULT_OBJECTIVE
    )


def get_test_navigation_viewer(objective_store: control.core.objective_store.ObjectiveStore, camera_pixel_size: float):
    return control.core.core.NavigationViewer(objective_store, camera_pixel_size)


def get_test_multi_point_controller(microscope: Microscope) -> MultiPointController:
    live_controller = get_test_live_controller(
        microscope=microscope, starting_objective=microscope.objective_store.default_objective
    )

    multi_point_controller = MultiPointController(
        camera=microscope.camera,
        stage=microscope.stage,
        microcontroller=microscope.low_level_drivers.microcontroller,
        live_controller=live_controller,
        autofocus_controller=get_test_autofocus_controller(
            microscope.camera, microscope.stage, live_controller, microscope.low_level_drivers.microcontroller
        ),
        channel_configuration_manager=microscope.channel_configuration_manager,
        scan_coordinates=get_test_scan_coordinates(
            microscope.objective_store,
            get_test_navigation_viewer(microscope.objective_store, microscope.camera.get_pixel_size_unbinned_um()),
            microscope.stage,
        ),
        piezo=get_test_piezo_stage(microscope.low_level_drivers.microcontroller),
        objective_store=microscope.objective_store,
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller
