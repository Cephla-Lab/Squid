"""Tests for the per-channel intensity floor (ruling 2026-09-28): the 560 nm source's own minimum, offered to the
GUI as a floor below which the controls must not go without an AOM to dim/close further.
"""

import contextlib
from unittest.mock import MagicMock

import pytest

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.microscope
import control.widgets
from control.core.config import ConfigRepository
from control.core.live_controller import LiveController
from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  D3: 14
  USB1: 0
channels:
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
    max_output: 0.2
  - name: Fluorescence 488 nm Ex
    type: epi_illumination
    controller_port: D2
    wavelength_nm: 488
  - name: Fluorescence 560 nm Ex
    type: epi_illumination
    controller_port: D3
    wavelength_nm: 560
"""


def _acquisition_channel(illumination_channel, intensity=100.0):
    return AcquisitionChannel(
        name=illumination_channel,
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(
            illumination_channel=illumination_channel,
            intensity=intensity,
        ),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=0.0),
    )


@pytest.fixture
def scope(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    microscope = control.microscope.Microscope.build_from_global_config(True)
    microscope.config_repo = ConfigRepository(base_path=tmp_path)
    yield microscope
    microscope.close()


@pytest.fixture
def live(scope):
    return LiveController(microscope=scope, camera=scope.camera)


def _fake_engine(floor_percent):
    engine = MagicMock()
    engine.intensity_floor_percent.side_effect = lambda wavelength: floor_percent if wavelength == 560 else 0.0
    return engine


def test_intensity_floor_percent_from_the_engine(scope, live):
    scope.addons.squid_laser_engine = _fake_engine(10.0)
    assert live.get_intensity_floor_percent(_acquisition_channel("Fluorescence 560 nm Ex")) == pytest.approx(10.0)


def test_intensity_floor_percent_zero_for_other_channels(scope, live):
    scope.addons.squid_laser_engine = _fake_engine(10.0)
    assert live.get_intensity_floor_percent(_acquisition_channel("Fluorescence 488 nm Ex")) == 0.0


def test_intensity_floor_percent_zero_with_no_engine(scope, live):
    scope.addons.squid_laser_engine = None
    assert live.get_intensity_floor_percent(_acquisition_channel("Fluorescence 560 nm Ex")) == 0.0


def test_intensity_floor_percent_zero_when_the_engine_has_no_such_method(scope, live):
    """The 2024/25 laser engine addon has no intensity_floor_percent method."""
    scope.addons.squid_laser_engine = MagicMock(spec=["light_source", "get_latest_status"])
    assert live.get_intensity_floor_percent(_acquisition_channel("Fluorescence 560 nm Ex")) == 0.0


def test_update_illumination_clamps_up_to_the_floor(scope, live):
    scope.addons.squid_laser_engine = _fake_engine(10.0)
    scope.illumination_controller.set_intensity = MagicMock()
    live._log = MagicMock()

    live.currentConfiguration = _acquisition_channel("Fluorescence 560 nm Ex", intensity=5.0)
    live.update_illumination()
    scope.illumination_controller.set_intensity.assert_called_once_with(560, 10.0)
    warnings = [c.args[0] for c in live._log.warning.call_args_list]
    assert any("Clamping illumination intensity" in w and "channel minimum output" in w for w in warnings)

    live._log.reset_mock()
    scope.illumination_controller.set_intensity.reset_mock()
    live.currentConfiguration = _acquisition_channel("Fluorescence 560 nm Ex", intensity=50.0)
    live.update_illumination()
    scope.illumination_controller.set_intensity.assert_called_once_with(560, 50.0)
    assert live._log.warning.call_count == 0  # above the floor: no clamp, no warning


def _config(intensity=50.0):
    config = MagicMock()
    config.exposure_time = 10.0
    config.analog_gain = 0.0
    config.illumination_intensity = intensity
    config.z_offset_um = 0.0
    config.name = "ch"
    return config


def _channel_switch_stub(floor_percent, qtbot, cap_percent=100.0):
    """LiveControlWidget-shaped stub with real intensity controls, wired exactly like __init__ (~4663-4667):
    spinbox valueChanged -> the update method (update_config_illumination_intensity) and -> slider.setValue(int(x));
    slider valueChanged -> spinbox.setValue. This is what lets a cap/floor mismatch cause real Qt feedback
    (recursion, or the two controls fighting), not just a one-shot value check.
    """
    stub = MagicMock()
    stub.is_switching_mode = False
    stub.liveController.get_intensity_cap_percent.return_value = cap_percent
    stub.liveController.get_intensity_floor_percent.return_value = floor_percent
    stub.liveController.is_confocal_mode.return_value = False

    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    slider.setRange(0, 100)
    qtbot.addWidget(slider)
    stub.slider_illuminationIntensity = slider

    spin = control.widgets.QDoubleSpinBox()
    spin.setKeyboardTracking(False)
    spin.setRange(0, 100)
    qtbot.addWidget(spin)
    stub.entry_illuminationIntensity = spin

    spin.valueChanged.connect(lambda v: control.widgets.LiveControlWidget.update_config_illumination_intensity(stub, v))
    spin.valueChanged.connect(lambda x: slider.setValue(int(x)))
    slider.valueChanged.connect(spin.setValue)

    return stub, _config(50.0)


def test_live_control_widget_floors_intensity_controls_on_channel_switch(qtbot):
    stub, config = _channel_switch_stub(floor_percent=10.0, qtbot=qtbot)
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)

    assert stub.entry_illuminationIntensity.minimum() == pytest.approx(10.0)
    stub.slider_illuminationIntensity.setValue(3)
    assert stub.slider_illuminationIntensity.value() == 10


def test_live_control_widget_zero_floor_changes_nothing(qtbot):
    stub, config = _channel_switch_stub(floor_percent=0.0, qtbot=qtbot)
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)

    assert stub.entry_illuminationIntensity.minimum() == pytest.approx(0.0)
    stub.slider_illuminationIntensity.setValue(3)
    assert stub.slider_illuminationIntensity.value() == 3


# ---- floor/cap interplay: bounded clamp in CappedSlider, one ceiled floor for slider and spinbox ---------------------


@contextlib.contextmanager
def _bounded_slider_changes():
    """Counts CappedSlider.sliderChange invocations for the duration of the block. A RecursionError raised
    inside an overridden Qt virtual method does not reliably reach the Python caller or sys.excepthook
    (formatting a deep RecursionError can itself run out of stack), so the tests assert a bounded call count:
    that catches the cap and floor clamps oscillating forever directly and deterministically."""
    orig = control.widgets.CappedSlider.sliderChange
    count = [0]

    def counted(self, change):
        count[0] += 1
        return orig(self, change)

    control.widgets.CappedSlider.sliderChange = counted
    try:
        yield count
    finally:
        control.widgets.CappedSlider.sliderChange = orig


def test_channel_switch_from_a_floor_to_a_lower_cap_does_not_recurse(qtbot):
    """Switching from the 560 channel (floor 10) to a channel capped at 5 while the old floor is still set must
    not recurse forever between the cap and the floor clamps."""
    stub, _ = _channel_switch_stub(floor_percent=10.0, qtbot=qtbot, cap_percent=100.0)
    with _bounded_slider_changes() as count:
        control.widgets.LiveControlWidget.update_ui_for_mode(stub, _config(50.0))  # the 560 channel

        stub.liveController.get_intensity_cap_percent.return_value = 5.0
        stub.liveController.get_intensity_floor_percent.return_value = 0.0
        control.widgets.LiveControlWidget.update_ui_for_mode(stub, _config(3.0))  # then a channel capped at 5 %

    assert 0 < count[0] <= 20  # > 0: the counting hook really intercepted the slider
    assert stub.slider_illuminationIntensity.value() <= 5
    assert stub.entry_illuminationIntensity.value() <= 5


def test_floor_above_cap_settles_at_the_floor(qtbot):
    """A channel whose own floor is above its own cap must not recurse - the floor wins (the bounded clamp
    applies the cap first, then the floor)."""
    stub, _ = _channel_switch_stub(floor_percent=20.0, qtbot=qtbot, cap_percent=10.0)
    with _bounded_slider_changes() as count:
        control.widgets.LiveControlWidget.update_ui_for_mode(stub, _config(50.0))

    assert 0 < count[0] <= 20  # > 0: the counting hook really intercepted the slider
    assert stub.slider_illuminationIntensity.value() == 20
    assert stub.entry_illuminationIntensity.value() == pytest.approx(20.0)


def test_noninteger_floor_slider_and_spinbox_agree_after_editing_to_the_minimum(qtbot):
    """With a non-integer floor the slider (integer units) and the spinbox (2-decimal rounding) must land on the
    same ceiled floor, and editing down to the minimum must not spam update_illumination (without the shared
    ceiled floor the two controls fought: hundreds of calls and a RecursionError)."""
    stub, _ = _channel_switch_stub(floor_percent=100 * 200 / 1950, qtbot=qtbot, cap_percent=100.0)  # 10.256...%
    with _bounded_slider_changes() as count:
        control.widgets.LiveControlWidget.update_ui_for_mode(stub, _config(50.0))

        calls_before = stub.liveController.update_illumination.call_count
        stub.entry_illuminationIntensity.setValue(10.0)  # e.g. the down arrow stops at the spinbox minimum (11)

    assert 0 < count[0] <= 20  # > 0: the counting hook really intercepted the slider
    assert stub.slider_illuminationIntensity.value() == 11
    assert stub.entry_illuminationIntensity.value() == pytest.approx(11.0)
    assert stub.liveController.update_illumination.call_count - calls_before <= 3


def test_capped_slider_set_floor_clamps_values_below_it(qtbot):
    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    qtbot.addWidget(slider)
    slider.setRange(0, 100)
    slider.set_floor(10)

    slider.setValue(3)
    assert slider.value() == 10

    slider.setValue(50)
    assert slider.value() == 50


def test_capped_slider_overlay_region_nonempty_with_floor_or_cap(qtbot):
    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    qtbot.addWidget(slider)
    slider.setRange(0, 100)
    slider.setValue(50)

    opt = control.widgets.QStyleOptionSlider()
    slider.initStyleOption(opt)
    groove = slider.style().subControlRect(
        control.widgets.QStyle.CC_Slider, opt, control.widgets.QStyle.SC_SliderGroove, slider
    )

    slider.set_floor(10)
    region = slider._overlay_region()
    assert region is not None and not region.isEmpty()
    assert region.boundingRect().left() == groove.x()  # the floor's gray region is on the low side of the groove

    slider.set_floor(0)
    slider.set_cap(80)
    assert slider._overlay_region() is not None and not slider._overlay_region().isEmpty()
