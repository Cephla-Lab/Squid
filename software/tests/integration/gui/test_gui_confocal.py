import pytest

from tests.gui_helpers import EventCollector, click_widget


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
def test_confocal_widget_toggle_publishes_command(gui_factory):
    from squid.core.events import event_bus, SetConfocalModeCommand

    context, gui = build_gui(gui_factory, ENABLE_SPINNING_DISK_CONFOCAL=True, USE_DRAGONFLY=False)

    if not hasattr(gui, "spinningDiskConfocalWidget"):
        pytest.skip("Confocal widget not available")

    collector = EventCollector(event_bus).subscribe(SetConfocalModeCommand)
    click_widget(gui.spinningDiskConfocalWidget.btn_toggle_widefield)
    event = collector.wait_for(SetConfocalModeCommand)
    assert event.objective_name == gui.objectiveStore.current_objective
