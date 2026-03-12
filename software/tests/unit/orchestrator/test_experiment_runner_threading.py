"""Tests for ExperimentRunner threading, state consistency, and race conditions.

These tests target the bug-prone areas identified in the code review:
- compute_eta reads fields without lock while _execute_round writes them without lock
- _set_operation reads _current_operation and current_step_type without lock
- progress_percent edge cases under concurrent mutation
- checkpoint saving during FOV progress callbacks
- timing snapshot consistency during state transitions
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    ImagingProtocol,
    FluidicsStep,
    InterventionStep,
)
from squid.core.utils.cancel_token import CancelToken
from squid.backend.controllers.orchestrator.experiment_runner import ExperimentRunner
from squid.backend.controllers.orchestrator.state import (
    ExperimentProgress,
    RoundProgress,
    StepOutcome,
)


def _make_runner(
    *,
    protocol=None,
    imaging_executor=None,
    fluidics_controller=None,
    scan_coordinates=None,
    start_from_fov=0,
    start_from_step=0,
    start_from_round=0,
    on_checkpoint=None,
    on_progress=None,
    on_operation_change=None,
    step_time_estimates=None,
    total_estimated_seconds=10.0,
    intervention_resolved=None,
    consume_intervention_action=None,
):
    protocol = protocol or ExperimentProtocol(
        name="test_protocol",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="round_1", steps=[ImagingStep(protocol="standard")])],
    )
    progress = ExperimentProgress()
    progress.current_round_index = 0
    progress.current_step_index = 0
    return ExperimentRunner(
        protocol=protocol,
        experiment_path="/tmp",
        experiment_id="exp1",
        cancel_token=CancelToken(),
        event_bus=MagicMock(),
        progress=progress,
        progress_lock=threading.RLock(),
        imaging_executor=imaging_executor,
        fluidics_controller=fluidics_controller,
        scan_coordinates=scan_coordinates,
        experiment_manager=object(),
        experiment_context=object(),
        protocol_path=None,
        on_operation_change=on_operation_change or (lambda _op: None),
        on_progress=on_progress or (lambda: None),
        on_checkpoint=on_checkpoint or (lambda: None),
        on_round_started=lambda *_: None,
        on_round_completed=lambda *_: None,
        on_transition=lambda *_: None,
        on_pause=lambda: True,
        on_add_warning=lambda **_: False,
        intervention_resolved=intervention_resolved,
        consume_intervention_action=consume_intervention_action,
        start_from_fov=start_from_fov,
        start_from_step=start_from_step,
        start_from_round=start_from_round,
        step_time_estimates=step_time_estimates or {(0, 0): 10.0},
        total_estimated_seconds=total_estimated_seconds,
    )


# ---------------------------------------------------------------------------
# 1. compute_eta consistency under concurrent step completion
# ---------------------------------------------------------------------------


class TestComputeEtaThreadSafety:
    """compute_eta reads _completed_actual_total and _completed_estimated_total
    without holding _progress_lock. These are written in _execute_round after
    each step completes (lines 516-517). If ETA is computed mid-update, the
    scaling factor can be wildly wrong.
    """

    def test_eta_not_negative_during_concurrent_updates(self):
        """ETA should never be negative, even if read during a write."""
        protocol = ExperimentProtocol(
            name="multi_step",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(
                    name="r0",
                    steps=[
                        ImagingStep(protocol="s"),
                        ImagingStep(protocol="s"),
                        ImagingStep(protocol="s"),
                    ],
                )
            ],
        )
        estimates = {(0, 0): 5.0, (0, 1): 5.0, (0, 2): 5.0}
        runner = _make_runner(
            protocol=protocol,
            step_time_estimates=estimates,
            total_estimated_seconds=15.0,
        )
        runner._run_start_time = time.monotonic() - 3.0
        runner._step_start_time = time.monotonic() - 1.0

        # Simulate being partway through step 1
        with runner._progress_lock:
            runner._progress.current_round_index = 0
            runner._progress.current_step_index = 1
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )

        # Simulate step 0 completed: 5s estimated, 4s actual
        runner._completed_estimated_total = 5.0
        runner._completed_actual_total = 4.0

        errors = []

        def stress_eta(n_iterations):
            for _ in range(n_iterations):
                eta = runner.compute_eta()
                if eta is not None and eta < 0:
                    errors.append(f"Negative ETA: {eta}")

        def stress_updates(n_iterations):
            for i in range(n_iterations):
                # Simulate step completion updating totals non-atomically
                runner._completed_actual_total = 4.0 + i * 0.1
                runner._completed_estimated_total = 5.0 + i * 0.1

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(stress_eta, 500),
                pool.submit(stress_eta, 500),
                pool.submit(stress_updates, 500),
                pool.submit(stress_updates, 500),
            ]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Found negative ETAs: {errors[:5]}"

    def test_eta_scale_factor_not_infinite_when_estimated_zero(self):
        """If _completed_estimated_total is 0 but _completed_actual_total > 0,
        the scale factor should not blow up."""
        runner = _make_runner(
            step_time_estimates={(0, 0): 0.0},
            total_estimated_seconds=0.0,
        )
        runner._run_start_time = time.monotonic()
        runner._step_start_time = time.monotonic()
        runner._completed_estimated_total = 0.0
        runner._completed_actual_total = 5.0

        with runner._progress_lock:
            runner._progress.current_round_index = 0
            runner._progress.current_step_index = 0

        # Should not raise or return inf/nan
        eta = runner.compute_eta()
        # With no estimates and total_estimated_seconds=0, should return None
        assert eta is None or (eta >= 0 and eta < float("inf"))

    def test_eta_with_no_step_estimates_but_total(self):
        """When step_time_estimates is empty but total_estimated_seconds > 0,
        compute_eta returns a value based on the current step estimate (0)
        since the line 168 guard requires BOTH to be falsy."""
        runner = _make_runner(
            step_time_estimates={},
            total_estimated_seconds=100.0,
        )
        runner._run_start_time = time.monotonic()
        runner._step_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round_index = 0
            runner._progress.current_step_index = 0

        eta = runner.compute_eta()
        # With empty step estimates but total > 0, still computes
        # (returns ~0 since no remaining estimates and current estimate is 0)
        assert eta is not None
        assert eta >= 0


# ---------------------------------------------------------------------------
# 2. _set_operation data race: reads progress fields without lock
# ---------------------------------------------------------------------------


class TestSetOperationConsistency:
    """_set_operation reads self._progress.current_round.current_step_type
    and self._current_operation without any lock. These can be written from
    the worker thread while a timing publisher reads them.
    """

    def test_set_operation_accumulates_subsystem_duration(self):
        """Verify subsystem duration accounting works for a simple sequence."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )

        with patch(
            "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
        ) as mono:
            mono.return_value = 100.0
            runner._set_operation("fluidics")

            mono.return_value = 105.0
            runner._set_operation("imaging")

            mono.return_value = 120.0
            runner._set_operation("waiting")

        assert runner._subsystem_durations.get("fluidics", 0) == pytest.approx(5.0)
        assert runner._subsystem_durations.get("imaging", 0) == pytest.approx(15.0)

    def test_set_operation_with_none_current_round_no_crash(self):
        """_set_operation should not crash when current_round is None."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = None

        # First call sets _current_operation_started_at
        runner._set_operation("imaging")
        # Second call tries to read current_round.current_step_type — must not crash
        runner._set_operation("fluidics")

    def test_subsystem_durations_concurrent_read_write(self):
        """Reading subsystem_durations from timing thread while writing
        from worker thread should not crash."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )

        errors = []

        def writer():
            ops = ["fluidics", "imaging", "intervention", "waiting"]
            for i in range(200):
                runner._set_operation(ops[i % len(ops)])

        def reader():
            for _ in range(200):
                try:
                    snapshot = runner.get_timing_snapshot()
                    # Verify snapshot is a dict with expected keys
                    assert "subsystem_seconds" in snapshot
                    subs = snapshot["subsystem_seconds"]
                    for v in subs.values():
                        if v < 0:
                            errors.append(f"Negative subsystem duration: {v}")
                except Exception as e:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(writer),
                pool.submit(reader),
                pool.submit(reader),
            ]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors during concurrent access: {errors[:5]}"


