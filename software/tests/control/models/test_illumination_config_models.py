"""Tests for illumination channel config models, focused on max_output."""

import pytest
from pydantic import ValidationError

from control.core.config import ConfigRepository
from control.models.illumination_config import IlluminationChannel, IlluminationChannelConfig, IlluminationType


def _channel(**overrides):
    data = {
        "name": "Fluorescence 405 nm Ex",
        "type": IlluminationType.EPI_ILLUMINATION,
        "controller_port": "D1",
        "wavelength_nm": 405,
    }
    data.update(overrides)
    return IlluminationChannel(**data)


def test_max_output_defaults_to_full_output_when_absent():
    channel = _channel()
    assert channel.max_output == 1.0


def test_max_output_accepts_fractional_value():
    channel = _channel(max_output=0.2)
    assert channel.max_output == 0.2


@pytest.mark.parametrize("bad_value", [0, -0.1, 1.5])
def test_max_output_rejects_out_of_range_values(bad_value):
    with pytest.raises(ValidationError):
        _channel(max_output=bad_value)


def test_max_output_missing_in_yaml_loads_as_full_output(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(
        """\
version: 1
controller_port_mapping:
  D1: 11
channels:
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
"""
    )
    config = ConfigRepository(base_path=tmp_path).get_illumination_config()
    assert config.channels[0].max_output == 1.0


def test_max_output_round_trips_through_repository(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    repo = ConfigRepository(base_path=tmp_path)
    config = IlluminationChannelConfig(channels=[_channel(max_output=0.2)])
    repo.save_illumination_config(config)

    reloaded = ConfigRepository(base_path=tmp_path).get_illumination_config()
    assert reloaded.channels[0].max_output == 0.2
