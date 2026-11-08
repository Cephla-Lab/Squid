"""IlluminationController applies calibrations (design §6.1-§6.3): which file, the factor, the ceiling, the cap,
the description, and recovery when a file changes under a running app."""

import os
from unittest.mock import MagicMock

import pytest

import control.lighting
from control.core.config import ConfigRepository
from control.lighting import IlluminationController
from squid.intensity_calibration import write_calibration
from squid.power_meter import simulated_led_mw
from tests.squid.calibration_fixtures import DAC, make_calibration, master_lookup, write_legacy_csv
from tests.tools import get_test_microcontroller

YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  D3: 13
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
    intensity_calibration_file: 488nm_D2.csv
"""

SHARED_488 = """\
  - name: LED 488
    type: epi_illumination
    controller_port: D3
    wavelength_nm: 488
    intensity_calibration_file: 488nm_D3.csv
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "machine_configs" / "intensity_calibrations").mkdir(parents=True)
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    return ConfigRepository(base_path=tmp_path)


@pytest.fixture
def micro():
    mcu = get_test_microcontroller()
    mcu.set_illumination = MagicMock()
    return mcu


@pytest.fixture
def controller(micro, repo):
    c = IlluminationController(micro, config_repo=repo)
    c._log = MagicMock()
    return c


def _dir(repo):
    return repo.machine_configs_path / "intensity_calibrations"


def _sent(micro):
    return micro.set_illumination.call_args.args


def _bump_mtime(path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


def _logged(controller, level):
    return [call.args[1] for call in controller._log.log.call_args_list if call.args[0] == level]


def test_no_calibration_sends_intensity_as_dac_percent(controller, micro):
    controller.set_intensity(405, 37.5)
    assert _sent(micro) == (11, 37.5)


def test_healthy_legacy_file_matches_master_exactly(controller, micro, repo):
    path = _dir(repo) / "405.csv"
    write_legacy_csv(path, DAC, simulated_led_mw(DAC / 100 * 0.45))  # rises and saturates, no rollover
    for r in (0.0, 0.1, 1.0, 12.3, 50.0, 99.9, 100.0):
        controller.set_intensity(405, r)
        assert _sent(micro) == (11, float(master_lookup(path, r)))


def test_new_format_file_referenced_in_the_yaml_is_applied(controller, micro, repo):
    calibration = make_calibration(wavelength_nm=488, port="D2", channel="Fluorescence 488 nm Ex", factor=0.8)
    write_calibration(calibration, _dir(repo) / "488nm_D2.csv")
    controller.set_intensity(488, 50)
    expected, _ = calibration.commanded_percent(50, micro.illumination_intensity_factor, 1.0)
    assert _sent(micro) == (12, pytest.approx(expected))  # the file stores 10 significant digits


def test_dangling_reference_falls_back_to_the_wavelength_file(controller, micro, repo):
    path = _dir(repo) / "488.csv"
    write_legacy_csv(path, DAC, simulated_led_mw(DAC / 100 * 0.45))
    controller.set_intensity(488, 50)
    assert _sent(micro) == (12, float(master_lookup(path, 50)))


def test_corrupt_file_logs_once_and_recovers_when_fixed(controller, micro, repo):
    path = _dir(repo) / "488nm_D2.csv"
    calibration = make_calibration(wavelength_nm=488, port="D2", channel="Fluorescence 488 nm Ex")
    write_calibration(calibration, path)
    controller.set_intensity(488, 50)
    calibrated = _sent(micro)

    path.write_text("\x00garbage")
    _bump_mtime(path)
    controller.set_intensity(488, 40)
    controller.set_intensity(488, 40)
    assert _sent(micro) == (12, 40)
    assert len(_logged(controller, control.lighting.logging.ERROR)) == 1

    write_calibration(calibration, path)
    _bump_mtime(path)
    controller.set_intensity(488, 50)
    assert _sent(micro) == calibrated


def test_cap_by_calibration_kind(controller, repo):
    assert controller.get_intensity_cap_percent(405, 0.5) == 50.0  # none
    write_legacy_csv(_dir(repo) / "405.csv", DAC, simulated_led_mw(DAC / 100 * 0.45))
    assert controller.get_intensity_cap_percent(405, 0.5) == 50.0  # legacy keeps master's cap
    write_calibration(make_calibration(wavelength_nm=488, port="D2"), _dir(repo) / "488nm_D2.csv")
    assert controller.get_intensity_cap_percent(488, 0.5) == 100.0  # ceiling inside the lookup


def test_describe_intensity(controller, micro, repo):
    factor = micro.illumination_intensity_factor
    assert controller.describe_intensity(405) == {
        "intensity_unit": "dac_percent",
        "illumination_intensity_factor": factor,
    }
    write_calibration(make_calibration(wavelength_nm=488, port="D2"), _dir(repo) / "488nm_D2.csv")
    description = controller.describe_intensity(488)
    assert description["intensity_unit"] == "power_percent"
    assert description["calibration_file"] == "488nm_D2.csv"
    assert description["illumination_intensity_factor"] == factor
    software = IlluminationController(
        micro, intensity_control_mode=control.lighting.IntensityControlMode.Software, config_repo=repo
    )
    assert software.describe_intensity(488) == {"intensity_unit": "source_percent"}


def test_shared_wavelength_uses_the_calibration_of_the_driven_channel(tmp_path, micro):
    (tmp_path / "machine_configs" / "intensity_calibrations").mkdir(parents=True)
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML + SHARED_488)
    repo = ConfigRepository(base_path=tmp_path)
    write_calibration(make_calibration(wavelength_nm=488, port="D2"), _dir(repo) / "488nm_D2.csv")
    led = make_calibration(model=simulated_led_mw, wavelength_nm=488, port="D3", channel="LED 488")
    write_calibration(led, _dir(repo) / "488nm_D3.csv")
    controller = IlluminationController(micro, config_repo=repo)

    assert controller.channel_mappings_TTL[488] == 13  # the TTL mapping keeps the last channel
    controller.set_intensity(488, 50)
    assert _sent(micro) == (13, pytest.approx(led.commanded_percent(50, micro.illumination_intensity_factor, 1.0)[0]))


