"""
Protocol validation for experiment orchestration.

Validates protocols before execution and provides time/disk estimates.
"""

from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import squid.core.logging
from squid.core.protocol import (
    ExperimentProtocol,
    FluidicsCommand,
    FluidicsStep,
    ImagingStep,
    Round,
)
from squid.backend.controllers.orchestrator.validation import (
    DEFAULT_DISK_ESTIMATES,
    DEFAULT_TIMING_ESTIMATES,
    OperationEstimate,
    ValidationSummary,
)

if TYPE_CHECKING:
    from squid.backend.managers import ChannelConfigurationManager

_log = squid.core.logging.get_logger(__name__)


class ProtocolValidator:
    """Validates experiment protocols before execution.

    Performs pre-flight validation including:
    - Channel availability checks
    - Time estimation per round and total
    - Disk space estimation
    - Configuration consistency checks
    """

    def __init__(
        self,
        available_channels: Optional[Set[str]] = None,
        timing_estimates: Optional[Dict[str, float]] = None,
        disk_estimates: Optional[Dict[str, Any]] = None,
        camera_resolution: Tuple[int, int] = (2048, 2048),
    ):
        """Initialize the protocol validator.

        Args:
            available_channels: Set of channel names available on this microscope.
                If None, channel validation is skipped.
            timing_estimates: Custom timing estimates (overrides defaults).
            disk_estimates: Custom disk usage estimates (overrides defaults).
            camera_resolution: Camera resolution (width, height) for disk estimation.
        """
        self._available_channels = available_channels
        self._timing = {**DEFAULT_TIMING_ESTIMATES, **(timing_estimates or {})}
        self._disk = {**DEFAULT_DISK_ESTIMATES, **(disk_estimates or {})}
        self._camera_resolution = camera_resolution

    @classmethod
    def from_channel_manager(
        cls,
        channel_manager: "ChannelConfigurationManager",
        **kwargs,
    ) -> "ProtocolValidator":
        """Create a validator using channels from a ChannelConfigurationManager.

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
        """Validate a protocol and estimate time/disk usage.

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

        # Validate default channels
        default_channels = protocol.defaults.imaging.channels
        if self._available_channels and default_channels:
            missing = set(default_channels) - self._available_channels
            if missing:
                errors.append(
                    f"Default channels not available: {', '.join(sorted(missing))}"
                )

        # Validate each round
        for round_idx, round_ in enumerate(protocol.rounds):
            round_with_defaults = protocol.apply_defaults_to_round(round_)
            round_estimates = self._validate_round(
                round_with_defaults, round_idx, fov_count
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
        if total_bytes > 500 * (1024 ** 3):  # More than 500 GB
            gb = total_bytes / (1024 ** 3)
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
        round_: Round,
        round_idx: int,
        fov_count: int,
    ) -> List[OperationEstimate]:
        """Validate a single round and return operation estimates.

        Args:
            round_: The round to validate (with defaults applied).
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

        # Validate and estimate fluidics
        if round_.fluidics:
            fluidics_estimate = self._validate_fluidics(
                round_.fluidics, round_idx, round_.name
            )
            estimates.append(fluidics_estimate)

        # Validate and estimate imaging
        if round_.imaging:
            imaging_estimate = self._validate_imaging(
                round_.imaging, round_idx, round_.name, fov_count
            )
            estimates.append(imaging_estimate)

        # Intervention estimate
        if round_.requires_intervention:
            estimates.append(
                OperationEstimate(
                    operation_type="intervention",
                    round_index=round_idx,
                    round_name=round_.name,
                    description=round_.intervention_message or "Operator intervention",
                    estimated_seconds=self._timing["intervention_wait_seconds"],
                )
            )

        return estimates

    def _validate_fluidics(
        self,
        steps: List[FluidicsStep],
        round_idx: int,
        round_name: str,
    ) -> OperationEstimate:
        """Validate fluidics steps and estimate time.

        Args:
            steps: List of fluidics steps.
            round_idx: Index of the round.
            round_name: Name of the round.

        Returns:
            OperationEstimate for fluidics operations.
        """
        errors: List[str] = []
        warnings: List[str] = []
        total_seconds = 0.0

        for step_idx, step in enumerate(steps):
            step_time = self._estimate_fluidics_step_time(step)
            total_seconds += step_time * step.repeats

            # Validate step configuration
            if step.command in (FluidicsCommand.FLOW, FluidicsCommand.WASH):
                if step.volume_ul is None:
                    errors.append(
                        f"Round '{round_name}' fluidics step {step_idx + 1}: "
                        f"{step.command.value} requires volume_ul"
                    )
                if step.solution is None:
                    warnings.append(
                        f"Round '{round_name}' fluidics step {step_idx + 1}: "
                        f"no solution specified"
                    )

            if step.command == FluidicsCommand.INCUBATE:
                if step.duration_s is None:
                    errors.append(
                        f"Round '{round_name}' fluidics step {step_idx + 1}: "
                        f"incubate requires duration_s"
                    )

        description = f"{len(steps)} fluidics step(s)"

        return OperationEstimate(
            operation_type="fluidics",
            round_index=round_idx,
            round_name=round_name,
            description=description,
            estimated_seconds=total_seconds,
            estimated_disk_bytes=0,
            valid=len(errors) == 0,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
        )

    def _estimate_fluidics_step_time(self, step: FluidicsStep) -> float:
        """Estimate time for a single fluidics step.

        Args:
            step: The fluidics step.

        Returns:
            Estimated time in seconds.
        """
        if step.command == FluidicsCommand.INCUBATE:
            return step.duration_s or 0.0

        if step.command == FluidicsCommand.PRIME:
            return self._timing["fluidics_prime_seconds"]

        if step.command == FluidicsCommand.WASH:
            return self._timing["fluidics_wash_seconds"]

        if step.command in (FluidicsCommand.FLOW, FluidicsCommand.ASPIRATE):
            if step.volume_ul and step.flow_rate_ul_per_min:
                return (step.volume_ul / step.flow_rate_ul_per_min) * 60.0
            return self._timing["fluidics_per_step_seconds"]

        return self._timing["fluidics_per_step_seconds"]

    def _validate_imaging(
        self,
        imaging: ImagingStep,
        round_idx: int,
        round_name: str,
        fov_count: int,
    ) -> OperationEstimate:
        """Validate imaging configuration and estimate time/disk.

        Args:
            imaging: The imaging configuration.
            round_idx: Index of the round.
            round_name: Name of the round.
            fov_count: Number of FOVs to image.

        Returns:
            OperationEstimate for imaging operations.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Validate channels
        if not imaging.channels:
            warnings.append(f"Round '{round_name}': no imaging channels specified")
        elif self._available_channels:
            missing = set(imaging.channels) - self._available_channels
            if missing:
                errors.append(
                    f"Round '{round_name}': channels not available: "
                    f"{', '.join(sorted(missing))}"
                )

        # Estimate time
        time_seconds = self._estimate_imaging_time(imaging, fov_count)

        # Estimate disk usage
        disk_bytes = 0
        if not imaging.skip_saving:
            disk_bytes = self._estimate_imaging_disk(imaging, fov_count)

        description = (
            f"{len(imaging.channels)} channel(s), "
            f"{imaging.z_planes} z-plane(s), "
            f"{fov_count} FOV(s)"
        )

        return OperationEstimate(
            operation_type="imaging",
            round_index=round_idx,
            round_name=round_name,
            description=description,
            estimated_seconds=time_seconds,
            estimated_disk_bytes=disk_bytes,
            valid=len(errors) == 0,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
        )

    def _estimate_imaging_time(
        self,
        imaging: ImagingStep,
        fov_count: int,
    ) -> float:
        """Estimate imaging time for a round.

        Args:
            imaging: The imaging configuration.
            fov_count: Number of FOVs.

        Returns:
            Estimated time in seconds.
        """
        num_channels = len(imaging.channels)
        num_z = imaging.z_planes

        # Time per FOV
        time_per_fov = self._timing["stage_move_seconds"]

        # Autofocus time
        if imaging.use_autofocus:
            time_per_fov += self._timing["autofocus_seconds"]
        elif imaging.use_focus_lock:
            time_per_fov += self._timing["laser_autofocus_seconds"]

        # Time per channel
        for _ in range(num_channels):
            time_per_fov += self._timing["channel_switch_seconds"]

            # Time per z-plane
            exposure_time = (imaging.exposure_time_ms or 50.0) / 1000.0
            time_per_fov += num_z * (
                exposure_time + self._timing["exposure_overhead_seconds"]
            )

        return time_per_fov * fov_count

    def _estimate_imaging_disk(
        self,
        imaging: ImagingStep,
        fov_count: int,
    ) -> int:
        """Estimate disk usage for imaging.

        Args:
            imaging: The imaging configuration.
            fov_count: Number of FOVs.

        Returns:
            Estimated disk usage in bytes.
        """
        num_channels = len(imaging.channels)
        num_z = imaging.z_planes

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
        if total_bytes >= 1024 ** 3:
            return f"{total_bytes / (1024 ** 3):.1f} GB"
        elif total_bytes >= 1024 ** 2:
            return f"{total_bytes / (1024 ** 2):.1f} MB"
        elif total_bytes >= 1024:
            return f"{total_bytes / 1024:.1f} KB"
        else:
            return f"{total_bytes} B"
