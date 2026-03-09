"""
Experiment Orchestrator Controller.

Orchestrates multi-round fluidics-imaging experiments by coordinating
between the fluidics system, imaging system (via MultiPointController),
and operator interventions.

V2 Protocol Support:
    - Step-based rounds (FluidicsStep, ImagingStep, InterventionStep)
    - Named resources (fluidics_protocols, imaging_protocols, fov_sets)
    - Configurable error handling per failure type
"""

from datetime import datetime
import json
import os
import threading
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING

import squid.core.logging
from squid.core.events import EventBus, FocusLockPiezoLimitCritical, handles
from squid.core.state_machine import StateMachine
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import (
    ExperimentProtocol,
    FluidicsProtocolFile,
    ProtocolLoader,
    ImagingStep,
)

from squid.backend.controllers.orchestrator import protocol_helpers
from squid.backend.controllers.orchestrator.experiment_runner import ExperimentRunner
from squid.backend.controllers.orchestrator.run_logger import RunLogger
from squid.backend.controllers.orchestrator.state import (
    OrchestratorState,
    ORCHESTRATOR_TRANSITIONS,
    ExperimentProgress,
    Checkpoint,
    StepOutcome,
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorStepStarted,
    OrchestratorStepCompleted,
    OrchestratorError,
    StartOrchestratorCommand,
    StopOrchestratorCommand,
    PauseOrchestratorCommand,
    ResumeOrchestratorCommand,
    AcknowledgeInterventionCommand,
    ResolveInterventionCommand,
    SkipCurrentRoundCommand,
    SkipToRoundCommand,
    ClearWarningsCommand,
    SetWarningThresholdsCommand,
    AddWarningCommand,
    ValidateProtocolCommand,
    OrchestratorTimingSnapshot,
    ProtocolValidationStarted,
    ProtocolValidationComplete,
)  # fmt: skip
from squid.backend.controllers.orchestrator import checkpoint as ckpt
from squid.backend.controllers.orchestrator.warnings import (
    WarningCategory,
    WarningSeverity,
    WarningThresholds,
)
from squid.backend.controllers.orchestrator.warning_manager import WarningManager
from squid.backend.controllers.orchestrator.protocol_validator import ProtocolValidator

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController
    from squid.backend.controllers.multipoint.experiment_manager import ExperimentManager
    from squid.backend.controllers.multipoint.acquisition_planner import AcquisitionPlanner
    from squid.backend.managers.scan_coordinates import ScanCoordinates
    from squid.backend.controllers.orchestrator.imaging_executor import ImagingExecutor
    from squid.backend.controllers.fluidics_controller import FluidicsController
    from squid.core.config.repository import ConfigRepository

_log = squid.core.logging.get_logger(__name__)


