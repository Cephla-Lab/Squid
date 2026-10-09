"""Tests for per-channel max_output intensity capping.

The cap comes from the illumination channel's max_output (fraction of full
scale) and limits intensity to max_output*100 percent: in the GUI controls
and when illumination is actually applied.
"""

from unittest.mock import MagicMock

import pytest

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.microscope
import control.widgets
from control._def import LED_MATRIX_R_FACTOR
from control.core.config import ConfigRepository
from control.core.live_controller import LiveController
from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings
from squid.intensity_calibration import write_calibration
from tests.squid.calibration_fixtures import make_calibration

ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  USB1: 0
channels:
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
    max_output: 0.2
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
  - name: Fluorescence 488 nm Ex
    type: epi_illumination
    controller_port: D2
    wavelength_nm: 488
    max_output: 0.5
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


@pytest.mark.parametrize(
    "channel, expected_cap",
    [
        ("BF LED matrix full", 20.0),
        ("Fluorescence 405 nm Ex", 100.0),
        ("No Such Channel", 100.0),
    ],
)
def test_intensity_cap_percent(live, channel, expected_cap):
    """Cap comes from max_output; missing field or unknown channel falls back to 100%."""
    assert live.get_intensity_cap_percent(_acquisition_channel(channel)) == pytest.approx(expected_cap)


def test_update_illumination_clamps_led_matrix_intensity_to_cap(scope, live):
    live.currentConfiguration = _acquisition_channel("BF LED matrix full", intensity=100.0)
    scope.low_level_drivers.microcontroller.set_illumination_led_matrix = MagicMock()

    live.update_illumination()

    call = scope.low_level_drivers.microcontroller.set_illumination_led_matrix.call_args
    assert call.kwargs["r"] == pytest.approx((20.0 / 100) * LED_MATRIX_R_FACTOR)


@pytest.mark.parametrize("intensity, expected", [(80.0, 50.0), (30.0, 30.0)])
def test_update_illumination_clamps_laser_intensity_to_cap(scope, live, intensity, expected):
    live.currentConfiguration = _acquisition_channel("Fluorescence 488 nm Ex", intensity=intensity)
    scope.illumination_controller.set_intensity = MagicMock()

    live.update_illumination()

    scope.illumination_controller.set_intensity.assert_called_once_with(488, expected)


def _channel_switch_stub(cap_percent, qtbot):
    """LiveControlWidget-shaped stub with real intensity controls."""
    stub = MagicMock()
    stub.is_switching_mode = False
    stub.liveController.get_intensity_cap_percent.return_value = cap_percent
    stub.liveController.is_confocal_mode.return_value = False
    stub.liveController.get_intensity_description.return_value = {"intensity_unit": "dac_percent"}

    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    slider.setRange(0, 100)
    qtbot.addWidget(slider)
    stub.slider_illuminationIntensity = slider

    spin = control.widgets.GappedSpinBox()
    spin.setRange(0, 100)
    qtbot.addWidget(spin)
    stub.entry_illuminationIntensity = spin

    config = MagicMock()
    config.exposure_time = 10.0
    config.analog_gain = 0.0
    config.illumination_intensity = 100.0
    config.z_offset_um = 0.0
    config.name = "ch"
    return stub, config


def test_live_control_widget_caps_intensity_controls_on_channel_switch(qtbot):
    stub, config = _channel_switch_stub(cap_percent=20.0, qtbot=qtbot)
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)

    assert stub.entry_illuminationIntensity.maximum() == pytest.approx(20.0)
    assert stub.entry_illuminationIntensity.value() == pytest.approx(20.0)
    stub.slider_illuminationIntensity.setValue(50)
    assert stub.slider_illuminationIntensity.value() == 20


def test_capped_slider_clamps_values_above_cap(qtbot):
    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    qtbot.addWidget(slider)
    slider.setRange(0, 100)
    slider.set_cap(20)

    slider.setValue(50)
    assert slider.value() == 20

    slider.setValue(15)
    assert slider.value() == 15


def test_capped_slider_raising_cap_restores_full_range(qtbot):
    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    qtbot.addWidget(slider)
    slider.setRange(0, 100)
    slider.set_cap(20)
    slider.setValue(50)
    assert slider.value() == 20

    slider.set_cap(100)
    slider.setValue(50)
    assert slider.value() == 50


