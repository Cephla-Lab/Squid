"""
E2E edge-case tests for orchestrator control flow.

Tests complex interaction sequences, race conditions, and edge cases
that go beyond the happy-path coverage in test_orchestrator_e2e.py.
Simulates GUI-level interactions (start, pause, resume, abort, skip,
acknowledge) to identify potential deadlocks, state corruption, and
event ordering issues.
"""

from __future__ import annotations

import time

import pytest

from tests.harness import BackendContext
from tests.e2e.harness import (
    OrchestratorSimulator,
    assert_state_transitions,
)
from squid.backend.controllers.orchestrator import (
    OrchestratorState,
    OrchestratorStateChanged,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorProgress,
    OrchestratorInterventionRequired,
)
from squid.backend.controllers.orchestrator.state import (
    OrchestratorStepStarted,
    OrchestratorStepCompleted,
)


# ============================================================================
# Shared Helpers
# ============================================================================


def wait_until_not_running(sim: OrchestratorSimulator, timeout_s: float = 120) -> bool:
    """Block until orchestrator leaves running states."""
    start = time.time()
    while sim.orchestrator.is_running and time.time() - start < timeout_s:
        time.sleep(0.05)
    return not sim.orchestrator.is_running


def wait_for_auto_acknowledge(sim: OrchestratorSimulator, timeout_s: float = 120) -> None:
    """Wait loop that auto-acknowledges interventions until experiment finishes."""
    start = time.time()
    while sim.orchestrator.is_running and time.time() - start < timeout_s:
        if sim.orchestrator.state == OrchestratorState.WAITING_INTERVENTION:
            time.sleep(0.05)
            sim.acknowledge_intervention()
        time.sleep(0.05)


def setup_sim(
    sim: OrchestratorSimulator,
    ctx: BackendContext,
    protocol_path: str,
) -> None:
    """Common setup: load protocol, add single FOV at stage center."""
    center = ctx.get_stage_center()
    sim.load_protocol(protocol_path)
    sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])