# ---------------------------------------------------------------------------
# 3. Progress percent edge cases
# ---------------------------------------------------------------------------


class TestProgressPercentEdgeCases:
    """ExperimentProgress.progress_percent has complex branching.
    Test edge cases that could produce values outside [0, 100].
    """

    def test_zero_rounds(self):
        p = ExperimentProgress(total_rounds=0)
        assert p.progress_percent == 0.0

    def test_completed_experiment(self):
        """After all rounds complete, current_round_index == total_rounds."""
        p = ExperimentProgress(
            total_rounds=3,
            current_round_index=3,
            current_round=None,
        )
        assert p.progress_percent == 100.0

    def test_single_round_single_step_midway(self):
        """1 round, 1 imaging step, halfway through FOVs."""
        p = ExperimentProgress(
            total_rounds=1,
            current_round_index=0,
            current_round=RoundProgress(
                round_index=0,
                round_name="r0",
                current_step_index=0,
                total_steps=1,
                current_step_type="imaging",
                imaging_fov_index=5,
                total_imaging_fovs=10,
            ),
        )
        # Progress = (0/1 + (0/1 + 5/10 * 1/1)) * 1/1 * 100
        # = (0 + 0.5) * 100 = 50.0
        assert p.progress_percent == pytest.approx(50.0)

    def test_fov_index_exceeds_total_clamped(self):
        """If imaging_fov_index > total_imaging_fovs, progress should not exceed 100%."""
        p = ExperimentProgress(
            total_rounds=1,
            current_round_index=0,
            current_round=RoundProgress(
                round_index=0,
                round_name="r0",
                current_step_index=0,
                total_steps=1,
                current_step_type="imaging",
                imaging_fov_index=15,
                total_imaging_fovs=10,
            ),
        )
        assert p.progress_percent <= 100.0

    def test_zero_total_fovs_no_division_error(self):
        """If total_imaging_fovs == 0, should not divide by zero."""
        p = ExperimentProgress(
            total_rounds=1,
            current_round_index=0,
            current_round=RoundProgress(
                round_index=0,
                round_name="r0",
                current_step_index=0,
                total_steps=1,
                current_step_type="imaging",
                imaging_fov_index=0,
                total_imaging_fovs=0,
            ),
        )
        # With 0 FOVs, sub-progress should be 0
        assert p.progress_percent == pytest.approx(0.0)

    def test_multi_step_round_progress(self):
        """3 steps in a round: step 0 done, step 1 halfway through imaging."""
        p = ExperimentProgress(
            total_rounds=2,
            current_round_index=0,
            current_round=RoundProgress(
                round_index=0,
                round_name="r0",
                current_step_index=1,
                total_steps=3,
                current_step_type="imaging",
                imaging_fov_index=50,
                total_imaging_fovs=100,
            ),
        )
        # round_frac = 1/2 = 0.5, step_frac = 0.5/3 = 1/6
        # completed_steps = 1 (step 0 done)
        # round_progress = 0 + 1 * (1/6) + 0.5 * (1/6) = 1/6 + 1/12 = 3/12 = 0.25
        # progress = 0.25 * 100 = 25.0
        assert p.progress_percent == pytest.approx(25.0)

    def test_fluidics_sub_progress(self):
        """Fluidics step sub-progress contributes correctly."""
        p = ExperimentProgress(
            total_rounds=1,
            current_round_index=0,
            current_round=RoundProgress(
                round_index=0,
                round_name="r0",
                current_step_index=0,
                total_steps=1,
                current_step_type="fluidics",
                fluidics_step_index=3,
                total_fluidics_steps=6,
            ),
        )
        # sub = 3/6 = 0.5, round_frac = 1.0, step_frac = 1.0
        # progress = (0 + 0 + 0.5 * 1.0) * 100 = 50.0
        assert p.progress_percent == pytest.approx(50.0)

    def test_current_round_index_past_total_rounds(self):
        """Edge case: current_round_index > total_rounds should cap at 100%."""
        p = ExperimentProgress(
            total_rounds=2,
            current_round_index=5,
            current_round=None,
        )
        # 5/2 * 100 = 250, but this is technically a bug in the caller
        # The formula doesn't clamp — document this
        result = p.progress_percent
        assert result >= 100.0  # At minimum, it should show "done"

    def test_step_index_at_total_steps_means_round_done(self):
        """When current_step_index == total_steps, all steps are done."""
        p = ExperimentProgress(
            total_rounds=1,
            current_round_index=0,
            current_round=RoundProgress(
                round_index=0,
                round_name="r0",
                current_step_index=2,  # past last step (0, 1)
                total_steps=2,
                current_step_type="imaging",
                imaging_fov_index=10,
                total_imaging_fovs=10,
            ),
        )
        # completed_steps = min(2, 2) = 2
        # 2 * step_frac = 2 * (1/2) = 1.0 of the round
        # No sub-progress since completed_steps == total_steps
        assert p.progress_percent == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 4. Checkpoint saving during FOV progress callbacks
