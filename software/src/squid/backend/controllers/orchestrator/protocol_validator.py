"""
Protocol validation for experiment orchestration (V2).

Validates protocols before execution and provides time/disk estimates.
Supports V2 step-based protocol format.
"""

from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import squid.core.logging
from squid.core.events import AutofocusMode
from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    FluidicsStep,
    ImagingStep as ImagingStepV2,
    InterventionStep,
    ImagingProtocol,
)
from squid.backend.controllers.orchestrator.validation import (
    DEFAULT_DISK_ESTIMATES,
    DEFAULT_TIMING_ESTIMATES,
    OperationEstimate,
    ValidationSummary,
)

if TYPE_CHECKING:
    from squid.backend.managers import ChannelConfigService

_log = squid.core.logging.get_logger(__name__)


class ProtocolValidator:
    """Validates V2 experiment protocols before execution.

    Performs pre-flight validation including:
    - Channel availability checks (via imaging_protocols)
    - Fluidics protocol availability checks
    - Time estimation per round and total
    - Disk space estimation
    - Configuration consistency checks
    """

    def __init__(
        self,
        available_channels: Optional[Set[str]] = None,
        available_fluidics_protocols: Optional[Set[str]] = None,
        fluidics_duration_lookup: Optional[Callable[[str], Optional[float]]] = None,
        timing_estimates: Optional[Dict[str, float]] = None,
        disk_estimates: Optional[Dict[str, Any]] = None,
        camera_resolution: Tuple[int, int] = (2048, 2048),
    ):
        """Initialize the protocol validator.

        Args:
            available_channels: Set of channel names available on this microscope.
                If None, channel validation is skipped.
            available_fluidics_protocols: Set of fluidics protocol names that have
                been loaded. If None, fluidics protocol validation is skipped.
            fluidics_duration_lookup: Callable that takes a protocol name and returns
                estimated duration in seconds, or None if unknown. Typically
                FluidicsController.estimate_protocol_duration.
            timing_estimates: Custom timing estimates (overrides defaults).
            disk_estimates: Custom disk usage estimates (overrides defaults).
            camera_resolution: Camera resolution (width, height) for disk estimation.
        """
        self._available_channels = available_channels
        self._available_fluidics_protocols = available_fluidics_protocols
        self._fluidics_duration_lookup = fluidics_duration_lookup
        self._timing = {**DEFAULT_TIMING_ESTIMATES, **(timing_estimates or {})}
        self._disk = {**DEFAULT_DISK_ESTIMATES, **(disk_estimates or {})}
        self._camera_resolution = camera_resolution

    @classmethod
    def from_channel_manager(
        cls,
        channel_manager: "ChannelConfigService",
        **kwargs,
    ) -> "ProtocolValidator":
        """Create a validator using channels from a ChannelConfigService.

        Args:
            channel_manager: The channel configuration manager.
            **kwargs: Additional arguments passed to __init__.
        """
        available: Set[str] = set()
        if channel_manager.channel_definitions:
            available = {ch.name for ch in channel_manager.channel_definitions.channels}
        return cls(available_channels=available, **kwargs)

    def validate(
        self,
        protocol: ExperimentProtocol,
        fov_count: int = 1,
    ) -> ValidationSummary:
        """Validate a V2 protocol and estimate time/disk usage.

        Args:
            protocol: The experiment protocol to validate.
            fov_count: Number of fields of view to image.

        Returns:
            ValidationSummary with validation results and estimates.
        """
        errors: List[str] = []
        warnings: List[str] = []
        operation_estimates: List[OperationEstimate] = []

        # Validate protocol has rounds
        if not protocol.rounds:
            errors.append("Protocol has no rounds defined")
            return ValidationSummary.create_error(protocol.name, errors[0])

        # Validate named resources exist
        ref_errors = protocol.validate_references()
        errors.extend(ref_errors)

        # Inline fluidics protocols are not allowed
        if protocol.fluidics_protocols:
            errors.append(
                "Inline fluidics_protocols are not allowed. "
                "Load protocols into FluidicsController separately and reference by name."
            )

        # Validate channels in imaging_protocols
        if self._available_channels is not None:
            for config_name, config in protocol.imaging_protocols.items():
                channel_names = config.get_channel_names()
                missing = set(channel_names) - self._available_channels
                if missing:
                    errors.append(
                        f"Imaging config '{config_name}' channels not available: "
                        f"{', '.join(sorted(missing))}"
                    )

        # Validate each round
        for round_idx, round_ in enumerate(protocol.rounds):
            if not round_.steps:
                errors.append(f"Round '{round_.name}' has no steps")
                continue
            round_estimates = self._validate_round(
                protocol, round_, round_idx, fov_count
            )
            operation_estimates.extend(round_estimates)

        # Collect errors and warnings from operation estimates
        for op in operation_estimates:
            errors.extend(op.validation_errors)
            warnings.extend(op.validation_warnings)

        # Calculate totals
        total_seconds = sum(op.estimated_seconds for op in operation_estimates)
        total_bytes = sum(op.estimated_disk_bytes for op in operation_estimates)

        # Add warning if experiment is very long
        if total_seconds > 24 * 3600:  # More than 24 hours
            hours = total_seconds / 3600
            warnings.append(f"Experiment estimated to take {hours:.1f} hours")

        # Add warning if disk usage is very high
        if total_bytes > 500 * (1024**3):  # More than 500 GB
            gb = total_bytes / (1024**3)
            warnings.append(f"Estimated disk usage is {gb:.1f} GB")

        return ValidationSummary(
            protocol_name=protocol.name,
            total_rounds=len(protocol.rounds),
            total_estimated_seconds=total_seconds,
            total_disk_bytes=total_bytes,
            operation_estimates=tuple(operation_estimates),
            errors=tuple(errors),
            warnings=tuple(warnings),
            valid=len(errors) == 0,
        )

    def _validate_round(
        self,
        protocol: ExperimentProtocol,
        round_: Round,
        round_idx: int,
        fov_count: int,
    ) -> List[OperationEstimate]:
        """Validate a single V2 round and return operation estimates.

        Args:
            protocol: The full protocol (for accessing named resources).
            round_: The round to validate.
            round_idx: Index of the round (0-based).
            fov_count: Number of FOVs to image.

        Returns:
            List of OperationEstimate for operations in this round.
        """
        estimates: List[OperationEstimate] = []

        # Round setup overhead
        estimates.append(
            OperationEstimate(
                operation_type="setup",
                round_index=round_idx,
                round_name=round_.name,
                description=f"Round {round_idx + 1} setup",
                estimated_seconds=self._timing["round_setup_seconds"],
            )
        )

        # Validate each step in the round
        for step_idx, step in enumerate(round_.steps):
            if isinstance(step, FluidicsStep):
                fluidics_estimate = self._validate_fluidics_step(
                    step, round_idx, step_idx, round_.name
                )
                estimates.append(fluidics_estimate)

            elif isinstance(step, ImagingStepV2):
                imaging_estimate = self._validate_imaging_step(
                    protocol, step, round_idx, step_idx, round_.name, fov_count
                )
                estimates.append(imaging_estimate)

            elif isinstance(step, InterventionStep):
                estimates.append(
                    OperationEstimate(
                        operation_type="intervention",
                        round_index=round_idx,
                        round_name=round_.name,
                        description=step.message or "Operator intervention",
                        estimated_seconds=self._timing["intervention_wait_seconds"],
                        step_index=step_idx,
                    )
                )

        return estimates

    def _validate_fluidics_step(
        self,
        step: FluidicsStep,
        round_idx: int,
        step_idx: int,
        round_name: str,
    ) -> OperationEstimate:
        """Validate a V2 fluidics step and estimate time.

        Args:
            step: The FluidicsStep to validate.
            round_idx: Index of the round.
            step_idx: Index of the step within the round.
            round_name: Name of the round.

        Returns:
            OperationEstimate for fluidics operations.
        """
        errors: List[str] = []
        warnings: List[str] = []

        protocol_name = step.protocol

        # Validate protocol exists in loaded protocols (skip if availability unknown)
        if self._available_fluidics_protocols is not None:
            available = self._available_fluidics_protocols
            if protocol_name not in available:
                errors.append(
                    f"Round '{round_name}': Fluidics protocol '{protocol_name}' not loaded. "
                    f"Load protocols via FluidicsController before validation. "
                    f"Available: {', '.join(sorted(available)) or 'none'}"
                )

        # Estimate time from loaded fluidics protocols, fall back to default
        estimated_seconds = self._timing.get("fluidics_per_step_seconds", 60.0)
        if self._fluidics_duration_lookup is not None:
            duration = self._fluidics_duration_lookup(protocol_name)
            if duration is not None:
                estimated_seconds = duration

        description = f"Fluidics protocol: {protocol_name}"

        return OperationEstimate(
            operation_type="fluidics",
            round_index=round_idx,
            round_name=round_name,
            description=description,
            estimated_seconds=estimated_seconds,
            estimated_disk_bytes=0,
            step_index=step_idx,
            valid=len(errors) == 0,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
        )

    def _validate_imaging_step(
        self,
        protocol: ExperimentProtocol,
        step: ImagingStepV2,
        round_idx: int,
        step_idx: int,
        round_name: str,
        fov_count: int,
    ) -> OperationEstimate:
        """Validate a V2 imaging step and estimate time/disk.

        Args:
            protocol: The full protocol (for accessing named resources).
            step: The ImagingStep to validate.
            round_idx: Index of the round.
            step_idx: Index of the step within the round.
            round_name: Name of the round.
            fov_count: Number of FOVs to image.

        Returns:
            OperationEstimate for imaging operations.
        """
        errors: List[str] = []
        warnings: List[str] = []

        config_name = step.protocol

        # Validate imaging protocol exists
        if config_name not in protocol.imaging_protocols:
            errors.append(
                f"Round '{round_name}': Imaging protocol '{config_name}' not found"
            )
            return OperationEstimate(
                operation_type="imaging",
                round_index=round_idx,
                round_name=round_name,
                description=f"Unknown imaging protocol: {config_name}",
                estimated_seconds=0,
                estimated_disk_bytes=0,
                step_index=step_idx,
                valid=False,
                validation_errors=tuple(errors),
                validation_warnings=tuple(warnings),
            )

        imaging_config = protocol.imaging_protocols[config_name]

        # Validate FOV set if specified
        if step.fovs not in ("current", "default") and step.fovs not in protocol.fov_sets:
            errors.append(
                f"Round '{round_name}': FOV set '{step.fovs}' not found"
            )

        # Validate channels if we have available channels
        channel_names = imaging_config.get_channel_names()
        if not channel_names:
            warnings.append(
                f"Round '{round_name}': imaging config '{config_name}' has no channels"
            )
        elif self._available_channels is not None:
            missing = set(channel_names) - self._available_channels
            if missing:
                errors.append(
                    f"Round '{round_name}': channels not available: "
                    f"{', '.join(sorted(missing))}"
                )

        # Estimate time
        time_seconds = self._estimate_imaging_time(imaging_config, fov_count)

        # Estimate disk usage
        disk_bytes = 0
        if not imaging_config.skip_saving:
            disk_bytes = self._estimate_imaging_disk(imaging_config, fov_count)

        description = (
            f"Config: {config_name}, "
            f"{len(channel_names)} channel(s), "
            f"{imaging_config.z_stack.planes} z-plane(s), "
            f"{fov_count} FOV(s)"
        )

        return OperationEstimate(
            operation_type="imaging",
            round_index=round_idx,
            round_name=round_name,
            description=description,
            estimated_seconds=time_seconds,
            estimated_disk_bytes=disk_bytes,
            step_index=step_idx,
            valid=len(errors) == 0,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
        )

    def _estimate_imaging_time(
        self,
        imaging_config: ImagingProtocol,
        fov_count: int,
    ) -> float:
        """Estimate imaging time for an imaging config.

        Args:
            imaging_config: The imaging configuration.
            fov_count: Number of FOVs.

        Returns:
            Estimated time in seconds.
        """
        num_channels = len(imaging_config.get_channel_names())
        num_z = imaging_config.z_stack.planes

        # Time per FOV
        time_per_fov = self._timing["stage_move_seconds"]

        # Autofocus time
        focus_mode = imaging_config.focus.mode
        if focus_mode == AutofocusMode.LASER_REFLECTION:
            time_per_fov += self._timing["laser_autofocus_seconds"]
        elif focus_mode == AutofocusMode.CONTRAST:
            time_per_fov += self._timing["autofocus_seconds"]

        # Time per channel
        for _ in range(num_channels):
            time_per_fov += self._timing["channel_switch_seconds"]

            # Time per z-plane (use default exposure time estimate)
            exposure_time = 50.0 / 1000.0  # Default 50ms
            time_per_fov += num_z * (
                exposure_time + self._timing["exposure_overhead_seconds"]
            )

        return time_per_fov * fov_count

    def _estimate_imaging_disk(
        self,
        imaging_config: ImagingProtocol,
        fov_count: int,
    ) -> int:
        """Estimate disk usage for imaging.

        Args:
            imaging_config: The imaging configuration.
            fov_count: Number of FOVs.

        Returns:
            Estimated disk usage in bytes.
        """
        num_channels = len(imaging_config.get_channel_names())
        num_z = imaging_config.z_stack.planes

        # Pixels per image
        width, height = self._camera_resolution
        pixels_per_image = width * height

        # Bytes per image (assuming grayscale)
        bytes_per_pixel = self._disk["bytes_per_pixel_mono"]
        bytes_per_image = int(
            pixels_per_image * bytes_per_pixel * self._disk["compression_ratio"]
        )

        # Total images
        total_images = fov_count * num_channels * num_z

        # Total bytes including metadata
        total_bytes = (
            total_images * bytes_per_image
            + fov_count * self._disk["metadata_bytes_per_fov"]
        )

        return total_bytes

    def estimate_time_formatted(self, total_seconds: float) -> str:
        """Format estimated time as human-readable string.

        Args:
            total_seconds: Total time in seconds.

        Returns:
            Formatted string like "2h 30m" or "45m 30s".
        """
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def estimate_disk_formatted(self, total_bytes: int) -> str:
        """Format estimated disk usage as human-readable string.

        Args:
            total_bytes: Total disk usage in bytes.

        Returns:
            Formatted string like "1.5 GB" or "500 MB".
        """
        if total_bytes >= 1024**3:
            return f"{total_bytes / (1024 ** 3):.1f} GB"
        elif total_bytes >= 1024**2:
            return f"{total_bytes / (1024 ** 2):.1f} MB"
        elif total_bytes >= 1024:
            return f"{total_bytes / 1024:.1f} KB"
        else:
            return f"{total_bytes} B"
