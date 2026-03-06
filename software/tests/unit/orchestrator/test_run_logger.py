"""Tests for orchestrator structured run logging."""

import json
import time

from squid.core.events import EventBus
from squid.backend.controllers.multipoint import FovStatus, FovTaskCompleted, FovTaskStarted
from squid.backend.controllers.orchestrator.run_logger import RunLogger
from squid.backend.controllers.orchestrator.state import (
    OrchestratorProgress,
    OrchestratorStateChanged,
    OrchestratorStepCompleted,
    OrchestratorStepStarted,
    OrchestratorTimingSnapshot,
    WarningRaised,
)


def test_run_logger_writes_incremental_artifacts(tmp_path):
    event_bus = EventBus()
    event_bus.start()
    logger = RunLogger(event_bus, str(tmp_path))
    logger.start()

    event_bus.publish(
        OrchestratorStateChanged(
            old_state="IDLE",
            new_state="RUNNING",
            experiment_id="exp1",
        )
    )
    event_bus.publish(
        OrchestratorProgress(
            experiment_id="exp1",
            current_round=1,
            total_rounds=2,
            current_round_name="Round 1",
            progress_percent=12.5,
            current_operation="imaging",
            current_step_name="Image cells",
            current_step_index=0,
            total_steps=3,
            current_fov_label="FOV 1",
            current_fov_index=0,
            total_fovs=4,
            attempt=1,
            elapsed_seconds=10.0,
            effective_run_seconds=8.0,
            paused_seconds=2.0,
            retry_overhead_seconds=1.0,
            intervention_overhead_seconds=0.0,
        )
    )
    event_bus.publish(
        OrchestratorStepStarted(
            experiment_id="exp1",
            round_index=0,
            step_index=0,
            step_type="imaging",
            estimated_seconds=42.0,
        )
    )
    event_bus.publish(
        FovTaskStarted(
            fov_id="A1_0000",
            fov_index=0,
            region_id="A1",
            round_index=0,
            time_point=0,
            x_mm=1.0,
            y_mm=2.0,
            attempt=1,
            pending_count=3,
            completed_count=0,
        )
    )
    event_bus.publish(
        FovTaskCompleted(
            fov_id="A1_0000",
            fov_index=0,
            round_index=0,
            time_point=0,
            status=FovStatus.COMPLETED,
            attempt=1,
        )
    )
    event_bus.publish(
        WarningRaised(
            experiment_id="exp1",
            category="FOCUS",
            severity="HIGH",
            message="Focus drift detected",
            round_index=0,
            round_name="Round 1",
            time_point=0,
            fov_id="A1_0000",
            fov_index=0,
            total_warnings=1,
            warnings_in_category=1,
        )
    )
    event_bus.publish(
        OrchestratorTimingSnapshot(
            experiment_id="exp1",
            elapsed_seconds=12.0,
            effective_run_seconds=9.0,
            paused_seconds=3.0,
            retry_overhead_seconds=1.5,
            intervention_overhead_seconds=0.5,
            eta_seconds=30.0,
            subsystem_seconds={"imaging": 9.0, "paused": 3.0},
        )
    )
    event_bus.publish(
        OrchestratorStepCompleted(
            experiment_id="exp1",
            round_index=0,
            step_index=0,
            step_type="imaging",
            success=True,
            duration_seconds=11.0,
        )
    )

    time.sleep(0.2)

    assert (tmp_path / "orchestrator_run_log.jsonl").exists()
    assert (tmp_path / "step_metrics.csv").exists()
    assert (tmp_path / "fov_metrics.csv").exists()
    assert (tmp_path / "warning_summary.json").exists()
    assert (tmp_path / "timing_summary.json").exists()
    assert (tmp_path / "qc_summary.json").exists()

    logger.stop()
    event_bus.stop()

    timing_summary = json.loads((tmp_path / "timing_summary.json").read_text())
    assert timing_summary["elapsed_seconds"] == 12.0
    assert timing_summary["subsystem_seconds"]["imaging"] == 9.0

    qc_summary = json.loads((tmp_path / "qc_summary.json").read_text())
    assert qc_summary["warning_total"] == 1
    assert qc_summary["fov_attempts"] == 1
    assert qc_summary["step_attempts"] == 1

    warning_summary = json.loads((tmp_path / "warning_summary.json").read_text())
    assert warning_summary["warning_counts"]["FOCUS:HIGH"] == 1
