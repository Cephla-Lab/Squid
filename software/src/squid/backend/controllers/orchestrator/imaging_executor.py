"""
Imaging executor for orchestrated experiments.

Delegates imaging rounds to MultiPointController for actual image acquisition.
Bridges the orchestrator's round-based model with multipoint's acquisition model.

V2 Support:
    - execute_with_protocol() for ImagingProtocol-based imaging
    - resolve_protocol_channels() for read-only channel resolution
    - Focus interval configuration via AutofocusExecutor
"""

import copy
import os
import threading
from typing import Callable, List, Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.config.test_timing import scale_duration
from squid.core.events import (
    AutofocusMode,
    EventBus,
    FocusLockSettings,
    handles,
    auto_subscribe,
    auto_unsubscribe,
)
from squid.core.events import AcquisitionFinished, AcquisitionProgress
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import ImagingProtocol, ChannelConfigOverride

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint import MultiPointController
    from squid.backend.managers.scan_coordinates import ScanCoordinates
    from squid.backend.managers.channel_config_service import ChannelConfigService
    from squid.core.config.models import AcquisitionChannel

_log = squid.core.logging.get_logger(__name__)


def resolve_protocol_channels(
    protocol: ImagingProtocol,
    channel_config_service: "ChannelConfigService",
    objective: str,
) -> List["AcquisitionChannel"]:
    """Resolve protocol channel names to concrete AcquisitionChannel objects.

    Reads channel settings from ChannelConfigService (read-only).
    Applies ChannelConfigOverride if present. Returns new objects.
    Never mutates global config.

    Args:
        protocol: ImagingProtocol with channel names/overrides
        channel_config_service: Service for looking up channel configs
        objective: Current objective name

    Returns:
        List of AcquisitionChannel objects in protocol channel order

    Raises:
        ValueError: If a channel name is not found in available channels
    """
    resolved = []
    for ch in protocol.channels:
        ch_name = ch if isinstance(ch, str) else ch.name
        config = channel_config_service.get_channel_configuration_by_name(objective, ch_name)
        if config is None:
            raise ValueError(
                f"Channel '{ch_name}' not found in available channels for objective '{objective}'"
            )

        # Apply overrides to a copy — never mutate the original
        if isinstance(ch, ChannelConfigOverride):
            config = _apply_override_to_channel(config, ch)

        resolved.append(config)
    return resolved


def _apply_override_to_channel(
    channel: "AcquisitionChannel",
    override: ChannelConfigOverride,
) -> "AcquisitionChannel":
    """Apply a ChannelConfigOverride to an AcquisitionChannel, returning a new object.

    Creates a deep copy of the channel and mutates it with override values.
    The original channel is never modified.
    """
    channel = copy.deepcopy(channel)
    if override.exposure_time_ms is not None:
        channel.camera_settings.exposure_time_ms = override.exposure_time_ms
    if override.analog_gain is not None:
        channel.camera_settings.gain_mode = override.analog_gain
    if override.illumination_intensity is not None:
        channel.illumination_settings.intensity = override.illumination_intensity
    if "z_offset_um" in override.model_fields_set:
        channel.z_offset_um = override.z_offset_um
    return channel


