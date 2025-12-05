# GUI widgets module
from control.widgets.base import (
    error_dialog,
    check_space_available_with_error_dialog,
    WrapperWindow,
    CollapsibleGroupBox,
    PandasTableModel,
)
from control.widgets.config import (
    ConfigEditor,
    ConfigEditorBackwardsCompatible,
    ProfileWidget,
)
from control.widgets.camera import (
    CameraSettingsWidget,
    LiveControlWidget,
    RecordingWidget,
    MultiCameraRecordingWidget,
)
from control.widgets.stage import (
    StageUtils,
    PiezoWidget,
    NavigationWidget,
    AutoFocusWidget,
)
from control.widgets.acquisition import (
    FlexibleMultiPointWidget,
    WellplateMultiPointWidget,
    MultiPointWithFluidicsWidget,
)
from control.widgets.display import (
    StatsDisplayWidget,
    FocusMapWidget,
    NapariLiveWidget,
    NapariMultiChannelWidget,
    NapariMosaicDisplayWidget,
    WaveformDisplay,
    PlotWidget,
    SurfacePlotWidget,
)
from control.widgets.hardware import (
    LaserAutofocusSettingWidget,
    SpinningDiskConfocalWidget,
    DragonflyConfocalWidget,
    ObjectivesWidget,
    DACControWidget,
    FilterControllerWidget,
    TriggerControlWidget,
    LaserAutofocusControlWidget,
    LedMatrixSettingsDialog,
)
from control.widgets.wellplate import (
    WellSelectionWidget,
    WellplateFormatWidget,
    WellplateCalibration,
    CalibrationLiveViewer,
    Well1536SelectionWidget,
    SampleSettingsWidget,
)
from control.widgets.fluidics import (
    FluidicsWidget,
)
from control.widgets.tracking import (
    TrackingControllerWidget,
    PlateReaderAcquisitionWidget,
    PlateReaderNavigationWidget,
    DisplacementMeasurementWidget,
    Joystick,
)
from control.widgets.nl5 import (
    NL5Widget,
    NL5SettingsDialog,
)
from control.widgets.custom_multipoint import (
    TemplateMultiPointWidget,
)
from control.widgets.spectrometer import (
    SpectrometerControlWidget,
    SpectrumDisplay,
)

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
