import pathlib
from typing import TYPE_CHECKING

import squid.backend.microscope as microscope_module
import squid.backend.microcontroller as microcontroller_module

from squid.backend.managers import ChannelConfigurationManager, ConfigurationManager, ObjectiveStore
from squid.backend.microcontroller import Microcontroller
from squid.ui.widgets.display.navigation_viewer import NavigationViewer
from squid.backend.controllers.autofocus import LaserAFSettingManager
from squid.backend.controllers.multipoint import MultiPointController
from squid.backend.microscope import Microscope
from squid.backend.drivers.lighting.led import IlluminationController, IntensityControlMode, ShutterControlMode
from tests.tools import get_repo_root
import tests.control.test_stubs as ts


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
    microcontroller: Microcontroller,
) -> IlluminationController:
    return IlluminationController(
        microcontroller=microcontroller,
        intensity_control_mode=IntensityControlMode.Software,
        shutter_control_mode=ShutterControlMode.Software,
    )


def get_test_navigation_viewer(
    objective_store: ObjectiveStore,
    camera_pixel_size: float,
):
    return NavigationViewer(objective_store, camera_pixel_size)


def get_test_multi_point_controller(
    microscope: Microscope,
) -> MultiPointController:
    """Create a MultiPointController with signal bridge for testing.

    This replaces get_test_qt_multi_point_controller since QtMultiPointController
    has been removed. The signal bridge provides the same Qt signal functionality.
    """
    live_controller = ts.get_test_live_controller(
        microscope=microscope,
        starting_objective=microscope.objective_store.default_objective,
    )

    multi_point_controller = MultiPointController(
        microscope=microscope,
        live_controller=live_controller,
        autofocus_controller=ts.get_test_autofocus_controller(
            microscope.camera,
            microscope.stage,
            live_controller,
            microscope.low_level_drivers.microcontroller,
        ),
        channel_configuration_manager=microscope.channel_configuration_manager,
        scan_coordinates=ts.get_test_scan_coordinates(
            microscope.objective_store, microscope.stage, microscope.camera
        ),
        objective_store=microscope.objective_store,
        laser_autofocus_controller=ts.get_test_laser_autofocus_controller(microscope),
    )

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller


# Backwards compatibility alias
get_test_qt_multi_point_controller = get_test_multi_point_controller