class ImagingExecutor:
    """Executes imaging rounds by delegating to MultiPointController.

    The ImagingExecutor bridges the orchestrator's per-round imaging model
    with the MultiPointController's acquisition system.

    V2 Protocol Support:
        - execute_with_config(): Execute imaging using ImagingProtocol
        - resolve_protocol_channels(): Read-only channel resolution
        - Focus interval configuration via AutofocusExecutor

    Usage:
        executor = ImagingExecutor(
            event_bus=event_bus,
            multipoint_controller=multipoint,
            scan_coordinates=scan_coords,
        )

        # V2 style with ImagingProtocol
        success = executor.execute_with_config(
            imaging_config=protocol,
            output_path="/data/experiments/round_001",
            cancel_token=cancel_token,
            round_index=0,
        )
    """

    def __init__(
        self,
        event_bus: EventBus,
        multipoint_controller: "MultiPointController",
        scan_coordinates: Optional["ScanCoordinates"] = None,
        channel_config_manager: Optional["ChannelConfigService"] = None,
    ):
        """Initialize the imaging executor.

        Args:
            event_bus: EventBus for event communication
            multipoint_controller: MultiPointController for acquisitions
            scan_coordinates: ScanCoordinates with FOV positions
            channel_config_manager: ChannelConfigService for channel resolution
        """
        self._event_bus = event_bus
        self._multipoint = multipoint_controller
        self._scan_coordinates = scan_coordinates
        if channel_config_manager is not None:
            self._channel_config_manager = channel_config_manager
        else:
            self._channel_config_manager = getattr(
                multipoint_controller, "channelConfigurationManager", None
            )

        # Synchronization for acquisition completion
        self._acquisition_complete = threading.Event()
        self._acquisition_success = False
        self._acquisition_error: Optional[str] = None
        self._current_experiment_id: Optional[str] = None

        # FOV progress tracking
        self._images_per_fov: int = 1
        self._progress_callback: Optional[Callable[[int, int, Optional[float]], None]] = None

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

    def abort(self) -> None:
        """Abort a running acquisition immediately."""
        if hasattr(self._multipoint, "request_abort_aquisition"):
            self._multipoint.request_abort_aquisition()

    def execute_with_config(
        self,
        imaging_config: ImagingProtocol,
        output_path: str,
        cancel_token: CancelToken,
        round_index: int,
        resume_fov_index: int = 0,
        experiment_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, Optional[float]], None]] = None,
    ) -> bool:
        """Execute imaging using a V2 ImagingProtocol.

        Resolves channels from the ImagingProtocol without mutating global
        config, configures the MultiPointController, and runs the acquisition.

        Args:
            imaging_config: ImagingProtocol defining channels, z-stack, focus settings
            output_path: Base path where images should be saved
            cancel_token: CancelToken for pause/abort support
            round_index: Round index for experiment ID and FOV context
            resume_fov_index: FOV index to resume from (0 = start from beginning)
            experiment_id: Optional experiment ID override. If not provided,
                          auto-generates as "round_{round_index:03d}"
            progress_callback: Optional callback ``(fov_index, total_fovs, eta_seconds)``
                invoked when FOV progress changes during imaging.

        Returns:
            True if imaging completed successfully, False otherwise
        """
        if experiment_id is None:
            experiment_id = f"round_{round_index:03d}"
        self._current_experiment_id = experiment_id
        self._acquisition_complete.clear()
        self._acquisition_success = False
        self._acquisition_error = None
        self._images_per_fov = max(
            1,
            imaging_config.z_stack.planes * len(imaging_config.get_channel_names()),
        )
        self._progress_callback = progress_callback

        try:
            # Resolve channels without mutating global state
            resolved_channels = self._resolve_channels(imaging_config)
            channel_names = [c.name for c in resolved_channels] if resolved_channels else imaging_config.get_channel_names()

            # Configure multipoint base path and experiment ID
            self._multipoint.base_path = output_path
            self._multipoint.experiment_ID = experiment_id

            # Set round index if supported
            if hasattr(self._multipoint, "set_current_round_index"):
                self._multipoint.set_current_round_index(round_index)
            if hasattr(self._multipoint, "set_start_fov_index"):
                self._multipoint.set_start_fov_index(resume_fov_index)
                if resume_fov_index > 0:
                    _log.info(f"Set start FOV index to {resume_fov_index} for resume")

            # Configure z-stack
            direction_map = {
                "from_center": "FROM CENTER",
                "from_bottom": "FROM BOTTOM",
                "from_top": "FROM TOP",
            }
            self._multipoint.update_config(
                **{
                    "zstack.nz": imaging_config.z_stack.planes,
                    "zstack.delta_z_um": imaging_config.z_stack.step_um,
                    "zstack.stacking_direction": direction_map[imaging_config.z_stack.direction],
                }
            )

            # Configure focus
            focus = imaging_config.focus
            self._multipoint.update_config(
                **{
                    "focus.mode": AutofocusMode(focus.mode),
                    "focus.interval_fovs": focus.interval_fovs,
                    "focus.focus_lock": FocusLockSettings(**focus.focus_lock.model_dump()),
                }
            )

            # Configure skip_saving
            self._multipoint.update_config(skip_saving=imaging_config.skip_saving)

            # Configure acquisition_order
            acquisition_order = getattr(imaging_config, "acquisition_order", "channel_first")
            self._multipoint.update_config(acquisition_order=acquisition_order)

            # Set resolved channels (no global state mutation)
            if resolved_channels and hasattr(self._multipoint, "set_resolved_configurations"):
                self._multipoint.set_resolved_configurations(resolved_channels)
            elif hasattr(self._multipoint, "set_selected_configurations"):
                self._multipoint.set_selected_configurations(channel_names)

            # Create output directory and write acquisition config
            output_dir = os.path.join(output_path, experiment_id)
            os.makedirs(output_dir, exist_ok=True)
            self._write_acquisition_output(output_dir, resolved_channels or [], channel_names)

            # Start the acquisition
            _log.info(
                f"Starting imaging: channels={channel_names}, "
                f"z_planes={imaging_config.z_stack.planes}, "
                f"acquisition_order={acquisition_order}, "
                f"focus={imaging_config.focus.mode.value}"
            )
            started = self._multipoint.run_acquisition(acquire_current_fov=False)
            if not started:
                _log.error("run_acquisition() returned False — acquisition did not start")
                return False

            # Wait for acquisition to complete, checking cancel token
            wait_timeout_s = scale_duration(0.5, min_seconds=0.01)
            while not self._acquisition_complete.is_set():
                cancel_token.check_point()  # Raises CancellationError if cancelled
                self._acquisition_complete.wait(timeout=wait_timeout_s)

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
            if hasattr(self._multipoint, "set_start_fov_index"):
                # Ensure subsequent rounds start from the beginning.
                self._multipoint.set_start_fov_index(0)
            self._current_experiment_id = None

    def _resolve_channels(
        self,
        imaging_config: ImagingProtocol,
    ) -> Optional[List["AcquisitionChannel"]]:
        """Resolve protocol channels to AcquisitionChannel objects.

        Returns None if channel resolution is not possible (no service or no objective).
        """
        if self._channel_config_manager is None:
            return None

        current_objective = None
        if hasattr(self._multipoint, "objectiveStore") and self._multipoint.objectiveStore:
            current_objective = self._multipoint.objectiveStore.current_objective

        if current_objective is None:
            _log.warning("Cannot resolve channels: no current objective available")
            return None

        return resolve_protocol_channels(
            imaging_config,
            self._channel_config_manager,
            current_objective,
        )

    def _write_acquisition_output(
        self,
        output_dir: str,
        resolved_channels: List["AcquisitionChannel"],
        channel_names: List[str],
    ) -> None:
        """Write acquisition channel configuration to the round output folder."""
        if self._channel_config_manager is None:
            return
        if not hasattr(self._channel_config_manager, "save_acquisition_output"):
            return

        current_objective = None
        if hasattr(self._multipoint, "objectiveStore") and self._multipoint.objectiveStore:
            current_objective = self._multipoint.objectiveStore.current_objective
        if current_objective is None:
            _log.warning("Cannot write acquisition output: no current objective available")
            return

        # Use resolved channels if available, otherwise look up by name
        configs = resolved_channels if resolved_channels else []
        if not configs:
            for name in channel_names:
                config = self._channel_config_manager.get_channel_configuration_by_name(
                    current_objective, name
                )
                if config is not None:
                    configs.append(config)

        if not configs:
            _log.warning("No channel configurations found for acquisition output")
            return

        try:
            from pathlib import Path

            self._channel_config_manager.save_acquisition_output(
                Path(output_dir), current_objective, configs
            )
        except Exception as e:
            _log.warning(f"Failed to write acquisition output: {e}")

    @handles(AcquisitionProgress)
    def _on_acquisition_progress(self, event: AcquisitionProgress) -> None:
        """Convert image-level progress to FOV-level and invoke callback."""
        if self._current_experiment_id is None:
            return
        if event.experiment_id != self._current_experiment_id:
            return
        if self._progress_callback is None:
            return

        images_per_fov = max(self._images_per_fov, 1)
        current_image = max(event.current_fov, 1)
        fov_index = (current_image - 1) // images_per_fov
        total_images = max(event.total_fovs, 0)
        total_fovs = (
            (total_images + images_per_fov - 1) // images_per_fov
            if total_images > 0
            else 0
        )

        self._progress_callback(fov_index, total_fovs, event.eta_seconds)

    @handles(AcquisitionFinished)
    def _on_acquisition_finished(self, event: AcquisitionFinished) -> None:
        """Handle acquisition completion."""
        # Filter by experiment_id if we have one
        if self._current_experiment_id is not None:
            if hasattr(event, "experiment_id") and event.experiment_id != self._current_experiment_id:
                return

        self._acquisition_success = event.success
        if event.error is not None:
            self._acquisition_error = str(event.error)
        self._acquisition_complete.set()
