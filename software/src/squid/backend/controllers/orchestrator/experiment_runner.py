"""
ExperimentRunner — executes the experiment loop in the worker thread.

Created once per experiment. Encapsulates the round/step execution
logic that was previously inlined in OrchestratorController._run_experiment.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Callable, Optional, TYPE_CHECKING

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

        # Shared skip flags (written by controller, read by runner)
        self._skip_to_round_index: Optional[int] = None
        self._skip_current_round_now = False
        self._latest_eta_seconds: Optional[float] = None
        self._last_checkpoint_fov: Optional[int] = None

    def run(self, resume_checkpoint: Optional[Checkpoint] = None) -> StepResult:
        """Execute all rounds. Called from the worker thread.

        Args:
            resume_checkpoint: Optional checkpoint for resume

        Returns:
            StepResult summarizing the experiment outcome
        """
        start_round = 0
        resume_step_index = 0
        resume_imaging_fov = 0
        if resume_checkpoint is not None:
            start_round = resume_checkpoint.round_index
            resume_step_index = resume_checkpoint.step_index
            resume_imaging_fov = resume_checkpoint.imaging_fov_index

        for round_idx in range(start_round, len(self._protocol.rounds)):
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
            self._progress.current_round.completed_at = datetime.now()
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

            # Execute the step
            if isinstance(step, FluidicsStep):
                self._on_operation_change("fluidics")
                result = self._execute_fluidics_step(round_idx, step)
            elif isinstance(step, ImagingStep):
                self._on_operation_change("imaging")
                fov_resume = resume_imaging_fov if step_idx == resume_step_index else 0
                result = self._execute_imaging_step(round_idx, step, resume_fov=fov_resume)
            elif isinstance(step, InterventionStep):
                self._on_operation_change("intervention")
                result = self._execute_intervention_step(round_idx, step)
            else:
                result = StepResult.skipped("unknown", f"Unknown step type: {type(step)}")

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
            _log.debug(f"[SIMULATED] Fluidics protocol: {protocol_name}")
            return StepResult.ok("fluidics")

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

            with self._progress_lock:
                self._progress.current_round.imaging_fov_index = 0
                self._progress.current_round.total_imaging_fovs = 0
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

            # Calculate total FOVs
            if self._scan_coordinates is not None:
                total_fovs = sum(
                    len(coords)
                    for coords in self._scan_coordinates.region_fov_coordinates.values()
                )
                with self._progress_lock:
                    self._progress.current_round.total_imaging_fovs = total_fovs

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
                _log.debug(f"[SIMULATED] Imaging: config={config_name}")

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
