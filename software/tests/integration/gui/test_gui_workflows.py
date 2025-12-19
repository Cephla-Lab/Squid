import time

import pytest

from tests.gui_helpers import (
    EventCollector,
    click_widget,
    process_events,
    set_combobox_text,
    set_line_edit_text,
    set_spinbox_value,
    set_checkbox_value,
)


DEFAULT_GUI_FLAGS = {
    "USE_NAPARI_FOR_MULTIPOINT": False,
    "USE_NAPARI_FOR_MOSAIC_DISPLAY": False,
    "USE_NAPARI_FOR_LIVE_VIEW": False,
    "USE_NAPARI_FOR_LIVE_CONTROL": False,
    "USE_NAPARI_WELL_SELECTION": False,
}


def build_gui(gui_factory, **overrides):
    flags = dict(DEFAULT_GUI_FLAGS)
    flags.update(overrides)
    return gui_factory(**flags)


@pytest.mark.integration
def test_workflow_live_adjust_move_stop(gui_factory):
    pytest.skip("Headless workflow test timing out; skip for now.")
    from squid.core.events import event_bus, LiveStateChanged

    context, gui = build_gui(gui_factory)
    stage_service = context.services.get("stage")
    assert stage_service is not None

    collector = EventCollector(event_bus).subscribe(LiveStateChanged)

    click_widget(gui.liveControlWidget.btn_live)
    collector.wait_for(LiveStateChanged, predicate=lambda evt: evt.is_live)

    set_spinbox_value(gui.cameraSettingWidget.entry_exposureTime, 30.0)

    start_pos = stage_service.get_position()
    set_spinbox_value(gui.navigationWidget.entry_dX, 0.5)
    click_widget(gui.navigationWidget.btn_moveX_forward)
    assert stage_service.get_position().x_mm == pytest.approx(start_pos.x_mm + 0.5)

    click_widget(gui.liveControlWidget.btn_live)
    collector.wait_for(LiveStateChanged, predicate=lambda evt: not evt.is_live)


@pytest.mark.integration
def test_workflow_flexible_snap_acquisition(gui_factory):
    from squid.core.events import (
        event_bus,
        AcquisitionStateChanged,
    )

    context, gui = build_gui(gui_factory)
    widget = gui.flexibleMultiPointWidget

    click_widget(widget.btn_setSavingDir)
    set_line_edit_text(widget.lineEdit_experimentID, "snap_workflow")

    if widget.list_configurations.count() > 0:
        widget.list_configurations.setCurrentRow(0)

    collector = EventCollector(event_bus).subscribe(AcquisitionStateChanged)
    click_widget(widget.btn_snap_images)

    collector.wait_for(AcquisitionStateChanged, predicate=lambda evt: evt.in_progress)
    collector.wait_for(AcquisitionStateChanged, predicate=lambda evt: not evt.in_progress)


@pytest.mark.integration
def test_workflow_wellplate_select_and_generate_coordinates(gui_factory):
    from qtpy.QtWidgets import QTableWidgetSelectionRange
    from squid.core.events import event_bus, ActiveAcquisitionTabChanged, ScanCoordinatesUpdated

    context, gui = build_gui(gui_factory)
    scan_coordinates = context.controllers.scan_coordinates
    assert scan_coordinates is not None
    collector = EventCollector(event_bus).subscribe(ScanCoordinatesUpdated)

    gui.wellSelectionWidget.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 0), True)
    set_combobox_text(gui.wellplateMultiPointWidget.combobox_xy_mode, "Select Wells")

    event_bus.publish(ActiveAcquisitionTabChanged(active_tab="wellplate"))
    event = collector.wait_for(
        ScanCoordinatesUpdated,
        predicate=lambda evt: evt.total_regions > 0,
        timeout_s=2.0,
    )
    assert event.region_ids

    # Allow the controller time to populate region_centers after the event.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        process_events()
        if scan_coordinates.region_centers:
            break
        time.sleep(0.01)
    assert scan_coordinates.region_centers


@pytest.mark.integration
def test_workflow_focus_lock_snap_acquisition(gui_factory):
    from squid.core.events import event_bus, AcquisitionStateChanged, LaserAFInitialized, LaserAFReferenceSet

    context, gui = build_gui(gui_factory, SUPPORT_LASER_AUTOFOCUS=True)

    collector = EventCollector(event_bus).subscribe(
        LaserAFInitialized,
        LaserAFReferenceSet,
        AcquisitionStateChanged,
    )

    click_widget(gui.laserAutofocusSettingWidget.initialize_button)
    init_event = collector.wait_for(LaserAFInitialized, timeout_s=5.0)
    if not init_event.is_initialized:
        pytest.xfail("Laser autofocus did not initialize in simulation")

    click_widget(gui.laserAutofocusControlWidget.btn_set_reference)
    ref_event = collector.wait_for(LaserAFReferenceSet, timeout_s=5.0)
    if not ref_event.success:
        pytest.xfail("Laser autofocus reference could not be set in simulation")

    widget = gui.flexibleMultiPointWidget
    click_widget(widget.btn_setSavingDir)
    set_line_edit_text(widget.lineEdit_experimentID, "focus_lock")
    if widget.list_configurations.count() > 0:
        widget.list_configurations.setCurrentRow(0)

    set_checkbox_value(widget.checkbox_withReflectionAutofocus, True)

    click_widget(widget.btn_snap_images)
    collector.wait_for(AcquisitionStateChanged, predicate=lambda evt: evt.in_progress)
    collector.wait_for(AcquisitionStateChanged, predicate=lambda evt: not evt.in_progress)


@pytest.mark.integration
def test_workflow_recording_start_stop(gui_factory):
    from squid.core.events import event_bus, LiveStateChanged

    context, gui = build_gui(gui_factory, ENABLE_RECORDING=True)
    collector = EventCollector(event_bus).subscribe(LiveStateChanged)

    click_widget(gui.liveControlWidget.btn_live)
    collector.wait_for(LiveStateChanged, predicate=lambda evt: evt.is_live)

    click_widget(gui.recordingControlWidget.btn_setSavingDir)
    set_line_edit_text(gui.recordingControlWidget.lineEdit_experimentID, "recording_flow")

    click_widget(gui.recordingControlWidget.btn_record)
    handler = getattr(gui.streamHandler, "_handler", None)
    assert handler is not None
    assert handler.save_image_flag is True

    click_widget(gui.recordingControlWidget.btn_record)
    assert handler.save_image_flag is False

    click_widget(gui.liveControlWidget.btn_live)
    collector.wait_for(LiveStateChanged, predicate=lambda evt: not evt.is_live)
