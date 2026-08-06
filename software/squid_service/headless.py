"""Build a fully wired SquidCoreService without the GUI.

The HCS GUI wires MultiPointController and friends in gui_hcs.load_objects()
using Qt subclasses (QtMultiPointController, QtAutoFocusController). This module
performs the same wiring with the Qt-free base classes so the REST API can run
in a process that never creates a QApplication (see main_headless.py).
"""

from pathlib import Path
from typing import Optional

import control._def
from control.core.auto_focus_controller import AutoFocusController
from control.core.multi_point_controller import MultiPointController, NoOpCallbacks
from control.core.scan_coordinates import ScanCoordinates
from control.microscope import Microscope

from squid_service.service import SquidCoreService


def create_headless_service(
    microscope: Microscope,
    simulation: bool = False,
    job_persist_path: Optional[Path] = None,
    methods_dir: Optional[Path] = None,
) -> SquidCoreService:
    autofocus_controller = AutoFocusController(
        camera=microscope.camera,
        stage=microscope.stage,
        liveController=microscope.live_controller,
        microcontroller=microscope.low_level_drivers.microcontroller,
        finished_fn=lambda: None,
        image_to_display_fn=lambda image: None,
        nl5=microscope.addons.nl5,
    )
    scan_coordinates = ScanCoordinates(
        objectiveStore=microscope.objective_store,
        stage=microscope.stage,
        camera=microscope.camera,
    )
    # The GUI's live widget selects a default channel at startup; without one,
    # MultiPointController's end-of-acquisition reset restores a None mode.
    if microscope.live_controller.currentConfiguration is None:
        channels = microscope.live_controller.get_channels(microscope.objective_store.current_objective)
        if channels:
            microscope.live_controller.set_microscope_mode(channels[0])
    laser_af_controller = None
    if control._def.SUPPORT_LASER_AUTOFOCUS and microscope.addons.camera_focus:
        # Populate the microscope's own lazily-initialized controller so the
        # service's autofocus endpoints (microscope.laser_autofocus_controller)
        # and the acquisition worker share one instance, as in the GUI.
        microscope._ensure_laser_af_controller()
        laser_af_controller = microscope.laser_autofocus_controller
    multipoint_controller = MultiPointController(
        microscope,
        microscope.live_controller,
        autofocus_controller,
        microscope.objective_store,
        callbacks=NoOpCallbacks,
        scan_coordinates=scan_coordinates,
        laser_autofocus_controller=laser_af_controller,
    )
    return SquidCoreService(
        microscope=microscope,
        multipoint_controller=multipoint_controller,
        scan_coordinates=scan_coordinates,
        simulation=simulation,
        job_persist_path=job_persist_path,
        methods_dir=methods_dir,
    )
