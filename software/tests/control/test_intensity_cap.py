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

    slider = control.widgets.CappedSlider(control.widgets.Qt.Horizontal)
    slider.setRange(0, 100)
    qtbot.addWidget(slider)
    stub.slider_illuminationIntensity = slider

    spin = control.widgets.QDoubleSpinBox()
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