def _write_488_calibration(scope, tmp_path):
    calibrations = tmp_path / "machine_configs" / "intensity_calibrations"
    calibrations.mkdir(parents=True, exist_ok=True)
    factor = scope.low_level_drivers.microcontroller.illumination_intensity_factor
    calibration = make_calibration(
        wavelength_nm=488, port="D2", channel="Fluorescence 488 nm Ex", max_output=0.5, factor=factor
    )
    write_calibration(calibration, calibrations / "488.csv")


def test_calibrated_channel_is_not_capped_before_the_lut(scope, live, tmp_path):
    _write_488_calibration(scope, tmp_path)
    assert live.get_intensity_cap_percent(_acquisition_channel("Fluorescence 488 nm Ex")) == pytest.approx(100.0)
    live.currentConfiguration = _acquisition_channel("Fluorescence 488 nm Ex", intensity=80.0)
    scope.illumination_controller.set_intensity = MagicMock()
    live.update_illumination()
    scope.illumination_controller.set_intensity.assert_called_once_with(488, 80.0)


def test_intensity_description_names_the_unit(scope, live, tmp_path):
    assert live.get_intensity_description(_acquisition_channel("BF LED matrix full")) == {
        "intensity_unit": "dac_percent"
    }
    assert (
        live.get_intensity_description(_acquisition_channel("Fluorescence 405 nm Ex"))["intensity_unit"]
        == "dac_percent"
    )
    _write_488_calibration(scope, tmp_path)
    assert (
        live.get_intensity_description(_acquisition_channel("Fluorescence 488 nm Ex"))["intensity_unit"]
        == "power_percent"
    )


def test_live_control_widget_shows_the_intensity_unit(qtbot):
    stub, config = _channel_switch_stub(cap_percent=100.0, qtbot=qtbot)
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)
    assert stub.entry_illuminationIntensity.suffix() == " % DAC"

    stub.liveController.get_intensity_description.return_value = make_calibration().describe()
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)
    assert stub.entry_illuminationIntensity.suffix() == " % power"
    assert "Linear in power" in stub.entry_illuminationIntensity.toolTip()


def _gapped(qtbot, lowest):
    spin = control.widgets.GappedSpinBox()
    spin.setRange(0, 100)
    spin.setSingleStep(1)
    qtbot.addWidget(spin)
    spin.set_lowest(lowest)
    return spin


def test_the_intensity_box_has_no_values_between_off_and_the_lowest_power(qtbot):
    # the bench 488 nm laser gives nothing, then 6.7 % of its maximum: there is no 1-6 %
    spin = _gapped(qtbot, 6.7)
    spin.setValue(0.0)
    spin.stepBy(1)
    assert spin.value() == pytest.approx(6.7)  # up from off: the lowest power
    spin.stepBy(1)
    assert spin.value() == pytest.approx(7.7)
    spin.stepBy(-1)
    spin.stepBy(-1)
    assert spin.value() == 0.0  # down from the lowest power: off
    spin.setValue(3.0)
    assert spin.value() == pytest.approx(6.7)  # a slider or a saved 3 % shows what it gives
    assert spin.valueFromText("2") == pytest.approx(6.7)  # typed
    spin.setValue(50.0)
    assert spin.value() == pytest.approx(50.0)


def test_without_a_lowest_power_the_intensity_box_is_continuous(qtbot):
    spin = _gapped(qtbot, 0.0)
    spin.setValue(3.0)
    assert spin.value() == pytest.approx(3.0)
    spin.stepBy(-1)
    assert spin.value() == pytest.approx(2.0)


def test_live_control_widget_takes_the_lowest_power_from_the_calibration(qtbot):
    stub, config = _channel_switch_stub(cap_percent=100.0, qtbot=qtbot)
    config.illumination_intensity = 3.0  # a setting saved before the calibration
    stub.liveController.get_intensity_description.return_value = {
        **make_calibration().describe(),
        "lowest_percent": 6.7,
    }
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)
    assert stub.entry_illuminationIntensity.value() == pytest.approx(6.7)
    assert "Lowest non-zero: 6.7 %" in stub.entry_illuminationIntensity.toolTip()

    stub.liveController.get_intensity_description.return_value = {"intensity_unit": "dac_percent"}
    control.widgets.LiveControlWidget.update_ui_for_mode(stub, config)
    assert stub.entry_illuminationIntensity.value() == pytest.approx(3.0)  # another channel: no gap