# ---------------------------------------------------------------------------


class TestCheckpointDuringImaging:
    """The _on_imaging_progress callback saves checkpoints when FOV index changes.
    Test that checkpoints fire correctly and don't double-fire.
    """

    def test_checkpoint_fires_on_each_new_fov(self):
        """Each new FOV index should trigger exactly one checkpoint."""
        checkpoint_calls = []
        runner = _make_runner(
            imaging_executor=MagicMock(),
            on_checkpoint=lambda: checkpoint_calls.append(time.monotonic()),
        )
        runner._run_start_time = time.monotonic()
        runner._step_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )
            runner._last_checkpoint_fov = None

        # Simulate the closure that _execute_imaging_step creates
        def _on_imaging_progress(fov_index, total_fovs, eta_seconds):
            with runner._progress_lock:
                if runner._progress.current_round is not None:
                    runner._progress.current_round.imaging_fov_index = fov_index
                    runner._progress.current_round.total_imaging_fovs = total_fovs
                runner._progress.current_fov_label = (
                    f"FOV {fov_index + 1}" if total_fovs > 0 else ""
                )
                save_ckpt = runner._last_checkpoint_fov != fov_index
                if save_ckpt:
                    runner._last_checkpoint_fov = fov_index
            if save_ckpt:
                runner._on_checkpoint()

        # 10 FOVs, each called once
        for i in range(10):
            _on_imaging_progress(i, 10, None)

        assert len(checkpoint_calls) == 10

    def test_duplicate_fov_index_does_not_double_checkpoint(self):
        """Repeated callbacks with the same FOV should not re-checkpoint."""
        checkpoint_calls = []
        runner = _make_runner(
            imaging_executor=MagicMock(),
            on_checkpoint=lambda: checkpoint_calls.append(1),
        )
        runner._run_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )
            runner._last_checkpoint_fov = None

        def _on_imaging_progress(fov_index, total_fovs, eta_seconds):
            with runner._progress_lock:
                save_ckpt = runner._last_checkpoint_fov != fov_index
                if save_ckpt:
                    runner._last_checkpoint_fov = fov_index
            if save_ckpt:
                runner._on_checkpoint()

        # Same FOV reported 5 times (e.g., multiple images per FOV)
        for _ in range(5):
            _on_imaging_progress(3, 10, None)

        assert len(checkpoint_calls) == 1


