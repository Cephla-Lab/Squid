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
import csv
import json
import math
import os
import threading
import time
from collections import defaultdict
from typing import Callable, List, Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.config.test_timing import scale_duration
from squid.core.events import (
    AutofocusMode,
    EventBus,
    FocusLockMetricsUpdated,
    FocusLockPiezoLimitCritical,
    FocusLockSettings,
    FocusLockStatusChanged,
    FocusLockWarning,
    handles,
    auto_subscribe,
    auto_unsubscribe,
)
from squid.core.events import AcquisitionFinished, AcquisitionProgress
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import ImagingProtocol, ChannelConfigOverride
from squid.backend.controllers.orchestrator.state import AddWarningCommand

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint import MultiPointController
    from squid.backend.managers.scan_coordinates import ScanCoordinates
    from squid.backend.managers.channel_config_service import ChannelConfigService
    from squid.core.config.models import AcquisitionChannel

_log = squid.core.logging.get_logger(__name__)


class _FocusLockRunMonitor:
    """Collect focus-lock QC during a single imaging round."""

    def __init__(
        self,
        event_bus: EventBus,
        output_dir: str,
        round_index: int,
        round_name: str,
    ) -> None:
        self._event_bus = event_bus
        self._output_dir = output_dir
        self._round_index = round_index
        self._round_name = round_name
        self._subscriptions: list[tuple[type, object]] = []
        self._current_fov_index: Optional[int] = None
        self._current_total_fovs: int = 0
        self._start_time = time.monotonic()
        self._last_status: Optional[str] = None
        self._last_status_change_time = self._start_time
        self._status_durations: dict[str, float] = defaultdict(float)
        self._status_transition_counts: dict[str, int] = defaultdict(int)
        self._warning_counts: dict[str, int] = defaultdict(int)
        self._rows: list[dict[str, object]] = []
        self._abs_errors: list[float] = []
        self._rms_errors: list[float] = []
        self._drift_rates: list[float] = []
        self._snr_values: list[float] = []
        self._lock_quality_values: list[float] = []
        self._good_readings = 0
        self._total_readings = 0
        self._last_sample_time = 0.0
        self._low_snr_streak = 0
        self._quality_low_started: Optional[float] = None
        self._recovering_started: Optional[float] = None
        self._alert_last_time: dict[str, float] = {}

    def start(self) -> None:
        for event_type, handler in (
            (FocusLockStatusChanged, self._on_status_changed),
            (FocusLockMetricsUpdated, self._on_metrics_updated),
            (FocusLockWarning, self._on_warning),
            (FocusLockPiezoLimitCritical, self._on_piezo_critical),
        ):
            self._event_bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))

    def stop(self) -> None:
        now = time.monotonic()
        if self._last_status is not None:
            self._status_durations[self._last_status] += max(0.0, now - self._last_status_change_time)
        for event_type, handler in self._subscriptions:
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._write_outputs()

    def update_progress(self, fov_index: int, total_fovs: int) -> None:
        self._current_fov_index = fov_index
        self._current_total_fovs = total_fovs

    def _record_row(self, event_type: str, **extra: object) -> None:
        row = {
            "timestamp_monotonic": time.monotonic(),
            "event_type": event_type,
            "round_index": self._round_index,
            "round_name": self._round_name,
            "fov_index": self._current_fov_index,
            "total_fovs": self._current_total_fovs,
        }
        row.update(extra)
        self._rows.append(row)

    def _publish_warning(self, key: str, severity: str, message: str, context: Optional[dict[str, object]] = None) -> None:
        now = time.monotonic()
        last_time = self._alert_last_time.get(key)
        if last_time is not None and now - last_time < 5.0:
            return
        self._alert_last_time[key] = now
        self._event_bus.publish(
            AddWarningCommand(
                category="FOCUS",
                severity=severity,
                message=message,
                round_index=self._round_index,
                round_name=self._round_name,
                operation_type="imaging",
                fov_index=self._current_fov_index,
                context=context,
            )
        )

    def _on_status_changed(self, event: FocusLockStatusChanged) -> None:
        now = time.monotonic()
        if self._last_status is not None:
            self._status_durations[self._last_status] += max(0.0, now - self._last_status_change_time)
        self._last_status = event.status
        self._last_status_change_time = now
        self._status_transition_counts[event.status] += 1
        self._record_row(
            "status",
            status=event.status,
            lock_buffer_fill=event.lock_buffer_fill,
            lock_buffer_length=event.lock_buffer_length,
        )
        if event.status in ("lost", "searching"):
            self._publish_warning(
                f"status:{event.status}",
                "HIGH",
                f"Focus lock transitioned to {event.status}",
                context={"status": event.status},
            )
        if event.status == "recovering":
            self._recovering_started = now
        else:
            self._recovering_started = None

    def _on_metrics_updated(self, event: FocusLockMetricsUpdated) -> None:
        self._total_readings += 1
        if event.is_good_reading:
            self._good_readings += 1
        if not isinstance(event.z_error_um, float) or not math.isnan(event.z_error_um):
            self._abs_errors.append(abs(event.z_error_um))
        if not math.isnan(event.z_error_rms_um):
            self._rms_errors.append(event.z_error_rms_um)
        if not math.isnan(event.drift_rate_um_per_s):
            self._drift_rates.append(abs(event.drift_rate_um_per_s))
        if not math.isnan(event.spot_snr):
            self._snr_values.append(event.spot_snr)
        if not math.isnan(event.lock_quality):
            self._lock_quality_values.append(event.lock_quality)

        now = time.monotonic()
        if now - self._last_sample_time >= 1.0:
            self._last_sample_time = now
            self._record_row(
                "metrics",
                z_error_um=event.z_error_um,
                z_position_um=event.z_position_um,
                spot_snr=event.spot_snr,
                z_error_rms_um=event.z_error_rms_um,
                drift_rate_um_per_s=event.drift_rate_um_per_s,
                correlation=event.correlation,
                lock_quality=event.lock_quality,
            )

        if not math.isnan(event.spot_snr) and event.spot_snr < 5.0:
            self._low_snr_streak += 1
            if self._low_snr_streak == 1:
                self._publish_warning("snr_low_start", "LOW", "Focus lock SNR dropped below 5")
            elif self._low_snr_streak >= 10:
                self._publish_warning("snr_low_sustained", "MEDIUM", "Focus lock SNR remained low for 10 samples")
        else:
            self._low_snr_streak = 0

        if not math.isnan(event.lock_quality) and event.lock_quality < 0.3:
            if self._quality_low_started is None:
                self._quality_low_started = now
            elif now - self._quality_low_started >= 2.0:
                self._publish_warning("quality_low", "MEDIUM", "Focus lock quality remained below 0.3")
        else:
            self._quality_low_started = None

        if self._recovering_started is not None and now - self._recovering_started >= 5.0:
            self._publish_warning("recovering_sustained", "MEDIUM", "Focus lock stayed in recovering state for more than 5s")

    def _on_warning(self, event: FocusLockWarning) -> None:
        self._warning_counts[event.warning_type] += 1
        self._record_row("warning", warning_type=event.warning_type, message=event.message)
        severity = "LOW"
        if event.warning_type in {"signal_lost", "measurement_stale"}:
            severity = "MEDIUM"
        self._publish_warning(
            f"warning:{event.warning_type}",
            severity,
            event.message,
            context={"warning_type": event.warning_type},
        )

    def _on_piezo_critical(self, event: FocusLockPiezoLimitCritical) -> None:
        self._record_row(
            "critical",
            direction=event.direction,
            position_um=event.position_um,
            limit_um=event.limit_um,
            margin_um=event.margin_um,
        )

    def _quantile(self, values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return ordered[index]

    def _write_outputs(self) -> None:
        os.makedirs(self._output_dir, exist_ok=True)
        summary = {
            "round_index": self._round_index,
            "round_name": self._round_name,
            "status_transition_counts": dict(self._status_transition_counts),
            "status_durations_s": {key: round(value, 3) for key, value in self._status_durations.items()},
            "warning_counts": dict(self._warning_counts),
            "max_abs_z_error_um": max(self._abs_errors) if self._abs_errors else 0.0,
            "p95_abs_z_error_um": self._quantile(self._abs_errors, 0.95),
            "p95_rms_error_um": self._quantile(self._rms_errors, 0.95),
            "max_drift_rate_um_per_s": max(self._drift_rates) if self._drift_rates else 0.0,
            "min_spot_snr": min(self._snr_values) if self._snr_values else 0.0,
            "average_lock_quality": (
                sum(self._lock_quality_values) / len(self._lock_quality_values)
                if self._lock_quality_values
                else 0.0
            ),
            "fraction_good_readings": (
                self._good_readings / self._total_readings if self._total_readings else 0.0
            ),
        }

        with open(os.path.join(self._output_dir, "focus_lock_summary.json"), "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)

        fieldnames = [
            "timestamp_monotonic",
            "event_type",
            "round_index",
            "round_name",
            "fov_index",
            "total_fovs",
            "status",
            "lock_buffer_fill",
            "lock_buffer_length",
            "warning_type",
            "message",
            "z_error_um",
            "z_position_um",
            "spot_snr",
            "z_error_rms_um",
            "drift_rate_um_per_s",
            "correlation",
            "lock_quality",
            "direction",
            "position_um",
            "limit_um",
            "margin_um",
        ]
        with open(os.path.join(self._output_dir, "focus_lock_timeseries.csv"), "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in self._rows:
                writer.writerow(row)


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
        self._focus_lock_monitor: Optional[_FocusLockRunMonitor] = None

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

    @property
    def last_error(self) -> Optional[str]:
        """Return the most recent acquisition error, if any."""
        return self._acquisition_error

    def execute_with_config(
        self,
        imaging_config: ImagingProtocol,
        output_path: str,
        cancel_token: CancelToken,
        round_index: int,
        resume_fov_index: int = 0,
        experiment_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, Optional[float]], None]] = None,
        acquire_current_fov: bool = False,
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
                    "zstack.use_piezo": True,
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
            if focus.mode == AutofocusMode.FOCUS_LOCK:
                self._focus_lock_monitor = _FocusLockRunMonitor(
                    event_bus=self._event_bus,
                    output_dir=output_dir,
                    round_index=round_index,
                    round_name=f"Round {round_index + 1}",
                )
                self._focus_lock_monitor.start()

            # Start the acquisition
            _log.info(
                f"Starting imaging: channels={channel_names}, "
                f"z_planes={imaging_config.z_stack.planes}, "
                f"acquisition_order={acquisition_order}, "
                f"focus={imaging_config.focus.mode.value}"
            )
            started = self._multipoint.run_acquisition(acquire_current_fov=acquire_current_fov)
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
            if self._focus_lock_monitor is not None:
                self._focus_lock_monitor.stop()
                self._focus_lock_monitor = None
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

        images_per_fov = max(self._images_per_fov, 1)
        current_image = max(event.current_fov, 1)
        fov_index = (current_image - 1) // images_per_fov
        total_images = max(event.total_fovs, 0)
        total_fovs = (
            (total_images + images_per_fov - 1) // images_per_fov
            if total_images > 0
            else 0
        )

        if self._focus_lock_monitor is not None:
            self._focus_lock_monitor.update_progress(fov_index, total_fovs)

        if self._progress_callback is None:
            return
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
