"""
E2E tests validating stage X/Y movement and piezo Z-stepping
during orchestrator-driven acquisitions.

These tests verify the full orchestrator → ImagingExecutor → MultiPointController
→ MultiPointWorker → PositionController/ZStackExecutor path by monitoring
actual hardware service calls.

Key design insight validated here: the ImagingProtocol knows nothing about FOVs.
FOVs come from ScanCoordinates (either pre-configured or loaded from protocol CSV).
The protocol with `fovs: default` uses whatever is already in ScanCoordinates.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pytest

from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator


@dataclass
class MovementRecord:
    """Records of actual movement commands sent to services."""

    stage_x_moves: List[float] = field(default_factory=list)
    stage_y_moves: List[float] = field(default_factory=list)
    piezo_moves: List[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_stage_x(self, x_mm: float) -> None:
        with self._lock:
            self.stage_x_moves.append(x_mm)

    def record_stage_y(self, y_mm: float) -> None:
        with self._lock:
            self.stage_y_moves.append(y_mm)

    def record_piezo(self, position_um: float) -> None:
        with self._lock:
            self.piezo_moves.append(position_um)


def install_movement_monitor(ctx: BackendContext) -> MovementRecord:
    """Monkey-patch stage and piezo services to record all movement commands."""
    record = MovementRecord()

    stage = ctx.stage_service
    original_move_x = stage.move_x_to
    original_move_y = stage.move_y_to

    def tracked_move_x(x_mm, *args, **kwargs):
        record.record_stage_x(x_mm)
        return original_move_x(x_mm, *args, **kwargs)

    def tracked_move_y(y_mm, *args, **kwargs):
        record.record_stage_y(y_mm)
        return original_move_y(y_mm, *args, **kwargs)

    stage.move_x_to = tracked_move_x
    stage.move_y_to = tracked_move_y

    piezo = ctx.piezo_service
    if piezo is not None:
        original_move_to = piezo.move_to

        def tracked_piezo_move(position_um, *args, **kwargs):
            record.record_piezo(position_um)
            return original_move_to(position_um, *args, **kwargs)

        piezo.move_to = tracked_piezo_move

    return record


SIMULATION_DIR = Path(__file__).parent.parent / "configs" / "simulation"


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestOrchestratorMovement:
    """Tests that the orchestrator actually moves stage and piezo during acquisition."""

    def test_stage_moves_to_distinct_fov_positions(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Verify stage moves to distinct X,Y positions for each FOV.

        Uses fovs: default so the protocol respects pre-configured coordinates.
        """
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)

        # Set up 3 FOVs at well-separated positions (within stage limits)
        sim.add_single_fov("pos_A", x=20.0, y=20.0, z=0.5)
        sim.add_single_fov("pos_B", x=30.0, y=20.0, z=0.5)
        sim.add_single_fov("pos_C", x=20.0, y=30.0, z=0.5)

        # Use protocol with fovs: default (uses ScanCoordinates, no CSV override)
        protocol_path = str(SIMULATION_DIR / "movement_test.yaml")
        sim.load_protocol(protocol_path)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"

        # Should have X moves to at least 2 distinct positions (20.0 and 30.0)
        unique_x = set(round(x, 1) for x in record.stage_x_moves)
        unique_y = set(round(y, 1) for y in record.stage_y_moves)

        assert 20.0 in unique_x, (
            f"Expected X=20.0 in moves, got {unique_x}\n"
            f"All X moves: {record.stage_x_moves}"
        )
        assert 30.0 in unique_x, (
            f"Expected X=30.0 in moves, got {unique_x}\n"
            f"All X moves: {record.stage_x_moves}"
        )
        assert 20.0 in unique_y, (
            f"Expected Y=20.0 in moves, got {unique_y}\n"
            f"All Y moves: {record.stage_y_moves}"
        )
        assert 30.0 in unique_y, (
            f"Expected Y=30.0 in moves, got {unique_y}\n"
            f"All Y moves: {record.stage_y_moves}"
        )

    def test_piezo_steps_through_zstack(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Verify piezo moves through z-stack levels at each FOV.

        Protocol: 5 z-planes, 3um step, from_center (default).
        Expected piezo positions: center-6, center-3, center, center+3, center+6
        """
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)

        # Single FOV to isolate z-stack behavior
        center = e2e_backend_ctx.get_stage_center()
        sim.add_single_fov("center", x=center[0], y=center[1], z=center[2])

        protocol_path = str(SIMULATION_DIR / "movement_test.yaml")
        sim.load_protocol(protocol_path)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"

        # With 5 planes, 3um step: 4 z-steps within the stack
        # Plus FROM CENTER offset + return_to_start + restore
        assert len(record.piezo_moves) >= 4, (
            f"Expected at least 4 piezo moves for 5-plane z-stack, "
            f"got {len(record.piezo_moves)}: {record.piezo_moves}"
        )

        # Verify piezo positions span ~12um (5 planes * 3um = 12um range)
        if record.piezo_moves:
            min_pos = min(record.piezo_moves)
            max_pos = max(record.piezo_moves)
            z_range = max_pos - min_pos
            assert z_range >= 10.0, (
                f"Expected z-range >= 10um (5 planes * 3um), got {z_range:.1f}um\n"
                f"Piezo positions: {record.piezo_moves}"
            )

    def test_combined_xy_movement_and_zstack(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Verify both X,Y movement AND z-stack stepping work together."""
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)

        # Two FOVs at different positions
        sim.add_single_fov("left", x=20.0, y=30.0, z=0.5)
        sim.add_single_fov("right", x=40.0, y=30.0, z=0.5)

        protocol_path = str(SIMULATION_DIR / "movement_test.yaml")
        sim.load_protocol(protocol_path)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"

        # Verify X movement to both positions
        unique_x = set(round(x, 1) for x in record.stage_x_moves)
        assert 20.0 in unique_x and 40.0 in unique_x, (
            f"Expected both X=20.0 and X=40.0, got {unique_x}"
        )

        # Verify z-stack stepping for both FOVs
        # 2 FOVs * 4 z-steps = 8 minimum piezo moves
        assert len(record.piezo_moves) >= 8, (
            f"Expected at least 8 piezo moves (2 FOVs * 4 z-steps), "
            f"got {len(record.piezo_moves)}: {record.piezo_moves}"
        )

    def test_fov_csv_overrides_manual_coordinates(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Verify that protocol with fov_sets CSV overrides manually set FOVs.

        This documents the current behavior: when a protocol specifies
        fov_sets and the imaging step references that set, the CSV file
        is loaded into ScanCoordinates, replacing any manually configured FOVs.
        """
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)

        # Set manual FOVs at (20, 20) - these should be overridden by CSV
        sim.add_single_fov("manual", x=20.0, y=20.0, z=0.5)

        # Use protocol with fov_sets that loads a CSV
        protocol_path = str(SIMULATION_DIR / "quick_multipoint.yaml")
        sim.load_protocol(protocol_path)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"

        # The CSV has positions at (0,0), (0.5,0), (0,0.5), (0.5,0.5)
        # These get clamped by simulated stage limits, but the point is
        # that 20.0 should NOT appear in the moves (CSV overrode manual FOVs)
        assert 20.0 not in set(round(x, 1) for x in record.stage_x_moves), (
            "Manual FOV at X=20.0 was used despite protocol CSV override"
        )
