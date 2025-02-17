import pathlib

import control.core.core
import control.microcontroller
import control.lighting
import control.camera
import squid.abc

import control._def
from control.piezo import PiezoStage
from tests.tools import get_test_microcontroller, get_test_camera, get_test_stage, get_repo_root, get_test_piezo_stage


def get_test_live_controller(
    camera, microcontroller, config_manager, illumination_controller
) -> control.core.core.LiveController:
    controller = control.core.core.LiveController(camera, microcontroller, config_manager, illumination_controller)
    controller.set_microscope_mode(config_manager.configurations[0])

    return controller


def get_test_configuration_manager_path() -> pathlib.Path:
    return get_repo_root() / "channel_configurations.xml"


def get_test_configuration_manager() -> control.core.core.ConfigurationManager:
    return control.core.core.ConfigurationManager(get_test_configuration_manager_path())


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
        camera=camera, stage=stage, liveController=live_controller, microcontroller=microcontroller
    )


def get_test_scan_coordinates(
    objective_store: control.core.core.ObjectiveStore,
    navigation_viewer: control.core.core.NavigationViewer,
    stage: squid.abc.AbstractStage,
):
    return control.core.core.ScanCoordinates(objective_store, navigation_viewer, stage)


def get_test_objective_store():
    return control.core.core.ObjectiveStore(
        objectives_dict=control._def.OBJECTIVES, default_objective=control._def.DEFAULT_OBJECTIVE
    )


def get_test_navigation_viewer(objective_store: control.core.core.ObjectiveStore):
    return control.core.core.NavigationViewer(objective_store)


def get_test_multi_point_controller() -> control.core.core.MultiPointController:
    microcontroller = get_test_microcontroller()
    camera = get_test_camera()
    stage = get_test_stage(microcontroller)
    config_manager = get_test_configuration_manager()
    live_controller = get_test_live_controller(
        camera, microcontroller, config_manager, get_test_illumination_controller(microcontroller)
    )
    objective_store = get_test_objective_store()

    multi_point_controller = control.core.core.MultiPointController(
        camera=camera,
        stage=stage,
        microcontroller=microcontroller,
        liveController=live_controller,
        autofocusController=get_test_autofocus_controller(camera, stage, live_controller, microcontroller),
        configurationManager=get_test_configuration_manager(),
        scanCoordinates=get_test_scan_coordinates(objective_store, get_test_navigation_viewer(objective_store), stage),
        piezo=get_test_piezo_stage(microcontroller),
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller
