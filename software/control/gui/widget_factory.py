"""Factory functions for creating GUI widgets.

These helper functions extract widget creation logic from HighContentScreeningGui.load_widgets()
to reduce the size of the main gui_hcs.py file.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control.gui_hcs import HighContentScreeningGui

from control._def import (
    CAMERA_TYPE,
    ENABLE_SPINNING_DISK_CONFOCAL,
    ENABLE_NL5,
    USE_DRAGONFLY,
    USE_XERYON,
    SUPPORT_LASER_AUTOFOCUS,
    RUN_FLUIDICS,
    WELLPLATE_FORMAT,
)
import control.widgets as widgets
from squid.events import event_bus


def create_hardware_widgets(gui: "HighContentScreeningGui") -> None:
    """Create hardware control widgets (confocal, DAC, objectives, etc.)."""
    # Spinning disk confocal widget
    if ENABLE_SPINNING_DISK_CONFOCAL:
        if USE_DRAGONFLY:
            gui.spinningDiskConfocalWidget = widgets.DragonflyConfocalWidget(
                gui.dragonfly
            )
        else:
            gui.spinningDiskConfocalWidget = widgets.SpinningDiskConfocalWidget(
                gui.xlight
            )

    # NL5 widget
    if ENABLE_NL5:
        import control.widgets.nl5 as NL5Widget

        gui.nl5Wdiget = NL5Widget.NL5Widget(gui.nl5)

    # Camera settings widget
    camera_service = gui._services.get("camera") if gui._services else None
    exposure_limits = (0.1, 1000.0)
    gain_range = None
    pixel_format_names = ["MONO8"]
    current_pixel_format = None
    roi_info = (0, 0, 64, 64)
    resolution = (64, 64)
    binning_options = []
    current_binning = None
    if camera_service is not None:
        cam = camera_service._camera if hasattr(camera_service, "_camera") else None
        try:
            exposure_limits = camera_service.get_exposure_limits()
        except Exception:
            pass
        try:
            gain_range = camera_service.get_gain_range()
        except Exception:
            gain_range = None
        try:
            pixel_formats_enum = camera_service.get_available_pixel_formats()
            pixel_format_names = [pf.name for pf in pixel_formats_enum]
        except Exception:
            pass
        try:
            current_pixel = camera_service.get_pixel_format()
            current_pixel_format = current_pixel.name if current_pixel is not None else None
        except Exception:
            current_pixel_format = None
        try:
            roi_info = camera_service.get_region_of_interest()
        except Exception:
            pass
        try:
            resolution = camera_service.get_resolution()
        except Exception:
            pass
        try:
            binning_options = camera_service.get_binning_options()
            current_binning = camera_service.get_binning()
        except Exception:
            binning_options = []
            current_binning = None
    if CAMERA_TYPE in ["Toupcam", "Tucsen", "Kinetix"]:
        gui.cameraSettingWidget = widgets.CameraSettingsWidget(
            event_bus,
            exposure_limits=exposure_limits,
            gain_range=gain_range,
            pixel_format_names=pixel_format_names,
            current_pixel_format=current_pixel_format,
            roi_info=roi_info,
            resolution=resolution,
            binning_options=binning_options,
            current_binning=current_binning,
            include_gain_exposure_time=False,
            include_camera_temperature_setting=True,
            include_camera_auto_wb_setting=False,
        )
    else:
        gui.cameraSettingWidget = widgets.CameraSettingsWidget(
            event_bus,
            exposure_limits=exposure_limits,
            gain_range=gain_range,
            pixel_format_names=pixel_format_names,
            current_pixel_format=current_pixel_format,
            roi_info=roi_info,
            resolution=resolution,
            binning_options=binning_options,
            current_binning=current_binning,
            include_gain_exposure_time=False,
            include_camera_temperature_setting=False,
            include_camera_auto_wb_setting=True,
        )

    # Profile and live control widgets
    gui.profileWidget = widgets.ProfileWidget(gui.configurationManager)

    # Get camera limits from the camera (read-only, for widget initialization)
    camera = gui.microscope.camera
    try:
        gain_range = camera.get_gain_range()
    except NotImplementedError:
        gain_range = None

    gui.liveControlWidget = widgets.LiveControlWidget(
        event_bus,
        gui.streamHandler,
        gui.objectiveStore,
        gui.channelConfigurationManager,
        exposure_limits=camera.get_exposure_limits(),
        gain_range=gain_range,
        initial_trigger_mode=camera.get_acquisition_mode().value,
        show_display_options=False,
        show_autolevel=True,
        autolevel=True,
    )

    # Navigation and stage widgets
    stage_service = gui._services.get("stage") if gui._services else None
    gui.navigationWidget = widgets.NavigationWidget(
        stage_service,
        event_bus,
        widget_configuration=f"{WELLPLATE_FORMAT} well plate",
    )
    gui.stageUtils = widgets.StageUtils(
        stage_service,
        event_bus,
        is_wellplate=True,
    )

    # DAC control widget (uses EventBus, no service needed)
    gui.dacControlWidget = widgets.DACControWidget(event_bus)

    # Autofocus widget
    gui.autofocusWidget = widgets.AutoFocusWidget(event_bus)

    # Piezo widget
    if gui.piezo:
        gui.piezoWidget = widgets.PiezoWidget(gui.piezo)

    # Objectives widget
    if USE_XERYON:
        gui.objectivesWidget = widgets.ObjectivesWidget(
            gui.objectiveStore, gui.objective_changer
        )
    else:
        gui.objectivesWidget = widgets.ObjectivesWidget(gui.objectiveStore)

    # Filter controller widget
    if gui.emission_filter_wheel:
        gui.filterControllerWidget = widgets.FilterControllerWidget(
            gui.emission_filter_wheel, gui.liveController
        )

    # Recording widget
    gui.recordingControlWidget = widgets.RecordingWidget(
        gui.streamHandler, gui.imageSaver
    )


def create_wellplate_widgets(gui: "HighContentScreeningGui") -> None:
    """Create wellplate-related widgets (format, selection, focus map)."""
    import control.core.core as core

    # Get pixel size info from objectiveStore for calibration
    pixel_size_factor = gui.objectiveStore.get_pixel_size_factor() if hasattr(gui.objectiveStore, 'get_pixel_size_factor') else 1.0
    pixel_size_binned_um = gui.streamHandler.pixel_size_um if hasattr(gui.streamHandler, 'pixel_size_um') else 0.084665

    gui.wellplateFormatWidget = widgets.WellplateFormatWidget(
        event_bus=event_bus,
        navigationViewer=gui.navigationViewer,
        streamHandler=gui.streamHandler,
        stage_service=gui._services.get("stage") if gui._services else None,
        pixel_size_factor=pixel_size_factor,
        pixel_size_binned_um=pixel_size_binned_um,
    )
    if WELLPLATE_FORMAT != "1536 well plate":
        gui.wellSelectionWidget = widgets.WellSelectionWidget(
            WELLPLATE_FORMAT, gui.wellplateFormatWidget
        )
    else:
        gui.wellSelectionWidget = widgets.Well1536SelectionWidget()
    gui.scanCoordinates.add_well_selector(gui.wellSelectionWidget)
    gui.focusMapWidget = widgets.FocusMapWidget(
        gui.stage,
        gui.navigationViewer,
        gui.scanCoordinates,
        core.FocusMap(),
        stage_service=gui._services.get("stage") if gui._services else None,
    )


def create_laser_autofocus_widgets(gui: "HighContentScreeningGui") -> None:
    """Create laser autofocus widgets if supported."""
    import control.core.core as core

    if not SUPPORT_LASER_AUTOFOCUS:
        return

    # Focus camera doesn't have a service - skip camera settings widget
    # TODO: Create a focus camera service if camera settings are needed
    gui.cameraSettingWidget_focus_camera = None
    gui.laserAutofocusSettingWidget = widgets.LaserAutofocusSettingWidget(
        gui.streamHandler_focus_camera,
        gui.liveController_focus_camera,
        gui.laserAutofocusController,
        stretch=False,
    )
    gui.waveformDisplay = widgets.WaveformDisplay(
        N=1000, include_x=True, include_y=False
    )
    gui.displacementMeasurementWidget = widgets.DisplacementMeasurementWidget(
        gui.displacementMeasurementController, gui.waveformDisplay
    )
    gui.laserAutofocusControlWidget = widgets.LaserAutofocusControlWidget(
        gui.laserAutofocusController, gui.liveController
    )
    gui.imageDisplayWindow_focus = core.ImageDisplayWindow()
    # Connect image display window to settings widget for spot tracking
    gui.laserAutofocusSettingWidget.set_image_display_window(gui.imageDisplayWindow_focus)


def create_fluidics_widget(gui: "HighContentScreeningGui") -> None:
    """Create fluidics widget if enabled."""
    if RUN_FLUIDICS:
        gui.fluidicsWidget = widgets.FluidicsWidget(gui.fluidics)


def create_acquisition_widgets(gui: "HighContentScreeningGui") -> None:
    """Create acquisition widgets (multipoint, tracking, etc.)."""
    from control._def import (
        ENABLE_TRACKING,
        USE_TEMPLATE_MULTIPOINT,
        TRACKING_SHOW_MICROSCOPE_CONFIGURATIONS,
    )
    from control.widgets.custom_multipoint import TemplateMultiPointWidget

    gui.flexibleMultiPointWidget = widgets.FlexibleMultiPointWidget(
        gui.stage,
        gui.navigationViewer,
        gui.multipointController,
        gui.objectiveStore,
        gui.channelConfigurationManager,
        gui.scanCoordinates,
        gui.focusMapWidget,
        stage_service=gui._services.get("stage") if gui._services else None,
    )
    gui.wellplateMultiPointWidget = widgets.WellplateMultiPointWidget(
        gui.stage,
        gui.navigationViewer,
        gui.multipointController,
        gui.liveController,
        gui.objectiveStore,
        gui.channelConfigurationManager,
        gui.scanCoordinates,
        gui.focusMapWidget,
        gui.napariMosaicDisplayWidget,
        tab_widget=gui.recordTabWidget,
        well_selection_widget=gui.wellSelectionWidget,
        stage_service=gui._services.get("stage") if gui._services else None,
    )
    if USE_TEMPLATE_MULTIPOINT:
        gui.templateMultiPointWidget = TemplateMultiPointWidget(
            gui.stage,
            gui.navigationViewer,
            gui.multipointController,
            gui.objectiveStore,
            gui.channelConfigurationManager,
            gui.scanCoordinates,
            gui.focusMapWidget,
        )
    gui.multiPointWithFluidicsWidget = widgets.MultiPointWithFluidicsWidget(
        gui.stage,
        gui.navigationViewer,
        gui.multipointController,
        gui.objectiveStore,
        gui.channelConfigurationManager,
        gui.scanCoordinates,
        gui.napariMosaicDisplayWidget,
        stage_service=gui._services.get("stage") if gui._services else None,
    )
    gui.sampleSettingsWidget = widgets.SampleSettingsWidget(
        gui.objectivesWidget, gui.wellplateFormatWidget
    )

    if ENABLE_TRACKING:
        gui.trackingControlWidget = widgets.TrackingControllerWidget(
            gui.trackingController,
            gui.objectiveStore,
            gui.channelConfigurationManager,
            peripheral_service=gui._services.get("peripheral") if gui._services else None,
            show_configurations=TRACKING_SHOW_MICROSCOPE_CONFIGURATIONS,
        )
