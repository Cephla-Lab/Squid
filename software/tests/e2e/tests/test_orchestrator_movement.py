"""E2E tests validating orchestrator-owned FOV plans and piezo z-stacks."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import pytest
import yaml

from squid.core.events import ClearScanCoordinatesCommand, LoadScanCoordinatesCommand
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
PROTOCOLS_DIR = SIMULATION_DIR / "protocols"


def _write_fov_csv(
    tmp_path: Path,
    rows: Sequence[Tuple[str, float, float, float]],
) -> Path:
    csv_path = tmp_path / "fovs.csv"
    lines = ["region,x (mm),y (mm),z (mm)"]
    lines.extend(f"{region},{x},{y},{z}" for region, x, y, z in rows)
    csv_path.write_text("\n".join(lines) + "\n")
    return csv_path


def _write_protocol(
    tmp_path: Path,
    fov_csv_path: Path,
    *,
    imaging_protocol: Path = PROTOCOLS_DIR / "zstack_5plane.yaml",
) -> Path:
    protocol_path = tmp_path / "movement_protocol.yaml"
    protocol = {
        "name": "Movement Test",
        "version": "3.0",
        "resources": {"fov_file": str(fov_csv_path)},
        "rounds": [
            {
                "name": "Movement Validation",
                "steps": [
                    {
                        "step_type": "imaging",
                        "protocol": str(imaging_protocol),
                    }
                ],
            }
        ],
    }
    protocol_path.write_text(yaml.safe_dump(protocol, sort_keys=False))
    return protocol_path


def _wait_for(
    predicate,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestOrchestratorMovement:
    """Tests that the orchestrator actually moves stage and piezo during acquisition."""

    def test_stage_moves_to_distinct_fov_positions(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        tmp_path: Path,
    ):
        """Verify stage moves to the positions defined by the protocol FOV file."""
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)
        csv_path = _write_fov_csv(
            tmp_path,
            [
                ("pos_A", 20.0, 20.0, 0.5),
                ("pos_B", 30.0, 20.0, 0.5),
                ("pos_C", 20.0, 30.0, 0.5),
            ],
        )
        protocol_path = _write_protocol(tmp_path, csv_path)
        sim.load_protocol(str(protocol_path))

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
        tmp_path: Path,
    ):
        """Verify piezo moves through z-stack levels at each FOV.

        Protocol: 5 z-planes, 3um step, from_center (default).
        Expected piezo positions: center-6, center-3, center, center+3, center+6
        """
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)

        # Single FOV to isolate z-stack behavior
        center = e2e_backend_ctx.get_stage_center()
        csv_path = _write_fov_csv(tmp_path, [("center", center[0], center[1], center[2])])
        protocol_path = _write_protocol(tmp_path, csv_path)
        sim.load_protocol(str(protocol_path))

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
        tmp_path: Path,
    ):
        """Verify both X,Y movement AND z-stack stepping work together."""
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)
        csv_path = _write_fov_csv(
            tmp_path,
            [("left", 20.0, 30.0, 0.5), ("right", 40.0, 30.0, 0.5)],
        )
        protocol_path = _write_protocol(tmp_path, csv_path)
        sim.load_protocol(str(protocol_path))

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

    def test_run_uses_detached_fov_snapshot_after_start(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        tmp_path: Path,
    ):
        """Mutating live ScanCoordinates after start must not change the run."""
        sim = e2e_orchestrator
        record = install_movement_monitor(e2e_backend_ctx)
        csv_path = _write_fov_csv(
            tmp_path,
            [("left", 20.0, 30.0, 0.5), ("right", 40.0, 30.0, 0.5)],
        )
        protocol_path = _write_protocol(tmp_path, csv_path)
        sim.load_protocol(str(protocol_path))

        started = sim.start()
        assert started is True

        assert _wait_for(lambda: bool(record.piezo_moves), timeout_s=10.0), (
            "Acquisition did not begin z-stack stepping in time"
        )

        e2e_backend_ctx.event_bus.publish(
            ClearScanCoordinatesCommand(clear_displayed_fovs=True)
        )
        e2e_backend_ctx.event_bus.publish(
            LoadScanCoordinatesCommand(
                region_fov_coordinates={"mutated": ((55.0, 55.0, 0.5),)},
                region_centers={"mutated": (55.0, 55.0, 0.5)},
            )
        )

        start_time = time.monotonic()
        while sim.orchestrator.is_running and time.monotonic() - start_time < 60.0:
            time.sleep(0.1)

        assert sim.orchestrator.state.name == "COMPLETED"

        unique_x = set(round(x, 1) for x in record.stage_x_moves)
        assert 20.0 in unique_x and 40.0 in unique_x, (
            f"Expected the original protocol FOVs to run, got {unique_x}"
        )
        assert 55.0 not in unique_x, (
            "Live ScanCoordinates mutation leaked into the active acquisition"
        )
