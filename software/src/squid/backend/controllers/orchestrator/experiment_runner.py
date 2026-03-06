"""
ExperimentRunner — executes the experiment loop in the worker thread.

Created once per experiment. Encapsulates the round/step execution
logic that was previously inlined in OrchestratorController._run_experiment.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING

import squid.core.logging
from squid.core.config.test_timing import scale_duration
from squid.core.events import EventBus
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import (
    CapturePolicyConfig,
    ExperimentProtocol,
    FocusGateConfig,
    Round,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
    ImagingProtocol,
)

from squid.backend.controllers.orchestrator import protocol_helpers
from squid.backend.controllers.orchestrator.state import (
    OrchestratorState,
    ExperimentProgress,
    RoundProgress,
    StepResult,
    StepOutcome,
    Checkpoint,
    OrchestratorAttemptUpdate,
    OrchestratorInterventionRequired,
    OrchestratorStepStarted,
    OrchestratorStepCompleted,
)
from squid.backend.controllers.orchestrator.warnings import (
    WarningCategory,
    WarningSeverity,
)

if TYPE_CHECKING:
    from squid.backend.controllers.orchestrator.imaging_executor import ImagingExecutor
    from squid.backend.controllers.fluidics_controller import FluidicsController
    from squid.backend.managers.scan_coordinates import ScanCoordinates

_log = squid.core.logging.get_logger(__name__)


class ExperimentRunner:
    """Executes the experiment loop in the worker thread.

    Created once per experiment with all dependencies injected.
    No state persists between experiments.
    """

    def __init__(
        self,
        protocol: ExperimentProtocol,
        experiment_path: str,
        experiment_id: str,
        cancel_token: CancelToken,
        event_bus: EventBus,
        progress: ExperimentProgress,
        progress_lock: threading.RLock,
        imaging_executor: Optional["ImagingExecutor"],
        fluidics_controller: Optional["FluidicsController"],
        scan_coordinates: Optional["ScanCoordinates"],
        experiment_manager: object,
        experiment_context: object,
        protocol_path: Optional[str],
        # Callbacks into the controller
        on_operation_change: Callable[[str], None],
        on_progress: Callable[[], None],
        on_checkpoint: Callable[[], None],
        on_round_started: Callable[[int, str], None],
        on_round_completed: Callable[[int, str, bool, Optional[str]], None],
        on_transition: Callable[[OrchestratorState], None],
        on_pause: Callable[[], bool],
        on_add_warning: Callable[..., bool],
        intervention_resolved: Optional[threading.Event] = None,
        consume_intervention_action: Optional[Callable[[], str]] = None,
        intervention_acknowledged: Optional[threading.Event] = None,
        # Start-from parameters
        start_from_round: int = 0,
        start_from_step: int = 0,
        start_from_fov: int = 0,
        run_single_round: bool = False,
        # Time estimation
        step_time_estimates: Optional[Dict[Tuple[int, int], float]] = None,
        total_estimated_seconds: float = 0.0,
    ):
        self._protocol = protocol
        self._experiment_path = experiment_path
        self._experiment_id = experiment_id
        self._cancel_token = cancel_token
        self._event_bus = event_bus
        self._progress = progress
        self._progress_lock = progress_lock
        self._imaging_executor = imaging_executor
        self._fluidics_controller = fluidics_controller
        self._scan_coordinates = scan_coordinates
        self._experiment_manager = experiment_manager
        self._context = experiment_context
        self._protocol_path = protocol_path

        self._on_operation_change = on_operation_change
        self._on_progress = on_progress
        self._on_checkpoint = on_checkpoint
        self._on_round_started = on_round_started
        self._on_round_completed = on_round_completed
        self._on_transition = on_transition
        self._on_pause = on_pause
        self._on_add_warning = on_add_warning
        self._intervention_resolved = intervention_resolved or intervention_acknowledged or threading.Event()
        self._consume_intervention_action = consume_intervention_action or (lambda: "acknowledge")

        # Start-from parameters
        self._start_from_round = start_from_round
        self._start_from_step = start_from_step
        self._start_from_fov = start_from_fov
        self._run_single_round = run_single_round

        # Time estimation: per-step estimates from validation
        self._step_time_estimates: Dict[Tuple[int, int], float] = step_time_estimates or {}
        self._total_estimated_seconds = total_estimated_seconds

        # Time tracking (populated during execution)
        self._run_start_time: float = 0.0
        self._step_start_time: float = 0.0
        self._completed_estimated_total: float = 0.0
        self._completed_actual_total: float = 0.0
        self._paused_at: Optional[float] = None
        self._step_paused_total: float = 0.0
        self._total_paused_seconds: float = 0.0
        self._retry_overhead_seconds: float = 0.0
        self._intervention_overhead_seconds: float = 0.0
        self._subsystem_durations: Dict[str, float] = {}
        self._current_operation_started_at: Optional[float] = None
        self._current_operation: str = ""

        # Shared skip flags (written by controller, read by runner)
        self._skip_to_round_index: Optional[int] = None
        self._skip_current_round_now = False
        self._latest_eta_seconds: Optional[float] = None
        self._last_checkpoint_fov: Optional[int] = None

    def compute_eta(self) -> Optional[float]:
        """Compute estimated time remaining based on estimates and actuals.

        Uses a scaling approach: for completed steps, we know the actual time.
        For remaining steps, we apply a scaling factor derived from
        actual/estimated ratio of completed steps.

        Thread-safe: acquires _progress_lock internally. Do NOT call while
        already holding _progress_lock.

        Returns:
            Estimated seconds remaining, or None if no estimate available.
        """
        if not self._step_time_estimates and self._total_estimated_seconds <= 0:
            return None

        with self._progress_lock:
            current_round = self._progress.current_round_index
            current_step = self._progress.current_step_index

        # Sum estimates for steps not yet completed
        remaining_estimated = 0.0
        for (round_idx, step_idx), est in self._step_time_estimates.items():
            if round_idx > current_round or (
                round_idx == current_round and step_idx > current_step
            ):
                remaining_estimated += est

        # Estimate for current step: estimate minus elapsed in current step
        current_key = (current_round, current_step)
        current_est = self._step_time_estimates.get(current_key, 0.0)
        elapsed_in_step = self._effective_step_elapsed()
        current_remaining = max(0.0, current_est - elapsed_in_step)

        # Scaling factor from completed steps
        scale = 1.0
        if self._completed_estimated_total > 0 and self._completed_actual_total > 0:
            scale = self._completed_actual_total / self._completed_estimated_total

        return (remaining_estimated + current_remaining) * scale

    def notify_pause(self) -> None:
        """Record that execution is paused to keep ETA stable."""
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def notify_resume(self) -> None:
        """Record that execution resumed and account for paused time."""
        if self._paused_at is None:
            return
        paused_duration = time.monotonic() - self._paused_at
        if paused_duration > 0:
            self._step_paused_total += paused_duration
            self._total_paused_seconds += paused_duration
        self._paused_at = None

    def _effective_step_elapsed(self) -> float:
        """Elapsed step time excluding pauses."""
        if self._step_start_time <= 0:
            return 0.0
        now = time.monotonic()
        paused_at = self._paused_at
        if paused_at is not None:
            now = paused_at
        elapsed = now - self._step_start_time - self._step_paused_total
        return max(0.0, elapsed)

    def _effective_run_elapsed(self) -> float:
        """Elapsed experiment time excluding pauses."""
        if self._run_start_time <= 0:
            return 0.0
        now = time.monotonic()
        if self._paused_at is not None:
            now = self._paused_at
        return max(0.0, now - self._run_start_time - self._total_paused_seconds)

    def get_timing_snapshot(self) -> dict[str, object]:
        """Return a thread-safe timing snapshot for UI and disk logging."""
        eta = self.compute_eta()
        return {
            "elapsed_seconds": max(0.0, time.monotonic() - self._run_start_time) if self._run_start_time > 0 else 0.0,
            "effective_run_seconds": self._effective_run_elapsed(),
            "paused_seconds": self._total_paused_seconds,
            "retry_overhead_seconds": self._retry_overhead_seconds,
            "intervention_overhead_seconds": self._intervention_overhead_seconds,
            "eta_seconds": eta,
            "subsystem_seconds": dict(self._subsystem_durations),
        }

    def _set_operation(self, operation: str) -> None:
        """Track active subsystem duration accounting."""
        now = time.monotonic()
        if self._current_operation_started_at is not None:
            previous = self._progress.current_round.current_step_type if self._progress.current_round else self._current_operation
            previous = self._current_operation or previous
            if previous:
                self._subsystem_durations[previous] = self._subsystem_durations.get(previous, 0.0) + max(
                    0.0,
                    now - self._current_operation_started_at,
                )
        self._current_operation = operation
        self._current_operation_started_at = now
        self._on_operation_change(operation)
        self._on_progress()

    def _publish_attempt_update(
        self,
        round_idx: int,
        step_idx: int,
        step_type: str,
        attempt: int,
        phase: str,
        message: str = "",
    ) -> None:
        with self._progress_lock:
            current_fov_index = 0
            current_fov_label = self._progress.current_fov_label
            if self._progress.current_round is not None:
                current_fov_index = self._progress.current_round.imaging_fov_index
            self._progress.current_attempt = attempt
        self._event_bus.publish(
            OrchestratorAttemptUpdate(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                step_index=step_idx,
                step_type=step_type,
                attempt=attempt,
                phase=phase,
                message=message,
                current_fov_index=current_fov_index,
                current_fov_label=current_fov_label,
            )
        )

    def request_skip_current_round(self) -> bool:
        """Request skipping the remainder of the current round."""
        with self._progress_lock:
            if self._progress.current_round is None:
                return False
            self._skip_current_round_now = True
            self._skip_to_round_index = self._progress.current_round_index + 1
            return True

    def request_skip_to_round(self, round_index: int) -> bool:
        """Request skipping ahead to a specific round index.

        The request is accepted only while a round is active and when the
        target round is strictly ahead of the current round.
        """
        total_rounds = len(self._protocol.rounds)
        with self._progress_lock:
            if self._progress.current_round is None:
                return False
            current_round_index = self._progress.current_round_index
            if round_index < 0 or round_index >= total_rounds:
                return False
            if round_index <= current_round_index:
                return False
            self._skip_to_round_index = round_index
            return True

    def run(self, resume_checkpoint: Optional[Checkpoint] = None) -> StepResult:
        """Execute all rounds. Called from the worker thread.

        Args:
            resume_checkpoint: Optional checkpoint for resume

        Returns:
            StepResult summarizing the experiment outcome
        """
        initial_elapsed = max(0.0, resume_checkpoint.elapsed_seconds) if resume_checkpoint is not None else 0.0
        self._run_start_time = time.monotonic() - initial_elapsed
        # Initialize ETA from total estimate
        self._latest_eta_seconds = self._total_estimated_seconds if self._total_estimated_seconds > 0 else None
        start_round = self._start_from_round
        resume_step_index = self._start_from_step
        resume_imaging_fov = self._start_from_fov
        if resume_checkpoint is not None:
            start_round = resume_checkpoint.round_index
            resume_step_index = resume_checkpoint.step_index
            resume_imaging_fov = resume_checkpoint.imaging_fov_index
            self._total_paused_seconds = max(0.0, resume_checkpoint.paused_seconds)
            with self._progress_lock:
                self._progress.current_attempt = max(1, resume_checkpoint.current_attempt)
                self._progress.elapsed_seconds = max(0.0, resume_checkpoint.elapsed_seconds)
                self._progress.paused_seconds = max(0.0, resume_checkpoint.paused_seconds)
                self._progress.effective_run_seconds = max(
                    0.0,
                    resume_checkpoint.effective_run_seconds,
                )

        # Determine end round for run_single_round mode
        end_round = len(self._protocol.rounds)
        if self._run_single_round:
            end_round = min(start_round + 1, len(self._protocol.rounds))

        for round_idx in range(start_round, end_round):
            round_ = self._protocol.rounds[round_idx]
            self._cancel_token.check_point()

            # Update progress
            with self._progress_lock:
                self._progress.current_round_index = round_idx
                self._progress.current_round = RoundProgress(
                    round_index=round_idx,
                    round_name=round_.name,
                    started_at=datetime.now(),
                )
                self._progress.current_step_index = 0
                self._latest_eta_seconds = None
                self._last_checkpoint_fov = None

            with self._progress_lock:
                skip_to = self._skip_to_round_index

            if skip_to is not None and round_idx < skip_to:
                _log.info(f"Skipping round {round_idx} ({round_.name})")
                self._on_round_completed(round_idx, round_.name, True, "skipped")
                continue
            if skip_to is not None and round_idx == skip_to:
                with self._progress_lock:
                    self._skip_to_round_index = None

            # Execute round
            skipped = self._execute_round(
                round_idx,
                round_,
                resume_step_index=resume_step_index if round_idx == start_round else 0,
                resume_imaging_fov=resume_imaging_fov if round_idx == start_round else 0,
            )

            # Mark round complete
            with self._progress_lock:
                if self._progress.current_round is not None:
                    self._progress.current_round.completed_at = datetime.now()
            # Advance round index past completed round so progress reflects completion
            with self._progress_lock:
                self._progress.current_round_index = round_idx + 1
            if skipped:
                self._on_round_completed(round_idx, round_.name, True, "skipped")
            else:
                self._on_round_completed(round_idx, round_.name, True, None)

        return StepResult.ok("experiment")

    def _execute_round(
        self,
        round_idx: int,
        round_: Round,
        *,
        resume_step_index: int = 0,
        resume_imaging_fov: int = 0,
    ) -> bool:
        """Execute a single round using step-based execution."""
        _log.info(f"Executing round {round_idx}: name={round_.name}, steps={len(round_.steps)}")
        self._on_round_started(round_idx, round_.name)

        skipped = False
        for step_idx, step in enumerate(round_.steps):
            if step_idx < resume_step_index:
                continue

            self._cancel_token.check_point()

            # Update step progress
            with self._progress_lock:
                self._progress.current_step_index = step_idx
                if self._progress.current_round is not None:
                    self._progress.current_round.current_step_index = step_idx
                    self._progress.current_round.total_steps = len(round_.steps)
            self._on_checkpoint()

            # Determine step type
            if isinstance(step, FluidicsStep):
                step_type = "fluidics"
            elif isinstance(step, ImagingStep):
                step_type = "imaging"
            elif isinstance(step, InterventionStep):
                step_type = "intervention"
            else:
                step_type = "unknown"

            # Record step start time for ETA computation
            self._step_paused_total = 0.0
            self._paused_at = None
            self._step_start_time = time.monotonic()

            # Publish step started event
            step_estimate = self._step_time_estimates.get((round_idx, step_idx), 0.0)
            self._event_bus.publish(
                OrchestratorStepStarted(
                    experiment_id=self._experiment_id,
                    round_index=round_idx,
                    step_index=step_idx,
                    step_type=step_type,
                    estimated_seconds=step_estimate,
                )
            )

            # Update ETA before step executes
            eta = self.compute_eta()
            if eta is not None:
                self._latest_eta_seconds = eta
            self._on_progress()

            if isinstance(step, FluidicsStep):
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.current_step_type = "fluidics"
                self._set_operation("fluidics")
            elif isinstance(step, ImagingStep):
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.current_step_type = "imaging"
                self._set_operation("imaging")
            elif isinstance(step, InterventionStep):
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.current_step_type = "intervention"
                self._set_operation("intervention")
            else:
                self._set_operation("unknown")

            fov_resume = resume_imaging_fov if step_idx == resume_step_index else 0
            result = self._execute_step_with_retries(
                round_idx=round_idx,
                step_idx=step_idx,
                step=step,
                resume_fov=fov_resume,
            )

            while result.outcome == StepOutcome.FAILED:
                resolution = self._apply_step_failure_policy(
                    round_idx,
                    step_idx,
                    step,
                    result,
                )
                if resolution == "abort":
                    raise CancellationError(result.error_message or "Cancelled")
                if resolution == "skip_step":
                    result = StepResult.skipped(step_type, result.error_message or "Skipped after intervention")
                    break

                resume_fov = 0
                if isinstance(step, ImagingStep) and self._progress.current_round is not None:
                    resume_fov = max(self._progress.current_round.imaging_fov_index, 0)
                result = self._execute_step_with_retries(
                    round_idx=round_idx,
                    step_idx=step_idx,
                    step=step,
                    resume_fov=resume_fov,
                )

            if result.outcome == StepOutcome.CANCELLED:
                raise CancellationError(result.error_message or "Cancelled")

            # Record actual step duration and update ETA
            step_duration = self._effective_step_elapsed()
            step_key = (round_idx, step_idx)
            step_estimate = self._step_time_estimates.get(step_key, 0.0)
            self._completed_actual_total += step_duration
            self._completed_estimated_total += step_estimate

            eta = self.compute_eta()
            if eta is not None:
                self._latest_eta_seconds = eta

            # Publish step completed event
            self._event_bus.publish(
                OrchestratorStepCompleted(
                    experiment_id=self._experiment_id,
                    round_index=round_idx,
                    step_index=step_idx,
                    step_type=step_type,
                    success=result.success,
                    error=result.error_message,
                    duration_seconds=step_duration,
                )
            )

            # Advance step index past the completed step so progress reflects completion
            with self._progress_lock:
                self._progress.current_step_index = step_idx + 1
                if self._progress.current_round is not None:
                    self._progress.current_round.current_step_index = step_idx + 1
            if result.success:
                self._on_checkpoint()

            with self._progress_lock:
                should_skip = self._skip_current_round_now
                if should_skip:
                    self._skip_current_round_now = False
            if should_skip:
                skipped = True
                _log.info(f"Skipping remainder of round {round_idx} ({round_.name})")
                break

        self._on_progress()
        return skipped

    def _step_label(self, step: object, step_idx: int) -> str:
        if isinstance(step, (ImagingStep, FluidicsStep)) and step.label:
            return step.label
        if isinstance(step, ImagingStep):
            return step.output_label or step.protocol or f"Imaging {step_idx + 1}"
        if isinstance(step, FluidicsStep):
            return step.protocol or f"Fluidics {step_idx + 1}"
        if isinstance(step, InterventionStep):
            return f"Intervention {step_idx + 1}"
        return f"Step {step_idx + 1}"

    def _execute_step_with_retries(
        self,
        *,
        round_idx: int,
        step_idx: int,
        step: object,
        resume_fov: int = 0,
    ) -> StepResult:
        policy = getattr(step, "failure_policy", None) or self._protocol.step_failure_policy
        max_attempts = max(getattr(policy, "max_attempts", 1), 1)
        retry_delay = max(getattr(policy, "retry_delay_s", 0.0), 0.0)
        step_type = step.step_type if hasattr(step, "step_type") else "unknown"

        with self._progress_lock:
            self._progress.current_step_name = self._step_label(step, step_idx)

        for attempt in range(1, max_attempts + 1):
            self._publish_attempt_update(
                round_idx,
                step_idx,
                step_type,
                attempt,
                "started",
            )

            if isinstance(step, FluidicsStep):
                result = self._execute_fluidics_step(round_idx, step)
            elif isinstance(step, ImagingStep):
                result = self._execute_imaging_step(round_idx, step, resume_fov=resume_fov)
            elif isinstance(step, InterventionStep):
                result = self._execute_intervention_step(round_idx, step)
            else:
                result = StepResult.skipped("unknown", f"Unknown step type: {type(step)}")

            if result.outcome != StepOutcome.FAILED:
                self._publish_attempt_update(
                    round_idx,
                    step_idx,
                    step_type,
                    attempt,
                    "completed",
                    result.error_message or "",
                )
                return result

            self._publish_attempt_update(
                round_idx,
                step_idx,
                step_type,
                attempt,
                "failed",
                result.error_message or "",
            )

            if attempt >= max_attempts:
                return result

            self._retry_overhead_seconds += retry_delay
            self._publish_attempt_update(
                round_idx,
                step_idx,
                step_type,
                attempt + 1,
                "retry_scheduled",
                result.error_message or "",
            )
            if retry_delay > 0:
                deadline = time.monotonic() + retry_delay
                while time.monotonic() < deadline:
                    self._cancel_token.check_point()
                    time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

        return StepResult.failed(step_type, "Retry loop exhausted")

    def _apply_step_failure_policy(
        self,
        round_idx: int,
        step_idx: int,
        step: object,
        result: StepResult,
    ) -> str:
        policy = getattr(step, "failure_policy", None) or self._protocol.step_failure_policy
        action = getattr(policy, "on_fail", "pause")
        if action == "pause":
            return self._resolve_failure_intervention(
                round_idx=round_idx,
                step_idx=step_idx,
                step=step,
                result=result,
            )
        if action == "skip_step":
            return "skip_step"
        if action == "abort":
            return "abort"
        return "skip_step"

    def _resolve_failure_intervention(
        self,
        *,
        round_idx: int,
        step_idx: int,
        step: object,
        result: StepResult,
    ) -> str:
        self._on_transition(OrchestratorState.WAITING_INTERVENTION)
        self._intervention_resolved.clear()
        started_at = time.monotonic()
        with self._progress_lock:
            round_name = self._progress.current_round.round_name if self._progress.current_round else ""
            current_fov_label = self._progress.current_fov_label
            attempt = self._progress.current_attempt
        self._event_bus.publish(
            OrchestratorInterventionRequired(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                round_name=round_name,
                message=result.error_message or "Step failed",
                kind="failure",
                attempt=attempt,
                current_step_name=self._step_label(step, step_idx),
                current_fov_label=current_fov_label,
                allowed_actions=("retry", "skip", "abort"),
            )
        )
        while not self._intervention_resolved.is_set():
            self._cancel_token.check_point()
            self._intervention_resolved.wait(timeout=scale_duration(0.5, min_seconds=0.05))
        self._intervention_overhead_seconds += max(0.0, time.monotonic() - started_at)
        action = self._consume_intervention_action()
        self._on_transition(OrchestratorState.RUNNING)
        if action == "abort":
            return "abort"
        if action == "retry":
            return "retry"
        return "skip_step"

    def _execute_fluidics_step(self, round_idx: int, step: FluidicsStep) -> StepResult:
        """Execute a fluidics step."""
        if self._progress.current_round is None:
            return StepResult.skipped("fluidics", "No progress tracking")

        protocol_name = step.protocol
        _log.info(f"Round {round_idx}: Running fluidics protocol '{protocol_name}'")

        if self._fluidics_controller is None:
            _log.error(f"No fluidics controller configured for protocol '{protocol_name}'")
            return StepResult.failed("fluidics", "No fluidics controller configured")

        try:
            self._cancel_token.check_point()

            def _on_fluidics_progress(current_step: int, total_steps: int) -> None:
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.fluidics_step_index = current_step
                        self._progress.current_round.total_fluidics_steps = total_steps
                self._on_progress()

            result = self._fluidics_controller.run_protocol_blocking(
                protocol_name,
                cancel_token=self._cancel_token,
                progress_callback=_on_fluidics_progress,
            )

            if result is None:
                return StepResult.failed(
                    "fluidics", f"Failed to start fluidics protocol: {protocol_name}"
                )
            if not result.success:
                from squid.backend.controllers.fluidics_controller import FluidicsControllerState

                terminal_state = self._fluidics_controller.last_terminal_state
                if terminal_state == FluidicsControllerState.STOPPED:
                    return StepResult.cancelled(
                        "fluidics", f"Fluidics protocol '{protocol_name}' was stopped"
                    )
                return StepResult.failed(
                    "fluidics", f"Fluidics protocol '{protocol_name}' failed"
                )

            _log.info(f"Round {round_idx}: Fluidics protocol '{protocol_name}' completed")
            return StepResult.ok("fluidics")

        except CancellationError:
            return StepResult.cancelled("fluidics")
        except Exception as e:
            return StepResult.failed("fluidics", str(e))

    def _execute_imaging_step(
        self,
        round_idx: int,
        step: ImagingStep,
        *,
        resume_fov: int = 0,
    ) -> StepResult:
        """Execute an imaging step."""
        if self._progress.current_round is None:
            return StepResult.skipped("imaging", "No progress tracking")
        resume_fov = max(resume_fov, 0)

        try:
            config_name = step.protocol
            if config_name not in self._protocol.imaging_protocols:
                return StepResult.failed(
                    "imaging", f"Imaging protocol '{config_name}' not found in protocol"
                )
            imaging_config = self._resolve_imaging_config(step)

            # Load FOV set if specified
            if step.fovs not in ("current", "default"):
                if step.fovs not in self._protocol.fov_sets:
                    return StepResult.failed(
                        "imaging", f"FOV set '{step.fovs}' not found in protocol"
                    )
                csv_path = self._protocol.fov_sets[step.fovs]
                protocol_helpers.load_fov_set(csv_path, self._scan_coordinates, self._event_bus)

            total_fovs = 0
            if self._scan_coordinates is not None:
                region_fovs = getattr(self._scan_coordinates, "region_fov_coordinates", {})
                if isinstance(region_fovs, dict):
                    total_fovs = sum(len(coords) for coords in region_fovs.values())

            if total_fovs > 0 and resume_fov >= total_fovs:
                return StepResult.failed(
                    "imaging",
                    f"start_from_fov out of bounds ({resume_fov} not in [0, {total_fovs - 1}])",
                )

            with self._progress_lock:
                self._progress.current_round.imaging_fov_index = max(resume_fov, 0)
                self._progress.current_round.total_imaging_fovs = total_fovs
                self._progress.current_round.imaging_started = True
                self._progress.current_round.imaging_completed = False
                self._last_checkpoint_fov = None

            # Create round subfolder
            if hasattr(self._experiment_manager, "create_round_subfolder"):
                round_path = self._experiment_manager.create_round_subfolder(
                    context=self._context,
                    round_name=f"round_{round_idx:03d}_{step.protocol}",
                )
            else:
                round_path = self._experiment_path

            round_dir_name = os.path.basename(round_path)
            round_base_path = os.path.dirname(round_path)

            _log.info(
                f"Round {round_idx}: Imaging with config='{config_name}', "
                f"channels={imaging_config.get_channel_names()}"
            )

            # Progress callback for FOV-level updates
            def _on_imaging_progress(
                fov_index: int, total_fovs: int, eta_seconds: Optional[float]
            ) -> None:
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.imaging_fov_index = fov_index
                        self._progress.current_round.total_imaging_fovs = total_fovs
                    self._progress.current_fov_label = f"FOV {fov_index + 1}" if total_fovs > 0 else ""
                    self._progress.elapsed_seconds = max(
                        0.0,
                        time.monotonic() - self._run_start_time,
                    )
                    self._progress.effective_run_seconds = self._effective_run_elapsed()
                    self._progress.paused_seconds = self._total_paused_seconds
                    self._progress.retry_overhead_seconds = self._retry_overhead_seconds
                    self._progress.intervention_overhead_seconds = self._intervention_overhead_seconds
                    self._progress.subsystem_seconds = dict(self._subsystem_durations)
                    self._latest_eta_seconds = eta_seconds
                    save_ckpt = self._last_checkpoint_fov != fov_index
                    if save_ckpt:
                        self._last_checkpoint_fov = fov_index

                if save_ckpt:
                    self._on_checkpoint()
                self._on_progress()

            if self._imaging_executor is not None:
                success = self._imaging_executor.execute_with_config(
                    imaging_config=imaging_config,
                    output_path=round_base_path,
                    cancel_token=self._cancel_token,
                    round_index=round_idx,
                    resume_fov_index=resume_fov,
                    experiment_id=round_dir_name,
                    progress_callback=_on_imaging_progress,
                )
                if not success:
                    return StepResult.failed("imaging", f"Imaging failed for round {round_idx}")
            else:
                _log.error(f"No imaging executor configured for config '{config_name}'")
                return StepResult.failed("imaging", "No imaging executor configured")

            with self._progress_lock:
                self._progress.current_round.imaging_completed = True
            self._on_progress()
            return StepResult.ok("imaging")

        except CancellationError:
            return StepResult.cancelled("imaging")
        except Exception as e:
            return StepResult.failed("imaging", str(e))

    def _execute_intervention_step(
        self, round_idx: int, step: InterventionStep
    ) -> StepResult:
        """Wait for operator intervention."""
        try:
            self._on_transition(OrchestratorState.WAITING_INTERVENTION)
            self._intervention_resolved.clear()
            started_at = time.monotonic()

            with self._progress_lock:
                round_name = (
                    self._progress.current_round.round_name
                    if self._progress.current_round
                    else ""
                )

            self._event_bus.publish(
                OrchestratorInterventionRequired(
                    experiment_id=self._experiment_id,
                    round_index=round_idx,
                    round_name=round_name,
                    message=step.message,
                    kind="acknowledge",
                    attempt=self._progress.current_attempt,
                    current_step_name="Intervention",
                    current_fov_label=self._progress.current_fov_label,
                    allowed_actions=("acknowledge",),
                )
            )

            while not self._intervention_resolved.is_set():
                self._cancel_token.check_point()
                self._intervention_resolved.wait(
                    timeout=scale_duration(0.5, min_seconds=0.05)
                )

            self._intervention_overhead_seconds += max(0.0, time.monotonic() - started_at)
            action = self._consume_intervention_action()
            self._on_transition(OrchestratorState.RUNNING)
            if action == "abort":
                return StepResult.cancelled("intervention", "Operator abort")
            return StepResult.ok("intervention")

        except CancellationError:
            return StepResult.cancelled("intervention")
        except Exception as e:
            return StepResult.failed("intervention", str(e))

    def _resolve_imaging_config(self, step: ImagingStep) -> ImagingProtocol:
        """Merge step-level overrides into the referenced imaging protocol."""
        config_name = step.protocol
        if config_name not in self._protocol.imaging_protocols:
            raise KeyError(config_name)

        imaging_config = self._protocol.imaging_protocols[config_name]
        focus_gate = imaging_config.focus_gate
        capture_policy = imaging_config.capture_policy

        if step.focus_gate_override is not None:
            focus_gate = focus_gate.model_copy(
                update=step.focus_gate_override.model_dump(exclude_none=True)
            )
        if step.capture_policy_override is not None:
            capture_policy = capture_policy.model_copy(
                update=step.capture_policy_override.model_dump(exclude_none=True)
            )

        return imaging_config.model_copy(
            update={
                "focus_gate": focus_gate,
                "capture_policy": capture_policy,
            }
        )
