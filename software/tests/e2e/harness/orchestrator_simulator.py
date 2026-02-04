"""
Orchestrator workflow simulator.

Simulates multi-round experiment workflows by creating an OrchestratorController
and publishing commands similar to the GUI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

from squid.core.events import (
    AddFlexibleRegionCommand,
    ClearScanCoordinatesCommand,
    SetAcquisitionPathCommand,
)
from squid.backend.controllers.orchestrator import (
    OrchestratorController,
    OrchestratorState,
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorInterventionRequired,
    OrchestratorError,
    WarningRaised,
    ImagingExecutor,
)
from squid.backend.controllers.fluidics_controller import FluidicsController
from squid.backend.controllers.multipoint.experiment_manager import ExperimentManager
from squid.backend.controllers.multipoint.acquisition_planner import AcquisitionPlanner

from tests.harness.simulators.base import BaseSimulator
import squid.core.logging

if TYPE_CHECKING:
    from tests.harness.core.backend_context import BackendContext

_log = squid.core.logging.get_logger(__name__)


@dataclass
class OrchestratorResult:
    """Result from an orchestrator run."""

    success: bool = False
    error: Optional[str] = None
    total_rounds: int = 0
    completed_rounds: int = 0
    elapsed_time_s: float = 0.0
    state_changes: List[OrchestratorStateChanged] = field(default_factory=list)
    round_started_events: List[OrchestratorRoundStarted] = field(default_factory=list)
    round_completed_events: List[OrchestratorRoundCompleted] = field(default_factory=list)
    progress_events: List[OrchestratorProgress] = field(default_factory=list)
    warnings: List[WarningRaised] = field(default_factory=list)
    intervention_events: List[OrchestratorInterventionRequired] = field(default_factory=list)
    error_events: List[OrchestratorError] = field(default_factory=list)
    final_state: Optional[str] = None
    experiment_path: Optional[str] = None


class OrchestratorSimulator(BaseSimulator):
    """
    Simulates multi-round orchestrated experiment workflows.

    This simulator creates a real OrchestratorController with its executors
    and provides a high-level API for testing multi-round experiments.

    Usage:
        with BackendContext() as ctx:
            sim = OrchestratorSimulator(ctx)

            # Load protocol and set up regions
            sim.load_protocol("path/to/protocol.yaml")
            sim.add_grid_region("region_1", (10, 10, 1), n_x=2, n_y=2)

            # Run and wait for completion
            result = sim.run_and_wait(timeout_s=120)

            assert result.success
            assert result.completed_rounds == 4
    """

    def __init__(
        self,
        ctx: "BackendContext",
        *,
        auto_set_base_path: bool = True,
    ):
        super().__init__(ctx)

        self._auto_set_base_path = auto_set_base_path
        self._protocol_path: Optional[str] = None
        self._orchestrator: Optional[OrchestratorController] = None
        self._imaging_executor: Optional[ImagingExecutor] = None

        # Force creation of controllers/managers so they subscribe to EventBus
        _ = ctx.multipoint_controller
        _ = ctx.scan_coordinates
        self.sleep(0.1)

        # Set up base path via EventBus
        if self._auto_set_base_path:
            self.publish(SetAcquisitionPathCommand(base_path=ctx.base_path))
            self.sleep(0.2)

        # Subscribe to orchestrator events
        self.monitor.subscribe(
            OrchestratorStateChanged,
            OrchestratorProgress,
            OrchestratorRoundStarted,
            OrchestratorRoundCompleted,
            OrchestratorInterventionRequired,
            OrchestratorError,
            WarningRaised,
        )

        # Clear any existing coordinates
        self.clear_coordinates()

        # Create orchestrator with executors
        self._create_orchestrator()

    def _create_orchestrator(self) -> None:
        """Create the orchestrator controller with its executors."""
        ctx = self._ctx

        # Create experiment manager
        experiment_manager = ExperimentManager(
            objective_store=ctx.objective_store,
            channel_config_manager=ctx.channel_config_manager,
            camera_service=ctx.camera_service,
        )

        # Create acquisition planner
        acquisition_planner = AcquisitionPlanner(
            objective_store=ctx.objective_store,
            channel_config_manager=ctx.channel_config_manager,
            camera_service=ctx.camera_service,
        )

        # Create imaging executor
        self._imaging_executor = ImagingExecutor(
            event_bus=ctx.event_bus,
            multipoint_controller=ctx.multipoint_controller,
            scan_coordinates=ctx.scan_coordinates,
        )

        # Create fluidics controller (simulated - no fluidics_service)
        fluidics_controller = FluidicsController(event_bus=ctx.event_bus)
        fluidics_protocols_path = (
            Path(__file__).resolve().parent.parent / "configs/fluidics/test_fluidics_protocols.yaml"
        )
        if fluidics_protocols_path.exists():
            fluidics_controller.load_protocols(str(fluidics_protocols_path))
        else:
            _log.warning(
                "Fluidics protocols file not found for E2E simulator: %s",
                fluidics_protocols_path,
            )

        # Create orchestrator
        self._orchestrator = OrchestratorController(
            event_bus=ctx.event_bus,
            multipoint_controller=ctx.multipoint_controller,
            experiment_manager=experiment_manager,
            acquisition_planner=acquisition_planner,
            imaging_executor=self._imaging_executor,
            fluidics_controller=fluidics_controller,
            scan_coordinates=ctx.scan_coordinates,
        )

    @property
    def orchestrator(self) -> OrchestratorController:
        """Get the orchestrator controller."""
        if self._orchestrator is None:
            raise RuntimeError("Orchestrator not initialized")
        return self._orchestrator

    # =========================================================================
    # Coordinate Setup
    # =========================================================================

    def clear_coordinates(self) -> "OrchestratorSimulator":
        """Clear all scan coordinates."""
        self.publish(ClearScanCoordinatesCommand())
        self.sleep(0.2)
        return self

    def add_single_fov(
        self,
        region_id: str,
        x: float,
        y: float,
        z: float,
    ) -> "OrchestratorSimulator":
        """Add a single-FOV region."""
        self.publish(
            AddFlexibleRegionCommand(
                region_id=region_id,
                center_x_mm=x,
                center_y_mm=y,
                center_z_mm=z,
                n_x=1,
                n_y=1,
                overlap_percent=0.0,
            )
        )
        self.sleep(0.2)
        return self

    def add_grid_region(
        self,
        region_id: str,
        center: Tuple[float, float, float],
        n_x: int = 1,
        n_y: int = 1,
        overlap_pct: float = 10.0,
    ) -> "OrchestratorSimulator":
        """Add a grid region."""
        x, y, z = center
        self.publish(
            AddFlexibleRegionCommand(
                region_id=region_id,
                center_x_mm=x,
                center_y_mm=y,
                center_z_mm=z,
                n_x=n_x,
                n_y=n_y,
                overlap_percent=overlap_pct,
            )
        )
        self.sleep(0.2)
        return self

    # =========================================================================
    # Configuration
    # =========================================================================

    def load_protocol(self, protocol_path: str) -> "OrchestratorSimulator":
        """
        Set the protocol path for the experiment.

        Args:
            protocol_path: Path to protocol YAML file

        Returns:
            self for chaining
        """
        self._protocol_path = protocol_path
        return self

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def get_stage_center(self) -> Tuple[float, float, float]:
        """Get center position of the stage."""
        return self._ctx.get_stage_center()

    def get_available_channels(self) -> List[str]:
        """Get list of available channel names."""
        return self._ctx.get_available_channels()

    # =========================================================================
    # Execution
    # =========================================================================

    def start(
        self,
        experiment_id: Optional[str] = None,
        base_path: Optional[str] = None,
        resume_from_checkpoint: bool = False,
    ) -> bool:
        """
        Start the orchestrated experiment (non-blocking).

        Args:
            experiment_id: Unique experiment identifier
            base_path: Base path for experiment output
            resume_from_checkpoint: Resume from checkpoint in base_path

        Returns:
            True if started successfully
        """
        if self._protocol_path is None:
            raise ValueError("Protocol path not set. Call load_protocol() first.")

        return self.orchestrator.start_experiment(
            protocol_path=self._protocol_path,
            base_path=base_path or self._ctx.base_path,
            experiment_id=experiment_id,
            resume_from_checkpoint=resume_from_checkpoint,
        )

    def pause(self) -> bool:
        """Pause the experiment."""
        return self.orchestrator.pause()

    def resume(self) -> bool:
        """Resume a paused experiment."""
        return self.orchestrator.resume()

    def abort(self) -> bool:
        """Abort the experiment."""
        return self.orchestrator.abort()

    def acknowledge_intervention(self) -> bool:
        """Acknowledge an intervention request."""
        return self.orchestrator.acknowledge_intervention()

    def skip_current_round(self) -> None:
        """Skip the current round."""
        self.orchestrator.skip_current_round()

    def skip_to_round(self, round_index: int) -> None:
        """Skip to a specific round."""
        self.orchestrator.skip_to_round(round_index)

    def run_and_wait(
        self,
        experiment_id: Optional[str] = None,
        base_path: Optional[str] = None,
        timeout_s: float = 120.0,
        auto_acknowledge_interventions: bool = True,
        resume_from_checkpoint: bool = False,
    ) -> OrchestratorResult:
        """
        Run the experiment and wait for completion.

        Args:
            experiment_id: Unique experiment identifier
            base_path: Base path for experiment output
            timeout_s: Maximum time to wait for completion
            auto_acknowledge_interventions: Auto-acknowledge intervention requests
            resume_from_checkpoint: Resume from checkpoint in base_path

        Returns:
            OrchestratorResult with success status and collected events
        """
        # Clear previous events
        self.monitor.clear()

        start_time = time.time()
        base_path = base_path or self._ctx.base_path

        # Start experiment
        started = self.start(
            experiment_id=experiment_id,
            base_path=base_path,
            resume_from_checkpoint=resume_from_checkpoint,
        )
        if not started:
            return OrchestratorResult(
                success=False,
                error="Failed to start experiment",
                final_state=self.orchestrator.state.value,
            )

        # Wait for completion with auto-acknowledgment
        while self.orchestrator.is_running:
            elapsed = time.time() - start_time
            if elapsed > timeout_s:
                return OrchestratorResult(
                    success=False,
                    error=f"Timeout after {timeout_s}s",
                    elapsed_time_s=elapsed,
                    final_state=self.orchestrator.state.value,
                    state_changes=self.monitor.get_events(OrchestratorStateChanged),
                    round_started_events=self.monitor.get_events(OrchestratorRoundStarted),
                    round_completed_events=self.monitor.get_events(OrchestratorRoundCompleted),
                    progress_events=self.monitor.get_events(OrchestratorProgress),
                    warnings=self.monitor.get_events(WarningRaised),
                    intervention_events=self.monitor.get_events(OrchestratorInterventionRequired),
                    error_events=self.monitor.get_events(OrchestratorError),
                )

            # Auto-acknowledge interventions
            if (
                auto_acknowledge_interventions
                and self.orchestrator.state == OrchestratorState.WAITING_INTERVENTION
            ):
                self.sleep(0.1)  # Brief delay before acknowledging
                self.orchestrator.acknowledge_intervention()

            self.sleep(0.1)

        # Allow time for final events to be delivered
        self.sleep(0.3)

        elapsed = time.time() - start_time
        final_state = self.orchestrator.state

        # Collect events
        state_changes = self.monitor.get_events(OrchestratorStateChanged)
        round_started = self.monitor.get_events(OrchestratorRoundStarted)
        round_completed = self.monitor.get_events(OrchestratorRoundCompleted)
        progress_events = self.monitor.get_events(OrchestratorProgress)
        warnings = self.monitor.get_events(WarningRaised)
        intervention_events = self.monitor.get_events(OrchestratorInterventionRequired)
        error_events = self.monitor.get_events(OrchestratorError)

        # Determine success
        success = final_state == OrchestratorState.COMPLETED

        # Get experiment path
        experiment_path = getattr(self.orchestrator, "_experiment_path", None)

        return OrchestratorResult(
            success=success,
            error=error_events[0].message if error_events else None,
            total_rounds=len(self.orchestrator.protocol.rounds) if self.orchestrator.protocol else 0,
            completed_rounds=len([e for e in round_completed if e.success]),
            elapsed_time_s=elapsed,
            state_changes=state_changes,
            round_started_events=round_started,
            round_completed_events=round_completed,
            progress_events=progress_events,
            warnings=warnings,
            intervention_events=intervention_events,
            error_events=error_events,
            final_state=final_state.value,
            experiment_path=experiment_path,
        )

    def reset(self) -> None:
        """Reset simulator state."""
        super().reset()
        self.clear_coordinates()
        self._protocol_path = None

    def cleanup(self) -> None:
        """Clean up resources."""
        if self._imaging_executor:
            self._imaging_executor.shutdown()