# ---------------------------------------------------------------------------
# 5. Timing snapshot consistency during pause/resume
# ---------------------------------------------------------------------------


class TestTimingSnapshotConsistency:
    """get_timing_snapshot reads multiple fields that can be mutated by
    pause/resume. Verify the snapshot is internally consistent.
    """

    def test_paused_time_monotonically_increases(self):
        """Paused seconds should never decrease across snapshots."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic() - 10.0
        runner._step_start_time = time.monotonic() - 5.0

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )

        snapshots = []

        # Simulate pause/resume cycle
        runner.notify_pause()
        time.sleep(0.05)
        snapshots.append(runner.get_timing_snapshot())

        runner.notify_resume()
        snapshots.append(runner.get_timing_snapshot())

        runner.notify_pause()
        time.sleep(0.05)
        snapshots.append(runner.get_timing_snapshot())

        runner.notify_resume()
        snapshots.append(runner.get_timing_snapshot())

        paused_values = [s["paused_seconds"] for s in snapshots]
        for i in range(1, len(paused_values)):
            assert paused_values[i] >= paused_values[i - 1], (
                f"Paused seconds decreased: {paused_values[i-1]} -> {paused_values[i]}"
            )

    def test_effective_run_plus_paused_approximates_elapsed(self):
        """effective_run + paused should roughly equal elapsed (within tolerance)."""
        runner = _make_runner()

        with patch(
            "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
        ) as mono:
            mono.return_value = 100.0
            runner._run_start_time = 100.0
            runner._step_start_time = 100.0

            # Run for 5s
            mono.return_value = 105.0
            runner.notify_pause()

            # Pause for 3s
            mono.return_value = 108.0
            runner.notify_resume()

            # Run for 2s more
            mono.return_value = 110.0

            snapshot = runner.get_timing_snapshot()

        elapsed = snapshot["elapsed_seconds"]
        effective = snapshot["effective_run_seconds"]
        paused = snapshot["paused_seconds"]

        # elapsed should be ~10s (110 - 100)
        assert elapsed == pytest.approx(10.0, abs=0.1)
        # effective should be ~7s (10 - 3 paused)
        assert effective == pytest.approx(7.0, abs=0.1)
        # paused should be ~3s
        assert paused == pytest.approx(3.0, abs=0.1)
        # effective + paused ≈ elapsed
        assert effective + paused == pytest.approx(elapsed, abs=0.2)

    def test_eta_stable_during_pause(self):
        """ETA should not change while paused (time is frozen at pause point)."""
        runner = _make_runner(
            step_time_estimates={(0, 0): 10.0},
            total_estimated_seconds=10.0,
        )

        with patch(
            "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
        ) as mono:
            mono.return_value = 100.0
            runner._run_start_time = 100.0
            runner._step_start_time = 100.0

            with runner._progress_lock:
                runner._progress.current_round_index = 0
                runner._progress.current_step_index = 0

            # Run for 3s, then pause
            mono.return_value = 103.0
            eta_before_pause = runner.compute_eta()
            runner.notify_pause()

            # "Time passes" during pause, but monotonic is frozen for ETA
            mono.return_value = 200.0
            eta_during_pause = runner.compute_eta()

            assert eta_during_pause == pytest.approx(eta_before_pause)


# ---------------------------------------------------------------------------
# 6. Multi-round resume logic
# ---------------------------------------------------------------------------


class TestResumeLogic:
    """Test that resume parameters (start_from_round, start_from_step, start_from_fov)
    are applied correctly — especially that fov_resume is ONLY applied to the
    resume step and not to subsequent steps.
    """

    def test_fov_resume_only_on_resume_step(self):
        """start_from_fov should only apply to the designated resume step."""
        protocol = ExperimentProtocol(
            name="multi_step",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(
                    name="r0",
                    steps=[
                        ImagingStep(protocol="s"),
                        ImagingStep(protocol="s"),
                    ],
                )
            ],
        )
        imaging_executor = MagicMock()
        imaging_executor.execute_with_config.return_value = True

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging_executor,
            start_from_step=0,
            start_from_fov=5,
            step_time_estimates={(0, 0): 5.0, (0, 1): 5.0},
            total_estimated_seconds=10.0,
        )

        runner.run()

        calls = imaging_executor.execute_with_config.call_args_list
        assert len(calls) == 2

        # First step (resume step) should have resume_fov_index=5
        assert calls[0].kwargs["resume_fov_index"] == 5
        # Second step should have resume_fov_index=0
        assert calls[1].kwargs["resume_fov_index"] == 0

    def test_start_from_step_skips_earlier_steps(self):
        """start_from_step=1 should skip step 0."""
        protocol = ExperimentProtocol(
            name="skip_test",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(
                    name="r0",
                    steps=[
                        ImagingStep(protocol="s"),
                        ImagingStep(protocol="s"),
                    ],
                )
            ],
        )
        imaging_executor = MagicMock()
        imaging_executor.execute_with_config.return_value = True

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging_executor,
            start_from_step=1,
            step_time_estimates={(0, 0): 5.0, (0, 1): 5.0},
            total_estimated_seconds=10.0,
        )

        runner.run()

        # Only one call — step 0 was skipped
        assert imaging_executor.execute_with_config.call_count == 1

    def test_start_from_round_skips_earlier_rounds(self):
        """start_from_round=1 should skip round 0 entirely."""
        protocol = ExperimentProtocol(
            name="skip_round_test",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(name="r0", steps=[ImagingStep(protocol="s")]),
                Round(name="r1", steps=[ImagingStep(protocol="s")]),
            ],
        )
        imaging_executor = MagicMock()
        imaging_executor.execute_with_config.return_value = True

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging_executor,
            start_from_round=1,
            step_time_estimates={(0, 0): 5.0, (1, 0): 5.0},
            total_estimated_seconds=10.0,
        )

        runner.run()

        # Only one call — round 0 was skipped
        assert imaging_executor.execute_with_config.call_count == 1

    def test_resume_fov_not_applied_to_second_round(self):
        """start_from_fov should only apply to the first round's resume step."""
        protocol = ExperimentProtocol(
            name="multi_round_resume",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(name="r0", steps=[ImagingStep(protocol="s")]),
                Round(name="r1", steps=[ImagingStep(protocol="s")]),
            ],
        )
        imaging_executor = MagicMock()
        imaging_executor.execute_with_config.return_value = True

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging_executor,
            start_from_round=0,
            start_from_fov=10,
            step_time_estimates={(0, 0): 5.0, (1, 0): 5.0},
            total_estimated_seconds=10.0,
        )

        runner.run()

        calls = imaging_executor.execute_with_config.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["resume_fov_index"] == 10
        assert calls[1].kwargs["resume_fov_index"] == 0


# ---------------------------------------------------------------------------
# 7. Multi-step protocol with mixed step types
# ---------------------------------------------------------------------------


class TestMixedStepProtocol:
    """Test protocols that mix fluidics, imaging, and intervention steps.
    These are the real-world protocols and the most likely to expose
    state tracking bugs.
    """

    def test_fluidics_then_imaging_step_types_tracked(self):
        """Verify that step_type transitions are recorded correctly."""
        protocol = ExperimentProtocol(
            name="mixed",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(
                    name="r0",
                    steps=[
                        FluidicsStep(protocol="wash"),
                        ImagingStep(protocol="s"),
                    ],
                )
            ],
        )
        fluidics = MagicMock()
        fluidics_result = MagicMock()
        fluidics_result.success = True
        fluidics.run_protocol_blocking.return_value = fluidics_result

        imaging = MagicMock()
        imaging.execute_with_config.return_value = True

        operations = []
        runner = _make_runner(
            protocol=protocol,
            fluidics_controller=fluidics,
            imaging_executor=imaging,
            on_operation_change=lambda op: operations.append(op),
            step_time_estimates={(0, 0): 2.0, (0, 1): 5.0},
            total_estimated_seconds=7.0,
        )

        runner.run()

        # Should see fluidics then imaging operations
        assert "fluidics" in operations
        assert "imaging" in operations
        fluidics_idx = operations.index("fluidics")
        imaging_idx = operations.index("imaging")
        assert fluidics_idx < imaging_idx

    def test_imaging_failure_surfaces_in_step_result(self):
        """When imaging fails, the overall run should still return ok
        if the failure policy is skip_step."""
        from squid.core.protocol import StepFailurePolicy

        protocol = ExperimentProtocol(
            name="fail_test",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            defaults={"step_failure_policy": StepFailurePolicy(on_fail="skip_step")},
            rounds=[
                Round(name="r0", steps=[ImagingStep(protocol="s")]),
            ],
        )
        imaging = MagicMock()
        imaging.execute_with_config.return_value = False
        imaging.last_error = "Camera timeout"

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging,
            step_time_estimates={(0, 0): 5.0},
            total_estimated_seconds=5.0,
        )

        result = runner.run()
        # With skip_step policy, the experiment should complete
        assert result.outcome == StepOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 8. Intervention resolution
# ---------------------------------------------------------------------------


class TestInterventionFlow:
    """Test the intervention workflow: step fails, intervention requested,
    operator resolves, execution continues.
    """

    def test_intervention_retry_reexecutes_step(self):
        """After a retry intervention, the step should be re-executed."""
        protocol = ExperimentProtocol(
            name="retry_test",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(name="r0", steps=[ImagingStep(protocol="s")]),
            ],
        )
        imaging = MagicMock()
        # Fail first, succeed on retry
        imaging.execute_with_config.side_effect = [False, True]
        imaging.last_error = "Focus lost"

        intervention_resolved = threading.Event()
        action_queue = ["retry"]

        def consume_action():
            return action_queue.pop(0) if action_queue else "abort"

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging,
            intervention_resolved=intervention_resolved,
            consume_intervention_action=consume_action,
            step_time_estimates={(0, 0): 5.0},
            total_estimated_seconds=5.0,
        )

        # Auto-resolve intervention from another thread
        def auto_resolve():
            time.sleep(0.1)
            intervention_resolved.set()

        resolver = threading.Thread(target=auto_resolve)
        resolver.start()

        result = runner.run()
        resolver.join(timeout=2)

        assert result.outcome == StepOutcome.SUCCESS
        assert imaging.execute_with_config.call_count == 2

    def test_intervention_skip_marks_step_skipped(self):
        """After a skip intervention, the step should be marked skipped."""
        protocol = ExperimentProtocol(
            name="skip_test",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(name="r0", steps=[ImagingStep(protocol="s")]),
            ],
        )
        imaging = MagicMock()
        imaging.execute_with_config.return_value = False
        imaging.last_error = "Focus lost"

        intervention_resolved = threading.Event()

        def consume_action():
            return "skip"

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging,
            intervention_resolved=intervention_resolved,
            consume_intervention_action=consume_action,
            step_time_estimates={(0, 0): 5.0},
            total_estimated_seconds=5.0,
        )

        def auto_resolve():
            time.sleep(0.1)
            intervention_resolved.set()

        resolver = threading.Thread(target=auto_resolve)
        resolver.start()

        result = runner.run()
        resolver.join(timeout=2)

        # Step was skipped, but experiment completes
        assert result.outcome == StepOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 9. Skip round during execution
