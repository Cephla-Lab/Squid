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
    ExperimentProtocol,
    Round,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
    FailureAction,
)

from squid.backend.controllers.orchestrator import protocol_helpers
from squid.backend.controllers.orchestrator.state import (
    OrchestratorState,
    ExperimentProgress,
    RoundProgress,
    StepResult,
    StepOutcome,
    Checkpoint,
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
        intervention_acknowledged: threading.Event,
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
        self._intervention_acknowledged = intervention_acknowledged

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
        self._run_start_time = time.monotonic()
        # Initialize ETA from total estimate
        self._latest_eta_seconds = self._total_estimated_seconds if self._total_estimated_seconds > 0 else None
        start_round = self._start_from_round
        resume_step_index = self._start_from_step
        resume_imaging_fov = self._start_from_fov
        if resume_checkpoint is not None:
            start_round = resume_checkpoint.round_index
            resume_step_index = resume_checkpoint.step_index
            resume_imaging_fov = resume_checkpoint.imaging_fov_index

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

            # Execute the step
            if isinstance(step, FluidicsStep):
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.current_step_type = "fluidics"
                self._on_operation_change("fluidics")
                result = self._execute_fluidics_step(round_idx, step)
            elif isinstance(step, ImagingStep):
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.current_step_type = "imaging"
                self._on_operation_change("imaging")
                fov_resume = resume_imaging_fov if step_idx == resume_step_index else 0
                result = self._execute_imaging_step(round_idx, step, resume_fov=fov_resume)
            elif isinstance(step, InterventionStep):
                with self._progress_lock:
                    if self._progress.current_round is not None:
                        self._progress.current_round.current_step_type = "intervention"
                self._on_operation_change("intervention")
                result = self._execute_intervention_step(round_idx, step)
            else:
                result = StepResult.skipped("unknown", f"Unknown step type: {type(step)}")

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

            # Handle result
            if result.outcome == StepOutcome.CANCELLED:
                raise CancellationError(result.error_message or "Cancelled")

            if result.outcome == StepOutcome.FAILED:
                action = self._get_failure_action(step)
                if action == FailureAction.ABORT:
                    raise RuntimeError(result.error_message or f"Step failed in round {round_idx}")
                elif action == FailureAction.PAUSE:
                    _log.info(f"Pausing after step failure (error_handling={action.value})")
                    self._on_pause()
                    self._cancel_token.check_point()
                    _log.info("Resumed after step failure, continuing to next step")
                elif action == FailureAction.WARN:
                    self._on_add_warning(
                        category=WarningCategory.EXECUTION,
                        severity=WarningSeverity.MEDIUM,
                        message=f"{result.step_type.capitalize()} step failed: {result.error_message}",
                    )
                # SKIP: fall through

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

    def _get_failure_action(self, step) -> FailureAction:
        """Determine failure action for a step from protocol error_handling."""
        error_handling = self._protocol.error_handling
        if isinstance(step, FluidicsStep):
            return error_handling.fluidics_failure
        elif isinstance(step, ImagingStep):
            return error_handling.imaging_failure
        return FailureAction.ABORT

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
            imaging_config = self._protocol.imaging_protocols[config_name]

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
            self._intervention_acknowledged.clear()

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
                )
            )

            while not self._intervention_acknowledged.is_set():
                self._cancel_token.check_point()
                self._intervention_acknowledged.wait(
                    timeout=scale_duration(0.5, min_seconds=0.05)
                )

            self._on_transition(OrchestratorState.RUNNING)
            return StepResult.ok("intervention")

        except CancellationError:
            return StepResult.cancelled("intervention")
        except Exception as e:
            return StepResult.failed("intervention", str(e))
