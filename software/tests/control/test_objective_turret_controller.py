"""Tests for ObjectiveTurret4PosControllerSimulation (no hardware required)."""

from __future__ import annotations

import pytest

import control._def
import control.objective_turret_controller as otc
from control._def import OBJECTIVE_TURRET_POSITIONS, OBJECTIVE_RETRACTED_POS_MM
from control.objective_turret_controller import (
    ObjectiveTurret4PosController,
    ObjectiveTurret4PosControllerSimulation,
    CW_DISABLE,
    CW_ENABLE,
    REG_CONTROL_WORD,
    REG_CURRENT_POSITION,
    REG_MICROSTEP,
    REG_STATUS_WORD,
    REG_TARGET_POSITION,
)


class FakeStage:
    """Records move_z_to calls and reports a preset Z position."""

    def __init__(self, z_mm: float = 3.5):
        self._z_mm = z_mm
        self.z_moves: list[float] = []

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self.z_moves.append(abs_mm)
        self._z_mm = abs_mm

    def get_pos(self):
        class _Pos:
            pass

        p = _Pos()
        p.z_mm = self._z_mm
        return p


def _make_sim(stage=None):
    return ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        stage=stage,
    )


def test_init_opens_controller():
    sim = _make_sim()
    assert sim.is_open
    assert sim.current_objective is None
    sim.close()


def test_home_clears_current_objective():
    sim = _make_sim()
    sim.move_to_objective("10x")
    sim.home()
    assert sim.current_objective is None
    sim.close()


@pytest.mark.parametrize("name", list(OBJECTIVE_TURRET_POSITIONS))
def test_move_to_each_known_objective(name):
    sim = _make_sim()
    sim.move_to_objective(name)
    assert sim.current_objective == name
    sim.close()


def test_move_unknown_objective_raises_key_error():
    sim = _make_sim()
    with pytest.raises(KeyError):
        sim.move_to_objective("1000x")
    sim.close()


def test_clear_alarm_is_callable():
    sim = _make_sim()
    sim.clear_alarm()
    assert sim.is_open
    sim.close()


def test_enable_is_callable():
    sim = _make_sim()
    sim.enable()
    assert sim.is_open
    sim.close()


def test_operations_after_close_raise():
    sim = _make_sim()
    sim.close()
    with pytest.raises(RuntimeError):
        sim.home()
    with pytest.raises(RuntimeError):
        sim.move_to_objective("10x")
    with pytest.raises(RuntimeError):
        sim.clear_alarm()
    with pytest.raises(RuntimeError):
        sim.enable()


def test_close_is_idempotent():
    sim = _make_sim()
    sim.close()
    sim.close()
    assert not sim.is_open


def test_context_manager_closes_on_exit():
    with _make_sim() as sim:
        sim.move_to_objective("20x")
        assert sim.is_open
    assert not sim.is_open


def test_move_to_objective_retracts_and_restores_z(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)

    sim.move_to_objective("40x")

    # First switch: retract to OBJECTIVE_RETRACTED_POS_MM, then restore captured z.
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM, 3.5]
    assert sim.current_objective == "40x"

    # Second call with same objective: no-op (early exit), no new z motion.
    stage.z_moves.clear()
    sim.move_to_objective("40x")
    assert stage.z_moves == []

    sim.close()


def test_move_to_objective_skips_z_retract_when_no_stage(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    sim = _make_sim(stage=None)
    sim.move_to_objective("10x")  # must not raise even without a stage
    assert sim.current_objective == "10x"
    sim.close()


def test_move_to_objective_skips_z_retract_when_homing_z_disabled(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)
    sim.move_to_objective("10x")
    assert stage.z_moves == []  # retract is gated on HOMING_ENABLED_Z
    assert sim.current_objective == "10x"
    sim.close()


def test_move_between_aliased_objectives_skips_z_retract(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions={"4x_A": 1, "4x_B": 1, "10x": 2},
        stage=stage,
    )

    sim.move_to_objective("4x_A")
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM, 3.5]
    stage.z_moves.clear()

    # Switching to a different name that maps to the same physical
    # position updates the tracked objective but skips the Z dance.
    sim.move_to_objective("4x_B")
    assert stage.z_moves == []
    assert sim.current_objective == "4x_B"

    sim.close()


def test_move_to_objective_skips_restore_when_restore_z_false(monkeypatch):
    # At startup Z was just homed to 0 (below the working floor), so the turret
    # retracts and rotates but must NOT restore Z; the cached-Z restore handles it.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)

    sim.move_to_objective("40x", restore_z=False)

    # Retract happened; the restore back to the captured Z did not.
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM]
    assert sim.current_objective == "40x"
    sim.close()


class _FakeModbus:
    """Minimal ModbusRTUClient stand-in that records register writes.

    Reads return values that drive the controller's wait loops straight to a
    completed/idle state; writes are recorded so tests can assert the control-word
    sequence (in particular, that the motor ends de-energized).
    """

    def __init__(self):
        self.connected = False
        self.writes = []  # (address, value) in order
        self._position = 0

    def connect(self, port=None, baudrate=None):
        self.connected = True

    def disconnect(self):
        self.connected = False

    @property
    def is_connected(self):
        return self.connected

    def read_register(self, slave_id, address):
        # Microstep register must be 0..7; 4 -> 16 microsteps.
        return 4 if address == REG_MICROSTEP else 0

    def read_register_32bit(self, slave_id, address, signed=False):
        return 0

    def read_input_register(self, slave_id, address):
        # Status word: neither RUNNING nor FAULT -> wait loops see "idle".
        return 0

    def read_input_register_32bit(self, slave_id, address, signed=False):
        # Report the commanded target as the live position so the move-complete
        # tolerance check passes immediately.
        return self._position if address == REG_CURRENT_POSITION else 0

    def write_register(self, slave_id, address, value):
        self.writes.append((address, value))

    def write_register_32bit(self, slave_id, address, value, signed=False):
        self.writes.append((address, value))
        if address == REG_TARGET_POSITION:
            self._position = value

    def control_word_writes(self):
        return [value for (address, value) in self.writes if address == REG_CONTROL_WORD]


def _make_real_controller(monkeypatch):
    fake = _FakeModbus()
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: fake)
    controller = ObjectiveTurret4PosController(serial_number="SIM", stage=None)
    return controller, fake


def test_init_leaves_motor_deenergized(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_move_to_objective_deenergizes_when_idle(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.move_to_objective("40x")
    # The motor energizes to rotate but is de-energized once the move completes.
    assert fake.control_word_writes()[-1] == CW_DISABLE
    assert CW_ENABLE in fake.control_word_writes()  # it did energize to move
    controller.close()


def test_home_deenergizes_when_idle(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.home()
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()
