"""
Imaging executor for orchestrated experiments.

Delegates imaging rounds to MultiPointController for actual image acquisition.
Bridges the orchestrator's round-based model with multipoint's acquisition model.
"""

import os
import threading
from typing import Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.events import EventBus, handles, auto_subscribe, auto_unsubscribe
from squid.core.events import AcquisitionFinished
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import ImagingStep

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint import MultiPointController
    from squid.backend.managers.scan_coordinates import ScanCoordinates

_log = squid.core.logging.get_logger(__name__)


class ImagingExecutor:
    """Executes imaging rounds by delegating to MultiPointController.

    The ImagingExecutor bridges the orchestrator's per-round imaging model
    with the MultiPointController's acquisition system.

    Usage:
        executor = ImagingExecutor(
            event_bus=event_bus,
            multipoint_controller=multipoint,
            scan_coordinates=scan_coords,
        )

        success = executor.execute(
            imaging_step=round_.imaging,
            output_path="/data/experiments/round_001",
            cancel_token=cancel_token,
        )
    """

    def __init__(
        self,
        event_bus: EventBus,
        multipoint_controller: "MultiPointController",
        scan_coordinates: Optional["ScanCoordinates"] = None,
    ):
        """Initialize the imaging executor.

        Args:
            event_bus: EventBus for event communication
            multipoint_controller: MultiPointController for acquisitions
            scan_coordinates: ScanCoordinates with FOV positions
        """
        self._event_bus = event_bus
        self._multipoint = multipoint_controller
        self._scan_coordinates = scan_coordinates

        # Synchronization for acquisition completion
        self._acquisition_complete = threading.Event()
        self._acquisition_success = False
        self._acquisition_error: Optional[str] = None
        self._current_experiment_id: Optional[str] = None

        # Event subscriptions
        self._subscriptions = auto_subscribe(self, event_bus)

    def shutdown(self) -> None:
        """Cleanup subscriptions."""
        auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []

    def pause(self) -> bool:
        """Request a pause at the next safe boundary."""
        if hasattr(self._multipoint, "request_pause"):
            return bool(self._multipoint.request_pause())
        return False

    def resume(self) -> bool:
        """Resume a paused acquisition."""
        if hasattr(self._multipoint, "resume_acquisition"):
            return bool(self._multipoint.resume_acquisition())
        return False

    def execute(
        self,
        imaging_step: ImagingStep,
        output_path: str,
        cancel_token: CancelToken,
        experiment_id: Optional[str] = None,
        round_index: Optional[int] = None,
    ) -> bool:
        """Execute imaging for a round.

        Configures the MultiPointController with the imaging parameters
        and runs the acquisition. Blocks until acquisition completes.

        Args:
            imaging_step: ImagingStep from protocol defining channels, z-planes, etc.
            output_path: Base path where images should be saved
            cancel_token: CancelToken for pause/abort support
            experiment_id: Optional experiment identifier
            round_index: Optional round index for FOV event context

        Returns:
            True if imaging completed successfully, False otherwise
        """
        self._current_experiment_id = experiment_id
        self._acquisition_complete.clear()
        self._acquisition_success = False
        self._acquisition_error = None

        try:
            # Configure multipoint for this round's imaging
            if round_index is not None and hasattr(self._multipoint, "set_current_round_index"):
                self._multipoint.set_current_round_index(round_index)
            self._configure_acquisition(imaging_step, output_path, experiment_id)

            # Start the acquisition
            _log.info(
                f"Starting imaging: channels={imaging_step.channels}, "
                f"z_planes={imaging_step.z_planes}"
            )
            self._multipoint.run_acquisition(acquire_current_fov=False)

            # Wait for acquisition to complete, checking cancel token
            while not self._acquisition_complete.is_set():
                cancel_token.check_point()  # Raises CancellationError if cancelled
                self._acquisition_complete.wait(timeout=0.5)

            if not self._acquisition_success:
                _log.error(f"Imaging failed: {self._acquisition_error}")
                return False

            return True

        except CancellationError:
            _log.info("Imaging cancelled")
            if hasattr(self._multipoint, "request_abort_aquisition"):
                self._multipoint.request_abort_aquisition()
            raise

        except Exception as e:
            _log.exception(f"Imaging execution error: {e}")
            return False

        finally:
            self._current_experiment_id = None

    def _configure_acquisition(
        self,
        imaging_step: ImagingStep,
        base_path: str,
        experiment_id: Optional[str],
    ) -> None:
        """Configure MultiPointController for this imaging round.

        Args:
            imaging_step: ImagingStep with channels, z-planes, etc.
            base_path: Base path for saving images
            experiment_id: Experiment identifier for event filtering
        """
        # Set base path + experiment ID for multipoint outputs
        self._multipoint.base_path = base_path
        if experiment_id is not None:
            self._multipoint.experiment_ID = experiment_id
            os.makedirs(os.path.join(base_path, experiment_id), exist_ok=True)

        # Configure z-stack parameters
        self._multipoint.update_config(
            **{
                "zstack.nz": imaging_step.z_planes,
                "zstack.delta_z_um": imaging_step.z_step_um,
            }
        )

        # Configure channels via multipoint's selected_configurations
        # The actual channel configuration is managed by the ChannelConfigurationManager
        # Here we just set which channels to use
        if hasattr(self._multipoint, "set_selected_configurations"):
            self._multipoint.set_selected_configurations(imaging_step.channels)

        # Apply per-round imaging overrides
        self._multipoint.update_config(
            **{
                "focus.do_contrast_af": imaging_step.use_autofocus,
                "skip_saving": imaging_step.skip_saving,
            }
        )

    @handles(AcquisitionFinished)
    def _on_acquisition_finished(self, event: AcquisitionFinished) -> None:
        """Handle acquisition completion."""
        # Filter by experiment_id if we have one
        if self._current_experiment_id is not None:
            if hasattr(event, 'experiment_id') and event.experiment_id != self._current_experiment_id:
                return

        self._acquisition_success = event.success
        if event.error is not None:
            self._acquisition_error = str(event.error)
        self._acquisition_complete.set()
