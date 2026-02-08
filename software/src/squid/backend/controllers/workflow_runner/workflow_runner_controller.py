"""
Workflow Runner Controller.

StateMachine-based controller that executes workflow sequences combining
external scripts and built-in acquisitions. Uses EventBus for communication
and CancelToken for cooperative cancellation.

Ported from upstream control/workflow_runner.py WorkflowRunner (da8f193a),
adapted to the 3-layer architecture.
"""

import subprocess
import threading
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.events import EventBus, handles, AcquisitionFinished
from squid.core.state_machine import StateMachine
from squid.core.utils.cancel_token import CancelToken, CancellationError

from squid.backend.controllers.workflow_runner.state import (
    WorkflowRunnerState,
    WORKFLOW_RUNNER_TRANSITIONS,
    WorkflowRunnerStateChanged,
    WorkflowCycleStarted,
    WorkflowSequenceStarted,
    WorkflowSequenceFinished,
    WorkflowScriptOutput,
    WorkflowError,
    WorkflowLoadConfigRequest,
    WorkflowLoadConfigResponse,
    StartWorkflowCommand,
    StopWorkflowCommand,
    PauseWorkflowCommand,
    ResumeWorkflowCommand,
)
from squid.backend.controllers.workflow_runner.models import Workflow

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint import MultiPointController

_log = squid.core.logging.get_logger(__name__)


