import pytest

from tests.gui_helpers import EventCollector, click_widget, process_events


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
def test_laser_autofocus_initialize_and_reference(gui_factory):
    from squid.core.events import event_bus, LaserAFInitialized, LaserAFReferenceSet

    context, gui = build_gui(gui_factory, SUPPORT_LASER_AUTOFOCUS=True)

    collector = EventCollector(event_bus).subscribe(LaserAFInitialized, LaserAFReferenceSet)

    click_widget(gui.laserAutofocusSettingWidget.initialize_button)
    init_event = collector.wait_for(LaserAFInitialized, timeout_s=5.0)
    assert context.controllers.laser_autofocus is not None
    assert context.controllers.laser_autofocus.is_initialized == init_event.is_initialized

    gui.laserAutofocusControlWidget.update_init_state()
    process_events()

    if init_event.is_initialized:
        assert gui.laserAutofocusControlWidget.btn_set_reference.isEnabled()
        click_widget(gui.laserAutofocusControlWidget.btn_set_reference)
        collector.wait_for(LaserAFReferenceSet, timeout_s=5.0)
