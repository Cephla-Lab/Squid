"""
Imaging executor for orchestrated experiments.

Delegates imaging rounds to MultiPointController for actual image acquisition.
Bridges the orchestrator's round-based model with multipoint's acquisition model.

V2 Support:
    - execute_with_config() for ImagingConfig-based imaging
    - Channel overrides via ChannelConfigService
    - Focus interval configuration via AutofocusExecutor
"""

import os
import threading
from typing import Optional, TYPE_CHECKING, List, Union

import squid.core.logging
from squid.core.config.test_timing import scale_duration
from squid.core.events import EventBus, handles, auto_subscribe, auto_unsubscribe
from squid.core.events import AcquisitionFinished
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import ImagingConfig, ChannelConfigOverride

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint import MultiPointController
    from squid.backend.managers.scan_coordinates import ScanCoordinates
    from squid.backend.managers.channel_config_service import ChannelConfigService

_log = squid.core.logging.get_logger(__name__)


class ImagingExecutor:
    """Executes imaging rounds by delegating to MultiPointController.

    The ImagingExecutor bridges the orchestrator's per-round imaging model
    with the MultiPointController's acquisition system.

    V2 Protocol Support:
        - execute_with_config(): Execute imaging using ImagingConfig
        - Channel overrides applied via ChannelConfigService
        - Focus interval configuration via AutofocusExecutor

    Usage:
        executor = ImagingExecutor(
            event_bus=event_bus,
            multipoint_controller=multipoint,
            scan_coordinates=scan_coords,
        )

        # V2 style with ImagingConfig
        success = executor.execute_with_config(
            imaging_config=config,
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
            channel_config_manager: ChannelConfigService for channel overrides
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

    def execute_with_config(
        self,
        imaging_config: ImagingConfig,
        output_path: str,
        cancel_token: CancelToken,
        round_index: int,
        resume_fov_index: int = 0,
        experiment_id: Optional[str] = None,
    ) -> bool:
        """Execute imaging using a V2 ImagingConfig.

        Configures the MultiPointController with the imaging parameters
        from the ImagingConfig and runs the acquisition.

        Args:
            imaging_config: ImagingConfig defining channels, z-stack, focus settings
            output_path: Base path where images should be saved
            cancel_token: CancelToken for pause/abort support
            round_index: Round index for experiment ID and FOV context
            resume_fov_index: FOV index to resume from (0 = start from beginning)
            experiment_id: Optional experiment ID override. If not provided,
                          auto-generates as "round_{round_index:03d}"

        Returns:
            True if imaging completed successfully, False otherwise
        """
        if experiment_id is None:
            experiment_id = f"round_{round_index:03d}"
        self._current_experiment_id = experiment_id
        self._acquisition_complete.clear()
        self._acquisition_success = False
        self._acquisition_error = None

        try:
            # Get channel names
            channel_names = imaging_config.get_channel_names()

            override_snapshot = None
            override_objective = None

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
                    "focus.do_contrast_af": focus.enabled and focus.method == "contrast",
                    "focus.do_reflection_af": focus.enabled and focus.method == "laser",
                }
            )

            # Configure autofocus executor with interval if available
            if hasattr(self._multipoint, "_autofocus_executor") and self._multipoint._autofocus_executor:
                self._multipoint._autofocus_executor.configure(
                    do_autofocus=focus.enabled and focus.method == "contrast",
                    do_reflection_af=focus.enabled and focus.method == "laser",
                    fovs_per_af=focus.interval_fovs if focus.enabled else None,
                )

            # Configure skip_saving
            self._multipoint.update_config(skip_saving=imaging_config.skip_saving)

            # Set selected channels
            if hasattr(self._multipoint, "set_selected_configurations"):
                self._multipoint.set_selected_configurations(channel_names)

            # Apply channel overrides (snapshot for restore)
            override_objective, override_snapshot = self._snapshot_channel_overrides(
                imaging_config.channels
            )
            self._apply_channel_overrides(imaging_config.channels)

            # Create output directory and write acquisition config
            output_dir = os.path.join(output_path, experiment_id)
            os.makedirs(output_dir, exist_ok=True)
            self._write_acquisition_output(output_dir, channel_names)

            # Start the acquisition
            _log.info(
                f"Starting imaging: channels={channel_names}, "
                f"z_planes={imaging_config.z_stack.planes}, "
                f"focus={imaging_config.focus.method if imaging_config.focus.enabled else 'disabled'}"
            )
            self._multipoint.run_acquisition(acquire_current_fov=False)

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
            if override_snapshot and override_objective:
                self._restore_channel_overrides(override_objective, override_snapshot)
            self._current_experiment_id = None

    def _apply_channel_overrides(
        self,
        channels: List[Union[str, ChannelConfigOverride]],
    ) -> None:
        """Apply channel configuration overrides.

        For channels that are ChannelConfigOverride objects, apply the
        specified overrides to the ChannelConfigService.

        Args:
            channels: List of channel names or ChannelConfigOverride objects
        """
        if self._channel_config_manager is None:
            return

        # Get the current objective from multipoint controller
        current_objective = None
        if hasattr(self._multipoint, "objectiveStore") and self._multipoint.objectiveStore:
            current_objective = self._multipoint.objectiveStore.current_objective

        if current_objective is None:
            _log.warning("Cannot apply channel overrides: no current objective available")
            return

        # Convert ChannelConfigOverride objects to dicts for the manager
        overrides = []
        for ch in channels:
            if isinstance(ch, ChannelConfigOverride):
                override_dict = {"name": ch.name}
                if ch.exposure_time_ms is not None:
                    override_dict["exposure_time_ms"] = ch.exposure_time_ms
                if ch.analog_gain is not None:
                    override_dict["analog_gain"] = ch.analog_gain
                if ch.illumination_intensity is not None:
                    override_dict["illumination_intensity"] = ch.illumination_intensity
                if "z_offset_um" in ch.model_fields_set:
                    override_dict["z_offset_um"] = ch.z_offset_um
                overrides.append(override_dict)

        if overrides:
            try:
                self._channel_config_manager.apply_channel_overrides(current_objective, overrides)
                _log.debug(f"Applied {len(overrides)} channel override(s) for objective '{current_objective}'")
            except Exception as e:
                _log.warning(f"Failed to apply channel overrides: {e}")

    def _write_acquisition_output(self, output_dir: str, channel_names: List[str]) -> None:
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

        configs = []
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

    def _snapshot_channel_overrides(
        self,
        channels: List[Union[str, ChannelConfigOverride]],
    ) -> tuple[Optional[str], List[tuple[str, str, float]]]:
        """Capture original channel settings so overrides can be restored."""
        if self._channel_config_manager is None:
            return None, []

        current_objective = None
        if hasattr(self._multipoint, "objectiveStore") and self._multipoint.objectiveStore:
            current_objective = self._multipoint.objectiveStore.current_objective

        if current_objective is None:
            _log.warning("Cannot snapshot channel overrides: no current objective available")
            return None, []

        snapshots: List[tuple[str, str, float]] = []
        for ch in channels:
            if not isinstance(ch, ChannelConfigOverride):
                continue
            config = self._channel_config_manager.get_channel_configuration_by_name(
                current_objective, ch.name
            )
            if config is None:
                continue
            if ch.exposure_time_ms is not None:
                snapshots.append((ch.name, "ExposureTime", float(config.exposure_time)))
            if ch.analog_gain is not None:
                snapshots.append((ch.name, "AnalogGain", float(config.analog_gain)))
            if ch.illumination_intensity is not None:
                snapshots.append((ch.name, "IlluminationIntensity", float(config.illumination_intensity)))
            if "z_offset_um" in ch.model_fields_set:
                snapshots.append((ch.name, "ZOffset", float(config.z_offset)))

        return current_objective, snapshots

    def _restore_channel_overrides(
        self,
        objective: str,
        snapshots: List[tuple[str, str, float]],
    ) -> None:
        """Restore original channel settings after overrides."""
        if self._channel_config_manager is None:
            return
        for channel_name, attr_name, value in snapshots:
            try:
                self._channel_config_manager.update_configuration(
                    objective, channel_name, attr_name, value
                )
            except Exception as e:
                _log.warning(
                    "Failed to restore override for '%s' (%s): %s",
                    channel_name,
                    attr_name,
                    e,
                )

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
