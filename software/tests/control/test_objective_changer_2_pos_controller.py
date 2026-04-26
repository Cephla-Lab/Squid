"""Tests for the move_to_objective dispatcher on the Xeryon 2-pos simulation."""

from __future__ import annotations

import control._def
from control.objective_changer_2_pos_controller import (
    ObjectiveChanger2PosController_Simulation,
)


class FakeStage:
    def __init__(self):
        self.z_moves: list[float] = []

    def move_z(self, delta_mm: float):
        self.z_moves.append(delta_mm)


def test_move_to_objective_dispatches_pos1(monkeypatch):
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", ["4x", "10x"])
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", ["20x", "40x"])
    sim = ObjectiveChanger2PosController_Simulation(sn="SIM", stage=FakeStage())
    sim.move_to_objective("4x")
    assert sim.currentPosition() == 1


def test_move_to_objective_dispatches_pos2(monkeypatch):
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", ["4x", "10x"])
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", ["20x", "40x"])
    sim = ObjectiveChanger2PosController_Simulation(sn="SIM", stage=FakeStage())
    sim.move_to_objective("40x")
    assert sim.currentPosition() == 2


def test_move_to_objective_short_circuits_when_already_there(monkeypatch):
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", ["4x", "10x"])
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", ["20x", "40x"])
    stage = FakeStage()
    sim = ObjectiveChanger2PosController_Simulation(sn="SIM", stage=stage)
    sim.move_to_objective("40x")  # pos1 -> pos2: records Z move
    z_moves_after_first = list(stage.z_moves)
    sim.move_to_objective("40x")  # already at pos2: no extra Z move
    assert stage.z_moves == z_moves_after_first


def test_move_to_objective_unknown_raises(monkeypatch):
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", ["4x", "10x"])
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", ["20x", "40x"])
    import pytest

    sim = ObjectiveChanger2PosController_Simulation(sn="SIM", stage=FakeStage())
    with pytest.raises(KeyError):
        sim.move_to_objective("999x")
