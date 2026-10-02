"""The distance of CephlaStage's Z backlash move follows Z_BACKLASH_COMPENSATION_UM.

A blocking downward Z move is two commands: past the target by the backlash distance, then back up to it.
The distance was a constant of 5 um; it is now a setting with that default.
"""

import importlib.util

import pytest

import control._def as _def
import squid.config
import squid.stage.cephla
from control._def import CMD_SET
from control.microcontroller import Microcontroller, SimSerial


class _RecordingSerial(SimSerial):
    """SimSerial that keeps the Z commands it receives, in microsteps."""

    def __init__(self):
        super().__init__()
        self.z_commands = []

    def _respond_to(self, write_bytes):
        if write_bytes[1] in (CMD_SET.MOVE_Z, CMD_SET.MOVETO_Z):
            self.z_commands.append((write_bytes[1], self.unpack_position(write_bytes[2:6])))
        super()._respond_to(write_bytes)


def _cephla_module_with(monkeypatch, backlash_um):
    """A private copy of squid.stage.cephla, defined while the setting is `backlash_um`.

    The class reads the setting when it is defined. Reloading the real module would replace the class that the
    rest of the test session holds, so the copy is loaded under another name and the real module stays as it is.
    """
    monkeypatch.setattr(_def, "Z_BACKLASH_COMPENSATION_UM", backlash_um)
    spec = importlib.util.spec_from_file_location("cephla_with_pinned_backlash", squid.stage.cephla.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def make_stage(monkeypatch):
    """CephlaStage on a recording SimSerial, with the backlash setting pinned to `backlash_um`."""
    created = []

    def _make(backlash_um):
        serial = _RecordingSerial()
        mc = Microcontroller(serial, True)
        created.append(mc)
        stage = _cephla_module_with(monkeypatch, backlash_um).CephlaStage(mc, squid.config.get_stage_config())
        stage.move_z_to(1.0)
        serial.z_commands.clear()
        return stage, serial

    yield _make

    for mc in created:
        mc.close()


def _usteps(stage, um):
    return stage.get_config().Z_AXIS.convert_real_units_to_ustep(um / 1000.0)


def test_default_is_today_s_5_um(monkeypatch):
    # the default in the code, whatever the machine's ini says
    lines = open(_def.__file__, encoding="utf-8").read().splitlines()
    assert "Z_BACKLASH_COMPENSATION_UM = 5.0" in lines
    assert _cephla_module_with(monkeypatch, 5.0).CephlaStage._BACKLASH_COMPENSATION_DISTANCE_MM == pytest.approx(0.005)


def test_distance_follows_the_setting(make_stage):
    stage, _ = make_stage(3.0)
    assert stage._BACKLASH_COMPENSATION_DISTANCE_MM == pytest.approx(0.003)


def test_relative_downward_move_with_5_um(make_stage):
    stage, serial = make_stage(5.0)
    stage.move_z(-0.010)
    assert serial.z_commands == [(CMD_SET.MOVE_Z, _usteps(stage, -15.0)), (CMD_SET.MOVE_Z, _usteps(stage, 5.0))]


def test_relative_downward_move_with_3_um(make_stage):
    stage, serial = make_stage(3.0)
    stage.move_z(-0.010)
    assert serial.z_commands == [(CMD_SET.MOVE_Z, _usteps(stage, -13.0)), (CMD_SET.MOVE_Z, _usteps(stage, 3.0))]
    assert stage.get_pos().z_mm == pytest.approx(0.990, abs=1e-3)


def test_absolute_downward_move_with_3_um(make_stage):
    stage, serial = make_stage(3.0)
    stage.move_z_to(0.5)
    assert serial.z_commands == [(CMD_SET.MOVETO_Z, _usteps(stage, 497.0)), (CMD_SET.MOVETO_Z, _usteps(stage, 500.0))]


def test_upward_move_is_one_command(make_stage):
    stage, serial = make_stage(3.0)
    stage.move_z(0.010)
    assert serial.z_commands == [(CMD_SET.MOVE_Z, _usteps(stage, 10.0))]


def test_below_zero_counts_as_zero(make_stage):
    # A negative distance would make the final approach come from above.
    stage, serial = make_stage(-2.0)
    assert stage._BACKLASH_COMPENSATION_DISTANCE_MM == 0.0
    stage.move_z(-0.010)
    assert serial.z_commands[0] == (CMD_SET.MOVE_Z, _usteps(stage, -10.0))
    assert stage.get_pos().z_mm == pytest.approx(0.990, abs=1e-3)
