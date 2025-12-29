import pathlib

import control.microscope
import control.core.objective_store
import control.microcontroller
import control.lighting

from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.configuration_mananger import ConfigurationManager
from control.core.core import NavigationViewer
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.gui_hcs import QtMultiPointController
from control.microscope import Microscope
from tests.tools import get_repo_root
import tests.control.test_stubs as ts


def get_test_configuration_mananger_path() -> pathlib.Path:
    return get_repo_root() / "acquisition_configurations"


def get_test_configuration_mananger() -> ConfigurationManager:
    channel_manager = ChannelConfigurationManager()
    laser_af_manager = LaserAFSettingManager()
    return ConfigurationManager(
        channel_manager=channel_manager,
        laser_af_manager=laser_af_manager,
        base_config_path=get_test_configuration_mananger_path(),
    )


def get_test_illumination_controller(
    microcontroller: control.microcontroller.Microcontroller,
) -> control.lighting.IlluminationController:
    return control.lighting.IlluminationController(
        microcontroller=microcontroller,
        intensity_control_mode=control.lighting.IntensityControlMode.Software,
        shutter_control_mode=control.lighting.ShutterControlMode.Software,
    )


def get_test_navigation_viewer(objective_store: control.core.objective_store.ObjectiveStore, camera_pixel_size: float):
    return NavigationViewer(objective_store, camera_pixel_size)


def get_test_qt_multi_point_controller(microscope: Microscope) -> QtMultiPointController:
    live_controller = ts.get_test_live_controller(
        microscope=microscope, starting_objective=microscope.objective_store.default_objective
    )

    multi_point_controller = QtMultiPointController(
        microscope=microscope,
        live_controller=live_controller,
        autofocus_controller=ts.get_test_autofocus_controller(
            microscope.camera, microscope.stage, live_controller, microscope.low_level_drivers.microcontroller
        ),
        channel_configuration_mananger=microscope.channel_configuration_mananger,
        scan_coordinates=ts.get_test_scan_coordinates(microscope.objective_store, microscope.stage, microscope.camera),
        objective_store=microscope.objective_store,
        laser_autofocus_controller=ts.get_test_laser_autofocus_controller(microscope),
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment (qt)")

    return multi_point_controller
