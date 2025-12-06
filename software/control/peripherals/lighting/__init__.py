# Lighting peripherals module
from control.peripherals.lighting.led import (
    IlluminationController,
    LightSourceType,
    IntensityControlMode,
    ShutterControlMode,
)
from control.peripherals.lighting.xlight import XLight, XLight_Simulation
from control.peripherals.lighting.dragonfly import Dragonfly, Dragonfly_Simulation
from control.peripherals.lighting.ldi import LDI, LDI_Simulation
from control.peripherals.lighting.cellx import CellX, CellX_Simulation
from control.peripherals.lighting.sci_led_array import (
    SciMicroscopyLEDArray,
    SciMicroscopyLEDArray_Simulation,
)
from control.peripherals.lighting.celesta import CELESTA

__all__ = [
    # LED/base illumination
    "IlluminationController",
    "LightSourceType",
    "IntensityControlMode",
    "ShutterControlMode",
    # Spinning disk / confocal
    "XLight",
    "XLight_Simulation",
    "Dragonfly",
    "Dragonfly_Simulation",
    # LDI
    "LDI",
    "LDI_Simulation",
    # CellX
    "CellX",
    "CellX_Simulation",
    # LED array
    "SciMicroscopyLEDArray",
    "SciMicroscopyLEDArray_Simulation",
    # Celesta
    "CELESTA",
]
