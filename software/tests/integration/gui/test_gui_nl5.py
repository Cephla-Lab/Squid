import pytest

from tests.gui_helpers import click_widget, process_events, set_spinbox_value


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
def test_nl5_widget_updates_simulation(monkeypatch, tmp_path, gui_factory):
    monkeypatch.chdir(tmp_path)

    context, gui = build_gui(gui_factory, ENABLE_NL5=True)
    widget = getattr(gui, "nl5Wdiget", None) or getattr(gui, "nl5Widget", None)
    if widget is None or gui.nl5 is None:
        pytest.skip("NL5 widget not available in simulation")

    new_delay = widget.exposure_delay_input.value() + 5
    set_spinbox_value(widget.exposure_delay_input, new_delay)
    assert gui.nl5.exposure_delay_ms == new_delay

    new_speed = min(widget.line_speed_input.maximum(), widget.line_speed_input.value() + 100)
    set_spinbox_value(widget.line_speed_input, new_speed)
    assert gui.nl5.line_speed == new_speed

    new_fov = min(widget.fov_x_input.maximum(), widget.fov_x_input.value() + 10)
    set_spinbox_value(widget.fov_x_input, new_fov)
    assert gui.nl5.fov_x == new_fov

    assert widget.start_acquisition_button.isEnabled() is True
    click_widget(widget.bypass_button)
    process_events()
    assert widget.start_acquisition_button.isEnabled() is False
    click_widget(widget.bypass_button)
    process_events()
    assert widget.start_acquisition_button.isEnabled() is True
