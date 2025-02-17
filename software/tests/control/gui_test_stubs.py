import os
import pathlib

import control.core.core
import control.microcontroller
import control.lighting
import control.camera
import squid.stage.cephla
from squid.config import get_stage_config
import squid.abc
import git

import control._def


def get_test_microcontroller() -> control.microcontroller.Microcontroller:
    return control.microcontroller.Microcontroller(control.microcontroller.SimSerial(), True)


def get_test_camera():
    return control.camera.Camera_Simulation()


def get_test_live_controller(
    camera, microcontroller, config_manager, illumination_controller
) -> control.core.core.LiveController:
    controller = control.core.core.LiveController(camera, microcontroller, config_manager, illumination_controller)
    controller.set_microscope_mode(config_manager.configurations[0])

    return controller


def get_test_stage(microcontroller):
    return squid.stage.cephla.CephlaStage(microcontroller=microcontroller, stage_config=get_stage_config())


def get_repo_root() -> pathlib.Path:
    git_repo = git.Repo(os.getcwd(), search_parent_directories=True)
    git_root = git_repo.git.rev_parse("--show-toplevel")

    return pathlib.Path(git_root).absolute()


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
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller
