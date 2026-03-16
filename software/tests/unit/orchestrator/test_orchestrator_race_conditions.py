"""Regression tests for orchestrator race conditions.

Each test class targets a specific race condition identified in the
threading audit. Tests verify that the fix prevents the race, typically
by exercising concurrent access patterns that would fail without proper
synchronization.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    ImagingProtocol,
)
from squid.core.utils.cancel_token import CancelToken
from squid.backend.controllers.orchestrator.experiment_runner import ExperimentRunner
from squid.backend.controllers.orchestrator.orchestrator_controller import OrchestratorController
from squid.backend.controllers.orchestrator.state import (
    ExperimentProgress,
    RoundProgress,
    OrchestratorState,
)


def _make_runner(
    *,
    protocol=None,
    imaging_executor=None,
    fluidics_controller=None,
    scan_coordinates=None,
    intervention_resolved=None,
    consume_intervention_action=None,
    on_transition=None,
):
    protocol = protocol or ExperimentProtocol(
        name="test_protocol",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="round_1", steps=[ImagingStep(protocol="standard")])],
    )
    progress = ExperimentProgress()
    progress_lock = threading.RLock()
    return ExperimentRunner(
        protocol=protocol,
        experiment_path="/tmp",
        experiment_id="exp1",
        cancel_token=CancelToken(),
        event_bus=MagicMock(),
        progress=progress,
        progress_lock=progress_lock,
        imaging_executor=imaging_executor,
        fluidics_controller=fluidics_controller,
        scan_coordinates=scan_coordinates,
        experiment_manager=object(),
        experiment_context=object(),
        protocol_path=None,
        on_operation_change=lambda _op: None,
        on_progress=lambda: None,
        on_checkpoint=lambda: None,
        on_round_started=lambda *_: None,
        on_round_completed=lambda *_: None,
        on_transition=on_transition or (lambda *_: None),
        on_pause=lambda: True,
        on_add_warning=lambda **_: False,
        intervention_resolved=intervention_resolved,
        consume_intervention_action=consume_intervention_action,
        step_time_estimates={(0, 0): 10.0},
        total_estimated_seconds=10.0,
    )


def _make_controller():
    """Create a minimal OrchestratorController with mocked dependencies."""
    event_bus = MagicMock()
    return OrchestratorController(
        event_bus=event_bus,
        multipoint_controller=MagicMock(),
        experiment_manager=MagicMock(),
        acquisition_planner=MagicMock(),
    )


# ---------------------------------------------------------------------------
# RC-1: _intervention_action cross-thread access
# ---------------------------------------------------------------------------


class TestRC1InterventionActionLocking:
    """RC-1: _intervention_action is written by resolve_intervention (EventBus
    thread) and read by _consume_intervention_action (worker thread) without
    any lock. The fix protects both with _lock.
    """

    def test_consume_sees_action_set_by_resolve(self):
        """After resolve sets an action, consume must return that exact action."""
        ctrl = _make_controller()

        # Force into WAITING_INTERVENTION so resolve_intervention succeeds
        with ctrl._lock:
            ctrl._state = OrchestratorState.WAITING_INTERVENTION

        assert ctrl.resolve_intervention("abort") is True

        # Worker thread consumes
        action = ctrl._consume_intervention_action()
        assert action == "abort"

        # After consumption, action resets to "acknowledge"
        action2 = ctrl._consume_intervention_action()
        assert action2 == "acknowledge"

    def test_concurrent_resolve_and_consume_no_lost_action(self):
        """Concurrent resolve + consume should never lose an action or return
        a corrupted value. Only valid actions should be returned."""
        ctrl = _make_controller()
        valid_actions = {"acknowledge", "abort", "retry", "skip"}
        errors = []
        barrier = threading.Barrier(2)

        def resolver():
            barrier.wait()
            for action in ["abort", "retry", "skip", "acknowledge"] * 50:
                with ctrl._lock:
                    ctrl._intervention_action = action

        def consumer():
            barrier.wait()
            for _ in range(200):
                action = ctrl._consume_intervention_action()
                if action not in valid_actions:
                    errors.append(f"Invalid action: {action!r}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(resolver), pool.submit(consumer)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Invalid actions seen: {errors[:5]}"


# ---------------------------------------------------------------------------
# RC-8: resolve_intervention check-then-act
# ---------------------------------------------------------------------------


class TestRC8ResolveInterventionAtomicity:
    """RC-8: resolve_intervention() checks state with _is_in_state (acquires
    _lock, releases), then sets _intervention_action (no lock). A concurrent
    abort/transition between the check and set could leave the action set on
    a non-intervention state. The fix makes the check+set atomic under _lock.
    """

    def test_resolve_rejects_when_not_in_intervention_state(self):
        """resolve_intervention should return False when not WAITING_INTERVENTION."""
        ctrl = _make_controller()
        # Controller starts in IDLE — resolve should be rejected
        assert ctrl.resolve_intervention("abort") is False
        # Action should remain unchanged
        assert ctrl._intervention_action == "acknowledge"

    def test_resolve_accepts_when_in_intervention_state(self):
        """resolve_intervention should return True when WAITING_INTERVENTION."""
        ctrl = _make_controller()
        with ctrl._lock:
            ctrl._state = OrchestratorState.WAITING_INTERVENTION
        assert ctrl.resolve_intervention("abort") is True
        action = ctrl._consume_intervention_action()
        assert action == "abort"

    def test_resolve_does_not_set_action_after_concurrent_transition(self):
        """If state transitions away between check and set, action must NOT be set.

        We simulate this by having a thread rapidly transition away from
        WAITING_INTERVENTION while another thread calls resolve_intervention.
        After the fix, resolve should atomically check+set under _lock.
        """
        ctrl = _make_controller()
        errors = []
        barrier = threading.Barrier(2)

        def toggler():
            """Rapidly toggle between WAITING_INTERVENTION and RUNNING."""
            barrier.wait()
            for _ in range(200):
                with ctrl._lock:
                    ctrl._state = OrchestratorState.WAITING_INTERVENTION
                with ctrl._lock:
                    ctrl._state = OrchestratorState.RUNNING

        def resolver():
            """Call resolve_intervention repeatedly."""
            barrier.wait()
            for _ in range(200):
                result = ctrl.resolve_intervention("abort")
                if not result:
                    # If resolve returned False, action must not have been set to "abort"
                    # (consume to check and reset)
                    consumed = ctrl._consume_intervention_action()
                    if consumed == "abort":
                        errors.append("Action set despite resolve returning False")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(toggler), pool.submit(resolver)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Race detected: {errors[:5]}"


# ---------------------------------------------------------------------------
# RC-6: _progress.current_fov_label read outside lock in interventions
# ---------------------------------------------------------------------------


class TestRC6FovLabelInsideLock:
    """RC-6: _pause_for_protocol_review and _resolve_failure_intervention
    read _progress.current_fov_label outside the _progress_lock in the
    OrchestratorInterventionRequired event. The fix moves these reads
    inside the existing `with self._progress_lock:` blocks.
    """

    def test_intervention_event_has_fov_label(self):
        """The intervention event should contain the current_fov_label."""
        published_events = []
        event_bus = MagicMock()
        event_bus.publish = lambda e: published_events.append(e)

        intervention_resolved = threading.Event()
        action_holder = ["acknowledge"]

        def consume():
            a = action_holder[0]
            action_holder[0] = "acknowledge"
            return a

        runner = _make_runner(
            intervention_resolved=intervention_resolved,
            consume_intervention_action=consume,
            on_transition=lambda *_: None,
        )
        runner._event_bus = event_bus
        runner._experiment_id = "test_exp"
        runner._run_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )
            runner._progress.current_fov_label = "FOV 5"

        # Auto-resolve intervention after a short delay
        def auto_resolve():
            time.sleep(0.05)
            action_holder[0] = "acknowledge"
            intervention_resolved.set()

        threading.Thread(target=auto_resolve, daemon=True).start()

        step = ImagingStep(protocol="standard")
        runner._pause_for_protocol_review(0, step)

        # Check that the published intervention event has the fov label
        intervention_events = [
            e for e in published_events
            if hasattr(e, "current_fov_label")
        ]
        assert len(intervention_events) >= 1
        assert intervention_events[0].current_fov_label == "FOV 5"


# ---------------------------------------------------------------------------
# RC-9: notify_pause/resume timing fields without lock
# ---------------------------------------------------------------------------


class TestRC9PauseResumeTimingLock:
    """RC-9: notify_pause() and notify_resume() read/write _paused_at,
    _step_paused_total, and _total_paused_seconds without any lock.
    These are read by get_timing_snapshot() from the timing publisher thread.
    The fix protects them with _progress_lock.
    """

    def test_concurrent_pause_resume_and_snapshot(self):
        """Rapid pause/resume cycles concurrent with snapshot reads should
        not produce negative paused durations or crashes."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic() - 10.0
        runner._step_start_time = time.monotonic() - 5.0

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )

        errors = []

        def pause_resume_cycle():
            for _ in range(100):
                runner.notify_pause()
                time.sleep(0.001)
                runner.notify_resume()

        def snapshot_reader():
            for _ in range(200):
                try:
                    snap = runner.get_timing_snapshot()
                    if snap["paused_seconds"] < 0:
                        errors.append(f"Negative paused: {snap['paused_seconds']}")
                except Exception as e:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(pause_resume_cycle),
                pool.submit(snapshot_reader),
                pool.submit(snapshot_reader),
            ]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors: {errors[:5]}"

    def test_notify_pause_idempotent(self):
        """Calling notify_pause twice should not double-count."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()
        runner._step_start_time = time.monotonic()

        runner.notify_pause()
        first_paused_at = runner._paused_at
        runner.notify_pause()
        assert runner._paused_at == first_paused_at, "Second pause should not update _paused_at"

    def test_notify_resume_without_pause_is_noop(self):
        """Calling notify_resume when not paused should be a no-op."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()
        runner._total_paused_seconds = 0.0
        runner.notify_resume()
        assert runner._total_paused_seconds == 0.0
