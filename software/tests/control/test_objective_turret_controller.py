"""Tests for ObjectiveTurret4PosControllerSimulation (no hardware required)."""

from __future__ import annotations

import pytest

import control._def
from control._def import OBJECTIVE_TURRET_POSITIONS, OBJECTIVE_RETRACTED_POS_MM
from control.objective_turret_controller import (
    ObjectiveTurret4PosControllerSimulation,
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
