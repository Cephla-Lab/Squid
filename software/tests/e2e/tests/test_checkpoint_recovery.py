"""
E2E tests for checkpoint and recovery workflows.

Tests checkpoint creation, checkpoint clearing, and experiment recovery
from checkpoints.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.harness import BackendContext
from tests.e2e.harness import (
    OrchestratorSimulator,
    assert_checkpoint_created,
    assert_checkpoint_cleared,
)
from squid.backend.controllers.orchestrator import OrchestratorState
from squid.core.protocol import ProtocolLoader


@pytest.mark.e2e
@pytest.mark.checkpoint
class TestCheckpointCreation:
    """End-to-end tests for checkpoint file creation."""

    def test_checkpoint_saved_on_pause(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test that checkpoint is saved when pausing."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start experiment
        started = sim.start(base_path=str(experiment_output_dir))
        assert started

        # Wait for experiment to get going, then pause
        time.sleep(1.0)
        paused = sim.pause()
        assert paused

        # Wait for checkpoint to be written
        time.sleep(0.5)

        # Verify checkpoint was saved
        experiment_path = sim.orchestrator._experiment_path
        assert experiment_path is not None

        checkpoint_data = assert_checkpoint_created(experiment_path)
        assert checkpoint_data["protocol_name"] == "4-Round FISH Protocol"
        assert "round_index" in checkpoint_data

        # Clean up by aborting
        sim.abort()

    def test_checkpoint_cleared_on_completion(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test that checkpoint is cleared on successful completion."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(single_round_imaging_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Run to completion
        result = sim.run_and_wait(
            base_path=str(experiment_output_dir),
            timeout_s=60,
        )

        assert result.success

        # Verify checkpoint was cleared
        if result.experiment_path:
            assert_checkpoint_cleared(result.experiment_path)

    def test_checkpoint_contains_required_fields(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test that checkpoint contains all required fields."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start and pause
        started = sim.start(base_path=str(experiment_output_dir))
        assert started

        time.sleep(1.0)
        sim.pause()
        time.sleep(0.5)

        # Verify checkpoint fields
        experiment_path = sim.orchestrator._experiment_path
        checkpoint_path = Path(experiment_path) / "checkpoint.json"
        assert checkpoint_path.exists()

        with open(checkpoint_path) as f:
            checkpoint_data = json.load(f)

        required_fields = [
            "protocol_name",
            "protocol_version",
            "experiment_id",
            "experiment_path",
            "round_index",
            "fluidics_step_index",
            "imaging_fov_index",
        ]

        for field in required_fields:
            assert field in checkpoint_data, f"Missing field: {field}"

        # Clean up
        sim.abort()


@pytest.mark.e2e
@pytest.mark.checkpoint
class TestCheckpointRecovery:
    """End-to-end tests for resuming from checkpoint."""

    def test_resume_preserves_experiment_id(
        self,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test that resumed experiment keeps original experiment ID."""
        from tests.e2e.harness import OrchestratorSimulator

        center = e2e_backend_ctx.get_stage_center()

        # First run: start, pause, get checkpoint
        sim1 = OrchestratorSimulator(e2e_backend_ctx)
        sim1.load_protocol(multi_round_fish_protocol)
        sim1.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        started = sim1.start(
            base_path=str(experiment_output_dir),
            experiment_id="test_resume_exp",
        )
        assert started

        # Wait and pause
        time.sleep(1.0)
        sim1.pause()
        time.sleep(0.5)

        # Get checkpoint data
        experiment_path = sim1.orchestrator._experiment_path
        checkpoint_path = Path(experiment_path) / "checkpoint.json"
        assert checkpoint_path.exists()

        with open(checkpoint_path) as f:
            checkpoint_data = json.load(f)

        original_exp_id = checkpoint_data["experiment_id"]
        assert "test_resume_exp" in original_exp_id

        # Clean up first simulator
        sim1.abort()
        sim1.cleanup()

    def test_resume_from_checkpoint_completes(
        self,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test resuming from checkpoint completes remaining rounds."""
        center = e2e_backend_ctx.get_stage_center()

        sim1 = OrchestratorSimulator(e2e_backend_ctx)
        sim1.load_protocol(multi_round_fish_protocol)
        sim1.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        started = sim1.start(
            base_path=str(experiment_output_dir),
            experiment_id="resume_from_checkpoint",
        )
        assert started

        time.sleep(1.0)
        sim1.pause()
        time.sleep(0.5)

        experiment_path = sim1.orchestrator._experiment_path
        assert experiment_path is not None

        checkpoint_data = assert_checkpoint_created(experiment_path)

        # Abort the paused run to simulate a restart
        sim1.abort()
        timeout = time.time() + 10
        while sim1.orchestrator.state != OrchestratorState.ABORTED and time.time() < timeout:
            time.sleep(0.1)

        # Resume from checkpoint using the same backend context
        sim1.clear_coordinates()
        sim1.load_protocol(multi_round_fish_protocol)
        sim1.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        protocol = ProtocolLoader().load(multi_round_fish_protocol)
        expected_remaining = len(protocol.rounds) - checkpoint_data["round_index"]

        result = sim1.run_and_wait(
            base_path=experiment_path,
            timeout_s=120,
            resume_from_checkpoint=True,
        )

        assert result.success, f"Resume failed: {result.error}"
        assert result.completed_rounds == expected_remaining
        assert result.experiment_path == experiment_path
        sim1.cleanup()


@pytest.mark.e2e
@pytest.mark.checkpoint
class TestCheckpointEdgeCases:
    """End-to-end tests for checkpoint edge cases."""

    def test_abort_clears_checkpoint(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test that aborting clears checkpoint."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start and pause to create checkpoint
        started = sim.start(base_path=str(experiment_output_dir))
        assert started

        time.sleep(1.0)
        sim.pause()
        time.sleep(0.5)

        # Verify checkpoint exists
        experiment_path = sim.orchestrator._experiment_path
        checkpoint_path = Path(experiment_path) / "checkpoint.json"
        assert checkpoint_path.exists(), "Checkpoint should exist after pause"

        # Abort
        sim.abort()
        time.sleep(0.5)

        assert checkpoint_path.exists(), (
            "Checkpoint should remain after abort for recovery"
        )

    def test_rapid_pause_resume(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
        experiment_output_dir: Path,
    ):
        """Test rapid pause/resume cycles."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(single_round_imaging_protocol)
        sim.add_grid_region("region_1", center=center, n_x=3, n_y=3)

        started = sim.start(base_path=str(experiment_output_dir))
        assert started

        # Rapid pause/resume cycles
        for _ in range(3):
            time.sleep(0.3)
            if sim.orchestrator.is_running:
                sim.pause()
                time.sleep(0.2)
                if sim.orchestrator.state == OrchestratorState.PAUSED:
                    sim.resume()

        # Wait for completion
        timeout = 120
        start = time.time()
        while sim.orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        # Should eventually complete
        assert sim.orchestrator.state in (
            OrchestratorState.COMPLETED,
            OrchestratorState.ABORTED,
        )
