import pathlib

import control.microscope
import control.core.objective_store
import control.microcontroller
import control.lighting
import squid.abc

from control._def import OBJECTIVES, DEFAULT_OBJECTIVE

from control.core.auto_focus_controller import AutoFocusController
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.configuration_mananger import ConfigurationManager
from control.core.core import NavigationViewer
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.core.multi_point_controller import MultiPointController, NoOpCallbacks
from control.core.multi_point_utils import MultiPointControllerFunctions
from control.core.scan_coordinates import ScanCoordinates
from control.gui_hcs import QtMultiPointController
from control.microscope import Microscope
from tests.tools import get_repo_root


def get_test_live_controller(microscope: control.microscope.Microscope, starting_objective) -> LiveController:
    controller = LiveController(microscope=microscope, camera=microscope.camera)

    controller.set_microscope_mode(
        microscope.configuration_manager.channel_manager.get_configurations(objective=starting_objective)[0]
    )
    return controller


def get_test_configuration_manager_path() -> pathlib.Path:
    return get_repo_root() / "acquisition_configurations"


def get_test_configuration_manager() -> ConfigurationManager:
    channel_manager = ChannelConfigurationManager()
    laser_af_manager = LaserAFSettingManager()
    return ConfigurationManager(
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
    live_controller: LiveController,
    microcontroller: control.microcontroller.Microcontroller,
):
    return AutoFocusController(
        camera=camera, stage=stage, liveController=live_controller, microcontroller=microcontroller, nl5=None
    )


def get_test_laser_autofocus_controller(microscope: Microscope):
    return LaserAutofocusController(
        microcontroller=microscope.low_level_drivers.microcontroller,
        camera=microscope.addons.camera_focus,
        liveController=LiveController(microscope=microscope, camera=microscope.addons.camera_focus),
        stage=microscope.stage,
        piezo=microscope.addons.piezo_stage,
        objectiveStore=microscope.objective_store,
        laserAFSettingManager=microscope.laser_af_settings_manager,
    )


def get_test_scan_coordinates(
    objective_store: control.core.objective_store.ObjectiveStore,
    navigation_viewer: NavigationViewer,
    stage: squid.abc.AbstractStage,
):
    return ScanCoordinates(objective_store, navigation_viewer, stage)


def get_test_objective_store():
    return control.core.objective_store.ObjectiveStore(objectives_dict=OBJECTIVES, default_objective=DEFAULT_OBJECTIVE)


def get_test_navigation_viewer(objective_store: control.core.objective_store.ObjectiveStore, camera_pixel_size: float):
    return NavigationViewer(objective_store, camera_pixel_size)


def get_test_qt_multi_point_controller(microscope: Microscope) -> QtMultiPointController:
    live_controller = get_test_live_controller(
        microscope=microscope, starting_objective=microscope.objective_store.default_objective
    )

    multi_point_controller = QtMultiPointController(
        microscope=microscope,
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
        objective_store=microscope.objective_store,
        laser_autofocus_controller=get_test_laser_autofocus_controller(microscope),
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment (qt)")

    return multi_point_controller


def get_test_multi_point_controller(
    microscope: Microscope, callbacks: MultiPointControllerFunctions = NoOpCallbacks
) -> MultiPointController:
    live_controller = get_test_live_controller(
        microscope=microscope, starting_objective=microscope.objective_store.default_objective
    )

    multi_point_controller = MultiPointController(
        microscope=microscope,
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
        callbacks=callbacks,
        objective_store=microscope.objective_store,
        laser_autofocus_controller=get_test_laser_autofocus_controller(microscope),
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller
