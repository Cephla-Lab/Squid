"""
Controller layer for Squid microscopy software.

Controllers orchestrate workflows and manage state.
They subscribe to command events and publish state events.

Available controllers:
- MicroscopeModeController: Manages microscope channel/mode switching
- PeripheralsController: Manages objective, spinning disk, piezo
"""

from .microscope_mode_controller import MicroscopeModeController
from .peripherals_controller import PeripheralsController

__all__ = [
    "MicroscopeModeController",
    "PeripheralsController",
]
