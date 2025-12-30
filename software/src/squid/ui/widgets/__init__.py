"""UI widgets package.

This package is used as a convenience namespace via `import squid.ui.widgets as widgets`.
To keep unit tests and headless imports lightweight, we lazily import widget classes
on attribute access instead of importing everything eagerly at module import time.
"""

from __future__ import annotations

import importlib
from typing import Any, Final

__all__ = [
    # base
    "error_dialog",
    "check_space_available_with_error_dialog",
    "WrapperWindow",
    "CollapsibleGroupBox",
    "PandasTableModel",
    # config
    "ConfigEditor",
    "ConfigEditorBackwardsCompatible",
    "PreferencesDialog",
    "ProfileWidget",
    # camera
    "CameraSettingsWidget",
    "LiveControlWidget",
    "RecordingWidget",
    "MultiCameraRecordingWidget",
    # stage
    "StageUtils",
    "PiezoWidget",
    "NavigationWidget",
    "AutoFocusWidget",
    # acquisition
    "FlexibleMultiPointWidget",
    "WellplateMultiPointWidget",
    "MultiPointWithFluidicsWidget",
    # display
    "StatsDisplayWidget",
    "FocusMapWidget",
    "NapariLiveWidget",
    "NapariMultiChannelWidget",
    "NapariMosaicDisplayWidget",
    "WaveformDisplay",
    "PlotWidget",
    "SurfacePlotWidget",
    # hardware
    "LaserAutofocusSettingWidget",
    "SpinningDiskConfocalWidget",
    "DragonflyConfocalWidget",
    "ObjectivesWidget",
    "DACControWidget",
    "FilterControllerWidget",
    "TriggerControlWidget",
    "LaserAutofocusControlWidget",
    "LedMatrixSettingsDialog",
    # wellplate
    "WellSelectionWidget",
    "WellplateFormatWidget",
    "WellplateCalibration",
    "CalibrationLiveViewer",
    "Well1536SelectionWidget",
    "SampleSettingsWidget",
    # fluidics
    "FluidicsWidget",
    # tracking
    "TrackingControllerWidget",
    "PlateReaderAcquisitionWidget",
    "PlateReaderNavigationWidget",
    "DisplacementMeasurementWidget",
    "Joystick",
    # nl5
    "NL5Widget",
    "NL5SettingsDialog",
    # custom_multipoint
    "TemplateMultiPointWidget",
    # spectrometer
    "SpectrometerControlWidget",
    "SpectrumDisplay",
]

