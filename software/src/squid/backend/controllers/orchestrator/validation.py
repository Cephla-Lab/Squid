"""
Protocol validation system for experiment orchestration.

Provides pre-flight validation of protocols including:
- Channel availability checks
- Time estimation
- Disk space estimation
- Configuration validation
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from squid.core.protocol import ExperimentProtocol, Round, ImagingStep


@dataclass(frozen=True)
class OperationEstimate:
    """Estimate for a single operation within a round.

    Attributes:
        operation_type: Type of operation ("imaging", "fluidics", "intervention")
        round_index: Index of the round (0-based)
        round_name: Name of the round
        description: Human-readable description of the operation
        estimated_seconds: Estimated time in seconds
        estimated_disk_bytes: Estimated disk usage in bytes
        valid: Whether this operation is valid
        validation_errors: List of validation errors
        validation_warnings: List of validation warnings
    """

    operation_type: str
    round_index: int
    round_name: str
    description: str
    estimated_seconds: float = 0.0
    estimated_disk_bytes: int = 0
    step_index: int = -1  # Step index within round (-1 for setup overhead)
    valid: bool = True
    validation_errors: Tuple[str, ...] = ()
    validation_warnings: Tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        """Check if this operation has validation errors."""
        return len(self.validation_errors) > 0

    @property
    def has_warnings(self) -> bool:
        """Check if this operation has validation warnings."""
        return len(self.validation_warnings) > 0


@dataclass(frozen=True)
class ValidationSummary:
    """Summary of protocol validation results.

    Attributes:
        protocol_name: Name of the validated protocol
        total_rounds: Total number of rounds in protocol
        total_estimated_seconds: Total estimated time in seconds
        total_disk_bytes: Total estimated disk usage in bytes
        operation_estimates: Per-operation estimates
        errors: Global validation errors
        warnings: Global validation warnings
        valid: Whether the protocol is valid for execution
    """

    protocol_name: str
    total_rounds: int
    total_estimated_seconds: float
    total_disk_bytes: int
    operation_estimates: Tuple[OperationEstimate, ...]
    errors: Tuple[str, ...]
    warnings: Tuple[str, ...]
    valid: bool

    @property
    def estimated_hours(self) -> float:
        """Get estimated time in hours."""
        return self.total_estimated_seconds / 3600.0

    @property
    def estimated_disk_gb(self) -> float:
        """Get estimated disk usage in GB."""
        return self.total_disk_bytes / (1024 ** 3)

    @property
    def has_errors(self) -> bool:
        """Check if validation found any errors."""
        return len(self.errors) > 0 or any(op.has_errors for op in self.operation_estimates)

    @property
    def has_warnings(self) -> bool:
        """Check if validation found any warnings."""
        return len(self.warnings) > 0 or any(op.has_warnings for op in self.operation_estimates)

    def get_errors_for_round(self, round_index: int) -> List[str]:
        """Get all errors for a specific round."""
        errors = []
        for op in self.operation_estimates:
            if op.round_index == round_index:
                errors.extend(op.validation_errors)
        return errors

    def get_warnings_for_round(self, round_index: int) -> List[str]:
        """Get all warnings for a specific round."""
        warnings = []
        for op in self.operation_estimates:
            if op.round_index == round_index:
                warnings.extend(op.validation_warnings)
        return warnings

    @classmethod
    def create_empty(cls, protocol_name: str = "") -> "ValidationSummary":
        """Create an empty validation summary."""
        return cls(
            protocol_name=protocol_name,
            total_rounds=0,
            total_estimated_seconds=0.0,
            total_disk_bytes=0,
            operation_estimates=(),
            errors=(),
            warnings=(),
            valid=True,
        )

    @classmethod
    def create_error(cls, protocol_name: str, error: str) -> "ValidationSummary":
        """Create a validation summary with a single error."""
        return cls(
            protocol_name=protocol_name,
            total_rounds=0,
            total_estimated_seconds=0.0,
            total_disk_bytes=0,
            operation_estimates=(),
            errors=(error,),
            warnings=(),
            valid=False,
        )


# Default timing estimates (can be overridden)
DEFAULT_TIMING_ESTIMATES = {
    # Imaging timing (per FOV)
    "stage_move_seconds": 0.3,  # Time to move stage between FOVs
    "autofocus_seconds": 2.0,  # Time for software autofocus
    "laser_autofocus_seconds": 0.5,  # Time for laser autofocus
    "channel_switch_seconds": 0.2,  # Time to switch channels
    "exposure_overhead_seconds": 0.1,  # Camera overhead per exposure

    # Fluidics timing
    "fluidics_prime_seconds": 30.0,  # Time to prime fluidics
    "fluidics_per_step_seconds": 60.0,  # Default time per fluidics step
    "fluidics_wash_seconds": 45.0,  # Time for wash step

    # System timing
    "round_setup_seconds": 5.0,  # Overhead per round
    "intervention_wait_seconds": 0.0,  # No default wait for interventions
}

# Default disk usage estimates
DEFAULT_DISK_ESTIMATES = {
    "bytes_per_pixel_mono": 2,  # 16-bit grayscale
    "bytes_per_pixel_color": 6,  # 16-bit RGB
    "compression_ratio": 0.7,  # Typical TIFF compression
    "metadata_bytes_per_fov": 4096,  # JSON metadata per FOV
}