class WorkflowRunnerController(StateMachine[WorkflowRunnerState]):
    """Executes workflow sequences via StateMachine + worker thread.

    Coordinates external scripts (subprocess) and built-in acquisitions
    (via MultiPointController) in configurable cycles.

    State Machine:
        IDLE -> RUNNING_SCRIPT | RUNNING_ACQUISITION
        RUNNING_SCRIPT -> RUNNING_SCRIPT | RUNNING_ACQUISITION | PAUSED | COMPLETED | FAILED | ABORTED
        RUNNING_ACQUISITION -> RUNNING_SCRIPT | RUNNING_ACQUISITION | PAUSED | COMPLETED | FAILED | ABORTED
        PAUSED -> RUNNING_SCRIPT | RUNNING_ACQUISITION | ABORTED
        COMPLETED -> IDLE
        FAILED -> IDLE
        ABORTED -> IDLE

    Usage:
        controller = WorkflowRunnerController(
            event_bus=event_bus,
            multipoint_controller=multipoint,
        )

        # Start via command
        event_bus.publish(StartWorkflowCommand(workflow_dict=workflow.to_dict()))

        # Control
        event_bus.publish(PauseWorkflowCommand())
        event_bus.publish(ResumeWorkflowCommand())
        event_bus.publish(StopWorkflowCommand())
    """

    def __init__(
        self,
        event_bus: EventBus,
        multipoint_controller: "MultiPointController",
    ):
        super().__init__(
            initial_state=WorkflowRunnerState.IDLE,
            transitions=WORKFLOW_RUNNER_TRANSITIONS,
            event_bus=event_bus,
            name="WorkflowRunnerController",
        )

        self._event_bus = event_bus
        self._multipoint = multipoint_controller

        # Worker thread state
        self._cancel_token: Optional[CancelToken] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._current_process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()

        # Acquisition synchronization
        self._acquisition_complete = threading.Event()
        self._acquisition_success = False
        self._current_experiment_id: Optional[str] = None

        # Config loading synchronization
        self._config_load_complete = threading.Event()
        self._config_load_success = False
        self._config_load_error: Optional[str] = None

    # ========================================================================
    # StateMachine Implementation
    # ========================================================================

    def _publish_state_changed(
        self,
        old_state: WorkflowRunnerState,
        new_state: WorkflowRunnerState,
    ) -> None:
        """Publish state change event."""
        self._event_bus.publish(
            WorkflowRunnerStateChanged(
                old_state=old_state.name,
                new_state=new_state.name,
            )
        )

    # ========================================================================
    # Command Handlers
    # ========================================================================

    @handles(StartWorkflowCommand)
    def _on_start_command(self, cmd: StartWorkflowCommand) -> None:
        """Handle start workflow command."""
        self.start_workflow(cmd.workflow_dict)

    @handles(StopWorkflowCommand)
    def _on_stop_command(self, _cmd: StopWorkflowCommand) -> None:
        """Handle stop workflow command."""
        self.abort()

    @handles(PauseWorkflowCommand)
    def _on_pause_command(self, _cmd: PauseWorkflowCommand) -> None:
        """Handle pause workflow command."""
        self.pause()

    @handles(ResumeWorkflowCommand)
    def _on_resume_command(self, _cmd: ResumeWorkflowCommand) -> None:
        """Handle resume workflow command."""
        self.resume()

    @handles(AcquisitionFinished)
    def _on_acquisition_finished(self, event: AcquisitionFinished) -> None:
        """Handle acquisition completion - wake the waiting worker thread."""
        if not self._is_in_state(WorkflowRunnerState.RUNNING_ACQUISITION):
            return
        # Filter by experiment_id to prevent cross-talk with orchestrator
        if self._current_experiment_id is not None:
            if event.experiment_id != self._current_experiment_id:
                return
        self._acquisition_success = event.success
        self._acquisition_complete.set()

    @handles(WorkflowLoadConfigResponse)
    def _on_config_load_response(self, event: WorkflowLoadConfigResponse) -> None:
        """Handle config load response from UI."""
        self._config_load_success = event.success
        self._config_load_error = event.error_message
        self._config_load_complete.set()

    # ========================================================================
    # Public Control Methods
    # ========================================================================

    def start_workflow(self, workflow_dict: dict) -> bool:
        """Start executing a workflow.

        Args:
            workflow_dict: Serialized Workflow dict (from Workflow.to_dict())

        Returns:
            True if started successfully
        """
        # Allow restart from terminal states
        if self._is_in_state(
            WorkflowRunnerState.COMPLETED,
            WorkflowRunnerState.FAILED,
            WorkflowRunnerState.ABORTED,
        ):
            self._transition_to(WorkflowRunnerState.IDLE)

        if not self._is_in_state(WorkflowRunnerState.IDLE):
            _log.warning("Cannot start: workflow runner not idle")
            return False

        try:
            workflow = Workflow.from_dict(workflow_dict, ensure_acquisition=False)
        except Exception as e:
            _log.error(f"Invalid workflow data: {e}")
            self._event_bus.publish(WorkflowError(message=f"Invalid workflow: {e}"))
            return False

        self._cancel_token = CancelToken()

        self._worker_thread = threading.Thread(
            target=self._run_workflow,
            args=(workflow,),
            name="WorkflowRunner",
            daemon=True,
        )
        self._worker_thread.start()
        return True

    def pause(self) -> bool:
        """Pause the workflow after the current sequence completes."""
        if self._cancel_token is None:
            return False
        if self._is_in_state(
            WorkflowRunnerState.RUNNING_SCRIPT,
            WorkflowRunnerState.RUNNING_ACQUISITION,
        ):
            self._cancel_token.pause()
            self._transition_to(WorkflowRunnerState.PAUSED)
            return True
        return False

    def resume(self) -> bool:
        """Resume from pause."""
        if self._cancel_token is None:
            return False
        if self._is_in_state(WorkflowRunnerState.PAUSED):
            self._cancel_token.resume()
            # State transition happens in worker thread when it wakes up
            return True
        return False

    def abort(self) -> bool:
        """Abort the workflow, killing any running subprocess."""
        if self._cancel_token is None:
            return False
        if self._is_in_state(
            WorkflowRunnerState.IDLE,
            WorkflowRunnerState.COMPLETED,
            WorkflowRunnerState.FAILED,
            WorkflowRunnerState.ABORTED,
        ):
            return False

        self._cancel_token.cancel("User abort")
        # Kill subprocess if running
        with self._process_lock:
            if self._current_process is not None:
                self._current_process.terminate()
        # Wake acquisition wait if blocked
        self._acquisition_complete.set()
        return True

    # ========================================================================
    # Worker Thread
    # ========================================================================

    def _run_workflow(self, workflow: Workflow) -> None:
        """Execute the workflow (runs in background thread)."""
        success = True

        try:
            included_sequences = workflow.get_included_sequences()
            if not included_sequences:
                self._event_bus.publish(WorkflowError(message="No sequences to run"))
                self._transition_to(WorkflowRunnerState.FAILED)
                return

            num_cycles = workflow.num_cycles
            _log.info(f"Starting workflow with {len(included_sequences)} sequences, {num_cycles} cycle(s)")

            # Pick initial state based on first sequence type
            first_seq = included_sequences[0]
            if first_seq.is_acquisition():
                self._transition_to(WorkflowRunnerState.RUNNING_ACQUISITION)
            else:
                self._transition_to(WorkflowRunnerState.RUNNING_SCRIPT)

            for cycle in range(num_cycles):
                self._cancel_token.check_point()

                self._event_bus.publish(WorkflowCycleStarted(current_cycle=cycle, total_cycles=num_cycles))
                _log.info(f"Starting cycle {cycle + 1}/{num_cycles}")
                self._event_bus.publish(WorkflowScriptOutput(line=f"\n{'='*50}"))
                self._event_bus.publish(WorkflowScriptOutput(line=f"CYCLE {cycle + 1}/{num_cycles}"))
                self._event_bus.publish(WorkflowScriptOutput(line=f"{'='*50}"))

                for seq in included_sequences:
                    self._cancel_token.check_point()

                    # After resuming from pause, restore the running state
                    if self._is_in_state(WorkflowRunnerState.PAUSED):
                        if seq.is_acquisition():
                            self._transition_to(WorkflowRunnerState.RUNNING_ACQUISITION)
                        else:
                            self._transition_to(WorkflowRunnerState.RUNNING_SCRIPT)

                    # Find actual index in full sequence list for UI highlighting
                    seq_index = workflow.sequences.index(seq)
                    _log.info(f"Starting sequence: {seq.name}")
                    self._event_bus.publish(
                        WorkflowSequenceStarted(sequence_index=seq_index, sequence_name=seq.name)
                    )

                    # Transition to the appropriate running state
                    if seq.is_acquisition():
                        if not self._is_in_state(WorkflowRunnerState.RUNNING_ACQUISITION):
                            self._transition_to(WorkflowRunnerState.RUNNING_ACQUISITION)
                        seq_success = self._run_acquisition(cycle=cycle, config_path=seq.config_path)
                    else:
                        if not self._is_in_state(WorkflowRunnerState.RUNNING_SCRIPT):
                            self._transition_to(WorkflowRunnerState.RUNNING_SCRIPT)
                        cycle_value = None
                        if seq.cycle_arg_values:
                            values = seq.get_cycle_values()
                            if cycle < len(values):
                                cycle_value = values[cycle]
                        seq_success = self._run_script(seq, cycle_value)

                    self._event_bus.publish(
                        WorkflowSequenceFinished(
                            sequence_index=seq_index,
                            sequence_name=seq.name,
                            success=seq_success,
                        )
                    )

                    if not seq_success:
                        success = False
                        self._cancel_token.check_point()  # May raise if abort was requested

            self._transition_to(WorkflowRunnerState.COMPLETED if success else WorkflowRunnerState.FAILED)

        except CancellationError:
            _log.info("Workflow aborted by user")
            self._transition_to(WorkflowRunnerState.ABORTED)

        except Exception as e:
            _log.exception(f"Workflow error: {e}")
            self._event_bus.publish(WorkflowError(message=str(e)))
            self._transition_to(WorkflowRunnerState.FAILED)

        finally:
            self._cancel_token = None
            self._worker_thread = None

    def _run_acquisition(self, cycle: int = 0, config_path: Optional[str] = None) -> bool:
        """Request acquisition from MultiPointController and wait for completion.

        Args:
            cycle: Current cycle number (0-indexed).
            config_path: Optional path to acquisition.yaml file. If provided, the UI
                        will be asked to load settings from this file before starting
                        acquisition. If None or empty, uses current settings.
        """
        self._acquisition_complete.clear()
        self._acquisition_success = False

        try:
            # Load config from YAML if config_path is provided
            if config_path:
                _log.info(f"Requesting config load from: {config_path}")
                self._config_load_complete.clear()
                self._config_load_success = False
                self._config_load_error = None

                self._event_bus.publish(WorkflowLoadConfigRequest(config_path=config_path))

                # Wait for UI to respond
                while not self._config_load_complete.is_set():
                    self._cancel_token.check_point()
                    self._config_load_complete.wait(timeout=0.5)

                if not self._config_load_success:
                    error_msg = self._config_load_error or f"Failed to load config from '{config_path}'"
                    _log.error(error_msg)
                    self._event_bus.publish(WorkflowError(message=error_msg))
                    return False

            # Set experiment_ID on MultiPointController so _require_experiment_id() passes.
            # Uses direct assignment like ImagingExecutor (not start_new_experiment which
            # adds a timestamp and creates directories that may conflict with user setup).
            experiment_id = f"workflow_c{cycle:03d}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S.%f')}"
            self._multipoint.experiment_ID = experiment_id
            self._current_experiment_id = experiment_id
            _log.info(f"Starting acquisition with experiment_id={experiment_id}")

            started = self._multipoint.run_acquisition(acquire_current_fov=False)
            if not started:
                _log.error("run_acquisition() returned False — acquisition did not start")
                return False

            # Wait for AcquisitionFinished event
            while not self._acquisition_complete.is_set():
                self._cancel_token.check_point()
                self._acquisition_complete.wait(timeout=0.5)

            return self._acquisition_success

        finally:
            self._current_experiment_id = None

    def _run_script(self, seq, cycle_value) -> bool:
        """Execute a script sequence via subprocess."""
        try:
            cmd = seq.build_command(cycle_value)
            _log.info(f"Running command: {' '.join(cmd)}")
            self._event_bus.publish(WorkflowScriptOutput(line=f"$ {' '.join(cmd)}"))

            with self._process_lock:
                self._current_process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
                )

            # Stream output
            for line in self._current_process.stdout:
                line = line.rstrip()
                _log.debug(f"Script output: {line}")
                self._event_bus.publish(WorkflowScriptOutput(line=line))

            self._current_process.wait()
            return_code = self._current_process.returncode

            if return_code != 0:
                error_msg = f"Script '{seq.name}' failed with exit code {return_code}"
                _log.error(error_msg)
                self._event_bus.publish(WorkflowError(message=error_msg))
                return False

            _log.info(f"Script '{seq.name}' completed successfully")
            return True

        except FileNotFoundError as e:
            error_msg = f"Script '{seq.name}' failed: {e}"
            _log.error(error_msg)
            self._event_bus.publish(WorkflowError(message=error_msg))
            return False

        except Exception as e:
            error_msg = f"Script '{seq.name}' error: {e}"
            _log.exception(error_msg)
            self._event_bus.publish(WorkflowError(message=error_msg))
            return False

        finally:
            with self._process_lock:
                self._current_process = None