_LAZY_IMPORTS: Final[dict[str, tuple[str, str]]] = {
    # base
    "error_dialog": ("squid.ui.widgets.base", "error_dialog"),
    "check_space_available_with_error_dialog": ("squid.ui.widgets.base", "check_space_available_with_error_dialog"),
    "WrapperWindow": ("squid.ui.widgets.base", "WrapperWindow"),
    "CollapsibleGroupBox": ("squid.ui.widgets.base", "CollapsibleGroupBox"),
    "PandasTableModel": ("squid.ui.widgets.base", "PandasTableModel"),
    # config
    "ConfigEditor": ("squid.ui.widgets.config", "ConfigEditor"),
    "ConfigEditorBackwardsCompatible": ("squid.ui.widgets.config", "ConfigEditorBackwardsCompatible"),
    "PreferencesDialog": ("squid.ui.widgets.config", "PreferencesDialog"),
    "ProfileWidget": ("squid.ui.widgets.config", "ProfileWidget"),
    # camera
    "CameraSettingsWidget": ("squid.ui.widgets.camera", "CameraSettingsWidget"),
    "LiveControlWidget": ("squid.ui.widgets.camera", "LiveControlWidget"),
    "RecordingWidget": ("squid.ui.widgets.camera", "RecordingWidget"),
    "MultiCameraRecordingWidget": ("squid.ui.widgets.camera", "MultiCameraRecordingWidget"),
    # stage
    "StageUtils": ("squid.ui.widgets.stage", "StageUtils"),
    "PiezoWidget": ("squid.ui.widgets.stage", "PiezoWidget"),
    "NavigationWidget": ("squid.ui.widgets.stage", "NavigationWidget"),
    "AutoFocusWidget": ("squid.ui.widgets.stage", "AutoFocusWidget"),
    # acquisition
    "FlexibleMultiPointWidget": ("squid.ui.widgets.acquisition", "FlexibleMultiPointWidget"),
    "WellplateMultiPointWidget": ("squid.ui.widgets.acquisition", "WellplateMultiPointWidget"),
    "MultiPointWithFluidicsWidget": ("squid.ui.widgets.acquisition", "MultiPointWithFluidicsWidget"),
    # display
    "StatsDisplayWidget": ("squid.ui.widgets.display", "StatsDisplayWidget"),
    "FocusMapWidget": ("squid.ui.widgets.display", "FocusMapWidget"),
    "NapariLiveWidget": ("squid.ui.widgets.display", "NapariLiveWidget"),
    "NapariMultiChannelWidget": ("squid.ui.widgets.display", "NapariMultiChannelWidget"),
    "NapariMosaicDisplayWidget": ("squid.ui.widgets.display", "NapariMosaicDisplayWidget"),
    "WaveformDisplay": ("squid.ui.widgets.display", "WaveformDisplay"),
    "PlotWidget": ("squid.ui.widgets.display", "PlotWidget"),
    "SurfacePlotWidget": ("squid.ui.widgets.display", "SurfacePlotWidget"),
    # hardware
    "LaserAutofocusSettingWidget": ("squid.ui.widgets.hardware", "LaserAutofocusSettingWidget"),
    "SpinningDiskConfocalWidget": ("squid.ui.widgets.hardware", "SpinningDiskConfocalWidget"),
    "DragonflyConfocalWidget": ("squid.ui.widgets.hardware", "DragonflyConfocalWidget"),
    "ObjectivesWidget": ("squid.ui.widgets.hardware", "ObjectivesWidget"),
    "DACControWidget": ("squid.ui.widgets.hardware", "DACControWidget"),
    "FilterControllerWidget": ("squid.ui.widgets.hardware", "FilterControllerWidget"),
    "TriggerControlWidget": ("squid.ui.widgets.hardware", "TriggerControlWidget"),
    "LaserAutofocusControlWidget": ("squid.ui.widgets.hardware", "LaserAutofocusControlWidget"),
    "LedMatrixSettingsDialog": ("squid.ui.widgets.hardware", "LedMatrixSettingsDialog"),
    # wellplate
    "WellSelectionWidget": ("squid.ui.widgets.wellplate", "WellSelectionWidget"),
    "WellplateFormatWidget": ("squid.ui.widgets.wellplate", "WellplateFormatWidget"),
    "WellplateCalibration": ("squid.ui.widgets.wellplate", "WellplateCalibration"),
    "CalibrationLiveViewer": ("squid.ui.widgets.wellplate", "CalibrationLiveViewer"),
    "Well1536SelectionWidget": ("squid.ui.widgets.wellplate", "Well1536SelectionWidget"),
    "SampleSettingsWidget": ("squid.ui.widgets.wellplate", "SampleSettingsWidget"),
    # fluidics
    "FluidicsWidget": ("squid.ui.widgets.fluidics", "FluidicsWidget"),
    # tracking
    "TrackingControllerWidget": ("squid.ui.widgets.tracking", "TrackingControllerWidget"),
    "PlateReaderAcquisitionWidget": ("squid.ui.widgets.tracking", "PlateReaderAcquisitionWidget"),
    "PlateReaderNavigationWidget": ("squid.ui.widgets.tracking", "PlateReaderNavigationWidget"),
    "DisplacementMeasurementWidget": ("squid.ui.widgets.tracking", "DisplacementMeasurementWidget"),
    "Joystick": ("squid.ui.widgets.tracking", "Joystick"),
    # nl5
    "NL5Widget": ("squid.ui.widgets.nl5", "NL5Widget"),
    "NL5SettingsDialog": ("squid.ui.widgets.nl5", "NL5SettingsDialog"),
    # custom multipoint
    "TemplateMultiPointWidget": ("squid.ui.widgets.custom_multipoint", "TemplateMultiPointWidget"),
    # spectrometer
    "SpectrometerControlWidget": ("squid.ui.widgets.spectrometer", "SpectrometerControlWidget"),
    "SpectrumDisplay": ("squid.ui.widgets.spectrometer", "SpectrumDisplay"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

