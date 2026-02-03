"""
E2E tests for orchestrator workflows.

Tests multi-round experiments with fluidics and imaging using
the OrchestratorSimulator.
"""

from __future__ import annotations

import pytest

from tests.harness import BackendContext
from tests.e2e.harness import (
    OrchestratorSimulator,
    assert_round_sequence,
    assert_orchestrator_progress_monotonic,
    assert_round_events_match_protocol,
)


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestSingleRoundExperiment:
    """End-to-end tests for single-round orchestrated experiments."""

    def test_single_imaging_round(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
    ):
        """Test single-round imaging experiment."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        # Configure
        sim.load_protocol(single_round_imaging_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Run
        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 1
        assert result.final_state == "COMPLETED"

    def test_tiled_zstack_round(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        tiled_zstack_protocol: str,
    ):
        """Test tiled z-stack imaging experiment."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        # Configure with 2x2 grid
        sim.load_protocol(tiled_zstack_protocol)
        sim.add_grid_region(
            "region_1",
            center=center,
            n_x=2,
            n_y=2,
            overlap_pct=10.0,
        )

        # Run
        result = sim.run_and_wait(timeout_s=90)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 1


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestMultiRoundExperiment:
    """End-to-end tests for multi-round orchestrated experiments."""

    def test_four_round_fish_protocol(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test complete 4-round FISH protocol."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        # Configure
        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Run
        result = sim.run_and_wait(timeout_s=120)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 4
        assert result.final_state == "COMPLETED"

        # Verify round sequence
        expected_rounds = [
            "Round 1 - Hybridization",
            "Round 1 - Strip",
            "Round 2 - Hybridization",
            "Final Wash",
        ]
        assert_round_sequence(sim.monitor, expected_rounds)
        assert_round_events_match_protocol(sim.monitor, expected_rounds=4)
        assert_orchestrator_progress_monotonic(sim.monitor)

    def test_fluidics_imaging_alternation(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test that fluidics and imaging steps alternate correctly."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        result = sim.run_and_wait(timeout_s=120)

        assert result.success
        assert_round_events_match_protocol(sim.monitor, expected_rounds=4)

    def test_multi_region_fish_protocol(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test multi-region imaging with a multi-round protocol."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_grid_region("region_1", center=center, n_x=2, n_y=2)
        sim.add_grid_region(
            "region_2",
            center=(center[0] + 1.0, center[1], center[2]),
            n_x=2,
            n_y=2,
        )

        result = sim.run_and_wait(timeout_s=180)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 4


@pytest.mark.e2e
@pytest.mark.orchestrator
@pytest.mark.fluidics
class TestFluidicsOnlyExperiment:
    """End-to-end tests for fluidics-only workflows."""

    def test_fluidics_only_protocol(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        fluidics_only_protocol: str,
    ):
        """Test fluidics-only protocol (no imaging)."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        # Still need a region for the orchestrator
        sim.load_protocol(fluidics_only_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 4  # Prime, Stain, Secondary, Rinse


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestInterventionHandling:
    """End-to-end tests for intervention handling."""

    def test_intervention_auto_acknowledged(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """Test intervention with auto-acknowledgment."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(intervention_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Run with auto-acknowledge (default)
        result = sim.run_and_wait(timeout_s=90, auto_acknowledge_interventions=True)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 5  # 3 imaging + 2 interventions
        assert len(result.intervention_events) == 2

    def test_intervention_manual_acknowledged(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """Test intervention with manual acknowledgment via monitoring."""
        import threading
        import time

        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(intervention_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start experiment
        started = sim.start()
        assert started

        # Track interventions and acknowledge manually
        acknowledged_count = 0

        def acknowledge_interventions():
            nonlocal acknowledged_count
            from squid.backend.controllers.orchestrator import OrchestratorState

            timeout = 60
            start = time.time()

            while time.time() - start < timeout:
                if sim.orchestrator.state == OrchestratorState.WAITING_INTERVENTION:
                    time.sleep(0.2)  # Brief delay
                    sim.acknowledge_intervention()
                    acknowledged_count += 1
                elif sim.orchestrator.state == OrchestratorState.COMPLETED:
                    break
                elif sim.orchestrator.state in (
                    OrchestratorState.FAILED,
                    OrchestratorState.ABORTED,
                ):
                    break
                time.sleep(0.1)

        ack_thread = threading.Thread(target=acknowledge_interventions)
        ack_thread.start()

        # Wait for completion
        timeout = 90
        start = time.time()
        while sim.orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        ack_thread.join(timeout=5)

        # Verify
        from squid.backend.controllers.orchestrator import OrchestratorState

        assert sim.orchestrator.state == OrchestratorState.COMPLETED
        assert acknowledged_count == 2  # Two interventions in protocol


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestOrchestratorControl:
    """End-to-end tests for orchestrator control flow."""

    def test_pause_resume(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test pause and resume during experiment."""
        import time

        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start experiment
        started = sim.start()
        assert started

        # Wait briefly then pause
        time.sleep(0.5)
        paused = sim.pause()
        assert paused, "Failed to pause experiment"

        # Verify paused
        from squid.backend.controllers.orchestrator import OrchestratorState

        time.sleep(0.2)
        assert sim.orchestrator.state == OrchestratorState.PAUSED

        # Resume
        resumed = sim.resume()
        assert resumed, "Failed to resume experiment"

        # Wait for completion
        timeout = 120
        start = time.time()
        while sim.orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert sim.orchestrator.state == OrchestratorState.COMPLETED

    def test_abort_experiment(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test aborting experiment."""
        import time

        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start experiment
        started = sim.start()
        assert started

        # Wait briefly then abort
        time.sleep(0.5)
        aborted = sim.abort()
        assert aborted, "Failed to abort experiment"

        # Wait for abort to complete
        time.sleep(1.0)

        # Verify aborted
        from squid.backend.controllers.orchestrator import OrchestratorState

        assert sim.orchestrator.state == OrchestratorState.ABORTED

    def test_skip_to_round(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test skipping to a specific round."""
        import time

        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Start experiment
        started = sim.start()
        assert started

        # Request skip to round 3 (0-indexed)
        time.sleep(0.2)
        sim.skip_to_round(3)

        # Wait for completion
        timeout = 60
        start = time.time()
        while sim.orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        from squid.backend.controllers.orchestrator import OrchestratorState

        assert sim.orchestrator.state == OrchestratorState.COMPLETED


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestEventVerification:
    """End-to-end tests for event verification."""

    def test_state_transitions(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
    ):
        """Test that state transitions occur correctly."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(single_round_imaging_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        result = sim.run_and_wait(timeout_s=60)

        assert result.success

        # Check state transitions include expected states
        states = [e.new_state for e in result.state_changes]
        assert "RUNNING" in states
        assert "COMPLETED" in states

    def test_round_events_published(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Test that round started/completed events are published."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        result = sim.run_and_wait(timeout_s=120)

        assert result.success

        # Verify round events
        assert len(result.round_started_events) == 4
        assert len(result.round_completed_events) == 4

        # All rounds should match indices
        for i, (started, completed) in enumerate(
            zip(result.round_started_events, result.round_completed_events)
        ):
            assert started.round_index == i
            assert completed.round_index == i
            assert completed.success

    def test_no_error_events_on_success(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
    ):
        """Test that no error events are published on successful completion."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(single_round_imaging_protocol)
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert len(result.error_events) == 0
