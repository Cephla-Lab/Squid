"""A laser engine v2 instrument for calibration tests: the simulated engine and 560 nm source behind a real
IlluminationController, wired as Microscope wires them, on a temporary illumination config. Close it with
instrument.engine.close()."""

from types import SimpleNamespace

from control.core.config import ConfigRepository
from control.laser_engine_v2 import LaserEngineV2, wire_illumination
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_sim import FakeEngine, FakeSource
from control.lighting import IlluminationController, IntensityControlMode, LightSourceType, ShutterControlMode
from tests.squid.calibration_fixtures import FakeMicrocontroller

# Ports as the example config has them (D3 and D4 codes are swapped); 488 nm has Max Output 0.5, which engine lines
# ignore (the firmware ceiling is the maximum)
ENGINE_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  D3: 14
  D4: 13
  D5: 15
  USB1: 0
channels:
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
  - name: Fluorescence 488 nm Ex
    type: epi_illumination
    controller_port: D2
    wavelength_nm: 488
    max_output: 0.5
  - name: Fluorescence 560 nm Ex
    type: epi_illumination
    controller_port: D3
    wavelength_nm: 560
  - name: Fluorescence 638 nm Ex
    type: epi_illumination
    controller_port: D4
    wavelength_nm: 638
"""


def engine_instrument(tmp_path, serial="SN-1", fake=None, yaml=ENGINE_YAML, start=True):
    machine_configs = tmp_path / "machine_configs"
    (machine_configs / "intensity_calibrations").mkdir(parents=True, exist_ok=True)
    if yaml is not None:  # None: no illumination config file at all
        (machine_configs / "illumination_channel_config.yaml").write_text(yaml)
    repo = ConfigRepository(base_path=tmp_path)
    fake = fake or FakeEngine(tok_delay_polls=0)
    source = FakeSource()
    engine = LaserEngineV2(
        link_factory=lambda: EngineLink(fake),
        source_factory=lambda: source,
        query_interval_s=0.01,
        serial_number=serial,
    )
    engine.sim_engine, engine.sim_source = fake, source  # as build_simulated_engine does
    mcu = FakeMicrocontroller(0.6)
    illumination = IlluminationController(
        mcu,
        IntensityControlMode.Software,
        ShutterControlMode.TTL,
        LightSourceType.CephlaLaserEngineV2,
        engine.light_source,
        config_repo=repo,
    )
    wire_illumination(engine, illumination)
    if start:
        engine.start()  # the poll (heartbeat) and 560 source threads run, as in Squid
    return SimpleNamespace(
        repo=repo,
        fake=fake,
        source=source,
        engine=engine,
        mcu=mcu,
        illumination=illumination,
        calibrations=machine_configs / "intensity_calibrations",
    )
