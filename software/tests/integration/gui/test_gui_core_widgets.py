import time

import pytest

from tests.gui_helpers import (
    EventCollector,
    click_widget,
    process_events,
    set_combobox_text,
    set_slider_value,
    set_spinbox_value,
    set_checkbox_value,
    set_line_edit_text,
)


DEFAULT_GUI_FLAGS = {
    "USE_NAPARI_FOR_MULTIPOINT": False,
    "USE_NAPARI_FOR_MOSAIC_DISPLAY": False,
    "USE_NAPARI_FOR_LIVE_VIEW": False,
    "USE_NAPARI_FOR_LIVE_CONTROL": False,
    "USE_NAPARI_WELL_SELECTION": False,
}


def wait_for_event(collector, event_type, *, predicate=None, timeout_s=1.0, start_index=0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        process_events()
        events = collector.events(event_type)
        for event in events[start_index:]:
            if predicate is None or predicate(event):
                return event
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type.__name__}")


def build_gui(gui_factory, **overrides):
    flags = dict(DEFAULT_GUI_FLAGS)
    flags.update(overrides)
    return gui_factory(**flags)


@pytest.mark.integration
def test_live_control_start_stop_and_channel_updates(gui_factory):
    import _def
    from squid.core.events import event_bus, LiveStateChanged, TriggerModeChanged, TriggerFPSChanged

    context, gui = build_gui(gui_factory)
    collector = EventCollector(event_bus).subscribe(
        LiveStateChanged,
        TriggerModeChanged,
        TriggerFPSChanged,
    )

    click_widget(gui.liveControlWidget.btn_live)
    collector.wait_for(
        LiveStateChanged,
        predicate=lambda event: event.is_live and getattr(event, "camera", "main") == "main",
    )
    assert gui.liveController.is_live is True

    set_combobox_text(gui.liveControlWidget.dropdown_triggerManu, _def.TriggerMode.HARDWARE)
    collector.wait_for(
        TriggerModeChanged,
        predicate=lambda event: event.mode == _def.TriggerMode.HARDWARE
        and getattr(event, "camera", "main") == "main",
    )
    assert gui.liveControlWidget.dropdown_triggerManu.currentText() == _def.TriggerMode.HARDWARE

    set_spinbox_value(gui.liveControlWidget.entry_triggerFPS, 12)
    collector.wait_for(
        TriggerFPSChanged,
        predicate=lambda event: event.fps == 12 and getattr(event, "camera", "main") == "main",
    )
    assert gui.liveControlWidget.entry_triggerFPS.value() == pytest.approx(12)

    objective_name = gui.objectiveStore.current_objective
    config_name = gui.liveControlWidget.dropdown_modeSelection.currentText()
    channel_manager = context.microscope.channel_configuration_manager

    new_exposure = gui.liveControlWidget.entry_exposureTime.value() + 1.0
    set_spinbox_value(gui.liveControlWidget.entry_exposureTime, new_exposure)
    config = channel_manager.get_channel_configuration_by_name(objective_name, config_name)
    assert config is not None
    assert config.exposure_time == pytest.approx(new_exposure)

    if gui.liveControlWidget.entry_analogGain.isEnabled():
        new_gain = gui.liveControlWidget.entry_analogGain.value() + 1.0
        set_spinbox_value(gui.liveControlWidget.entry_analogGain, new_gain)
        config = channel_manager.get_channel_configuration_by_name(objective_name, config_name)
        assert config is not None
        assert config.analog_gain == pytest.approx(new_gain)

    set_slider_value(gui.liveControlWidget.slider_illuminationIntensity, 42)
    config = channel_manager.get_channel_configuration_by_name(objective_name, config_name)
    assert config is not None
    assert config.illumination_intensity == pytest.approx(42)

    click_widget(gui.liveControlWidget.btn_live)
    collector.wait_for(
        LiveStateChanged,
        predicate=lambda event: (not event.is_live) and getattr(event, "camera", "main") == "main",
    )
    assert gui.liveController.is_live is False


