"""The illumination intensity slider applies its value on release, not while it is dragged."""

import types

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.widgets
from control.widgets import LiveControlWidget


class _Controls:
    pass  # a plain class, not SimpleNamespace: PyQt needs to weak-reference a connected method's object


def _controls(qtbot):
    """LiveControlWidget-shaped object with the real intensity slider, entry and wiring."""
    stub = _Controls()
    stub.applied = []
    stub.update_config_illumination_intensity = stub.applied.append  # PyQt cannot connect a MagicMock

    stub.slider_illuminationIntensity = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    stub.slider_illuminationIntensity.setRange(0, 100)
    stub.slider_illuminationIntensity.setValue(20)
    qtbot.addWidget(stub.slider_illuminationIntensity)

    stub.entry_illuminationIntensity = control.widgets.QDoubleSpinBox()
    stub.entry_illuminationIntensity.setRange(0, 100)
    stub.entry_illuminationIntensity.setValue(20)
    qtbot.addWidget(stub.entry_illuminationIntensity)

    for name in ("_preview_illumination_intensity", "_apply_illumination_slider_value"):
        setattr(stub, name, types.MethodType(getattr(LiveControlWidget, name), stub))
    LiveControlWidget._connect_illumination_intensity_controls(stub)
    return stub


def test_dragging_shows_the_value_and_applies_it_once_on_release(qtbot):
    stub = _controls(qtbot)
    slider = stub.slider_illuminationIntensity

    slider.setSliderDown(True)
    for position in (25, 30, 40):
        slider.setSliderPosition(position)

    assert stub.applied == []
    assert stub.entry_illuminationIntensity.value() == 40

    slider.setSliderDown(False)

    assert stub.applied == [40]
    assert slider.value() == 40


def test_typed_value_is_applied_once_and_moves_the_slider(qtbot):
    stub = _controls(qtbot)

    stub.entry_illuminationIntensity.setValue(35)

    assert stub.applied == [35]
    assert stub.slider_illuminationIntensity.value() == 35


def test_keyboard_step_on_the_slider_applies_immediately(qtbot):
    stub = _controls(qtbot)

    stub.slider_illuminationIntensity.triggerAction(control.widgets.QAbstractSlider.SliderSingleStepAdd)

    assert stub.applied == [21]
    assert stub.entry_illuminationIntensity.value() == 21
