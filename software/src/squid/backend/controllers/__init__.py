"""
Controller layer for Squid microscopy software.

Controllers orchestrate workflows and manage state.
They subscribe to command events and publish state events.
"""

# Core controllers
from squid.backend.controllers.microscope_mode_controller import MicroscopeModeController
from squid.backend.controllers.peripherals_controller import PeripheralsController
from squid.backend.controllers.image_click_controller import ImageClickController
from squid.backend.controllers.live_controller import LiveController
from squid.backend.controllers.tracking_controller import TrackingControllerCore

# Autofocus controllers
from squid.backend.controllers.autofocus import (
    AutoFocusController,
    LaserAutofocusController,
)

# Multipoint acquisition controllers
from squid.backend.controllers.multipoint import (
    MultiPointController,
    MultiPointWorker,
)

__all__ = [
    # Core
    "MicroscopeModeController",
    "PeripheralsController",
    "ImageClickController",
    "LiveController",
    "TrackingControllerCore",
    # Autofocus
    "AutoFocusController",
    "LaserAutofocusController",
    # Multipoint
    "MultiPointController",
    "MultiPointWorker",
]