def test_stale_factor_is_logged_once_and_the_top_clamps(controller, micro, repo):
    calibration = make_calibration(wavelength_nm=488, port="D2", factor=1.0)
    write_calibration(calibration, _dir(repo) / "488nm_D2.csv")
    micro.illumination_intensity_factor = 0.6
    controller.set_intensity(488, 100)
    controller.set_intensity(488, 100)
    assert _sent(micro) == (12, 100.0)
    warnings = _logged(controller, control.lighting.logging.WARNING)
    assert sum("lowered" in w for w in warnings) == 1
    assert sum("clamped" in w for w in warnings) == 1


def test_lowered_max_output_clamps_the_dac(controller, micro, repo):
    write_calibration(make_calibration(wavelength_nm=488, port="D2", factor=0.6), _dir(repo) / "488nm_D2.csv")
    config = repo.get_illumination_config(for_edit=True)
    config.channels[2].max_output = 0.3
    repo.save_illumination_config(config)
    micro.illumination_intensity_factor = 0.6
    controller.set_intensity(488, 100)
    assert _sent(micro) == (12, 30.0)


def test_a_calibration_for_another_port_is_not_applied(controller, micro, repo):
    # measured on D2; the channel was moved to D3 in the channel editor afterwards
    write_calibration(make_calibration(wavelength_nm=488, port="D2"), _dir(repo) / "488nm_D2.csv")
    config = repo.get_illumination_config(for_edit=True)
    config.channels[2].controller_port = "D3"
    repo.save_illumination_config(config)

    controller.set_intensity(488, 50)
    controller.set_intensity(488, 50)
    assert _sent(micro) == (13, 50)
    assert controller.describe_intensity(488)["intensity_unit"] == "dac_percent"
    assert controller.get_intensity_cap_percent(488, 0.5) == 50.0
    mismatch = [w for w in _logged(controller, control.lighting.logging.WARNING) if "D2" in w and "D3" in w]
    assert len(mismatch) == 1


def test_a_calibration_for_a_remapped_port_is_not_applied(controller, micro, repo):
    # review 4: the channel stays on D2, but D2 now drives controller source 13 instead of 12
    write_calibration(make_calibration(wavelength_nm=488, port="D2", source_code=12), _dir(repo) / "488nm_D2.csv")
    config = repo.get_illumination_config(for_edit=True)
    config.controller_port_mapping["D2"] = 13
    repo.save_illumination_config(config)
    controller.set_intensity(488, 50)
    assert _sent(micro) == (13, 50)  # uncalibrated: the % goes out as DAC %
    assert controller.describe_intensity(488)["intensity_unit"] == "dac_percent"
