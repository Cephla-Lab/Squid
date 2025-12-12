"""Factory functions for creating GUI widgets.

These helper functions extract widget creation logic from HighContentScreeningGui.load_widgets()
to reduce the size of the main gui_hcs.py file.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.ui.main_window import HighContentScreeningGui

from _def import (
    CAMERA_TYPE,
    ENABLE_SPINNING_DISK_CONFOCAL,
    ENABLE_NL5,
    USE_DRAGONFLY,
    USE_XERYON,
    SUPPORT_LASER_AUTOFOCUS,
    RUN_FLUIDICS,
    WELLPLATE_FORMAT,
    TUBE_LENS_MM,
    DEFAULT_OBJECTIVE,
)
import squid.ui.widgets as widgets
from squid.core.utils.config_utils import ChannelMode


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
        import squid.ui.widgets.nl5 as NL5Widget

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
            gui._ui_event_bus,
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
            gui._ui_event_bus,
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
    gui.profileWidget = widgets.ProfileWidget(
        gui.configurationManager, event_bus=gui._ui_event_bus
    )

    # Seed initial state for live control widget
    objective_name = getattr(gui.objectiveStore, "current_objective", None) or DEFAULT_OBJECTIVE
    channel_configs = gui.channelConfigurationManager.get_configurations(objective_name)
    initial_configuration = gui.liveController.currentConfiguration
    if initial_configuration is None and channel_configs:
        initial_configuration = channel_configs[0]
    initial_channel_names = [mode.name for mode in channel_configs]
    # Ensure the initial configuration name appears in the dropdown options
    if initial_configuration and initial_configuration.name not in initial_channel_names:
        initial_channel_names.append(initial_configuration.name)
    # Last resort fallback to a minimal configuration to avoid crashes if configs are missing
    if initial_configuration is None:
        initial_configuration = ChannelMode(
            id="default",
            name="Default",
            exposure_time=10.0,
            analog_gain=0.0,
            illumination_source=0,
            illumination_intensity=0.0,
            camera_sn="",
            z_offset=0.0,
            emission_filter_position=1,
        )
        if not initial_channel_names:
            initial_channel_names = [initial_configuration.name]

    # Get camera limits from the camera (read-only, for widget initialization)
    camera = gui.microscope.camera
    try:
        gain_range = camera.get_gain_range()
    except NotImplementedError:
        gain_range = None

    gui.liveControlWidget = widgets.LiveControlWidget(
        gui._ui_event_bus,
        gui.streamHandler,
        initial_configuration=initial_configuration,
        initial_objective=objective_name,
        initial_channel_configs=initial_channel_names,
        exposure_limits=camera.get_exposure_limits(),
        gain_range=gain_range,
        initial_trigger_mode=camera.get_acquisition_mode().value,
        show_display_options=False,
        show_autolevel=True,
        autolevel=True,
    )

    # Navigation and stage widgets
    gui.navigationWidget = widgets.NavigationWidget(
        gui._ui_event_bus,
        widget_configuration=f"{WELLPLATE_FORMAT} well plate",
    )
    gui.stageUtils = widgets.StageUtils(
        gui._ui_event_bus,
        is_wellplate=True,
    )

    # DAC control widget (uses UIEventBus for thread-safe updates)
    gui.dacControlWidget = widgets.DACControWidget(gui._ui_event_bus)

    # Autofocus widget
    gui.autofocusWidget = widgets.AutoFocusWidget(gui._ui_event_bus)

    # Piezo widget
    if gui.piezo:
        gui.piezoWidget = widgets.PiezoWidget(gui.piezo, event_bus=gui._ui_event_bus)

    # Objectives widget
    if USE_XERYON:
        gui.objectivesWidget = widgets.ObjectivesWidget(
            gui.objectiveStore, gui.objective_changer, gui._ui_event_bus
        )
    else:
        gui.objectivesWidget = widgets.ObjectivesWidget(
            gui.objectiveStore, event_bus=gui._ui_event_bus
        )

    # Filter controller widget
    if gui.emission_filter_wheel:
        # Get filter wheel config for number of positions
        try:
            wheel_info = gui.emission_filter_wheel.get_filter_wheel_info(1)
            num_positions = wheel_info.number_of_slots
        except Exception:
            num_positions = 7  # Default fallback
        initial_position = 1
        filter_wheel_service = gui._services.get("filter_wheel") if gui._services else None
        if filter_wheel_service is not None:
            try:
                initial_position = filter_wheel_service.get_position(1)
            except Exception:
                pass
        initial_auto_switch = getattr(
            gui.liveController, "enable_channel_auto_filter_switching", True
        )
        gui.filterControllerWidget = widgets.FilterControllerWidget(
            event_bus=gui._ui_event_bus,
            wheel_index=1,
            num_positions=num_positions,
            initial_position=initial_position,
            initial_auto_switch=initial_auto_switch,
        )

    # Recording widget
    gui.recordingControlWidget = widgets.RecordingWidget(
        gui.streamHandler, gui.imageSaver
    )


def create_wellplate_widgets(gui: "HighContentScreeningGui") -> None:
    """Create wellplate-related widgets (format, selection, focus map)."""
    from squid.ops.navigation.focus_map import FocusMap

    # Get pixel size info from objectiveStore for calibration
    pixel_size_factor = gui.objectiveStore.get_pixel_size_factor() if hasattr(gui.objectiveStore, 'get_pixel_size_factor') else 1.0
    pixel_size_binned_um = gui.streamHandler.pixel_size_um if hasattr(gui.streamHandler, 'pixel_size_um') else 0.084665

    gui.wellplateFormatWidget = widgets.WellplateFormatWidget(
        event_bus=gui._ui_event_bus,
        navigationViewer=gui.navigationViewer,
        streamHandler=gui.streamHandler,
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
        gui.navigationViewer,
        gui.scanCoordinates,
        FocusMap(),
        gui._ui_event_bus,
    )


def create_laser_autofocus_widgets(gui: "HighContentScreeningGui") -> None:
    """Create laser autofocus widgets if supported."""
    from squid.ui.widgets.display.image_display import ImageDisplayWindow

    if not SUPPORT_LASER_AUTOFOCUS:
        return

    # Focus camera uses its own service for exposure/gain limits
    gui.cameraSettingWidget_focus_camera = None
    focus_camera_service = gui._services.get("camera_focus") if gui._services else None
    focus_camera_exposure_limits = (
        focus_camera_service.get_exposure_limits()
        if focus_camera_service is not None
        else (0.1, 1000.0)
    )

    # Extract initial properties from controller for widget initialization
    laser_af_props = gui.laserAutofocusController.laser_af_properties
    initial_properties = laser_af_props.model_dump() if hasattr(laser_af_props, 'model_dump') else vars(laser_af_props)

    gui.laserAutofocusSettingWidget = widgets.LaserAutofocusSettingWidget(
        streamHandler=gui.streamHandler_focus_camera,
        event_bus=gui._ui_event_bus,
        initial_properties=initial_properties,
        initial_is_initialized=gui.laserAutofocusController.is_initialized,
        initial_characterization_mode=gui.laserAutofocusController.characterization_mode,
        exposure_limits=focus_camera_exposure_limits,
        stretch=False,
    )
    gui.waveformDisplay = widgets.WaveformDisplay(
        N=1000, include_x=True, include_y=False
    )
    gui.displacementMeasurementWidget = widgets.DisplacementMeasurementWidget(
        event_bus=gui._ui_event_bus,
    )
    gui.laserAutofocusControlWidget = widgets.LaserAutofocusControlWidget(
        event_bus=gui._ui_event_bus,
        initial_is_initialized=gui.laserAutofocusController.is_initialized,
        initial_has_reference=laser_af_props.has_reference,
    )
    gui.imageDisplayWindow_focus = ImageDisplayWindow()
    # Connect image display window to settings widget for spot tracking
    gui.laserAutofocusSettingWidget.set_image_display_window(gui.imageDisplayWindow_focus)


def create_fluidics_widget(gui: "HighContentScreeningGui") -> None:
    """Create fluidics widget if enabled."""
    if RUN_FLUIDICS:
        gui.fluidicsWidget = widgets.FluidicsWidget(gui.fluidics)


def create_acquisition_widgets(gui: "HighContentScreeningGui") -> None:
    """Create acquisition widgets (multipoint, tracking, etc.)."""
    from _def import (
        ENABLE_TRACKING,
        USE_TEMPLATE_MULTIPOINT,
        TRACKING_SHOW_MICROSCOPE_CONFIGURATIONS,
    )
    from squid.ui.widgets.custom_multipoint import TemplateMultiPointWidget

    # Seed initial state for acquisition widgets
    channel_manager = gui.channelConfigurationManager
    objective_name = getattr(gui.objectiveStore, "current_objective", None)
    objective_dict = getattr(gui.objectiveStore, "objectives_dict", {}) or {}
    objective_names = list(objective_dict.keys())
    objective_pixel_size_factors = {
        name: gui.objectiveStore.calculate_pixel_size_factor(obj, TUBE_LENS_MM)
        for name, obj in objective_dict.items()
    } if objective_dict else {}
    initial_pixel_size_factor = objective_pixel_size_factors.get(objective_name, 1.0)
    channel_configs = (
        channel_manager.get_configurations(objective_name) if objective_name else []
    )
    initial_channel_names = [mode.name for mode in channel_configs]

    stage_service = gui._services.get("stage") if gui._services else None
    initial_stage_pos = stage_service.get_position() if stage_service else None
    initial_z_mm = getattr(initial_stage_pos, "z_mm", 0.0) if initial_stage_pos else 0.0
    # Stage conversion is optional; pass through if available
    z_ustep_per_mm = getattr(stage_service, "z_ustep_per_mm", None)

    gui.flexibleMultiPointWidget = widgets.FlexibleMultiPointWidget(
        gui.navigationViewer,
        gui.scanCoordinates,
        gui.focusMapWidget,
        gui._ui_event_bus,
        initial_channel_configs=initial_channel_names,
        z_ustep_per_mm=z_ustep_per_mm,
        initial_z_mm=initial_z_mm,
    )
    gui.wellplateMultiPointWidget = widgets.WellplateMultiPointWidget(
        gui.navigationViewer,
        gui.scanCoordinates,
        gui._ui_event_bus,
        initial_channel_configs=initial_channel_names,
        initial_objective=objective_name,
        objective_pixel_size_factors=objective_pixel_size_factors,
        focusMapWidget=gui.focusMapWidget,
        napariMosaicWidget=gui.napariMosaicDisplayWidget,
        tab_widget=gui.recordTabWidget,
        well_selection_widget=gui.wellSelectionWidget,
        z_ustep_per_mm=z_ustep_per_mm,
        initial_z_mm=initial_z_mm,
    )
    if USE_TEMPLATE_MULTIPOINT:
        gui.templateMultiPointWidget = TemplateMultiPointWidget(
            gui.navigationViewer,
            gui.multipointController,
            gui.objectiveStore,
            gui.channelConfigurationManager,
            gui.scanCoordinates,
            gui.focusMapWidget,
            gui._ui_event_bus,
        )
    gui.multiPointWithFluidicsWidget = widgets.MultiPointWithFluidicsWidget(
        gui.navigationViewer,
        gui.scanCoordinates,
        gui._ui_event_bus,
        initial_channel_configs=initial_channel_names,
        napariMosaicWidget=gui.napariMosaicDisplayWidget,
        z_ustep_per_mm=z_ustep_per_mm,
    )
    gui.sampleSettingsWidget = widgets.SampleSettingsWidget(
        gui.objectivesWidget, gui.wellplateFormatWidget
    )

    if ENABLE_TRACKING:
        gui.trackingControlWidget = widgets.TrackingControllerWidget(
            event_bus=gui._ui_event_bus,
            initial_channel_configs=initial_channel_names,
            peripheral_service=gui._services.get("peripheral") if gui._services else None,
            objectivesWidget=gui.objectivesWidget,
            initial_objective=objective_name,
            initial_pixel_size_um=initial_pixel_size_factor,
            show_configurations=TRACKING_SHOW_MICROSCOPE_CONFIGURATIONS,
        )
