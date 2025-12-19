import pytest

from tests.gui_helpers import (
    EventCollector,
    click_widget,
    process_events,
    set_combobox_text,
    set_spinbox_value,
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
def test_camera_exposure_clamped_to_limits(gui_factory):
    context, gui = build_gui(gui_factory)
    camera_service = context.services.get("camera")
    assert camera_service is not None

    min_exp, max_exp = camera_service.get_exposure_limits()
    widget = gui.cameraSettingWidget

    set_spinbox_value(widget.entry_exposureTime, max_exp + 100)
    assert widget.entry_exposureTime.value() <= max_exp
    assert camera_service.get_exposure_time() <= max_exp

    set_spinbox_value(widget.entry_exposureTime, min_exp - 100)
    assert widget.entry_exposureTime.value() >= min_exp
    assert camera_service.get_exposure_time() >= min_exp


@pytest.mark.integration
def test_camera_roi_rounds_to_valid_multiple(gui_factory):
    context, gui = build_gui(gui_factory)
    camera_service = context.services.get("camera")
    assert camera_service is not None

    widget = gui.cameraSettingWidget
    width_input = 33
    height_input = 25

    set_spinbox_value(widget.entry_ROI_width, width_input)
    set_spinbox_value(widget.entry_ROI_height, height_input)

    x_offset, y_offset, width, height = camera_service.get_region_of_interest()

    assert width % 8 == 0
    assert height % 8 == 0
    assert x_offset >= 0
    assert y_offset >= 0
    assert x_offset % 8 == 0
    assert y_offset % 8 == 0


@pytest.mark.integration
def test_flexible_multipoint_grid_clamps_to_valid_range(gui_factory):
    from squid.core.events import (
        event_bus,
        AddFlexibleRegionCommand,
        AddFlexibleRegionWithStepSizeCommand,
    )

    context, gui = build_gui(gui_factory)
    stage_service = context.services.get("stage")
    assert stage_service is not None

    tab_index = gui.recordTabWidget.indexOf(gui.flexibleMultiPointWidget)
    if tab_index >= 0:
        gui.recordTabWidget.setCurrentIndex(tab_index)
        process_events()

    stage_service.move_to(x_mm=1.0, y_mm=2.0, z_mm=0.3)
    event_bus.drain(timeout_s=0.2)
    process_events()

    set_spinbox_value(gui.flexibleMultiPointWidget.entry_NX, 0)
    set_spinbox_value(gui.flexibleMultiPointWidget.entry_NY, 1000)
    assert gui.flexibleMultiPointWidget.entry_NX.value() == 1
    assert gui.flexibleMultiPointWidget.entry_NY.value() == 50

    collector = EventCollector(event_bus).subscribe(
        AddFlexibleRegionCommand,
        AddFlexibleRegionWithStepSizeCommand,
    )
    click_widget(gui.flexibleMultiPointWidget.btn_add)

    try:
        event = collector.wait_for(AddFlexibleRegionCommand, timeout_s=1.0)
    except AssertionError:
        event = collector.wait_for(AddFlexibleRegionWithStepSizeCommand, timeout_s=1.0)
    assert event is not None
    assert event.n_x == 1
    assert event.n_y == 50


@pytest.mark.integration
def test_flexible_multipoint_requires_path_and_channel(gui_factory):
    from squid.core.events import event_bus, StartAcquisitionCommand, StartNewExperimentCommand

    context, gui = build_gui(gui_factory)
    widget = gui.flexibleMultiPointWidget

    tab_index = gui.recordTabWidget.indexOf(widget)
    if tab_index >= 0:
        gui.recordTabWidget.setCurrentIndex(tab_index)
        process_events()

    collector = EventCollector(event_bus).subscribe(
        StartAcquisitionCommand, StartNewExperimentCommand
    )

    initial_count = len(collector.events(StartAcquisitionCommand))
    click_widget(widget.btn_startAcquisition)
    process_events()
    assert widget.btn_startAcquisition.isChecked() is False
    assert len(collector.events(StartAcquisitionCommand)) == initial_count


@pytest.mark.integration
def test_recording_requires_base_path(gui_factory):
    context, gui = build_gui(gui_factory, ENABLE_RECORDING=True)

    widget = gui.recordingControlWidget
    click_widget(widget.btn_record)
    assert widget.btn_record.isChecked() is False
    if hasattr(gui.streamHandler, "_handler"):
        assert gui.streamHandler._handler.save_image_flag is False


@pytest.mark.integration
def test_wellplate_scan_size_clamps_to_min(gui_factory):
    from squid.core.events import event_bus, SetWellSelectionScanCoordinatesCommand

    context, gui = build_gui(gui_factory)
    widget = gui.wellplateMultiPointWidget

    tab_index = gui.recordTabWidget.indexOf(widget)
    if tab_index >= 0:
        gui.recordTabWidget.setCurrentIndex(tab_index)
        process_events()

    set_combobox_text(widget.combobox_xy_mode, "Select Wells")
    set_spinbox_value(widget.entry_scan_size, widget.entry_scan_size.minimum() + 1.0)
    collector = EventCollector(event_bus).subscribe(SetWellSelectionScanCoordinatesCommand)

    set_spinbox_value(widget.entry_scan_size, 0.0)
    event = collector.wait_for(SetWellSelectionScanCoordinatesCommand)
    assert event.scan_size_mm == pytest.approx(widget.entry_scan_size.minimum())
