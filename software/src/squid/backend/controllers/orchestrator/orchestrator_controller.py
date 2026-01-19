"""
Experiment Orchestrator Controller.

Orchestrates multi-round fluidics-imaging experiments by coordinating
between the fluidics system, imaging system (via MultiPointController),
and operator interventions.

V2 Protocol Support:
    - Step-based rounds (FluidicsStep, ImagingStep, InterventionStep)
    - Named resources (fluidics_protocols, imaging_configs, fov_sets)
    - Configurable error handling per failure type
"""

from datetime import datetime
import os
import threading
from typing import Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.events import EventBus, handles, AcquisitionProgress, LoadScanCoordinatesCommand
from squid.core.state_machine import StateMachine
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import (
    ExperimentProtocol,
    ProtocolLoader,
    Round,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
    FailureAction,
)

from squid.backend.controllers.orchestrator.state import (
    OrchestratorState,
    ORCHESTRATOR_TRANSITIONS,
    ExperimentProgress,
    RoundProgress,
    Checkpoint,
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorInterventionRequired,
    OrchestratorError,
    StartOrchestratorCommand,
    StopOrchestratorCommand,
    PauseOrchestratorCommand,
    ResumeOrchestratorCommand,
    AcknowledgeInterventionCommand,
    SkipCurrentRoundCommand,
    SkipToRoundCommand,
    ClearWarningsCommand,
    SetWarningThresholdsCommand,
    AddWarningCommand,
    ValidateProtocolCommand,
    ProtocolValidationStarted,
    ProtocolValidationComplete,
)
from squid.backend.controllers.orchestrator.checkpoint import CheckpointManager
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

_log = squid.core.logging.get_logger(__name__)


