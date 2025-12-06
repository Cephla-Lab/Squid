# Hardware control widgets
# This package contains widgets for controlling microscope hardware components

from control.widgets.hardware.laser_autofocus import (
    LaserAutofocusSettingWidget,
    LaserAutofocusControlWidget,
)
from control.widgets.hardware.confocal import (
    SpinningDiskConfocalWidget,
    DragonflyConfocalWidget,
)
from control.widgets.hardware.objectives import ObjectivesWidget
from control.widgets.hardware.dac import DACControWidget
from control.widgets.hardware.filter_controller import FilterControllerWidget
from control.widgets.hardware.trigger import TriggerControlWidget
from control.widgets.hardware.led_matrix import LedMatrixSettingsDialog

__all__ = [
    "LaserAutofocusSettingWidget",
    "LaserAutofocusControlWidget",
    "SpinningDiskConfocalWidget",
    "DragonflyConfocalWidget",
    "ObjectivesWidget",
    "DACControWidget",
    "FilterControllerWidget",
    "TriggerControlWidget",
    "LedMatrixSettingsDialog",
]
