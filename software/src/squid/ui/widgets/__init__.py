# GUI widgets module
from squid.ui.widgets.base import (
    error_dialog,
    check_space_available_with_error_dialog,
    WrapperWindow,
    CollapsibleGroupBox,
    PandasTableModel,
)
from squid.ui.widgets.config import (
    ConfigEditor,
    ConfigEditorBackwardsCompatible,
    ProfileWidget,
)
from squid.ui.widgets.camera import (
    CameraSettingsWidget,
    LiveControlWidget,
    RecordingWidget,
    MultiCameraRecordingWidget,
)
from squid.ui.widgets.stage import (
    StageUtils,
    PiezoWidget,
    NavigationWidget,
    AutoFocusWidget,
)
from squid.ui.widgets.acquisition import (
    FlexibleMultiPointWidget,
    WellplateMultiPointWidget,
    MultiPointWithFluidicsWidget,
)
from squid.ui.widgets.display import (
    StatsDisplayWidget,
    FocusMapWidget,
    NapariLiveWidget,
    NapariMultiChannelWidget,
    NapariMosaicDisplayWidget,
    WaveformDisplay,
    PlotWidget,
    SurfacePlotWidget,
)
from squid.ui.widgets.hardware import (
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
from squid.ui.widgets.wellplate import (
    WellSelectionWidget,
    WellplateFormatWidget,
    WellplateCalibration,
    CalibrationLiveViewer,
    Well1536SelectionWidget,
    SampleSettingsWidget,
)
from squid.ui.widgets.fluidics import (
    FluidicsWidget,
)
from squid.ui.widgets.tracking import (
    TrackingControllerWidget,
    PlateReaderAcquisitionWidget,
    PlateReaderNavigationWidget,
    DisplacementMeasurementWidget,
    Joystick,
)
from squid.ui.widgets.nl5 import (
    NL5Widget,
    NL5SettingsDialog,
)
from squid.ui.widgets.custom_multipoint import (
    TemplateMultiPointWidget,
)
from squid.ui.widgets.spectrometer import (
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
