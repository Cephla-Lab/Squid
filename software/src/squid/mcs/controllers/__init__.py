"""
Controller layer for Squid microscopy software.

Controllers orchestrate workflows and manage state.
They subscribe to command events and publish state events.

Available controllers:
- MicroscopeModeController: Manages microscope channel/mode switching
- PeripheralsController: Manages objective, spinning disk, piezo
- ImageClickController: Converts image clicks to stage movements
"""

from .microscope_mode_controller import MicroscopeModeController
from .peripherals_controller import PeripheralsController
from .image_click_controller import ImageClickController

__all__ = [
    "MicroscopeModeController",
    "PeripheralsController",
    "ImageClickController",
]
