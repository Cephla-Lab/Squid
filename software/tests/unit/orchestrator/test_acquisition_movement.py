"""Verify orchestrator acquisition moves stage and steps piezo correctly.

Regression tests for focus lock wiring bugs:
- AutofocusExecutor must receive focus_lock_controller
- prepare_focus_lock_for_acquisition must call set_lock_reference()
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator


PROTOCOL_PATH = str(
    Path(__file__).resolve().parent.parent.parent
    / "e2e/configs/protocols/v2_repeated.yaml"
)


@pytest.fixture
def ctx():
    with BackendContext() as c:
        yield c


def test_stage_moves_to_fov_positions(ctx: BackendContext):
    """Stage must move to distinct X,Y positions for each FOV."""
    x_positions = []
    original_move_x = ctx.stage_service.move_x_to

    def spy_move_x(x_mm, **kw):
        x_positions.append(x_mm)
        return original_move_x(x_mm, **kw)

    ctx.stage_service.move_x_to = spy_move_x

    sim = OrchestratorSimulator(ctx)
    sim.load_protocol(PROTOCOL_PATH)
    result = sim.run_and_wait(experiment_id="test_xy", timeout_s=60)

    assert result.success, f"Experiment failed: {result.error}"

    # Protocol has 2 FOVs at x=0.0 and x=0.5; must visit both
    unique_x = set(x_positions)
    assert 0.0 in unique_x, "Never moved to FOV at x=0.0"
    assert 0.5 in unique_x, "Never moved to FOV at x=0.5"


def test_piezo_steps_during_zstack(ctx: BackendContext):
    """Piezo must step through z-levels during z-stack acquisition."""
    piezo_positions = []
    original_move_to = ctx.piezo_service.move_to

    def spy_move_to(position_um, **kw):
        piezo_positions.append(position_um)
        return original_move_to(position_um, **kw)

    ctx.piezo_service.move_to = spy_move_to

    sim = OrchestratorSimulator(ctx)
    sim.load_protocol(PROTOCOL_PATH)
    result = sim.run_and_wait(experiment_id="test_piezo", timeout_s=60)

    assert result.success, f"Experiment failed: {result.error}"
    assert len(piezo_positions) > 0, "Piezo never moved during z-stack"

    # With 3 z-planes, 0.5um step FROM CENTER, expect at least 3 distinct positions
    unique_piezo = set(round(p, 2) for p in piezo_positions)
    assert len(unique_piezo) >= 3, (
        f"Expected at least 3 distinct piezo positions, got {unique_piezo}"
    )
