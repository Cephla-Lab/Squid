"""Signal/slot connection helpers for the main GUI.

These helper functions extract signal connection logic from HighContentScreeningGui.make_connections()
to reduce the size of the main gui_hcs.py file.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control.gui_hcs import HighContentScreeningGui

# Note: Event imports moved to widgets that subscribe directly via UIEventBus

from control._def import (
    ENABLE_FLEXIBLE_MULTIPOINT,
    ENABLE_WELLPLATE_MULTIPOINT,
    RUN_FLUIDICS,
    USE_NAPARI_FOR_LIVE_VIEW,
    USE_NAPARI_FOR_LIVE_CONTROL,
    SUPPORT_LASER_AUTOFOCUS,
    ENABLE_SPINNING_DISK_CONFOCAL,
)


def connect_acquisition_signals(gui: "HighContentScreeningGui") -> None:
    """Connect acquisition-related signals."""
    if ENABLE_FLEXIBLE_MULTIPOINT:
        gui.flexibleMultiPointWidget.signal_acquisition_started.connect(
            gui.toggleAcquisitionStart
        )

    if ENABLE_WELLPLATE_MULTIPOINT:
        gui.wellplateMultiPointWidget.signal_acquisition_started.connect(
            gui.toggleAcquisitionStart
        )
        gui.wellplateMultiPointWidget.signal_toggle_live_scan_grid.connect(
            gui.toggle_live_scan_grid
        )

    if RUN_FLUIDICS:
        gui.multiPointWithFluidicsWidget.signal_acquisition_started.connect(
            gui.toggleAcquisitionStart
        )
        gui.fluidicsWidget.fluidics_initialized_signal.connect(
            gui.multiPointWithFluidicsWidget.init_fluidics
        )


def connect_profile_signals(gui: "HighContentScreeningGui") -> None:
    """Connect profile and configuration signals.

    Note: ProfileWidget.signal_profile_changed -> LiveControlWidget.refresh_mode_list
    and select_new_microscope_mode_by_name connections have been migrated to EventBus.
    LiveControlWidget now subscribes directly to ProfileChanged events.
    """
    # Note: The following connections have been migrated to EventBus:
    # - ProfileWidget.signal_profile_changed -> LiveControlWidget.refresh_mode_list
    # - ProfileWidget.signal_profile_changed -> LiveControlWidget.select_new_microscope_mode_by_name
    # LiveControlWidget now subscribes to ProfileChanged event directly.

    gui.objectivesWidget.signal_objective_changed.connect(
        lambda: gui.liveControlWidget.select_new_microscope_mode_by_name(
            gui.liveControlWidget.currentConfiguration.name
        )
    )


def connect_live_control_signals(gui: "HighContentScreeningGui") -> None:
    """Connect live control signals."""
    gui.liveControlWidget.signal_newExposureTime.connect(
        gui.cameraSettingWidget.set_exposure_time
    )
    gui.liveControlWidget.signal_newAnalogGain.connect(
        gui.cameraSettingWidget.set_analog_gain
    )
    if not gui.live_only_mode:
        gui.liveControlWidget.signal_start_live.connect(gui.onStartLive)
    gui.liveControlWidget.update_camera_settings()


def connect_navigation_signals(gui: "HighContentScreeningGui") -> None:
    """Connect navigation and movement signals."""
    if gui._ui_event_bus is None:
        return

    gui.navigationViewer.signal_coordinates_clicked.connect(gui.move_from_click_mm)
    gui.objectivesWidget.signal_objective_changed.connect(
        gui.navigationViewer.redraw_fov
    )
    gui.cameraSettingWidget.signal_binning_changed.connect(
        gui.navigationViewer.redraw_fov
    )
    if ENABLE_FLEXIBLE_MULTIPOINT:
        gui.objectivesWidget.signal_objective_changed.connect(
            gui.flexibleMultiPointWidget.update_fov_positions
        )
    # Note: StageMovementStopped subscription removed from here
    # NavigationViewer now subscribes to StageMovementStopped directly via UIEventBus

    gui._multipoint_signal_bridge.signal_register_current_fov.connect(
        gui.navigationViewer.register_fov
    )
    gui._multipoint_signal_bridge.signal_current_configuration.connect(
        gui.liveControlWidget.update_ui_for_mode
    )
    # Note: PiezoPositionChanged subscription removed from here
    # PiezoWidget now subscribes to PiezoPositionChanged directly via UIEventBus
    gui._multipoint_signal_bridge.signal_set_display_tabs.connect(
        gui.setAcquisitionDisplayTabs
    )


def connect_tab_signals(gui: "HighContentScreeningGui") -> None:
    """Connect tab change signals."""
    gui.recordTabWidget.currentChanged.connect(gui.onTabChanged)
    if not gui.live_only_mode:
        gui.imageDisplayTabs.currentChanged.connect(gui.onDisplayTabChanged)


def connect_wellplate_signals(gui: "HighContentScreeningGui") -> None:
    """Connect wellplate-related signals."""
    gui.wellplateFormatWidget.signalWellplateSettings.connect(
        gui.navigationViewer.update_wellplate_settings
    )
    gui.wellplateFormatWidget.signalWellplateSettings.connect(
        gui.scanCoordinates.update_wellplate_settings
    )
    gui.wellplateFormatWidget.signalWellplateSettings.connect(
        gui.wellSelectionWidget.onWellplateChanged
    )
    gui.wellplateFormatWidget.signalWellplateSettings.connect(
        lambda format_, *args: gui.onWellplateChanged(format_)
    )

    gui.wellSelectionWidget.signal_wellSelectedPos.connect(gui.move_to_mm)
    if ENABLE_WELLPLATE_MULTIPOINT:
        gui.wellSelectionWidget.signal_wellSelected.connect(
            gui.wellplateMultiPointWidget.update_well_coordinates
        )
        gui.objectivesWidget.signal_objective_changed.connect(
            gui.wellplateMultiPointWidget.update_coordinates
        )


def connect_display_signals(gui: "HighContentScreeningGui") -> None:
    """Connect image display signals (non-Napari)."""
    if USE_NAPARI_FOR_LIVE_VIEW and not gui.live_only_mode:
        gui._multipoint_signal_bridge.signal_current_configuration.connect(
            gui.napariLiveWidget.update_ui_for_mode
        )
        gui._autofocus_image_bridge.image_to_display.connect(
            lambda image: gui.napariLiveWidget.updateLiveLayer(
                image, from_autofocus=True
            )
        )
        gui.streamHandler.image_to_display.connect(
            lambda image: gui.napariLiveWidget.updateLiveLayer(
                image, from_autofocus=False
            )
        )
        gui._multipoint_signal_bridge.image_to_display.connect(
            lambda image: gui.napariLiveWidget.updateLiveLayer(
                image, from_autofocus=False
            )
        )
        gui.napariLiveWidget.signal_coordinates_clicked.connect(
            gui.move_from_click_image
        )
        gui.liveControlWidget.signal_live_configuration.connect(
            gui.napariLiveWidget.set_live_configuration
        )

        if USE_NAPARI_FOR_LIVE_CONTROL:
            gui.napariLiveWidget.signal_newExposureTime.connect(
                gui.cameraSettingWidget.set_exposure_time
            )
            gui.napariLiveWidget.signal_newAnalogGain.connect(
                gui.cameraSettingWidget.set_analog_gain
            )
            gui.napariLiveWidget.signal_autoLevelSetting.connect(
                gui.imageDisplayWindow.set_autolevel
            )
    else:
        gui.streamHandler.image_to_display.connect(gui.imageDisplay.enqueue)
        gui.imageDisplay.image_to_display.connect(gui.imageDisplayWindow.display_image)
        gui._autofocus_image_bridge.image_to_display.connect(
            gui.imageDisplayWindow.display_image
        )
        gui._multipoint_signal_bridge.image_to_display.connect(
            gui.imageDisplayWindow.display_image
        )
        gui.liveControlWidget.signal_autoLevelSetting.connect(
            gui.imageDisplayWindow.set_autolevel
        )
        gui.imageDisplayWindow.image_click_coordinates.connect(
            gui.move_from_click_image
        )


def connect_laser_autofocus_signals(gui: "HighContentScreeningGui") -> None:
    """Connect laser autofocus signals if supported.

    Note: LaserAutofocusSettingWidget.update_values() and LaserAutofocusControlWidget.update_init_state()
    connections for profile/objective changes have been migrated to EventBus.
    These widgets now subscribe directly to ProfileChanged and ObjectiveChanged events.
    """
    if not SUPPORT_LASER_AUTOFOCUS:
        return

    # Controller still needs notification of settings changes
    def slot_settings_changed_laser_af():
        gui.laserAutofocusController.on_settings_changed()

    gui.profileWidget.signal_profile_changed.connect(slot_settings_changed_laser_af)
    gui.objectivesWidget.signal_objective_changed.connect(
        slot_settings_changed_laser_af
    )
    if gui.cameraSettingWidget_focus_camera:
        gui.laserAutofocusSettingWidget.signal_newExposureTime.connect(
            gui.cameraSettingWidget_focus_camera.set_exposure_time
        )
        gui.laserAutofocusSettingWidget.signal_newAnalogGain.connect(
            gui.cameraSettingWidget_focus_camera.set_analog_gain
        )
    gui.laserAutofocusSettingWidget.signal_apply_settings.connect(
        gui.laserAutofocusControlWidget.update_init_state
    )
    gui.laserAutofocusSettingWidget.signal_laser_spot_location.connect(
        gui.imageDisplayWindow_focus.mark_spot
    )
    gui.laserAutofocusSettingWidget.update_exposure_time(
        gui.laserAutofocusSettingWidget.exposure_spinbox.value()
    )
    gui.laserAutofocusSettingWidget.update_analog_gain(
        gui.laserAutofocusSettingWidget.analog_gain_spinbox.value()
    )
    gui.laserAutofocusController.signal_cross_correlation.connect(
        gui.laserAutofocusSettingWidget.show_cross_correlation_result
    )

    gui.streamHandler_focus_camera.signal_new_frame_received.connect(
        gui.liveController_focus_camera.on_new_frame
    )
    gui.streamHandler_focus_camera.image_to_display.connect(
        gui.imageDisplayWindow_focus.display_image
    )

    gui.streamHandler_focus_camera.image_to_display.connect(
        gui.displacementMeasurementController.update_measurement
    )
    gui.displacementMeasurementController.signal_plots.connect(gui.waveformDisplay.plot)
    gui.displacementMeasurementController.signal_readings.connect(
        gui.displacementMeasurementWidget.display_readings
    )
    gui.laserAutofocusController.image_to_display.connect(
        gui.imageDisplayWindow_focus.display_image
    )

    # Add connection for piezo position updates
    if gui.piezoWidget:
        gui.laserAutofocusController.signal_piezo_position_update.connect(
            gui.piezoWidget.update_displacement_um_display
        )


def connect_confocal_signals(gui: "HighContentScreeningGui") -> None:
    """Connect spinning disk confocal signals if enabled."""
    if not ENABLE_SPINNING_DISK_CONFOCAL:
        return

    gui.spinningDiskConfocalWidget.signal_toggle_confocal_widefield.connect(
        gui.channelConfigurationManager.toggle_confocal_widefield
    )
    gui.spinningDiskConfocalWidget.signal_toggle_confocal_widefield.connect(
        lambda: gui.liveControlWidget.select_new_microscope_mode_by_name(
            gui.liveControlWidget.currentConfiguration.name
        )
    )


def connect_plot_signals(gui: "HighContentScreeningGui") -> None:
    """Connect z plot signals."""
    gui._multipoint_signal_bridge.signal_coordinates.connect(gui.zPlotWidget.add_point)

    def plot_after_each_region(
        current_region: int, total_regions: int, current_timepoint: int
    ):
        if current_region > 1:
            gui.zPlotWidget.plot()
        gui.zPlotWidget.clear()

    gui._multipoint_signal_bridge.signal_acquisition_progress.connect(plot_after_each_region)
    gui._multipoint_signal_bridge.acquisition_finished.connect(gui.zPlotWidget.plot)


def connect_well_selector_button(gui: "HighContentScreeningGui") -> None:
    """Connect well selector button if present."""
    if hasattr(gui.imageDisplayWindow, "btn_well_selector"):
        gui.imageDisplayWindow.btn_well_selector.clicked.connect(
            lambda: gui.toggleWellSelector(not gui.dock_wellSelection.isVisible())
        )


def connect_slide_position_controller(gui: "HighContentScreeningGui") -> None:
    """Connect slide position controller signals.

    Note: Loading/scanning position reached -> disable/enable acquisition button
    connections have been migrated to EventBus. The multipoint widgets now subscribe
    directly to LoadingPositionReached and ScanningPositionReached events.
    """
    # Note: The following connections have been migrated to EventBus:
    # - FlexibleMultiPointWidget, WellplateMultiPointWidget, MultiPointWithFluidicsWidget
    #   now subscribe directly to LoadingPositionReached and ScanningPositionReached events

    gui.stageUtils.signal_scanning_position_reached.connect(
        lambda *_args: gui.navigationViewer.clear_slide()
    )