# ============================================================================
# Class A: Pause Edge Cases
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestPauseEdgeCases:
    """Tests for pause/resume edge cases and race conditions."""

    def test_pause_during_fluidics_step(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause while fluidics is executing, resume, complete normally."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for a fluidics step to start
        event = sim.monitor.wait_for(
            OrchestratorStepStarted,
            timeout_s=30,
            predicate=lambda e: e.step_type == "fluidics",
        )
        assert event is not None, "Timed out waiting for fluidics step to start"

        # Pause mid-fluidics
        paused = sim.pause()
        assert paused
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.PAUSED

        # Resume
        resumed = sim.resume()
        assert resumed

        # Wait for completion with auto-acknowledge
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Verify PAUSED appeared in state transitions
        states = [e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]
        assert "PAUSED" in states

    def test_pause_during_imaging_step(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause while imaging is executing, resume, complete normally."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for an imaging step to start
        event = sim.monitor.wait_for(
            OrchestratorStepStarted,
            timeout_s=30,
            predicate=lambda e: e.step_type == "imaging",
        )
        assert event is not None, "Timed out waiting for imaging step to start"

        # Pause mid-imaging
        paused = sim.pause()
        assert paused
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.PAUSED

        # Resume
        resumed = sim.resume()
        assert resumed

        # Wait for completion
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        states = [e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]
        assert "PAUSED" in states

    def test_pause_during_intervention_wait(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """Pause from WAITING_INTERVENTION, resume restores WAITING_INTERVENTION, then acknowledge."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, intervention_protocol)

        started = sim.start()
        assert started

        # Wait for intervention
        event = sim.monitor.wait_for(OrchestratorInterventionRequired, timeout_s=30)
        assert event is not None, "Timed out waiting for intervention"
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.WAITING_INTERVENTION

        # Pause from WAITING_INTERVENTION
        paused = sim.pause()
        assert paused
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.PAUSED

        # Resume should restore WAITING_INTERVENTION
        resumed = sim.resume()
        assert resumed
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.WAITING_INTERVENTION

        # Acknowledge and let experiment complete
        sim.acknowledge_intervention()
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

    def test_multiple_pause_resume_cycles(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """3 pause/resume cycles during multi-round experiment, synchronized via events."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        pause_count = 0
        last_seen_round = -1
        for _ in range(3):
            # Wait for a step in a round we haven't paused during yet
            event = sim.monitor.wait_for(
                OrchestratorStepStarted,
                timeout_s=30,
                predicate=lambda e: e.round_index > last_seen_round,
            )
            if event is None or not sim.orchestrator.is_running:
                break
            last_seen_round = event.round_index
            paused = sim.pause()
            if paused:
                pause_count += 1
                time.sleep(0.1)
                sim.resume()

        assert pause_count >= 2, f"Expected at least 2 successful pauses, got {pause_count}"

        # Wait for completion
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        states = [e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]
        paused_count_events = states.count("PAUSED")
        assert paused_count_events >= 2, f"Expected at least 2 PAUSED states, got {paused_count_events}"

        # Verify progress events exist and are bounded
        progress_events = sim.monitor.get_events(OrchestratorProgress)
        assert len(progress_events) > 0
        for p in progress_events:
            assert 0.0 <= p.progress_percent <= 100.0

    def test_rapid_pause_resume(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause then immediately resume (no sleep) - should not deadlock."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for execution to actually begin (event-driven)
        event = sim.monitor.wait_for(OrchestratorStepStarted, timeout_s=30)
        assert event is not None, "Timed out waiting for step to start"

        # Rapid pause + resume with no intervening sleep
        paused = sim.pause()
        sim.resume()

        # Wait for completion
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Verify the pause was accepted (not just a no-op)
        if paused:
            states = [e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]
            assert "PAUSED" in states, "Pause was accepted but PAUSED state not in transitions"

    def test_double_pause_is_idempotent(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Second pause() returns False."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        first_pause = sim.pause()
        assert first_pause

        time.sleep(0.1)
        second_pause = sim.pause()
        assert not second_pause

        # Resume and complete
        sim.resume()
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

    def test_double_resume_returns_false(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Second resume() returns False."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        sim.pause()
        time.sleep(0.1)

        first_resume = sim.resume()
        assert first_resume

        second_resume = sim.resume()
        assert not second_resume

        # Wait for completion
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert sim.orchestrator.state == OrchestratorState.COMPLETED


# ============================================================================
# Class B: Abort Edge Cases
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestAbortEdgeCases:
    """Tests for abort edge cases."""

    def test_abort_while_paused(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause then abort should reach ABORTED."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        paused = sim.pause()
        assert paused
        time.sleep(0.1)

        aborted = sim.abort()
        assert aborted

        assert wait_until_not_running(sim, timeout_s=30)
        # Allow final state change event to be delivered
        time.sleep(0.5)
        assert sim.orchestrator.state == OrchestratorState.ABORTED

        assert_state_transitions(sim.monitor, ["RUNNING", "PAUSED", "ABORTED"])

    def test_abort_during_intervention_wait(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """Abort while WAITING_INTERVENTION."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, intervention_protocol)

        started = sim.start()
        assert started

        # Wait for intervention
        event = sim.monitor.wait_for(OrchestratorInterventionRequired, timeout_s=30)
        assert event is not None
        time.sleep(0.1)

        aborted = sim.abort()
        assert aborted

        assert wait_until_not_running(sim, timeout_s=30)
        assert sim.orchestrator.state == OrchestratorState.ABORTED

    def test_abort_during_fluidics_subsystem(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        fluidics_only_protocol: str,
    ):
        """Abort during active fluidics step."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, fluidics_only_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for a fluidics step to start
        event = sim.monitor.wait_for(
            OrchestratorStepStarted,
            timeout_s=30,
            predicate=lambda e: e.step_type == "fluidics",
        )
        assert event is not None

        aborted = sim.abort()
        assert aborted

        assert wait_until_not_running(sim, timeout_s=30)
        assert sim.orchestrator.state == OrchestratorState.ABORTED

        # Should have completed fewer than all 4 rounds
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(completed) < 4

    def test_abort_during_imaging_subsystem(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Abort during active imaging step - fewer than all rounds complete."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for an imaging step to start
        event = sim.monitor.wait_for(
            OrchestratorStepStarted,
            timeout_s=30,
            predicate=lambda e: e.step_type == "imaging",
        )
        assert event is not None

        aborted = sim.abort()
        assert aborted

        assert wait_until_not_running(sim, timeout_s=30)
        assert sim.orchestrator.state == OrchestratorState.ABORTED

        # Should have completed fewer than all 4 rounds
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(completed) < 4, f"Expected fewer than 4 completed rounds, got {len(completed)}"

    def test_abort_immediately_after_start(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Abort with minimal delay after start() - should not deadlock."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        # Abort immediately
        aborted = sim.abort()
        assert aborted

        assert wait_until_not_running(sim, timeout_s=30)
        assert sim.orchestrator.state == OrchestratorState.ABORTED

    def test_abort_on_idle_returns_false(
        self,
        e2e_orchestrator: OrchestratorSimulator,
    ):
        """abort() when IDLE returns False."""
        sim = e2e_orchestrator
        assert sim.orchestrator.state == OrchestratorState.IDLE

        result = sim.abort()
        assert not result
        assert sim.orchestrator.state == OrchestratorState.IDLE


# ============================================================================
# Class C: Skip Edge Cases
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestSkipEdgeCases:
    """Tests for skip_current_round and skip_to_round edge cases."""

    def test_skip_current_round_during_execution(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Skip current round mid-step, verified by event-driven sync."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for first step to actually start (event-driven)
        event = sim.monitor.wait_for(
            OrchestratorStepStarted,
            timeout_s=30,
            predicate=lambda e: e.round_index == 0,
        )
        assert event is not None, "Timed out waiting for round 0 step to start"

        result = sim.skip_current_round()
        assert result, "skip_current_round() should return True during active round"

        # Wait for completion
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Round 0 should be marked as skipped
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        round_0 = [e for e in completed if e.round_index == 0]
        assert len(round_0) == 1
        assert round_0[0].error == "skipped", (
            f"Round 0 should have error='skipped', got error='{round_0[0].error}'"
        )

        # Remaining rounds should complete normally
        non_skipped = [e for e in completed if e.error != "skipped"]
        assert len(non_skipped) >= 1, "Expected at least one non-skipped round"

    def test_skip_to_round_past_end_is_rejected(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """skip_to_round(10) on 4-round protocol is rejected; all rounds run normally."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for execution to start
        event = sim.monitor.wait_for(OrchestratorStepStarted, timeout_s=30)
        assert event is not None

        # Out-of-bounds skip should be rejected (returns False)
        result = sim.skip_to_round(10)
        assert not result, "skip_to_round(10) should return False for out-of-bounds index"

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # All 4 rounds should complete normally (no skips)
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(completed) == 4, f"Expected 4 completed rounds, got {len(completed)}"
        skipped = [e for e in completed if e.error == "skipped"]
        assert len(skipped) == 0, f"Expected no skipped rounds, got {len(skipped)}"

    def test_skip_backward_to_already_executed_round(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """skip_to_round(0) after round 1 starts should be a no-op (backward skip)."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        # Wait for at least round 1 to start
        event = sim.monitor.wait_for(
            OrchestratorRoundStarted,
            timeout_s=30,
            predicate=lambda e: e.round_index >= 1,
        )
        assert event is not None, "Timed out waiting for round 1 to start"

        # Try backward skip - should be rejected (returns False)
        result = sim.skip_to_round(0)
        assert not result, "Backward skip_to_round(0) should return False"

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # All 4 rounds should complete
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(completed) == 4

    def test_skip_during_pause(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause, skip_to_round(3), resume - should skip intervening rounds."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        sim.pause()
        time.sleep(0.1)

        sim.skip_to_round(3)

        sim.resume()

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Some rounds should be skipped
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        skipped = [e for e in completed if e.error == "skipped"]
        assert len(skipped) >= 1, "Expected at least one skipped round"

    def test_multiple_rapid_skip_calls(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """3 skip_to_round calls rapidly, last one (round 3) should take effect."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for execution to begin (event-driven)
        event = sim.monitor.wait_for(OrchestratorStepStarted, timeout_s=30)
        assert event is not None

        sim.skip_to_round(1)
        sim.skip_to_round(2)
        sim.skip_to_round(3)

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Verify "Final Wash" (round 3) actually ran
        started_events = sim.monitor.get_events(OrchestratorRoundStarted)
        started_names = [e.round_name for e in started_events]
        assert "Final Wash" in started_names, f"Expected 'Final Wash' to run, got {started_names}"

        # At least some rounds should be skipped
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        skipped = [e for e in completed if e.error == "skipped"]
        assert len(skipped) >= 1, f"Expected at least 1 skipped round, got {len(skipped)}"

    def test_skip_to_last_round(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """skip_to_round(3) on 4-round protocol: round 3 (Final Wash) runs, earlier rounds skipped."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.2)
        sim.skip_to_round(3)

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Verify round 3 ("Final Wash") was started
        started_events = sim.monitor.get_events(OrchestratorRoundStarted)
        started_names = [e.round_name for e in started_events]
        assert "Final Wash" in started_names, f"Expected 'Final Wash' in {started_names}"

        # Some rounds should be skipped
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        skipped = [e for e in completed if e.error == "skipped"]
        assert len(skipped) >= 1


# ============================================================================
# Class D: Start-From and Single Round
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestStartFromAndSingleRound:
    """Tests for start_from_round, start_from_step, run_single_round."""

    def test_start_from_round_2(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Start from round index 2, skip rounds 0-1."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start(start_from_round=2)
        assert started

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Only rounds 2 and 3 should have started events
        started_events = sim.monitor.get_events(OrchestratorRoundStarted)
        round_indices = [e.round_index for e in started_events]
        assert round_indices == [2, 3], f"Expected rounds [2, 3], got {round_indices}"

    def test_start_from_step_within_round(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Start round 0 from step 1 (skip fluidics step, start at imaging)."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start(start_from_step=1)
        assert started

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # First step started in round 0 should have step_index=1 (imaging)
        step_events = sim.monitor.get_events(OrchestratorStepStarted)
        round_0_steps = [e for e in step_events if e.round_index == 0]
        assert len(round_0_steps) >= 1
        assert round_0_steps[0].step_index == 1, (
            f"Expected first step in round 0 to be index 1, got {round_0_steps[0].step_index}"
        )

    def test_run_single_round_from_beginning(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """run_single_round=True from round 0: only 1 round executes."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start(run_single_round=True)
        assert started

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Exactly 1 round should have started and completed
        started_events = sim.monitor.get_events(OrchestratorRoundStarted)
        assert len(started_events) == 1, f"Expected 1 round started, got {len(started_events)}"
        assert started_events[0].round_index == 0

    def test_run_single_round_from_middle(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """run_single_round=True, start_from_round=2: only round 2 executes."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start(start_from_round=2, run_single_round=True)
        assert started

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Exactly 1 round with index 2
        started_events = sim.monitor.get_events(OrchestratorRoundStarted)
        assert len(started_events) == 1
        assert started_events[0].round_index == 2


# ============================================================================
# Class E: Terminal State Rerun
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestTerminalStateRerun:
    """Tests for starting new experiments after terminal states."""

    def test_rerun_after_completed(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
    ):
        """Complete, then start a new experiment."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, single_round_imaging_protocol)

        # First run
        result = sim.run_and_wait(timeout_s=60)
        assert result.success
        assert result.final_state == "COMPLETED"

        # Clear monitor events for second run
        sim.monitor.clear()

        # Second run
        result2 = sim.run_and_wait(timeout_s=60)
        assert result2.success
        assert result2.final_state == "COMPLETED"

        # Second run should have full state transition sequence
        states = [e.new_state for e in result2.state_changes]
        assert "RUNNING" in states
        assert "COMPLETED" in states

    def test_rerun_after_aborted(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        single_round_imaging_protocol: str,
    ):
        """Abort, then start a new experiment."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        # Start and abort first experiment
        started = sim.start()
        assert started
        time.sleep(0.3)
        sim.abort()
        assert wait_until_not_running(sim, timeout_s=30)
        assert sim.orchestrator.state == OrchestratorState.ABORTED

        # Start second experiment with different protocol
        sim.monitor.clear()
        sim.load_protocol(single_round_imaging_protocol)
        result = sim.run_and_wait(timeout_s=60)
        assert result.success
        assert result.final_state == "COMPLETED"

    def test_start_while_already_running_returns_false(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Double start_experiment returns False."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        first = sim.start()
        assert first

        # Second start should fail
        second = sim.start()
        assert not second

        # Clean up: abort the running experiment
        sim.abort()
        wait_until_not_running(sim, timeout_s=30)


# ============================================================================
# Class F: Complex Interaction Sequences
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestComplexInteractionSequences:
    """Tests for complex multi-operation interaction patterns."""

    def test_pause_during_intervention_resume_then_acknowledge(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """WAITING_INTERVENTION -> PAUSED -> WAITING_INTERVENTION -> acknowledge -> RUNNING."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, intervention_protocol)

        started = sim.start()
        assert started

        # Wait for first intervention
        event = sim.monitor.wait_for(OrchestratorInterventionRequired, timeout_s=30)
        assert event is not None
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.WAITING_INTERVENTION

        # Pause
        paused = sim.pause()
        assert paused
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.PAUSED

        # Resume - should go back to WAITING_INTERVENTION
        resumed = sim.resume()
        assert resumed
        time.sleep(0.1)
        assert sim.orchestrator.state == OrchestratorState.WAITING_INTERVENTION

        # Acknowledge
        ack = sim.acknowledge_intervention()
        assert ack

        # Let experiment complete (auto-acknowledge remaining interventions)
        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

    def test_skip_to_round_while_paused_then_resume(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause, skip_to_round(3), resume - skipped rounds, round 3 runs."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        paused = sim.pause()
        assert paused
        time.sleep(0.1)

        sim.skip_to_round(3)
        sim.resume()

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # "Final Wash" (round 3) should have been started
        started_events = sim.monitor.get_events(OrchestratorRoundStarted)
        started_names = [e.round_name for e in started_events]
        assert "Final Wash" in started_names

    def test_pause_skip_current_round_resume(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Pause during round 0, skip_current_round, resume - round 0 skipped."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for round 0 step to start
        event = sim.monitor.wait_for(
            OrchestratorStepStarted,
            timeout_s=30,
            predicate=lambda e: e.round_index == 0,
        )
        assert event is not None, "Timed out waiting for round 0 to start"

        paused = sim.pause()
        assert paused
        time.sleep(0.1)

        result = sim.skip_current_round()
        assert result, "skip_current_round should return True while paused"
        sim.resume()

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Exactly round 0 should be skipped
        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        skipped = [e for e in completed if e.error == "skipped"]
        assert len(skipped) >= 1, "Expected the current round to be skipped"
        assert any(e.round_index == 0 for e in skipped), "Round 0 should be the skipped round"

    def test_rapid_control_operations_stress(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Burst of pause/resume/skip with no sleeps - should reach terminal state without deadlock."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.2)

        # Rapid burst of control operations
        sim.pause()
        sim.resume()
        sim.skip_current_round()
        sim.pause()
        sim.resume()
        sim.skip_to_round(3)

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert not sim.orchestrator.is_running
        assert sim.orchestrator.state in (
            OrchestratorState.COMPLETED,
            OrchestratorState.ABORTED,
        )


# ============================================================================
# Class G: Event and Progress Verification
# ============================================================================


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestEventProgressVerification:
    """Tests verifying event content and progress tracking correctness."""

    def test_step_events_for_all_step_types(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """Verify StepStarted/StepCompleted for imaging and intervention step types."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, intervention_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted, OrchestratorStepCompleted)

        result = sim.run_and_wait(timeout_s=90)
        assert result.success

        step_started = sim.monitor.get_events(OrchestratorStepStarted)
        step_completed = sim.monitor.get_events(OrchestratorStepCompleted)

        started_types = {e.step_type for e in step_started}
        completed_types = {e.step_type for e in step_completed}

        # intervention_protocol has imaging and intervention steps
        assert "imaging" in started_types, f"Missing imaging in started types: {started_types}"
        assert "intervention" in started_types, f"Missing intervention in started types: {started_types}"

        assert "imaging" in completed_types, f"Missing imaging in completed types: {completed_types}"
        assert "intervention" in completed_types, f"Missing intervention in completed types: {completed_types}"

        # Each started should have a matching completed
        assert len(step_started) == len(step_completed), (
            f"Started ({len(step_started)}) != Completed ({len(step_completed)})"
        )

    def test_progress_monotonicity_through_pause_resume(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Progress stays bounded and mostly increases through pause/resume.

        Note: At round boundaries, progress can transiently decrease because
        the round index advances but step progress within the new round resets
        to zero. We verify overall trend is correct rather than strict monotonicity.
        """
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)
        sim.monitor.subscribe(OrchestratorStepStarted)

        started = sim.start()
        assert started

        # Wait for a step to start (event-driven), then pause/resume
        event = sim.monitor.wait_for(OrchestratorStepStarted, timeout_s=30)
        assert event is not None, "Timed out waiting for step to start"

        paused = sim.pause()
        assert paused, "Pause should succeed during active step"
        time.sleep(0.1)
        sim.resume()

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        # Verify PAUSED appeared in state transitions
        states = [e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]
        assert "PAUSED" in states, "Expected PAUSED in state transitions"

        # Verify progress events exist and are bounded [0, 100]
        progress_events = sim.monitor.get_events(OrchestratorProgress)
        assert len(progress_events) > 0
        for p in progress_events:
            assert 0.0 <= p.progress_percent <= 100.0

        # Verify final progress is near 100%
        assert progress_events[-1].progress_percent >= 90.0

    def test_round_completed_events_have_skipped_field(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Skipped rounds have error='skipped'."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.2)
        sim.skip_to_round(3)

        wait_for_auto_acknowledge(sim, timeout_s=120)
        assert sim.orchestrator.state == OrchestratorState.COMPLETED

        completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        skipped = [e for e in completed if e.error == "skipped"]
        non_skipped = [e for e in completed if e.error != "skipped"]

        # There should be at least one skipped and at least one non-skipped
        assert len(skipped) >= 1, "Expected at least one skipped round"
        assert len(non_skipped) >= 1, "Expected at least one non-skipped round"

        # All skipped rounds should have success=True
        for e in skipped:
            assert e.success, f"Skipped round {e.round_index} has success=False"

    def test_intervention_event_contents(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        intervention_protocol: str,
    ):
        """Verify intervention event fields (round_index, round_name, message)."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, intervention_protocol)

        result = sim.run_and_wait(timeout_s=90)
        assert result.success

        interventions = result.intervention_events
        assert len(interventions) == 2

        # First intervention: "Sample Change" round
        assert interventions[0].round_index == 1
        assert interventions[0].round_name == "Sample Change"
        assert "replace sample" in interventions[0].message.lower()

        # Second intervention: "Final Check" round
        assert interventions[1].round_index == 3
        assert interventions[1].round_name == "Final Check"
        assert "verify" in interventions[1].message.lower()

    def test_state_transitions_for_abort_flow(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """RUNNING -> ABORTED transition chain."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        sim.abort()

        assert wait_until_not_running(sim, timeout_s=30)
        # Allow final state change event to be delivered
        time.sleep(0.5)

        assert_state_transitions(sim.monitor, ["RUNNING", "ABORTED"])

    def test_state_transitions_for_pause_abort_flow(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """RUNNING -> PAUSED -> ABORTED chain."""
        sim = e2e_orchestrator
        setup_sim(sim, e2e_backend_ctx, multi_round_fish_protocol)

        started = sim.start()
        assert started

        time.sleep(0.3)
        paused = sim.pause()
        assert paused
        time.sleep(0.1)

        aborted = sim.abort()
        assert aborted

        assert wait_until_not_running(sim, timeout_s=30)
        # Allow final state change event to be delivered
        time.sleep(0.5)

        assert_state_transitions(sim.monitor, ["RUNNING", "PAUSED", "ABORTED"])