class OrchestratorController(StateMachine[OrchestratorState]):
    """Orchestrates multi-round fluidics-imaging experiments.

    V2 Protocol Model:
        - Rounds contain ordered steps (fluidics, imaging, intervention)
        - Named resources (fluidics_protocols, imaging_configs, fov_sets)
        - Configurable error handling per failure type

    State Machine:
        IDLE -> INITIALIZING -> RUNNING_FLUIDICS <-> RUNNING_IMAGING
                                    |                    |
                                    v                    v
                         WAITING_INTERVENTION <---+------+
                                    |
                                    v
                                 PAUSED
                                    |
                                    v
                               RECOVERING
                                    |
                                    v
                    COMPLETED / FAILED / ABORTED

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
        self._checkpoint_manager = CheckpointManager()
        self._protocol_loader = ProtocolLoader()
        self._resume_checkpoint: Optional[Checkpoint] = None
        self._warning_manager = WarningManager(event_bus=event_bus)

        # Current experiment state
        self._protocol: Optional[ExperimentProtocol] = None
        self._experiment_id: str = ""
        self._experiment_label: str = ""
        self._experiment_path: str = ""
        self._context: Optional[object] = None  # ExperimentContext from experiment_manager
        self._progress = ExperimentProgress()
        self._cancel_token: Optional[CancelToken] = None
        self._resume_state: Optional[OrchestratorState] = None
        self._current_acquisition_id: Optional[str] = None
        self._latest_eta_seconds: Optional[float] = None
        self._skip_to_round_index: Optional[int] = None

        # Worker thread
        self._worker_thread: Optional[threading.Thread] = None
        self._intervention_acknowledged = threading.Event()

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
        self.acknowledge_intervention()

    @handles(SkipCurrentRoundCommand)
    def _on_skip_current_round(self, _cmd: SkipCurrentRoundCommand) -> None:
        """Handle skip current round command."""
        self.skip_current_round()

    @handles(SkipToRoundCommand)
    def _on_skip_to_round(self, cmd: SkipToRoundCommand) -> None:
        """Handle skip to round command."""
        self.skip_to_round(cmd.round_index)

    @handles(ClearWarningsCommand)
    def _on_clear_warnings(self, cmd: ClearWarningsCommand) -> None:
        """Handle clear warnings command."""
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

        # Apply focus_failure handling from protocol if this is a focus warning
        if category == WarningCategory.FOCUS and self._protocol is not None:
            focus_action = self._protocol.error_handling.focus_failure
            if focus_action == FailureAction.PAUSE:
                if self._is_in_state(OrchestratorState.RUNNING_IMAGING):
                    _log.warning(f"Focus failure (action=pause): {cmd.message}")
                    self.pause()
                    return
            elif focus_action == FailureAction.ABORT:
                if self._is_in_state(OrchestratorState.RUNNING_IMAGING):
                    _log.error(f"Focus failure (action=abort): {cmd.message}")
                    self.abort()
                    return
            # For SKIP and WARN, just continue (warning already logged)

        if should_pause and self._is_in_state(
            OrchestratorState.RUNNING_IMAGING,
            OrchestratorState.RUNNING_FLUIDICS,
        ):
            _log.warning(f"Warning threshold reached, pausing: {cmd.message}")
            self.pause()

    @handles(ValidateProtocolCommand)
    def _on_validate_protocol(self, cmd: ValidateProtocolCommand) -> None:
        """Handle validate protocol command."""
        if self.state != OrchestratorState.IDLE:
            _log.warning("Cannot validate protocol: orchestrator not idle")
            return

        self._transition_to(OrchestratorState.VALIDATING)
        self._event_bus.publish(ProtocolValidationStarted(protocol_path=cmd.protocol_path))

        try:
            # Load protocol
            protocol = self._protocol_loader.load(cmd.protocol_path)

            # Create validator with available fluidics protocols
            # (Include both already-loaded protocols AND those defined in the protocol)
            available_fluidics = set(protocol.fluidics_protocols.keys())
            if self._fluidics_controller is not None:
                available_fluidics.update(self._fluidics_controller.list_protocols())
            validator = ProtocolValidator(
                available_fluidics_protocols=available_fluidics,
            )

            # Get FOV count from scan coordinates if available
            fov_count = 1
            if self._scan_coordinates is not None:
                fov_count = sum(
                    len(coords)
                    for coords in self._scan_coordinates.region_fov_coordinates.values()
                )

            # Validate
            summary = validator.validate(protocol, fov_count=fov_count)

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
        finally:
            if self.state == OrchestratorState.VALIDATING:
                self._transition_to(OrchestratorState.IDLE)

    @handles(AcquisitionProgress)
    def _on_acquisition_progress(self, event: AcquisitionProgress) -> None:
        """Track imaging progress for the active round."""
        if self._current_acquisition_id is None:
            return
        if event.experiment_id != self._current_acquisition_id:
            return
        if self._progress.current_round is None:
            return

        self._progress.current_round.imaging_fov_index = event.current_fov
        self._progress.current_round.total_imaging_fovs = event.total_fovs
        self._latest_eta_seconds = event.eta_seconds
        self._publish_progress()

    # ========================================================================
    # Public Control Methods
    # ========================================================================

    def start_experiment(
        self,
        protocol_path: str,
        base_path: str,
        experiment_id: Optional[str] = None,
        resume_from_checkpoint: bool = False,
    ) -> bool:
        """Start a new orchestrated experiment.

        Args:
            protocol_path: Path to protocol YAML file
            base_path: Base directory for experiment data
            experiment_id: Optional experiment identifier

        Returns:
            True if started successfully
        """
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

        if not self._is_in_state(OrchestratorState.IDLE):
            _log.warning("Cannot start: orchestrator not idle")
            return False

        try:
            # Load protocol
            self._protocol = self._protocol_loader.load(protocol_path)
            _log.info(f"Loaded protocol: {self._protocol.name}")

            # Capture user-visible label (unique ID set once context is created)
            self._experiment_label = experiment_id or self._protocol.name
            self._experiment_id = self._experiment_label
            self._warning_manager.experiment_id = self._experiment_id
            self._warning_manager.clear()  # Clear warnings from any previous experiment

            # Load fluidics protocols from protocol into FluidicsController
            self._initialize_fluidics_protocols()

            # Load checkpoint if resuming (base_path should be experiment folder)
            self._resume_checkpoint = None
            self._skip_to_round_index = None
            if resume_from_checkpoint:
                self._resume_checkpoint = self._checkpoint_manager.load(base_path)
                if self._resume_checkpoint is None:
                    raise RuntimeError(f"No checkpoint found in {base_path}")

            # Create cancel token
            self._cancel_token = CancelToken()

            # Transition to initializing
            self._transition_to(OrchestratorState.INITIALIZING)

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
        """Pause the experiment."""
        if self._cancel_token is None:
            return False

        if self._is_in_state(
            OrchestratorState.RUNNING_FLUIDICS,
            OrchestratorState.RUNNING_IMAGING,
            OrchestratorState.WAITING_INTERVENTION,
        ):
            self._resume_state = self.state
            if (
                self._is_in_state(OrchestratorState.RUNNING_IMAGING)
                and self._imaging_executor is not None
            ):
                self._imaging_executor.pause()
            self._cancel_token.pause()
            self._transition_to(OrchestratorState.PAUSED)
            self._save_checkpoint()
            return True

        return False

    def resume(self) -> bool:
        """Resume from pause."""
        if self._cancel_token is None:
            return False

        if self._is_in_state(OrchestratorState.PAUSED):
            if self._imaging_executor is not None:
                self._imaging_executor.resume()
            self._cancel_token.resume()

            # FluidicsController will reset abort state when next protocol is run

            if self._resume_state in (
                OrchestratorState.RUNNING_FLUIDICS,
                OrchestratorState.RUNNING_IMAGING,
                OrchestratorState.WAITING_INTERVENTION,
            ):
                self._transition_to(self._resume_state)
            else:
                self._transition_to(OrchestratorState.RECOVERING)
            self._resume_state = None
            return True

        return False

    def abort(self) -> bool:
        """Abort the experiment."""
        if self._cancel_token is None:
            return False

        if not self._is_in_state(
            OrchestratorState.IDLE,
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
            OrchestratorState.ABORTED,
        ):
            self._cancel_token.cancel("User abort")

            # Stop fluidics controller to interrupt any running protocol
            if self._fluidics_controller is not None:
                self._fluidics_controller.stop()

            # State transition happens in worker thread
            return True

        return False

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
        round_index = self._progress.current_round_index
        round_name = ""
        operation_type = ""
        if self._progress.current_round:
            round_name = self._progress.current_round.round_name
            if self._is_in_state(OrchestratorState.RUNNING_IMAGING):
                operation_type = "imaging"
            elif self._is_in_state(OrchestratorState.RUNNING_FLUIDICS):
                operation_type = "fluidics"

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
        if should_pause and self._is_in_state(
            OrchestratorState.RUNNING_IMAGING,
            OrchestratorState.RUNNING_FLUIDICS,
        ):
            _log.warning(f"Warning threshold reached, pausing: {message}")
            self.pause()

        return should_pause

    def acknowledge_intervention(self) -> bool:
        """Acknowledge intervention and continue."""
        if self._is_in_state(OrchestratorState.WAITING_INTERVENTION):
            self._intervention_acknowledged.set()
            return True
        return False

    def skip_current_round(self) -> bool:
        """Skip to the next round (takes effect after current round finishes)."""
        if self._progress.current_round is None:
            return False
        self._skip_to_round_index = self._progress.current_round_index + 1
        return True

    def skip_to_round(self, round_index: int) -> bool:
        """Skip ahead to a specific round index (0-based)."""
        if round_index < 0:
            return False
        self._skip_to_round_index = round_index
        return True

    # ========================================================================
    # Protocol Initialization
    # ========================================================================

    def _initialize_fluidics_protocols(self) -> None:
        """Load protocol's fluidics_protocols into FluidicsController."""
        if self._fluidics_controller is None or self._protocol is None:
            return

        for name, protocol in self._protocol.fluidics_protocols.items():
            self._fluidics_controller.add_protocol(name, protocol)
            _log.debug(f"Added fluidics protocol: {name}")

    def _load_fov_set(self, csv_path: str) -> None:
        """Load FOV positions from CSV.

        Expected columns: region, x (mm), y (mm) (optional: z (mm))

        Args:
            csv_path: Path to CSV file with FOV positions
        """
        import pandas as pd
        from pathlib import Path

        if not Path(csv_path).exists():
            raise FileNotFoundError(f"FOV CSV file not found: {csv_path}")

        df = pd.read_csv(csv_path)

        # Normalize column names (handle variations)
        col_map = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if "region" in col_lower:
                col_map["region"] = col
            elif "x" in col_lower and "mm" in col_lower:
                col_map["x"] = col
            elif "y" in col_lower and "mm" in col_lower:
                col_map["y"] = col

        if not all(k in col_map for k in ["region", "x", "y"]):
            raise ValueError(
                f"CSV must have region, x (mm), y (mm) columns. Found: {list(df.columns)}"
            )

        region_fov_coordinates = {}
        region_centers = {}

        for region_id in df[col_map["region"]].unique():
            region_points = df[df[col_map["region"]] == region_id]
            coords = tuple(
                (float(x), float(y))
                for x, y in zip(region_points[col_map["x"]], region_points[col_map["y"]])
            )
            region_fov_coordinates[str(region_id)] = coords
            region_centers[str(region_id)] = (
                float(region_points[col_map["x"]].mean()),
                float(region_points[col_map["y"]].mean()),
            )

        self._event_bus.publish(
            LoadScanCoordinatesCommand(
                region_fov_coordinates=region_fov_coordinates,
                region_centers=region_centers,
            )
        )

        _log.info(
            f"Loaded {sum(len(c) for c in region_fov_coordinates.values())} FOVs "
            f"from {len(region_fov_coordinates)} regions"
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
                self._context = self._experiment_manager.start_experiment(
                    base_path=base_path,
                    experiment_id=self._experiment_label,
                    configurations=[],  # Will be set per-round
                    acquisition_params={
                        "protocol": self._protocol.name,
                        "experiment_label": self._experiment_label,
                    },
                )
                self._experiment_id = self._context.experiment_id
                self._warning_manager.experiment_id = self._experiment_id
                self._experiment_path = self._context.experiment_path

            # Execute rounds
            start_round = 0
            resume_step_index = 0
            resume_imaging_fov = 0
            if self._resume_checkpoint is not None:
                start_round = self._resume_checkpoint.round_index
                resume_step_index = self._resume_checkpoint.step_index
                resume_imaging_fov = self._resume_checkpoint.imaging_fov_index

            for round_idx in range(start_round, len(self._protocol.rounds)):
                round_ = self._protocol.rounds[round_idx]
                if self._cancel_token is not None:
                    self._cancel_token.check_point()

                # Update progress
                self._progress.current_round_index = round_idx
                self._progress.current_round = RoundProgress(
                    round_index=round_idx,
                    round_name=round_.name,
                    started_at=datetime.now(),
                )
                self._progress.current_step_index = 0
                self._latest_eta_seconds = None

                if (
                    self._skip_to_round_index is not None
                    and round_idx < self._skip_to_round_index
                ):
                    _log.info(f"Skipping round {round_idx} ({round_.name})")
                    self._publish_round_completed(round_idx, round_.name, success=True, error="skipped")
                    continue
                if self._skip_to_round_index is not None and round_idx == self._skip_to_round_index:
                    self._skip_to_round_index = None

                # Execute round
                self._execute_round(
                    round_idx,
                    round_,
                    resume_step_index=resume_step_index if round_idx == start_round else 0,
                    resume_imaging_fov=resume_imaging_fov if round_idx == start_round else 0,
                )

                # Mark round complete
                self._progress.current_round.completed_at = datetime.now()
                self._publish_round_completed(round_idx, round_.name, success=True)

            # Success
            self._transition_to(OrchestratorState.COMPLETED)
            self._experiment_manager.finalize_experiment(self._context, success=True)
            self._checkpoint_manager.clear(self._experiment_path)

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
            self._cancel_token = None
            self._context = None
            self._worker_thread = None
            self._resume_checkpoint = None

    def _execute_round(
        self,
        round_idx: int,
        round_: Round,
        *,
        resume_step_index: int = 0,
        resume_imaging_fov: int = 0,
    ) -> None:
        """Execute a single round using step-based execution."""
        _log.info(f"Executing round {round_idx}: name={round_.name}, steps={len(round_.steps)}")
        self._publish_round_started(round_idx, round_.name)

        for step_idx, step in enumerate(round_.steps):
            if step_idx < resume_step_index:
                continue

            if self._cancel_token is not None:
                self._cancel_token.check_point()

            # Update step progress in both ExperimentProgress and RoundProgress
            self._progress.current_step_index = step_idx
            if self._progress.current_round is not None:
                self._progress.current_round.current_step_index = step_idx
                self._progress.current_round.total_steps = len(round_.steps)
            self._save_checkpoint()

            try:
                if isinstance(step, FluidicsStep):
                    self._transition_to(OrchestratorState.RUNNING_FLUIDICS)
                    self._execute_fluidics_step(round_idx, step)

                elif isinstance(step, ImagingStep):
                    self._transition_to(OrchestratorState.RUNNING_IMAGING)
                    fov_resume = resume_imaging_fov if step_idx == resume_step_index else 0
                    self._execute_imaging_step(round_idx, step, resume_fov=fov_resume)

                elif isinstance(step, InterventionStep):
                    self._wait_for_intervention(round_idx, step.message)

            except CancellationError:
                raise
            except Exception as e:
                self._handle_step_failure(step, e)

        # Update progress
        self._publish_progress()

    def _execute_fluidics_step(self, round_idx: int, step: FluidicsStep) -> None:
        """Execute a fluidics step.

        Args:
            round_idx: Index of the current round
            step: FluidicsStep to execute
        """
        if self._progress.current_round is None or self._cancel_token is None:
            return

        protocol_name = step.protocol
        _log.info(f"Round {round_idx}: Running fluidics protocol '{protocol_name}'")

        if self._fluidics_controller is None:
            # No fluidics controller - simulate
            _log.debug(f"[SIMULATED] Fluidics protocol: {protocol_name}")
            return

        # Check for cancellation before starting
        self._cancel_token.check_point()

        # Start the protocol
        success = self._fluidics_controller.run_protocol(protocol_name)
        if not success:
            raise RuntimeError(f"Failed to start fluidics protocol: {protocol_name}")

        # Wait for protocol completion by polling controller state
        import time
        from squid.backend.controllers.fluidics_controller import FluidicsControllerState

        while True:
            # Check for orchestrator abort
            if self._cancel_token.is_cancelled:
                self._fluidics_controller.stop()
                raise CancellationError("Fluidics cancelled by orchestrator")

            # Check controller state
            state = self._fluidics_controller.state
            if state in (
                FluidicsControllerState.IDLE,
                FluidicsControllerState.COMPLETED,
                FluidicsControllerState.FAILED,
                FluidicsControllerState.STOPPED,
            ):
                break

            # Update progress based on controller's step tracking
            if self._progress.current_round is not None:
                self._progress.current_round.fluidics_step_index = self._fluidics_controller.current_step_index
                self._progress.current_round.total_fluidics_steps = self._fluidics_controller.total_steps
                self._publish_progress()

            time.sleep(0.1)

        terminal_state = self._fluidics_controller.last_terminal_state
        last_result = self._fluidics_controller.last_result
        if terminal_state == FluidicsControllerState.FAILED or (
            last_result is not None and not last_result.success
        ):
            raise RuntimeError(f"Fluidics protocol '{protocol_name}' failed")
        if terminal_state == FluidicsControllerState.STOPPED:
            raise CancellationError(f"Fluidics protocol '{protocol_name}' was stopped")
        if terminal_state is None:
            _log.warning(
                "Fluidics protocol '%s' finished without terminal state",
                protocol_name,
            )

        _log.info(f"Round {round_idx}: Fluidics protocol '{protocol_name}' completed")

    def _execute_imaging_step(
        self,
        round_idx: int,
        step: ImagingStep,
        *,
        resume_fov: int = 0,
    ) -> None:
        """Execute an imaging step.

        Args:
            round_idx: Index of the current round
            step: ImagingStep to execute
            resume_fov: FOV index to resume from (for checkpoint recovery)
        """
        if self._progress.current_round is None or self._cancel_token is None:
            return
        if self._protocol is None:
            return

        # Get imaging config
        config_name = step.config
        if config_name not in self._protocol.imaging_configs:
            raise RuntimeError(f"Imaging config '{config_name}' not found in protocol")
        imaging_config = self._protocol.imaging_configs[config_name]

        # Load FOV set if specified
        if step.fovs != "default" and step.fovs in self._protocol.fov_sets:
            csv_path = self._protocol.fov_sets[step.fovs]
            self._load_fov_set(csv_path)

        self._progress.current_round.imaging_started = True

        # Create round subfolder for images
        if hasattr(self._experiment_manager, "create_round_subfolder"):
            round_path = self._experiment_manager.create_round_subfolder(
                context=self._context,
                round_name=f"round_{round_idx:03d}_{step.config}",
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
            self._progress.current_round.total_imaging_fovs = total_fovs

        _log.info(
            f"Round {round_idx}: Imaging with config='{config_name}', "
            f"channels={imaging_config.get_channel_names()}"
        )

        # Execute imaging via executor
        if self._imaging_executor is not None:
            self._current_acquisition_id = round_dir_name
            try:
                success = self._imaging_executor.execute_with_config(
                    imaging_config=imaging_config,
                    output_path=round_base_path,
                    cancel_token=self._cancel_token,
                    round_index=round_idx,
                    resume_fov_index=resume_fov,
                    experiment_id=round_dir_name,  # Match orchestrator's expected ID
                )
                if not success:
                    raise RuntimeError(f"Imaging failed for round {round_idx}")
            finally:
                self._current_acquisition_id = None
        else:
            # No imaging executor - log and continue (for testing)
            _log.debug(f"[SIMULATED] Imaging: config={config_name}")

        self._progress.current_round.imaging_completed = True
        self._publish_progress()

    def _wait_for_intervention(self, round_idx: int, message: str) -> None:
        """Wait for operator intervention."""
        self._transition_to(OrchestratorState.WAITING_INTERVENTION)
        self._intervention_acknowledged.clear()

        self._event_bus.publish(
            OrchestratorInterventionRequired(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                round_name=self._progress.current_round.round_name if self._progress.current_round else "",
                message=message,
            )
        )

        # Wait for acknowledgment
        while not self._intervention_acknowledged.is_set():
            if self._cancel_token is not None:
                self._cancel_token.check_point()
            self._intervention_acknowledged.wait(timeout=0.5)

    def _handle_step_failure(self, step, error: Exception) -> None:
        """Handle step failure according to error_handling config.

        Args:
            step: The step that failed
            error: The exception that was raised
        """
        if self._protocol is None:
            raise error

        error_handling = self._protocol.error_handling

        # Determine which failure action to use
        if isinstance(step, FluidicsStep):
            action = error_handling.fluidics_failure
            failure_type = "fluidics"
        elif isinstance(step, ImagingStep):
            action = error_handling.imaging_failure
            failure_type = "imaging"
        else:
            # Intervention steps don't have configurable failure handling
            raise error

        _log.warning(f"{failure_type.capitalize()} step failed: {error}")

        if action == FailureAction.ABORT:
            raise error
        elif action == FailureAction.PAUSE:
            _log.info(f"Pausing after {failure_type} step failure (error_handling={action.value})")
            self.pause()
            # Wait for user to resume or cancel
            # check_point() will block until resumed, or raise CancellationError if cancelled
            if self._cancel_token is not None:
                self._cancel_token.check_point()
            # If we get here, user resumed - continue to next step
            _log.info(f"Resumed after {failure_type} step failure, continuing to next step")
        elif action == FailureAction.SKIP:
            _log.info(f"Skipping failed {failure_type} step (error_handling={action.value})")
            # Don't raise - continue to next step
        elif action == FailureAction.WARN:
            self.add_warning(
                category=WarningCategory.EXECUTION,
                severity=WarningSeverity.MEDIUM,
                message=f"{failure_type.capitalize()} step failed: {error}",
            )
            # Don't raise - continue to next step

    # ========================================================================
    # Progress and Checkpoint
    # ========================================================================

    def _save_checkpoint(self) -> None:
        """Save current state to checkpoint."""
        if not self._experiment_path or self._protocol is None:
            return

        if self._progress.current_round is None:
            return

        checkpoint = self._checkpoint_manager.create_checkpoint(
            protocol_name=self._protocol.name,
            protocol_version=self._protocol.version,
            experiment_id=self._experiment_id,
            experiment_path=self._experiment_path,
            round_index=self._progress.current_round_index,
            step_index=self._progress.current_step_index,
            imaging_fov_index=self._progress.current_round.imaging_fov_index,
        )

        self._checkpoint_manager.save(checkpoint, self._experiment_path)

    def _publish_progress(self) -> None:
        """Publish progress event."""
        current_round_name = ""
        if self._progress.current_round is not None:
            current_round_name = self._progress.current_round.round_name

        self._event_bus.publish(
            OrchestratorProgress(
                experiment_id=self._experiment_id,
                current_round=self._progress.current_round_index + 1,
                total_rounds=self._progress.total_rounds,
                current_round_name=current_round_name,
                progress_percent=self._progress.progress_percent,
                eta_seconds=self._latest_eta_seconds,
                current_operation=self.state.name.lower(),
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