@pytest.mark.integration
def test_camera_settings_widget_updates_service(gui_factory):
    from squid.core.events import (
        event_bus,
        AutoWhiteBalanceChanged,
        ExposureTimeChanged,
        AnalogGainChanged,
        SetExposureTimeCommand,
        SetAnalogGainCommand,
        SetROICommand,
        ROIChanged,
        SetBinningCommand,
        BinningChanged,
        SetPixelFormatCommand,
        PixelFormatChanged,
    )

    context, gui = build_gui(gui_factory)
    camera_service = context.services.get("camera")
    assert camera_service is not None
    widget = gui.cameraSettingWidget
    collector = EventCollector(event_bus).subscribe(
        ExposureTimeChanged,
        AnalogGainChanged,
        SetExposureTimeCommand,
        SetAnalogGainCommand,
        SetROICommand,
        ROIChanged,
        SetBinningCommand,
        BinningChanged,
        SetPixelFormatCommand,
        PixelFormatChanged,
    )

    limits = camera_service.get_exposure_limits()
    target_exposure = min(limits[1], max(limits[0], widget.entry_exposureTime.value() + 5.0))
    exposure_command_index = len(collector.events(SetExposureTimeCommand))
    exposure_changed_index = len(collector.events(ExposureTimeChanged))
    set_spinbox_value(widget.entry_exposureTime, target_exposure)
    wait_for_event(
        collector,
        SetExposureTimeCommand,
        predicate=lambda event: event.exposure_time_ms == pytest.approx(target_exposure),
        start_index=exposure_command_index,
    )
    wait_for_event(
        collector,
        ExposureTimeChanged,
        predicate=lambda event: event.exposure_time_ms == pytest.approx(target_exposure),
        start_index=exposure_changed_index,
    )

    if widget.entry_analogGain.isEnabled():
        gain_range = camera_service.get_gain_range()
        target_gain = min(gain_range.max_gain, gain_range.min_gain + gain_range.gain_step)
        gain_command_index = len(collector.events(SetAnalogGainCommand))
        gain_changed_index = len(collector.events(AnalogGainChanged))
        set_spinbox_value(widget.entry_analogGain, target_gain)
        wait_for_event(
            collector,
            SetAnalogGainCommand,
            predicate=lambda event: event.gain == pytest.approx(target_gain),
            start_index=gain_command_index,
        )
        wait_for_event(
            collector,
            AnalogGainChanged,
            predicate=lambda event: event.gain == pytest.approx(target_gain),
            start_index=gain_changed_index,
        )

    roi_command_index = len(collector.events(SetROICommand))
    roi_changed_index = len(collector.events(ROIChanged))
    set_spinbox_value(widget.entry_ROI_offset_x, 0)
    set_spinbox_value(widget.entry_ROI_offset_y, 0)
    set_spinbox_value(widget.entry_ROI_width, widget.entry_ROI_width.minimum())
    set_spinbox_value(widget.entry_ROI_height, widget.entry_ROI_height.minimum())
    expected_roi = (
        widget.entry_ROI_offset_x.value(),
        widget.entry_ROI_offset_y.value(),
        widget.entry_ROI_width.value(),
        widget.entry_ROI_height.value(),
    )
    wait_for_event(
        collector,
        SetROICommand,
        predicate=lambda event: (
            event.x_offset,
            event.y_offset,
            event.width,
            event.height,
        )
        == expected_roi,
        start_index=roi_command_index,
    )
    wait_for_event(collector, ROIChanged, start_index=roi_changed_index)

    if widget.dropdown_binning.isEnabled() and widget.dropdown_binning.count() > 0:
        option = widget.dropdown_binning.itemText(0)
        binning_command_index = len(collector.events(SetBinningCommand))
        binning_changed_index = len(collector.events(BinningChanged))
        set_combobox_text(widget.dropdown_binning, option)
        bin_x, bin_y = (int(part) for part in option.split("x"))
        wait_for_event(
            collector,
            SetBinningCommand,
            predicate=lambda event: (event.binning_x, event.binning_y) == (bin_x, bin_y),
            start_index=binning_command_index,
        )
        wait_for_event(
            collector,
            BinningChanged,
            predicate=lambda event: (event.binning_x, event.binning_y) == (bin_x, bin_y),
            start_index=binning_changed_index,
        )

    if widget.dropdown_pixelFormat.count() > 1:
        new_format = widget.dropdown_pixelFormat.itemText(1)
        pixel_format_command_index = len(collector.events(SetPixelFormatCommand))
        pixel_format_changed_index = len(collector.events(PixelFormatChanged))
        set_combobox_text(widget.dropdown_pixelFormat, new_format)
        wait_for_event(
            collector,
            SetPixelFormatCommand,
            predicate=lambda event: event.pixel_format == new_format,
            start_index=pixel_format_command_index,
        )
        wait_for_event(
            collector,
            PixelFormatChanged,
            predicate=lambda event: event.pixel_format.name == new_format,
            start_index=pixel_format_changed_index,
        )

    if hasattr(widget, "btn_auto_wb"):
        collector = EventCollector(event_bus).subscribe(AutoWhiteBalanceChanged)
        click_widget(widget.btn_auto_wb)
        collector.wait_for(AutoWhiteBalanceChanged, predicate=lambda event: event.enabled is True)


