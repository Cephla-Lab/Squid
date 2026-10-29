"""Shared setup for tests that drive a LiveController or the GUI against a spinning-disk unit."""

import control._def
import control.core.live_controller
import control.microscope
from control.core.config import ConfigRepository
from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
channels:
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
"""


def enable_spinning_disk(monkeypatch, *modules, dragonfly: bool):
    """Turn the confocal flags on in every module that star-imported them from control._def."""
    for module in (control._def, control.core.live_controller, *modules):
        monkeypatch.setattr(module, "ENABLE_SPINNING_DISK_CONFOCAL", True)
        monkeypatch.setattr(module, "USE_DRAGONFLY", dragonfly)


def make_channel(**overrides) -> AcquisitionChannel:
    """A 405 nm channel matching ILLUMINATION_YAML; overrides go straight to AcquisitionChannel."""
    fields = dict(
        name="Fluorescence 405 nm Ex",
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel="Fluorescence 405 nm Ex", intensity=10.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=0.0),
    )
    fields.update(overrides)
    return AcquisitionChannel(**fields)


def build_simulated_microscope(tmp_path) -> control.microscope.Microscope:
    """A simulated Microscope whose config repository reads ILLUMINATION_YAML from tmp_path."""
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    microscope = control.microscope.Microscope.build_from_global_config(True)
    microscope.config_repo = ConfigRepository(base_path=tmp_path)
    return microscope