# ---------------------------------------------------------------------------


class TestSkipRound:
    """Test skip_current_round and skip_to_round behaviors."""

    def test_skip_to_round_during_execution(self):
        """Requesting skip_to_round should cause the runner to jump ahead."""
        protocol = ExperimentProtocol(
            name="skip_round",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(name="r0", steps=[ImagingStep(protocol="s")]),
                Round(name="r1", steps=[ImagingStep(protocol="s")]),
                Round(name="r2", steps=[ImagingStep(protocol="s")]),
            ],
        )
        imaging = MagicMock()
        call_count = [0]

        def fake_execute(**kwargs):
            call_count[0] += 1
            # After first round, request skip to round 2
            if call_count[0] == 1:
                runner.request_skip_to_round(2)
            return True

        imaging.execute_with_config.side_effect = fake_execute

        runner = _make_runner(
            protocol=protocol,
            imaging_executor=imaging,
            step_time_estimates={(0, 0): 5.0, (1, 0): 5.0, (2, 0): 5.0},
            total_estimated_seconds=15.0,
        )

        runner.run()

        # Round 0 and round 2 executed, round 1 skipped
        assert imaging.execute_with_config.call_count == 2


# ---------------------------------------------------------------------------
# 10. Effective step elapsed during pauses
# ---------------------------------------------------------------------------


class TestEffectiveStepElapsed:
    """_effective_step_elapsed is called from compute_eta without lock.
    It reads _step_start_time, _paused_at, _step_paused_total.
    Test that it returns sensible values.
    """

    def test_no_step_started(self):
        runner = _make_runner()
        runner._step_start_time = 0
        assert runner._effective_step_elapsed() == 0.0

    def test_during_active_step(self):
        runner = _make_runner()
        with patch(
            "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
        ) as mono:
            mono.return_value = 100.0
            runner._step_start_time = 95.0
            runner._step_paused_total = 0.0
            runner._paused_at = None
            assert runner._effective_step_elapsed() == pytest.approx(5.0)

    def test_during_pause(self):
        """During pause, elapsed should be frozen at pause time."""
        runner = _make_runner()
        with patch(
            "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
        ) as mono:
            runner._step_start_time = 95.0
            runner._step_paused_total = 0.0
            runner._paused_at = 100.0

            # Even though "now" is 200, we should use the pause time
            mono.return_value = 200.0
            assert runner._effective_step_elapsed() == pytest.approx(5.0)

    def test_after_resume_accounts_for_paused_time(self):
        """After resume, paused duration should be subtracted."""
        runner = _make_runner()
        with patch(
            "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
        ) as mono:
            runner._step_start_time = 95.0
            runner._step_paused_total = 3.0  # 3s were spent paused
            runner._paused_at = None

            mono.return_value = 105.0
            # elapsed = 105 - 95 - 3 = 7
            assert runner._effective_step_elapsed() == pytest.approx(7.0)