@pytest.mark.integration
def test_stage_navigation_and_stage_utils(gui_factory):
    from squid.core.events import (
        event_bus,
        StageMoveToLoadingPositionFinished,
        StageMovementStopped,
    )

    context, gui = build_gui(gui_factory)
    stage_service = context.services.get("stage")
    assert stage_service is not None
    collector = EventCollector(event_bus).subscribe(StageMoveToLoadingPositionFinished, StageMovementStopped)

    start_pos = stage_service.get_position()

    set_spinbox_value(gui.navigationWidget.entry_dX, 1.0)
    move_count = len(collector.events(StageMovementStopped))
    click_widget(gui.navigationWidget.btn_moveX_forward)
    wait_for_event(
        collector,
        StageMovementStopped,
        predicate=lambda event: event.x_mm == pytest.approx(start_pos.x_mm + 1.0),
        start_index=move_count,
    )
    assert stage_service.get_position().x_mm == pytest.approx(start_pos.x_mm + 1.0)

    set_spinbox_value(gui.navigationWidget.entry_dY, 2.0)
    base_pos = stage_service.get_position()
    min_y = stage_service.get_config().Y_AXIS.MIN_POSITION
    expected_y = max(base_pos.y_mm - 2.0, min_y)
    move_count = len(collector.events(StageMovementStopped))
    click_widget(gui.navigationWidget.btn_moveY_backward)
    wait_for_event(
        collector,
        StageMovementStopped,
        predicate=lambda event: event.y_mm == pytest.approx(expected_y),
        start_index=move_count,
    )
    assert stage_service.get_position().y_mm == pytest.approx(expected_y)

    set_spinbox_value(gui.navigationWidget.entry_dZ, 100.0)
    base_pos = stage_service.get_position()
    max_z = stage_service.get_config().Z_AXIS.MAX_POSITION
    delta_z = gui.navigationWidget.entry_dZ.value() / 1000.0
    expected_z = min(base_pos.z_mm + delta_z, max_z)
    move_count = len(collector.events(StageMovementStopped))
    click_widget(gui.navigationWidget.btn_moveZ_forward)
    wait_for_event(
        collector,
        StageMovementStopped,
        predicate=lambda event: event.z_mm == pytest.approx(expected_z),
        start_index=move_count,
    )
    assert stage_service.get_position().z_mm == pytest.approx(expected_z)

    click_widget(gui.stageUtils.btn_load_slide)
    wait_for_event(collector, StageMoveToLoadingPositionFinished)
    assert gui.stageUtils.slide_position == "loading"


@pytest.mark.integration
def test_filter_wheel_and_objectives(gui_factory):
    from squid.core.events import event_bus, FilterPositionChanged, FilterAutoSwitchChanged, ObjectiveChanged

    context, gui = build_gui(gui_factory)

    if hasattr(gui, "filterControllerWidget"):
        filter_service = context.services.get("filter_wheel")
        assert filter_service is not None
        if filter_service.is_available():
            collector = EventCollector(event_bus).subscribe(FilterPositionChanged, FilterAutoSwitchChanged)
            if gui.filterControllerWidget.comboBox.count() > 1:
                option = gui.filterControllerWidget.comboBox.itemText(1)
                set_combobox_text(gui.filterControllerWidget.comboBox, option)
                collector.wait_for(FilterPositionChanged, predicate=lambda event: event.position == 2)
                assert filter_service.get_position(1) == 2

            set_checkbox_value(gui.filterControllerWidget.checkBox, True)
            collector.wait_for(FilterAutoSwitchChanged, predicate=lambda event: event.enabled is False)

    objective_names = list(gui.objectiveStore.objectives_dict.keys())
    if objective_names:
        target = objective_names[0]
        if target == gui.objectiveStore.current_objective and len(objective_names) > 1:
            target = objective_names[1]
        if target != gui.objectiveStore.current_objective:
            collector = EventCollector(event_bus).subscribe(ObjectiveChanged)
            set_combobox_text(gui.objectivesWidget.dropdown, target)
            collector.wait_for(ObjectiveChanged, predicate=lambda event: event.objective_name == target)
            assert gui.objectiveStore.current_objective == target