class OrchestratorController(StateMachine[OrchestratorState]):
    """Orchestrates multi-round fluidics-imaging experiments.

    V2 Protocol Model:
        - Rounds contain ordered steps (fluidics, imaging, intervention)
        - Named resources (fluidics_protocols, imaging_protocols, fov_sets)
        - Configurable error handling per failure type

    State Machine (7 states):
        IDLE -> RUNNING <-> WAITING_INTERVENTION
                  |                 |
                  v                 v
               PAUSED          PAUSED
                  |
                  v
        COMPLETED / FAILED / ABORTED

    Current activity (fluidics/imaging) tracked via _current_operation field
    and published in OrchestratorProgress.current_operation.

    Usage:
        orchestrator = OrchestratorController(
            event_bus=event_bus,
            multipoint_controller=multipoint,
            experiment_manager=experiment_manager,
            acquisition_planner=planner,
            fluidics_controller=fluidics_controller,
        )

        # Start via command
        event_bus.publish(StartOrchestratorCommand(
            protocol_path="protocol.yaml",
            base_path="/data/experiments",
        ))

        # Or directly
        orchestrator.start_experiment(
            protocol_path="protocol.yaml",
            base_path="/data/experiments",
        )

        # Control
        orchestrator.pause()
        orchestrator.resume()
        orchestrator.abort()
    """

    def __init__(
        self,
        event_bus: EventBus,
        multipoint_controller: "MultiPointController",
        experiment_manager: "ExperimentManager",
        acquisition_planner: "AcquisitionPlanner",
        imaging_executor: Optional["ImagingExecutor"] = None,
        fluidics_controller: Optional["FluidicsController"] = None,
        scan_coordinates: Optional["ScanCoordinates"] = None,
        config_repo: Optional["ConfigRepository"] = None,
    ):
        """Initialize the orchestrator.

        Args:
            event_bus: EventBus for communication
            multipoint_controller: MultiPointController for imaging
            experiment_manager: ExperimentManager for folder/metadata
            acquisition_planner: AcquisitionPlanner for validation
            imaging_executor: Optional ImagingExecutor for imaging rounds
            fluidics_controller: Optional FluidicsController for fluidics protocols
            scan_coordinates: ScanCoordinates for imaging positions
            config_repo: Optional ConfigRepository for resolving stored imaging protocols
        """
        super().__init__(
            initial_state=OrchestratorState.IDLE,
            transitions=ORCHESTRATOR_TRANSITIONS,
            event_bus=event_bus,
            name="OrchestratorController",
        )

        self._event_bus = event_bus
        self._multipoint = multipoint_controller
        self._experiment_manager = experiment_manager
        self._planner = acquisition_planner
        self._imaging_executor = imaging_executor
        self._fluidics_controller = fluidics_controller
        self._scan_coordinates = scan_coordinates
        self._protocol_loader = ProtocolLoader(config_repo=config_repo)
        self._resume_checkpoint: Optional[Checkpoint] = None
        self._warning_manager = WarningManager(event_bus=event_bus)

        # Lock protecting shared mutable fields accessed from both
        # the worker thread and EventBus thread (progress, skip flags,
        # acquisition tracking, ETA).
        self._progress_lock = threading.RLock()

        # Current experiment state
        self._protocol: Optional[ExperimentProtocol] = None
        self._experiment_id: str = ""
        self._experiment_label: str = ""
        self._experiment_path: str = ""
        self._context: Optional[object] = None  # ExperimentContext from experiment_manager
        self._progress = ExperimentProgress()
        self._cancel_token: Optional[CancelToken] = None
        self._resume_state: Optional[OrchestratorState] = None
        self._current_operation: str = ""  # "initializing", "fluidics", "imaging", "intervention"
        self._protocol_path: Optional[str] = None

        # Start-from parameters (set before starting experiment)
        self._start_from_round: int = 0
        self._start_from_step: int = 0
        self._start_from_fov: int = 0
        self._run_single_round: bool = False

        # Step time estimates from validation (populated by _on_validate_protocol)
        self._step_time_estimates: Dict[Tuple[int, int], float] = {}
        self._total_estimated_seconds: float = 0.0

        # Worker thread and runner
        self._worker_thread: Optional[threading.Thread] = None
        self._runner: Optional[ExperimentRunner] = None
        self._intervention_resolved = threading.Event()
        self._intervention_action = "acknowledge"
        self._run_logger: Optional[RunLogger] = None
        self._timing_stop = threading.Event()
        self._timing_thread: Optional[threading.Thread] = None

    # ========================================================================
    # StateMachine Implementation
    # ========================================================================

    def _publish_state_changed(
        self,
        old_state: OrchestratorState,
        new_state: OrchestratorState,
    ) -> None:
        """Publish state change event."""
        self._event_bus.publish(
            OrchestratorStateChanged(
                old_state=old_state.name,
                new_state=new_state.name,
                experiment_id=self._experiment_id,
            )
        )

    # ========================================================================
    # Command Handlers
    # ========================================================================

    @handles(StartOrchestratorCommand)
    def _on_start_command(self, cmd: StartOrchestratorCommand) -> None:
        """Handle start command."""
        self.start_experiment(
            protocol_path=cmd.protocol_path,
            base_path=cmd.base_path,
            experiment_id=cmd.experiment_id,
            resume_from_checkpoint=cmd.resume_from_checkpoint,
            start_from_round=cmd.start_from_round,
            start_from_step=cmd.start_from_step,
            start_from_fov=cmd.start_from_fov,
            run_single_round=cmd.run_single_round,
        )

    @handles(StopOrchestratorCommand)
    def _on_stop_command(self, cmd: StopOrchestratorCommand) -> None:
        """Handle stop command."""
        self.abort()

    @handles(PauseOrchestratorCommand)
    def _on_pause_command(self, cmd: PauseOrchestratorCommand) -> None:
        """Handle pause command."""
        self.pause()

    @handles(ResumeOrchestratorCommand)
    def _on_resume_command(self, cmd: ResumeOrchestratorCommand) -> None:
        """Handle resume command."""
        self.resume()

    @handles(AcknowledgeInterventionCommand)
    def _on_acknowledge_intervention(self, cmd: AcknowledgeInterventionCommand) -> None:
        """Handle intervention acknowledgment."""
        del cmd
        self.acknowledge_intervention()

    @handles(ResolveInterventionCommand)
    def _on_resolve_intervention(self, cmd: ResolveInterventionCommand) -> None:
        """Handle explicit intervention action resolution."""
        self.resolve_intervention(cmd.action)

    @handles(SkipCurrentRoundCommand)
    def _on_skip_current_round(self, _cmd: SkipCurrentRoundCommand) -> None:
        """Handle skip current round command."""
        if not self.skip_current_round():
            _log.warning("Skip current round request ignored")

    @handles(SkipToRoundCommand)
    def _on_skip_to_round(self, cmd: SkipToRoundCommand) -> None:
        """Handle skip to round command."""
        if not self.skip_to_round(cmd.round_index):
            _log.warning(f"Skip-to-round request ignored: round_index={cmd.round_index}")

    @handles(ClearWarningsCommand)
    def _on_clear_warnings(self, cmd: ClearWarningsCommand) -> None:
        """Handle clear warnings command."""
        if (
            cmd.experiment_id
            and self._experiment_id
            and cmd.experiment_id != self._experiment_id
        ):
            _log.warning(
                "Ignoring ClearWarningsCommand for stale experiment_id "
                f"'{cmd.experiment_id}' (active='{self._experiment_id}')"
            )
            return
        self._warning_manager.clear(categories=cmd.categories)

    @handles(SetWarningThresholdsCommand)
    def _on_set_warning_thresholds(self, cmd: SetWarningThresholdsCommand) -> None:
        """Handle set warning thresholds command."""
        thresholds = WarningThresholds(
            pause_after_count=cmd.pause_after_count,
            pause_on_severity=(
                (WarningSeverity.CRITICAL,)
                if cmd.pause_on_critical
                else ()
            ) + (
                (WarningSeverity.HIGH,)
                if cmd.pause_on_high
                else ()
            ),
            max_stored_warnings=cmd.max_stored_warnings,
        )
        self._warning_manager.set_thresholds(thresholds)

    @handles(AddWarningCommand)
    def _on_add_warning(self, cmd: AddWarningCommand) -> None:
        """Handle warning command from other subsystems.

        For FOCUS category warnings, applies the protocol's focus_failure action
        if a protocol is loaded.
        """
        try:
            category = WarningCategory[cmd.category]
            severity = WarningSeverity[cmd.severity]
        except KeyError:
            _log.warning(
                f"Unknown warning category/severity: {cmd.category}/{cmd.severity}"
            )
            return

        should_pause = self._warning_manager.add_warning(
            category=category,
            severity=severity,
            message=cmd.message,
            round_index=cmd.round_index,
            round_name=cmd.round_name,
            time_point=cmd.time_point,
            operation_type=cmd.operation_type,
            operation_index=cmd.operation_index,
            fov_id=cmd.fov_id,
            fov_index=cmd.fov_index,
            context=cmd.context,
        )

        if should_pause and self._is_in_state(OrchestratorState.RUNNING):
            _log.warning(f"Warning threshold reached, pausing: {cmd.message}")
            self.pause()

    @handles(FocusLockPiezoLimitCritical)
    def _on_focus_lock_piezo_critical(self, event: FocusLockPiezoLimitCritical) -> None:
        """Handle critical piezo limit warning from focus lock.

        Feeds the warning into the WarningManager so that the orchestrator's
        existing focus-failure handling (pause/abort/skip/warn) applies.
        """
        self.add_warning(
            category=WarningCategory.FOCUS,
            severity=WarningSeverity.HIGH,
            message=(
                f"Focus lock piezo near {event.direction} limit: "
                f"{event.position_um:.1f} um (limit={event.limit_um:.1f}, "
                f"margin={event.margin_um:.1f})"
            ),
            context={
                "direction": event.direction,
                "position_um": event.position_um,
                "limit_um": event.limit_um,
            },
        )

    @handles(ValidateProtocolCommand)
    def _on_validate_protocol(self, cmd: ValidateProtocolCommand) -> None:
        """Handle validate protocol command.

        Validation runs synchronously without changing orchestrator state.
        """
        # Allow validation from IDLE or terminal states
        if not self._is_in_state(
            OrchestratorState.IDLE,
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
            OrchestratorState.ABORTED,
        ):
            _log.warning(f"Cannot validate protocol: orchestrator in state {self.state}")
            return

        self._event_bus.publish(ProtocolValidationStarted(protocol_path=cmd.protocol_path))

        try:
            # Always clear previous validation estimates first to avoid stale ETA
            self._step_time_estimates = {}
            self._total_estimated_seconds = 0.0

            # Load protocol
            protocol = self._protocol_loader.load(cmd.protocol_path)

            validator = self._build_protocol_validator(protocol=protocol)

            fov_count = self._current_fov_count(cmd.fov_count)

            # Validate
            summary = validator.validate(protocol, fov_count=fov_count)

            # Store step-level time estimates for ETA computation during execution
            if summary.valid:
                self._step_time_estimates = {}
                self._total_estimated_seconds = summary.total_estimated_seconds
                for op in summary.operation_estimates:
                    if op.step_index >= 0:
                        self._step_time_estimates[(op.round_index, op.step_index)] = (
                            op.estimated_seconds
                        )

            # Publish result
            self._event_bus.publish(
                ProtocolValidationComplete(
                    protocol_name=summary.protocol_name,
                    valid=summary.valid,
                    total_rounds=summary.total_rounds,
                    estimated_seconds=summary.total_estimated_seconds,
                    estimated_disk_bytes=summary.total_disk_bytes,
                    operation_estimates=summary.operation_estimates,
                    errors=summary.errors,
                    warnings=summary.warnings,
                )
            )

        except Exception as e:
            self._step_time_estimates = {}
            self._total_estimated_seconds = 0.0
            _log.exception(f"Protocol validation failed: {e}")
            self._event_bus.publish(
                ProtocolValidationComplete(
                    protocol_name="",
                    valid=False,
                    total_rounds=0,
                    estimated_seconds=0.0,
                    estimated_disk_bytes=0,
                    operation_estimates=(),
                    errors=(str(e),),
                    warnings=(),
                )
            )

    # ========================================================================
    # Public Control Methods
    # ========================================================================

    def start_experiment(
        self,
        protocol_path: str,
        base_path: str,
        experiment_id: Optional[str] = None,
        resume_from_checkpoint: bool = False,
        start_from_round: int = 0,
        start_from_step: int = 0,
        start_from_fov: int = 0,
        run_single_round: bool = False,
    ) -> bool:
        """Start a new orchestrated experiment.

        Args:
            protocol_path: Path to protocol YAML file
            base_path: Base directory for experiment data
            experiment_id: Optional experiment identifier
            resume_from_checkpoint: Resume from saved checkpoint
            start_from_round: 0-based round index to start from
            start_from_step: 0-based step index within first round
            start_from_fov: 0-based FOV index within first imaging step
            run_single_round: If True, execute only the start round

        Returns:
            True if started successfully
        """
        self._start_from_round = start_from_round
        self._start_from_step = start_from_step
        self._start_from_fov = start_from_fov
        self._run_single_round = run_single_round
        if not self._is_in_state(OrchestratorState.IDLE):
            if self._is_in_state(
                OrchestratorState.COMPLETED,
                OrchestratorState.FAILED,
                OrchestratorState.ABORTED,
            ):
                self._transition_to(OrchestratorState.IDLE)
                # FluidicsController resets abort state when run_protocol() is called
            else:
                _log.warning("Cannot start: orchestrator not idle")
                return False

        try:
            # Load protocol
            protocol = self._protocol_loader.load(protocol_path)
            total_rounds = len(protocol.rounds)
            if total_rounds == 0:
                _log.warning("Cannot start: protocol has no rounds")
                return False
            if not resume_from_checkpoint:
                if start_from_round < 0 or start_from_round >= total_rounds:
                    _log.warning(
                        "Cannot start: start_from_round out of bounds "
                        f"({start_from_round} not in [0, {total_rounds - 1}])"
                    )
                    return False
                steps_in_round = len(protocol.rounds[start_from_round].steps)
                if start_from_step < 0 or start_from_step >= steps_in_round:
                    _log.warning(
                        "Cannot start: start_from_step out of bounds "
                        f"({start_from_step} not in [0, {steps_in_round - 1}])"
                    )
                    return False
                if start_from_fov < 0:
                    _log.warning(
                        "Cannot start: start_from_fov out of bounds "
                        f"({start_from_fov} must be >= 0)"
                    )
                    return False

            self._protocol = protocol
            self._protocol_path = protocol_path
            _log.info(f"Loaded protocol: {self._protocol.name}")

            # Capture user-visible label (unique ID set once context is created)
            self._experiment_label = experiment_id or self._protocol.name
            self._experiment_id = self._experiment_label
            self._warning_manager.experiment_id = self._experiment_id
            self._warning_manager.clear()  # Clear warnings from any previous experiment
            self._intervention_resolved.clear()
            self._intervention_action = "acknowledge"

            # Load fluidics protocols from protocol into FluidicsController
            self._initialize_fluidics_protocols()

            # Auto-load resource files specified in the protocol
            self._auto_load_resources()

            if not resume_from_checkpoint and start_from_fov > 0:
                step = protocol.rounds[start_from_round].steps[start_from_step]
                if not isinstance(step, ImagingStep):
                    _log.warning(
                        "Cannot start: start_from_fov requires an imaging start step"
                    )
                    return False
                if self._scan_coordinates is not None:
                    region_fovs = getattr(self._scan_coordinates, "region_fov_coordinates", {})
                    known_fov_total = 0
                    if isinstance(region_fovs, dict):
                        known_fov_total = sum(len(coords) for coords in region_fovs.values())
                    if known_fov_total > 0 and start_from_fov >= known_fov_total:
                        _log.warning(
                            "Cannot start: start_from_fov out of bounds "
                            f"({start_from_fov} not in [0, {known_fov_total - 1}])"
                        )
                        return False

            # Run the same preflight used by the Validate flow.
            preflight = self._build_protocol_validator(protocol=self._protocol).validate(
                self._protocol,
                fov_count=self._current_fov_count(),
            )
            self._step_time_estimates = {}
            self._total_estimated_seconds = 0.0
            if not preflight.valid:
                for err in preflight.errors:
                    _log.warning(f"Preflight validation error: {err}")
                return False
            self._total_estimated_seconds = preflight.total_estimated_seconds
            for op in preflight.operation_estimates:
                if op.step_index >= 0:
                    self._step_time_estimates[(op.round_index, op.step_index)] = (
                        op.estimated_seconds
                    )

            # Load checkpoint if resuming (base_path should be experiment folder)
            self._resume_checkpoint = None
            if resume_from_checkpoint:
                self._resume_checkpoint = ckpt.load_checkpoint(base_path)
                if self._resume_checkpoint is None:
                    raise RuntimeError(f"No checkpoint found in {base_path}")
                if (
                    self._resume_checkpoint.protocol_name != self._protocol.name
                    or self._resume_checkpoint.protocol_version != self._protocol.version
                ):
                    raise RuntimeError(
                        "Checkpoint protocol mismatch: "
                        f"checkpoint={self._resume_checkpoint.protocol_name}@{self._resume_checkpoint.protocol_version}, "
                        f"loaded={self._protocol.name}@{self._protocol.version}"
                    )

            # Create cancel token
            self._cancel_token = CancelToken()

            # Transition to running
            self._current_operation = "initializing"
            self._transition_to(OrchestratorState.RUNNING)

            # Initialize progress
            self._progress = ExperimentProgress(
                total_rounds=len(self._protocol.rounds),
                started_at=datetime.now(),
            )

            # Start worker thread
            self._worker_thread = threading.Thread(
                target=self._run_experiment,
                args=(base_path,),
                name="OrchestratorWorker",
                daemon=True,
            )
            self._worker_thread.start()

            return True

        except Exception as e:
            _log.exception(f"Failed to start experiment: {e}")
            self._publish_error("initialization", str(e))
            self._force_state(OrchestratorState.FAILED, str(e))
            return False

    def pause(self) -> bool:
        """Pause the experiment.

        Pauses executors first (idempotent), then atomically captures
        pre-pause state and transitions to PAUSED under a single lock hold
        to avoid race conditions.
        """
        if self._cancel_token is None:
            return False

        # Pause executors first (idempotent — safe even if state check fails)
        if self._imaging_executor is not None:
            self._imaging_executor.pause()
        if self._fluidics_controller is not None:
            self._fluidics_controller.pause()
        self._cancel_token.pause()
        if self._runner is not None:
            self._runner.notify_pause()

        # Atomic: capture pre-state + transition under single lock hold
        with self._lock:
            pre_pause_state = self._state
            if pre_pause_state not in (
                OrchestratorState.RUNNING,
                OrchestratorState.WAITING_INTERVENTION,
            ):
                return False
            valid_targets = self._transitions.get(pre_pause_state, frozenset())
            if OrchestratorState.PAUSED not in valid_targets:
                return False
            self._resume_state = pre_pause_state
            old_state = self._state
            self._state = OrchestratorState.PAUSED

        self._fire_state_change(old_state, OrchestratorState.PAUSED)
        self._save_checkpoint()
        return True

    def resume(self) -> bool:
        """Resume from pause.

        Uses _try_transition_to for atomic check-and-transition.
        """
        if self._cancel_token is None:
            return False

        if not self._is_in_state(OrchestratorState.PAUSED):
            return False

        if self._imaging_executor is not None:
            self._imaging_executor.resume()
        if self._fluidics_controller is not None:
            self._fluidics_controller.resume()
        if self._runner is not None:
            self._runner.notify_resume()
        self._cancel_token.resume()

        # Resume to previous state (RUNNING or WAITING_INTERVENTION)
        resume_target = self._resume_state or OrchestratorState.RUNNING
        if not self._try_transition_to(resume_target):
            # Fallback: go to RUNNING
            self._transition_to(OrchestratorState.RUNNING)
        self._resume_state = None
        return True

    def abort(self) -> bool:
        """Abort the experiment.

        Signals cancellation; actual state transition happens in worker thread.
        """
        if self._cancel_token is None:
            return False

        if self._is_in_state(
            OrchestratorState.IDLE,
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
            OrchestratorState.ABORTED,
        ):
            return False

        self._cancel_token.cancel("User abort")

        # Stop imaging executor to interrupt any running acquisition
        if self._imaging_executor is not None:
            self._imaging_executor.abort()

        # Stop fluidics controller to interrupt any running protocol
        if self._fluidics_controller is not None:
            self._fluidics_controller.stop()

        # State transition happens in worker thread
        return True

    @property
    def warning_manager(self) -> WarningManager:
        """Get the warning manager for this orchestrator."""
        return self._warning_manager

    def add_warning(
        self,
        category: WarningCategory,
        severity: WarningSeverity,
        message: str,
        *,
        fov_id: Optional[str] = None,
        fov_index: Optional[int] = None,
        time_point: Optional[int] = None,
        context: Optional[dict] = None,
    ) -> bool:
        """Add a warning during acquisition.

        Args:
            category: Warning category (FOCUS, HARDWARE, etc.)
            severity: Warning severity (LOW, MEDIUM, HIGH, CRITICAL)
            message: Human-readable warning message
            fov_id: Optional FOV identifier
            context: Optional context dictionary

        Returns:
            True if a warning threshold was reached (should pause)
        """
        with self._progress_lock:
            round_index = self._progress.current_round_index
            round_name = ""
            operation_type = ""
            if self._progress.current_round:
                round_name = self._progress.current_round.round_name
                operation_type = self._current_operation

        should_pause = self._warning_manager.add_warning(
            category=category,
            severity=severity,
            message=message,
            round_index=round_index,
            round_name=round_name,
            time_point=time_point or 0,
            operation_type=operation_type,
            fov_id=fov_id,
            fov_index=fov_index,
            context=context,
        )

        # If threshold reached, transition to PAUSED
        if should_pause and self._is_in_state(OrchestratorState.RUNNING):
            _log.warning(f"Warning threshold reached, pausing: {message}")
            self.pause()

        return should_pause

    def acknowledge_intervention(self) -> bool:
        """Acknowledge intervention and continue."""
        return self.resolve_intervention("acknowledge")

    def resolve_intervention(self, action: str) -> bool:
        """Resolve an intervention with a fixed operator action."""
        if not self._is_in_state(OrchestratorState.WAITING_INTERVENTION):
            return False
        self._intervention_action = action
        self._intervention_resolved.set()
        return True

    def skip_current_round(self) -> bool:
        """Skip to the next round (takes effect after current round finishes)."""
        runner = self._runner
        if runner is None:
            return False
        if not self._is_in_state(
            OrchestratorState.RUNNING,
            OrchestratorState.PAUSED,
            OrchestratorState.WAITING_INTERVENTION,
        ):
            return False
        return runner.request_skip_current_round()

    def skip_to_round(self, round_index: int) -> bool:
        """Skip ahead to a specific round index (0-based)."""
        runner = self._runner
        if runner is None:
            return False
        if not self._is_in_state(
            OrchestratorState.RUNNING,
            OrchestratorState.PAUSED,
            OrchestratorState.WAITING_INTERVENTION,
        ):
            return False
        return runner.request_skip_to_round(round_index)

    def _set_operation(self, operation: str) -> None:
        """Set current operation (called by ExperimentRunner)."""
        self._current_operation = operation

    def _consume_intervention_action(self) -> str:
        """Consume the most recent intervention action and reset to acknowledge."""
        action = self._intervention_action
        self._intervention_action = "acknowledge"
        return action

    def _current_fov_count(self, requested_fov_count: int = 0) -> int:
        """Resolve the effective FOV count for validation and ETA."""
        if requested_fov_count > 0:
            return requested_fov_count
        if self._scan_coordinates is not None:
            region_fovs = getattr(self._scan_coordinates, "region_fov_coordinates", {})
            if isinstance(region_fovs, dict):
                total = sum(len(coords) for coords in region_fovs.values())
                if total > 0:
                    return total
        return 1

    def _build_protocol_validator(
        self,
        *,
        protocol: Optional[ExperimentProtocol] = None,
        available_fluidics: Optional[set[str]] = None,
        available_channels: Optional[set[str]] = None,
        fluidics_duration_lookup: Optional[Callable[[str], Optional[float]]] = None,
    ) -> ProtocolValidator:
        """Build the shared validator used by both Validate and Start."""
        if protocol is None:
            protocol = self._protocol
        file_protocols: Optional[FluidicsProtocolFile] = None
        if available_fluidics is None:
            available_fluidics = set()
            if self._fluidics_controller is not None:
                available_fluidics.update(self._fluidics_controller.list_protocols())
            if protocol is not None and protocol.fluidics_protocols_file:
                file_protocols = FluidicsProtocolFile.load_from_yaml(
                    protocol.fluidics_protocols_file
                )
                available_fluidics.update(file_protocols.list_protocols())
        if available_channels is None:
            raw_channels = self._planner.get_available_channel_names()
            if isinstance(raw_channels, (list, tuple, set, frozenset)):
                available_channels = set(raw_channels)
        if fluidics_duration_lookup is None:
            controller_lookup = None
            if self._fluidics_controller is not None:
                controller_lookup = self._fluidics_controller.estimate_protocol_duration

            if file_protocols is None and protocol is not None and protocol.fluidics_protocols_file:
                file_protocols = FluidicsProtocolFile.load_from_yaml(
                    protocol.fluidics_protocols_file
                )

            def _lookup(protocol_name: str) -> Optional[float]:
                if controller_lookup is not None:
                    duration = controller_lookup(protocol_name)
                    if duration is not None:
                        return duration
                if file_protocols is None:
                    return None
                protocol_definition = file_protocols.get_protocol(protocol_name)
                if protocol_definition is None:
                    return None
                return protocol_definition.estimated_duration_s()

            fluidics_duration_lookup = _lookup

        return ProtocolValidator(
            available_fluidics_protocols=available_fluidics,
            available_channels=available_channels,
            fluidics_duration_lookup=fluidics_duration_lookup,
        )

    def _write_experiment_metadata_file(self) -> None:
        """Persist the canonical protocol snapshot and runtime context to disk."""
        if not self._experiment_path or self._protocol is None:
            return
        metadata_path = os.path.join(self._experiment_path, "experiment_metadata.json")
        payload = {
            "experiment_id": self._experiment_id,
            "protocol_path": self._protocol_path,
            "protocol": self._protocol.model_dump(mode="json", exclude_none=True),
            "written_at": datetime.now().isoformat(),
        }
        with open(metadata_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def _start_timing_publisher(self) -> None:
        """Publish continuously updated timing/progress snapshots during a run."""
        self._timing_stop.clear()

        def _worker() -> None:
            while not self._timing_stop.wait(0.5):
                runner = self._runner
                if runner is None:
                    continue
                if self._is_in_state(
                    OrchestratorState.RUNNING,
                    OrchestratorState.WAITING_INTERVENTION,
                    OrchestratorState.PAUSED,
                ):
                    self._publish_progress()

        self._timing_thread = threading.Thread(
            target=_worker,
            name="OrchestratorTimingPublisher",
            daemon=True,
        )
        self._timing_thread.start()

    def _stop_timing_publisher(self) -> None:
        """Stop the background timing publisher."""
        self._timing_stop.set()
        if self._timing_thread is not None:
            self._timing_thread.join(timeout=1.0)
        self._timing_thread = None

    # ========================================================================
    # Protocol Initialization
    # ========================================================================

    def _initialize_fluidics_protocols(self) -> None:
        """Load protocol's fluidics_protocols into FluidicsController."""
        if self._protocol is None:
            return
        if self._protocol.fluidics_protocols:
            raise RuntimeError(
                "Inline fluidics_protocols are not allowed. "
                "Load protocols into FluidicsController separately and reference by name."
            )

    def _auto_load_resources(self) -> None:
        """Auto-load resource files specified in the protocol.

        Loads fluidics protocols, FOV positions, and validates fluidics config
        when paths are provided in the protocol YAML.
        """
        if self._protocol is None:
            return

        # Load fluidics protocols file into FluidicsController
        if self._protocol.fluidics_protocols_file:
            if self._fluidics_controller is None:
                _log.warning(
                    "Protocol specifies fluidics_protocols_file but no FluidicsController is available"
                )
            else:
                path = self._protocol.fluidics_protocols_file
                count = self._fluidics_controller.load_protocols(path)
                _log.info(f"Auto-loaded {count} fluidics protocols from {path}")

        # Validate fluidics config file exists (stored for app-layer use)
        if self._protocol.fluidics_config_file:
            from pathlib import Path

            config_path = Path(self._protocol.fluidics_config_file)
            if not config_path.exists():
                raise FileNotFoundError(
                    f"Fluidics config file not found: {config_path}"
                )
            _log.info(f"Fluidics config file validated: {config_path}")

        # Load FOV file into scan coordinates
        if self._protocol.fov_file:
            protocol_helpers.load_fov_set(
                csv_path=self._protocol.fov_file,
                scan_coordinates=self._scan_coordinates,
                event_bus=self._event_bus,
            )

    # ========================================================================
    # Worker Thread
    # ========================================================================

    def _run_experiment(self, base_path: str) -> None:
        """Main experiment execution loop (runs in worker thread)."""
        try:
            if self._protocol is None:
                raise RuntimeError("No protocol loaded")

            if self._resume_checkpoint is not None:
                from squid.backend.controllers.multipoint.experiment_manager import ExperimentContext

                checkpoint = self._resume_checkpoint
                self._experiment_id = checkpoint.experiment_id
                self._warning_manager.experiment_id = self._experiment_id
                self._experiment_path = checkpoint.experiment_path
                self._context = ExperimentContext(
                    experiment_id=checkpoint.experiment_id,
                    base_path=os.path.dirname(checkpoint.experiment_path),
                    experiment_path=checkpoint.experiment_path,
                )
            else:
                # Create experiment folder
                configurations = protocol_helpers.collect_experiment_configurations(
                    self._protocol, self._multipoint,
                )
                self._context = self._experiment_manager.start_experiment(
                    base_path=base_path,
                    experiment_id=self._experiment_label,
                    configurations=configurations,
                    acquisition_params={
                        "protocol": self._protocol.name,
                        "experiment_label": self._experiment_label,
                    },
                )
                self._experiment_id = self._context.experiment_id
                self._warning_manager.experiment_id = self._experiment_id
                self._experiment_path = self._context.experiment_path
                if hasattr(self._experiment_manager, "write_experiment_metadata"):
                    metadata = protocol_helpers.build_experiment_metadata(
                        self._protocol, self._protocol_path,
                    )
                    if metadata:
                        self._experiment_manager.write_experiment_metadata(
                            self._context,
                            metadata,
                        )
            self._write_experiment_metadata_file()
            self._run_logger = RunLogger(self._event_bus, self._experiment_path)
            self._run_logger.start()
            self._start_timing_publisher()

            # Create runner and execute
            runner = ExperimentRunner(
                protocol=self._protocol,
                experiment_path=self._experiment_path,
                experiment_id=self._experiment_id,
                cancel_token=self._cancel_token,
                event_bus=self._event_bus,
                progress=self._progress,
                progress_lock=self._progress_lock,
                imaging_executor=self._imaging_executor,
                fluidics_controller=self._fluidics_controller,
                scan_coordinates=self._scan_coordinates,
                experiment_manager=self._experiment_manager,
                experiment_context=self._context,
                protocol_path=self._protocol_path,
                on_operation_change=self._set_operation,
                on_progress=self._publish_progress,
                on_checkpoint=self._save_checkpoint,
                on_round_started=self._publish_round_started,
                on_round_completed=self._publish_round_completed,
                on_transition=self._transition_to,
                on_pause=self.pause,
                on_add_warning=self.add_warning,
                intervention_resolved=self._intervention_resolved,
                consume_intervention_action=self._consume_intervention_action,
                start_from_round=self._start_from_round,
                start_from_step=self._start_from_step,
                start_from_fov=self._start_from_fov,
                run_single_round=self._run_single_round,
                step_time_estimates=self._step_time_estimates,
                total_estimated_seconds=self._total_estimated_seconds,
            )
            self._runner = runner
            result = runner.run(resume_checkpoint=self._resume_checkpoint)

            if result.outcome == StepOutcome.SUCCESS:
                self._transition_to(OrchestratorState.COMPLETED)
                self._experiment_manager.finalize_experiment(self._context, success=True)
                ckpt.clear_checkpoint(self._experiment_path)

        except CancellationError:
            _log.info("Experiment aborted by user")
            self._transition_to(OrchestratorState.ABORTED)
            if self._context is not None:
                self._experiment_manager.finalize_experiment(self._context, success=False)

        except Exception as e:
            _log.exception(f"Experiment failed: {e}")
            self._publish_error("execution", str(e))
            self._transition_to(OrchestratorState.FAILED)
            if self._context is not None:
                self._experiment_manager.finalize_experiment(self._context, success=False)

        finally:
            self._stop_timing_publisher()
            if self._run_logger is not None:
                self._run_logger.stop()
                self._run_logger = None
            self._runner = None
            self._cancel_token = None
            self._context = None
            self._worker_thread = None
            self._resume_checkpoint = None

    # ========================================================================
    # Progress and Checkpoint
    # ========================================================================

    def _save_checkpoint(self) -> None:
        """Save current state to checkpoint."""
        if not self._experiment_path or self._protocol is None:
            return

        with self._progress_lock:
            if self._progress.current_round is None:
                return

            checkpoint = ckpt.create_checkpoint(
                protocol_name=self._protocol.name,
                protocol_version=self._protocol.version,
                experiment_id=self._experiment_id,
                experiment_path=self._experiment_path,
                round_index=self._progress.current_round_index,
                step_index=self._progress.current_step_index,
                imaging_fov_index=max(self._progress.current_round.imaging_fov_index, 0),
                current_attempt=max(self._progress.current_attempt, 1),
                elapsed_seconds=self._progress.elapsed_seconds,
                paused_seconds=self._progress.paused_seconds,
                effective_run_seconds=self._progress.effective_run_seconds,
            )

        ckpt.save_checkpoint(checkpoint, self._experiment_path)

    def _publish_progress(self) -> None:
        """Publish progress event."""
        runner = self._runner
        timing = runner.get_timing_snapshot() if runner is not None else {
            "elapsed_seconds": self._progress.elapsed_seconds,
            "effective_run_seconds": self._progress.effective_run_seconds,
            "paused_seconds": self._progress.paused_seconds,
            "retry_overhead_seconds": self._progress.retry_overhead_seconds,
            "intervention_overhead_seconds": self._progress.intervention_overhead_seconds,
            "eta_seconds": None,
            "subsystem_seconds": dict(self._progress.subsystem_seconds),
        }

        with self._progress_lock:
            current_round_name = ""
            total_steps = 0
            current_fov_index = 0
            total_fovs = 0
            if self._progress.current_round is not None:
                current_round_name = self._progress.current_round.round_name
                total_steps = self._progress.current_round.total_steps
                current_fov_index = self._progress.current_round.imaging_fov_index
                total_fovs = self._progress.current_round.total_imaging_fovs
            total_rounds = self._progress.total_rounds
            if total_rounds <= 0:
                current_round = 0
            else:
                current_round = min(self._progress.current_round_index + 1, total_rounds)

            self._progress.elapsed_seconds = float(timing["elapsed_seconds"])
            self._progress.effective_run_seconds = float(timing["effective_run_seconds"])
            self._progress.paused_seconds = float(timing["paused_seconds"])
            self._progress.retry_overhead_seconds = float(timing["retry_overhead_seconds"])
            self._progress.intervention_overhead_seconds = float(timing["intervention_overhead_seconds"])
            self._progress.subsystem_seconds = dict(timing["subsystem_seconds"])

            event = OrchestratorProgress(
                experiment_id=self._experiment_id,
                current_round=current_round,
                total_rounds=total_rounds,
                current_round_name=current_round_name,
                progress_percent=self._progress.progress_percent,
                eta_seconds=timing["eta_seconds"],
                current_operation=self._current_operation,
                current_step_name=self._progress.current_step_name,
                current_step_index=self._progress.current_step_index,
                total_steps=total_steps,
                current_fov_label=self._progress.current_fov_label,
                current_fov_index=current_fov_index,
                total_fovs=total_fovs,
                attempt=self._progress.current_attempt,
                elapsed_seconds=self._progress.elapsed_seconds,
                effective_run_seconds=self._progress.effective_run_seconds,
                paused_seconds=self._progress.paused_seconds,
                retry_overhead_seconds=self._progress.retry_overhead_seconds,
                intervention_overhead_seconds=self._progress.intervention_overhead_seconds,
            )

        self._event_bus.publish(event)
        self._event_bus.publish(
            OrchestratorTimingSnapshot(
                experiment_id=self._experiment_id,
                elapsed_seconds=float(timing["elapsed_seconds"]),
                effective_run_seconds=float(timing["effective_run_seconds"]),
                paused_seconds=float(timing["paused_seconds"]),
                retry_overhead_seconds=float(timing["retry_overhead_seconds"]),
                intervention_overhead_seconds=float(timing["intervention_overhead_seconds"]),
                eta_seconds=timing["eta_seconds"],
                subsystem_seconds=dict(timing["subsystem_seconds"]),
            )
        )

    def _publish_round_started(self, round_idx: int, name: str) -> None:
        """Publish round started event."""
        self._event_bus.publish(
            OrchestratorRoundStarted(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                round_name=name,
                round_type="step_based",  # V2 rounds are step-based
            )
        )

    def _publish_round_completed(
        self,
        round_idx: int,
        name: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Publish round completed event."""
        self._event_bus.publish(
            OrchestratorRoundCompleted(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                round_name=name,
                success=success,
                error=error,
            )
        )

    def _publish_error(self, error_type: str, message: str) -> None:
        """Publish error event."""
        self._event_bus.publish(
            OrchestratorError(
                experiment_id=self._experiment_id,
                error_type=error_type,
                message=message,
            )
        )

    # ========================================================================
    # Properties
    # ========================================================================

    @property
    def experiment_id(self) -> str:
        """Get current experiment ID."""
        return self._experiment_id

    @property
    def protocol(self) -> Optional[ExperimentProtocol]:
        """Get loaded protocol."""
        return self._protocol

    @property
    def progress(self) -> ExperimentProgress:
        """Get current progress."""
        return self._progress

    @property
    def is_running(self) -> bool:
        """Check if experiment is running."""
        return not self._is_in_state(
            OrchestratorState.IDLE,
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
            OrchestratorState.ABORTED,
        )
