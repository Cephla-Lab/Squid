# Hardware control widgets
# This package contains widgets for controlling microscope hardware components

from squid.ui.widgets.hardware.laser_autofocus import (
    LaserAutofocusSettingWidget,
    LaserAutofocusControlWidget,
)
from squid.ui.widgets.hardware.confocal import (
    SpinningDiskConfocalWidget,
    DragonflyConfocalWidget,
)
from squid.ui.widgets.hardware.objectives import ObjectivesWidget
from squid.ui.widgets.hardware.dac import DACControWidget
from squid.ui.widgets.hardware.filter_controller import FilterControllerWidget
from squid.ui.widgets.hardware.trigger import TriggerControlWidget
from squid.ui.widgets.hardware.led_matrix import LedMatrixSettingsDialog

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