@pytest.mark.integration
def test_well_selection_publishes_selected_cells(gui_factory):
    from qtpy.QtWidgets import QTableWidgetSelectionRange
    from squid.core.events import event_bus, SelectedWellsChanged

    context, gui = build_gui(gui_factory)
    collector = EventCollector(event_bus).subscribe(SelectedWellsChanged)

    widget = gui.wellSelectionWidget
    widget.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 0), True)
    event = collector.wait_for(SelectedWellsChanged)
    assert (0, 0) in event.selected_cells


@pytest.mark.integration
def test_wellplate_format_change_updates_selection_widget(gui_factory):
    from squid.core.events import event_bus, WellplateFormatChanged

    context, gui = build_gui(gui_factory)
    collector = EventCollector(event_bus).subscribe(WellplateFormatChanged)

    combo = gui.wellplateFormatWidget.comboBox
    for index in range(combo.count()):
        format_name = combo.itemData(index)
        if format_name and format_name != "custom" and format_name != gui.wellplateFormatWidget.wellplate_format:
            combo.setCurrentIndex(index)
            event = collector.wait_for(WellplateFormatChanged)
            assert event.format_name == format_name
            assert gui.wellSelectionWidget.format == format_name
            break


@pytest.mark.integration
def test_wellplate_calibration_event_updates_format(gui_factory):
    from squid.core.events import event_bus, SaveWellplateCalibrationCommand, WellplateFormatChanged

    context, gui = build_gui(gui_factory)
    collector = EventCollector(event_bus).subscribe(WellplateFormatChanged)

    calibration = {
        "rows": 1,
        "cols": 1,
        "well_spacing_mm": 0.0,
        "well_size_mm": 0.0,
        "a1_x_mm": 0.0,
        "a1_y_mm": 0.0,
        "a1_x_pixel": 0,
        "a1_y_pixel": 0,
        "number_of_skip": 0,
    }
    event_bus.publish(
        SaveWellplateCalibrationCommand(name="test_calibration", calibration=calibration)
    )
    event = collector.wait_for(
        WellplateFormatChanged, predicate=lambda evt: evt.format_name == "test_calibration"
    )
    assert event.format_name == "test_calibration"
    assert gui.wellplateFormatWidget.wellplate_format == "test_calibration"


@pytest.mark.integration
def test_flexible_multipoint_add_region_publishes_command(gui_factory):
    from squid.core.events import event_bus, AddFlexibleRegionCommand, ActiveAcquisitionTabChanged

    context, gui = build_gui(gui_factory)
    stage_service = context.services.get("stage")
    assert stage_service is not None

    stage_service.move_to(x_mm=1.0, y_mm=2.0, z_mm=0.3)
    event_bus.drain(timeout_s=0.2)
    process_events()

    event_bus.publish(ActiveAcquisitionTabChanged(active_tab="flexible"))
    event_bus.drain(timeout_s=0.2)
    process_events()

    set_spinbox_value(gui.flexibleMultiPointWidget.entry_NX, 2)
    set_spinbox_value(gui.flexibleMultiPointWidget.entry_NY, 3)

    collector = EventCollector(event_bus).subscribe(AddFlexibleRegionCommand)
    click_widget(gui.flexibleMultiPointWidget.btn_add)
    event = collector.wait_for(AddFlexibleRegionCommand)
    assert event.n_x == 2
    assert event.n_y == 3


@pytest.mark.integration
def test_template_multipoint_adds_region(gui_factory, monkeypatch, tmp_path):
    from qtpy.QtWidgets import QFileDialog
    from squid.core.events import event_bus, AddTemplateRegionCommand, ActiveAcquisitionTabChanged

    template_path = tmp_path / "template.csv"
    template_path.write_text("x_offset_mm,y_offset_mm\n0.1,0.2\n0.2,0.4\n")

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(template_path), "")),
    )

    context, gui = build_gui(gui_factory, USE_TEMPLATE_MULTIPOINT=True)
    stage_service = context.services.get("stage")
    assert stage_service is not None

    stage_service.move_to(x_mm=1.0, y_mm=2.0, z_mm=0.3)
    event_bus.drain(timeout_s=0.2)
    process_events()

    event_bus.publish(ActiveAcquisitionTabChanged(active_tab="template"))
    event_bus.drain(timeout_s=0.2)
    process_events()

    collector = EventCollector(event_bus).subscribe(AddTemplateRegionCommand)
    click_widget(gui.templateMultiPointWidget.btn_load_template)
    click_widget(gui.templateMultiPointWidget.btn_add_from_template)

    event = collector.wait_for(AddTemplateRegionCommand)
    assert event.x_offsets_mm == (0.1, 0.2)
    assert event.y_offsets_mm == (0.2, 0.4)


