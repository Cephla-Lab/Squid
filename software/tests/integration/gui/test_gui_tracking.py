import pytest

from tests.gui_helpers import EventCollector, click_widget, set_line_edit_text


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
def test_tracking_widget_publishes_commands(gui_factory):
    from squid.core.events import (
        event_bus,
        StartTrackingExperimentCommand,
        SetTrackingChannelsCommand,
        StartTrackingCommand,
        StopTrackingCommand,
    )

    context, gui = build_gui(gui_factory, ENABLE_TRACKING=True)
    widget = gui.trackingControlWidget

    click_widget(widget.btn_setSavingDir)
    assert widget.base_path_is_set is True

    set_line_edit_text(widget.lineEdit_experimentID, "tracking_test")

    if widget.list_configurations.count() > 0:
        widget.list_configurations.setCurrentRow(0)

    collector = EventCollector(event_bus).subscribe(
        StartTrackingExperimentCommand,
        SetTrackingChannelsCommand,
        StartTrackingCommand,
        StopTrackingCommand,
    )
    click_widget(widget.btn_track)

    collector.wait_for(StartTrackingExperimentCommand)
    collector.wait_for(SetTrackingChannelsCommand)
    collector.wait_for(StartTrackingCommand)

    click_widget(widget.btn_track)
    collector.wait_for(StopTrackingCommand)
