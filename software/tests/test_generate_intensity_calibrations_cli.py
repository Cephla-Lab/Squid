"""tools/generate_intensity_calibrations.py: the headless calibration runs end to end in simulation."""

import importlib.util
from pathlib import Path

import pytest

from control.core.config import ConfigRepository

TOOL = Path(__file__).resolve().parents[1] / "tools" / "generate_intensity_calibrations.py"
YAML = """\
version: 1
controller_port_mapping:
  D1: 11
channels:
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
"""


def _tool():
    spec = importlib.util.spec_from_file_location("generate_intensity_calibrations", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_calibrates_and_saves_in_simulation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # main() changes into software/; monkeypatch restores the cwd afterwards
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    repo = ConfigRepository(base_path=tmp_path)

    status = _tool().main(
        ["--simulation", "--settle-s", "0", "--hold-s", "0", "--rest-s", "0", "--save"], config_repo=repo
    )

    assert status == 0
    out = capsys.readouterr().out
    assert "Fluorescence 405 nm Ex: " in out
    assert "Illumination watchdog armed" in out  # the simulated controller reports firmware 1.1+
    assert (tmp_path / "machine_configs" / "intensity_calibrations" / "405nm_D1.csv").is_file()
    assert repo.get_illumination_config().channels[0].intensity_calibration_file == "405nm_D1.csv"


def test_cli_refuses_a_sensor_limit_above_the_sensor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    repo = ConfigRepository(base_path=tmp_path)
    assert _tool().main(["--simulation", "--sensor-limit-mw", "900"], config_repo=repo) == 2


def test_cli_with_no_matching_channel_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    repo = ConfigRepository(base_path=tmp_path)
    assert _tool().main(["--simulation", "--channels", "nope"], config_repo=repo) == 2


def test_cli_saves_a_failed_channel_only_when_told_to(tmp_path, monkeypatch, capsys):
    # the bench 561 nm LED failed verification and --save saved it with no warning; the GUI asks first
    monkeypatch.chdir(tmp_path)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr("squid.intensity_calibration_run.VERIFY_REL_TOL", 1e-9)  # every channel fails
    saved = tmp_path / "machine_configs" / "intensity_calibrations" / "405nm_D1.csv"
    args = ["--simulation", "--settle-s", "0", "--hold-s", "0", "--rest-s", "0", "--save"]

    assert _tool().main(args, config_repo=repo) == 1
    assert not saved.exists() and repo.get_illumination_config().channels[0].intensity_calibration_file is None
    assert "not saved (failed verification); --save-failed saves it anyway" in capsys.readouterr().out

    assert _tool().main(args + ["--save-failed"], config_repo=repo) == 1
    assert saved.is_file() and repo.get_illumination_config().channels[0].intensity_calibration_file == "405nm_D1.csv"


@pytest.mark.parametrize("flag", ["USE_LDI_SERIAL_CONTROL", "USE_CELESTA_ETHERNET_CONTROL", "USE_ANDOR_LASER_CONTROL"])
def test_cli_refuses_an_instrument_whose_light_source_sets_the_power_before_opening_hardware(
    tmp_path, monkeypatch, capsys, flag
):
    import control._def
    import control.microcontroller

    monkeypatch.chdir(tmp_path)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    monkeypatch.setattr(control._def, flag, True)
    opened = []
    monkeypatch.setattr(
        control.microcontroller, "get_microcontroller_serial_device", lambda *a, **k: opened.append("controller")
    )

    status = _tool().main([], config_repo=ConfigRepository(base_path=tmp_path))

    assert status == 2 and opened == []
    assert "sets illumination power in the light source itself" in capsys.readouterr().out


def test_cli_in_simulation_calibrates_as_the_simulated_gui_does(tmp_path, monkeypatch):
    # in simulation Squid builds no external light source: the controller DAC drives the channels, as in the GUI
    import control._def

    monkeypatch.chdir(tmp_path)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    monkeypatch.setattr(control._def, "USE_LDI_SERIAL_CONTROL", True)
    args = ["--simulation", "--settle-s", "0", "--hold-s", "0", "--rest-s", "0"]
    assert _tool().main(args, config_repo=ConfigRepository(base_path=tmp_path)) == 0


def test_cli_refuses_a_laser_engine_instrument(tmp_path, monkeypatch, capsys):
    import control._def

    monkeypatch.chdir(tmp_path)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    monkeypatch.setattr(control._def, "LASER_ENGINE", "v2")
    status = _tool().main(["--simulation"], config_repo=ConfigRepository(base_path=tmp_path))
    assert status == 2
    assert "calibrate its lines from the GUI" in capsys.readouterr().out


def test_laser_engine_v2_is_the_light_source_in_simulation_too(monkeypatch):
    import control._def
    from control.lighting import LightSourceType, external_light_source

    monkeypatch.setattr(control._def, "LASER_ENGINE", "v2")
    assert external_light_source(True) == external_light_source(False) == LightSourceType.CephlaLaserEngineV2