@pytest.mark.integration
def test_wellplate_multipoint_emits_scan_coordinates_command(gui_factory):
    from squid.core.events import (
        event_bus,
        ActiveAcquisitionTabChanged,
        SetWellSelectionScanCoordinatesCommand,
    )

    context, gui = build_gui(gui_factory)
    set_combobox_text(gui.wellplateMultiPointWidget.combobox_xy_mode, "Select Wells")

    collector = EventCollector(event_bus).subscribe(SetWellSelectionScanCoordinatesCommand)
    event_bus.publish(ActiveAcquisitionTabChanged(active_tab="wellplate"))
    event = collector.wait_for(SetWellSelectionScanCoordinatesCommand)
    assert event.scan_size_mm == pytest.approx(gui.wellplateMultiPointWidget.entry_scan_size.value())


@pytest.mark.integration
def test_focus_map_add_point_publishes_overlay(gui_factory):
    from squid.core.events import event_bus, FocusPointOverlaySet, ScanCoordinatesSnapshot

    context, gui = build_gui(gui_factory)
    stage_service = context.services.get("stage")
    assert stage_service is not None

    gui.focusMapWidget.disable_updating_focus_points_on_signal()

    stage_service.move_to(x_mm=1.0, y_mm=1.0, z_mm=0.2)
    request_id = "test"
    gui.focusMapWidget._scan_snapshot_request_id = request_id
    event_bus.publish(
        ScanCoordinatesSnapshot(
            request_id=request_id,
            region_fov_coordinates={"region_1": ((1.0, 1.0, 0.2),)},
            region_centers={"region_1": (1.0, 1.0, 0.2)},
        )
    )
    event_bus.drain(timeout_s=0.2)
    process_events()

    collector = EventCollector(event_bus).subscribe(FocusPointOverlaySet)
    gui.focusMapWidget.setEnabled(True)
    click_widget(gui.focusMapWidget.add_point_btn)
    event = collector.wait_for(FocusPointOverlaySet, predicate=lambda evt: bool(evt.points))
    assert len(event.points) == 1


@pytest.mark.integration
def test_autofocus_widget_updates_controller_params(gui_factory):
    context, gui = build_gui(gui_factory)
    controller = context.controllers.autofocus
    assert controller is not None

    set_spinbox_value(gui.autofocusWidget.entry_delta, 2.0)
    set_spinbox_value(gui.autofocusWidget.entry_N, 7)

    assert controller.N == 7
    assert controller.deltaZ == pytest.approx(0.002)


@pytest.mark.integration
def test_dac_widget_emits_values(gui_factory):
    from squid.core.events import event_bus, DACValueChanged

    context, gui = build_gui(gui_factory)
    collector = EventCollector(event_bus).subscribe(DACValueChanged)

    set_spinbox_value(gui.dacControlWidget.entry_DAC0, 25.0)
    event = collector.wait_for(DACValueChanged, predicate=lambda evt: evt.channel == 0)
    assert event.value == pytest.approx(25.0)


@pytest.mark.integration
def test_piezo_widget_moves(gui_factory):
    context, gui = build_gui(gui_factory, HAS_OBJECTIVE_PIEZO=True)
    if gui.piezoWidget is None:
        pytest.skip("Piezo widget not available")

    target = min(gui.piezo.range_um, gui.piezo._home_position_um + 5.0)
    set_spinbox_value(gui.piezoWidget.spinBox, target)
    assert gui.piezo.position == pytest.approx(target)


@pytest.mark.integration
def test_recording_widget_start_stop(gui_factory):
    context, gui = build_gui(gui_factory, ENABLE_RECORDING=True)

    widget = gui.recordingControlWidget
    click_widget(widget.btn_setSavingDir)
    assert widget.base_path_is_set is True

    set_line_edit_text(widget.lineEdit_experimentID, "headless_test")

    click_widget(widget.btn_record)
    handler = getattr(gui.streamHandler, "_handler", None)
    assert handler is not None
    assert handler.save_image_flag is True

    click_widget(widget.btn_record)
    assert handler.save_image_flag is False
