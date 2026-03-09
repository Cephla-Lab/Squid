"""Tests for RunState frozen dataclass and ThroughputTracker."""
import time
from datetime import datetime
from unittest.mock import patch

import pytest

from squid.backend.controllers.orchestrator.state import (
    RunState,
    ThroughputTracker,
    RunStateUpdated,
    OrchestratorState,
    Checkpoint,
)


class TestRunState:
    def test_frozen(self):
        rs = RunState(
            experiment_id="exp1",
            state=OrchestratorState.RUNNING,
            round_index=0,
            total_rounds=1,
            round_name="r0",
            step_index=0,
            total_steps=1,
            step_type="imaging",
            step_label="scan",
            fov_index=0,
            total_fovs=10,
            elapsed_s=5.0,
            active_s=5.0,
            paused_s=0.0,
            eta_s=10.0,
            attempt=1,
            focus_status=None,
            focus_error_um=None,
            throughput_fov_per_min=None,
            subsystem_seconds={},
            started_at=None,
            snapshot_at=datetime.now(),
        )
        with pytest.raises(AttributeError):
            rs.round_index = 5

    def test_progress_percent(self):
        rs = RunState(
            experiment_id="exp1",
            state=OrchestratorState.RUNNING,
            round_index=0,
            total_rounds=2,
            round_name="r0",
            step_index=0,
            total_steps=2,
            step_type="imaging",
            step_label="scan",
            fov_index=5,
            total_fovs=10,
            elapsed_s=5.0,
            active_s=5.0,
            paused_s=0.0,
            eta_s=10.0,
            attempt=1,
            focus_status=None,
            focus_error_um=None,
            throughput_fov_per_min=None,
            subsystem_seconds={},
            started_at=None,
            snapshot_at=datetime.now(),
        )
        pct = rs.progress_percent
        assert 0 <= pct <= 100
        # round_index=0, total_rounds=2, step_index=0, total_steps=2, fov_index=5, total_fovs=10
        # round_progress = 0/2 = 0
        # round_frac = 1/2 = 0.5, step_frac = 0.5/2 = 0.25
        # completed_steps = 0, sub = 5/10 = 0.5
        # round_progress += 0 * 0.25 + 0.5 * 0.25 = 0.125
        # result = 0.125 * 100 = 12.5
        assert pct == pytest.approx(12.5)

    def test_progress_percent_zero_rounds(self):
        rs = RunState(
            experiment_id="exp1",
            state=OrchestratorState.IDLE,
            round_index=0,
            total_rounds=0,
            round_name="",
            step_index=0,
            total_steps=0,
            step_type="",
            step_label="",
            fov_index=0,
            total_fovs=0,
            elapsed_s=0.0,
            active_s=0.0,
            paused_s=0.0,
            eta_s=None,
            attempt=1,
            subsystem_seconds={},
            started_at=None,
            snapshot_at=datetime.now(),
        )
        assert rs.progress_percent == 0.0

    def test_to_checkpoint(self):
        rs = RunState(
            experiment_id="exp1",
            state=OrchestratorState.RUNNING,
            round_index=1,
            total_rounds=3,
            round_name="r1",
            step_index=2,
            total_steps=4,
            step_type="imaging",
            step_label="scan",
            fov_index=7,
            total_fovs=20,
            elapsed_s=120.0,
            active_s=100.0,
            paused_s=20.0,
            eta_s=60.0,
            attempt=1,
            focus_status="locked",
            focus_error_um=0.1,
            throughput_fov_per_min=3.5,
            subsystem_seconds={"imaging": 80.0, "fluidics": 20.0},
            started_at=datetime(2026, 3, 9, 10, 0, 0),
            snapshot_at=datetime.now(),
        )
        ckpt = rs.to_checkpoint(protocol_name="test", protocol_version="3.0", experiment_path="/tmp/exp1")
        assert ckpt.round_index == 1
        assert ckpt.step_index == 2
        assert ckpt.imaging_fov_index == 7
        assert ckpt.elapsed_seconds == 120.0
        assert ckpt.paused_seconds == 20.0


class TestRunStateUpdated:
    def test_event_wraps_run_state(self):
        rs = RunState(
            experiment_id="exp1",
            state=OrchestratorState.RUNNING,
            round_index=0,
            total_rounds=1,
            round_name="r0",
            step_index=0,
            total_steps=1,
            step_type="imaging",
            step_label="scan",
            fov_index=0,
            total_fovs=10,
            elapsed_s=0.0,
            active_s=0.0,
            paused_s=0.0,
            eta_s=None,
            attempt=1,
            subsystem_seconds={},
            started_at=None,
            snapshot_at=datetime.now(),
        )
        event = RunStateUpdated(run_state=rs)
        assert event.run_state is rs


class TestThroughputTracker:
    def test_empty_returns_none(self):
        t = ThroughputTracker()
        assert t.fovs_per_minute() is None

    def test_single_fov_returns_none(self):
        t = ThroughputTracker()
        t.record_fov(0)
        assert t.fovs_per_minute() is None

    def test_two_fovs_computes_rate(self):
        t = ThroughputTracker()
        with patch("time.monotonic") as mono:
            mono.return_value = 100.0
            t.record_fov(0)
            mono.return_value = 130.0  # 30 seconds later
            t.record_fov(1)
            rate = t.fovs_per_minute(window_seconds=120)
        # 1 FOV in 30 seconds = 2 FOVs/min
        assert rate == pytest.approx(2.0)

    def test_window_excludes_old_entries(self):
        t = ThroughputTracker()
        with patch("time.monotonic") as mono:
            # Old entries outside 60s window
            mono.return_value = 0.0
            t.record_fov(0)
            mono.return_value = 10.0
            t.record_fov(1)
            # Recent entries inside 60s window
            mono.return_value = 100.0
            t.record_fov(2)
            mono.return_value = 120.0
            t.record_fov(3)
            mono.return_value = 140.0
            t.record_fov(4)
            rate = t.fovs_per_minute(window_seconds=60)
        # Within last 60s (from 140): entries at 100, 120, 140
        # 2 FOVs over 40 seconds = 3.0 FOVs/min
        assert rate == pytest.approx(3.0)

    def test_reset_clears_history(self):
        t = ThroughputTracker()
        t.record_fov(0)
        t.record_fov(1)
        t.reset()
        assert t.fovs_per_minute() is None
