"""
Controller layer for Squid microscopy software.

Controllers orchestrate workflows and manage state.
They subscribe to command events and publish state events.
"""

# Base class
from squid.backend.controllers.base import BaseController

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
    ContinuousFocusLockController,
)

# Multipoint acquisition controllers
from squid.backend.controllers.multipoint import (
    MultiPointController,
    MultiPointWorker,
)

# Fluidics controller
from squid.backend.controllers.fluidics_controller import (
    FluidicsController,
    FluidicsControllerState,
)

__all__ = [
    # Base
    "BaseController",
    # Core
    "MicroscopeModeController",
    "PeripheralsController",
    "ImageClickController",
    "LiveController",
    "TrackingControllerCore",
    # Autofocus
    "AutoFocusController",
    "LaserAutofocusController",
    "ContinuousFocusLockController",
    # Multipoint
    "MultiPointController",
    "MultiPointWorker",
    # Fluidics
    "FluidicsController",
    "FluidicsControllerState",
]
