"""
Protocol schema definitions for experiment orchestration.

Defines the YAML structure for multi-round fluidics-imaging experiments.
Uses Pydantic for validation and type safety.

Example protocol:
    name: "10-round FISH"
    version: "1.0"
    description: "10-round fluorescence in situ hybridization"

    defaults:
      imaging:
        channels: ["DAPI", "Cy3", "Cy5"]
        z_planes: 5
        z_step_um: 0.5

    rounds:
      - name: "Round 1"
        fluidics_protocol: "probe_delivery_1"  # References named protocol
        imaging:
          channels: ["DAPI", "Cy3"]

      - name: "Wash"
        type: "wash"
        fluidics_protocol: "standard_wash"  # References named protocol
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class RoundType(str, Enum):
    """Type of experimental round."""

    IMAGING = "imaging"  # Standard imaging round
    WASH = "wash"  # Wash/rinse step (may skip imaging)
    BLEACH = "bleach"  # Photobleaching step
    CUSTOM = "custom"  # User-defined step


class FluidicsCommand(str, Enum):
    """Fluidics operation commands."""

    FLOW = "flow"  # Flow solution through chamber
    ASPIRATE = "aspirate"  # Remove solution
    INCUBATE = "incubate"  # Wait with solution in place
    PRIME = "prime"  # Prime tubing
    WASH = "wash"  # Wash step (flow + aspirate)


class FluidicsStep(BaseModel):
    """A single fluidics operation.

    Attributes:
        command: The fluidics command to execute
        solution: Name/ID of the solution (for flow/wash)
        volume_ul: Volume in microliters
        flow_rate_ul_per_min: Flow rate in microliters per minute
        duration_s: Duration in seconds (for incubate)
        repeats: Number of times to repeat this step
    """

    command: FluidicsCommand
    solution: Optional[str] = None
    volume_ul: Optional[float] = None
    flow_rate_ul_per_min: Optional[float] = None
    duration_s: Optional[float] = None
    repeats: int = 1

    @field_validator("repeats")
    @classmethod
    def validate_repeats(cls, v: int) -> int:
        if v < 1:
            raise ValueError("repeats must be >= 1")
        return v


class ImagingStep(BaseModel):
    """Imaging configuration for a round.

    Attributes:
        channels: List of channel names to acquire
        z_planes: Number of z-planes for z-stack
        z_step_um: Z step size in microns
        use_autofocus: Whether to run autofocus
        use_focus_lock: Whether to use hardware focus lock
        exposure_time_ms: Override exposure time (uses channel default if None)
        skip_saving: If True, don't save images (useful for preview rounds)
    """

    channels: List[str] = Field(default_factory=list)
    z_planes: int = 1
    z_step_um: float = 0.5
    use_autofocus: bool = False
    use_focus_lock: bool = True
    exposure_time_ms: Optional[float] = None
    skip_saving: bool = False

    @field_validator("z_planes")
    @classmethod
    def validate_z_planes(cls, v: int) -> int:
        if v < 1:
            raise ValueError("z_planes must be >= 1")
        return v

    @field_validator("z_step_um")
    @classmethod
    def validate_z_step(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("z_step_um must be > 0")
        return v


class Round(BaseModel):
    """A single experimental round.

    A round typically consists of:
    1. Optional fluidics protocol (buffer exchange, staining, etc.)
    2. Optional imaging acquisition

    Attributes:
        name: Human-readable name for the round
        type: Type of round (imaging, wash, bleach, custom)
        fluidics_protocol: Name of the fluidics protocol to run (from fluidics_protocols.yaml)
        imaging: Imaging configuration (None to skip imaging)
        requires_intervention: If True, pause for operator intervention
        intervention_message: Message to show during intervention pause
        metadata: Additional round-specific metadata
    """

    name: str
    type: RoundType = RoundType.IMAGING
    fluidics_protocol: Optional[str] = None
    imaging: Optional[ImagingStep] = None
    requires_intervention: bool = False
    intervention_message: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ImagingDefaults(BaseModel):
    """Default imaging settings applied to all rounds."""

    channels: List[str] = Field(default_factory=list)
    z_planes: int = 1
    z_step_um: float = 0.5
    use_autofocus: bool = False
    use_focus_lock: bool = True


class FluidicsDefaults(BaseModel):
    """Default fluidics settings applied to all rounds."""

    flow_rate_ul_per_min: float = 50.0
    wash_volume_ul: float = 500.0


class ProtocolDefaults(BaseModel):
    """Default settings for the protocol."""

    imaging: ImagingDefaults = Field(default_factory=ImagingDefaults)
    fluidics: FluidicsDefaults = Field(default_factory=FluidicsDefaults)


class ExperimentProtocol(BaseModel):
    """Complete experiment protocol definition.

    Attributes:
        name: Human-readable protocol name
        version: Protocol version string
        description: Detailed description of the protocol
        author: Protocol author
        created: Creation date string
        defaults: Default settings for imaging and fluidics
        rounds: List of experimental rounds
        fov_positions_file: Optional path to CSV file with FOV positions
            CSV format: region_id,x_mm,y_mm,z_mm (one FOV per row)
            If specified, FOVs are loaded from this file at protocol load time.
            If not specified, FOVs must be loaded via GUI before starting.
        output_directory: Optional default output directory for experiment data
        metadata: Additional protocol-level metadata
    """

    name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    created: str = ""
    defaults: ProtocolDefaults = Field(default_factory=ProtocolDefaults)
    rounds: List[Round] = Field(default_factory=list)
    fov_positions_file: Optional[str] = None
    fluidics_protocols_file: Optional[str] = None
    output_directory: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("rounds")
    @classmethod
    def validate_rounds(cls, v: List[Round]) -> List[Round]:
        if not v:
            raise ValueError("Protocol must have at least one round")
        return v

    def get_round_by_name(self, name: str) -> Optional[Round]:
        """Find a round by name."""
        for round_ in self.rounds:
            if round_.name == name:
                return round_
        return None

    def get_imaging_rounds(self) -> List[Round]:
        """Get all rounds that include imaging."""
        return [r for r in self.rounds if r.imaging is not None]

    def total_imaging_rounds(self) -> int:
        """Count rounds that include imaging."""
        return len(self.get_imaging_rounds())

    def apply_defaults_to_round(self, round_: Round) -> Round:
        """Apply protocol defaults to a round.

        Returns a new Round with defaults applied where values were not specified.
        Note: Fluidics defaults are now applied by the FluidicsController when
        executing named protocols.
        """
        merged_imaging = None
        if round_.imaging is not None:
            defaults = self.defaults.imaging
            imaging = round_.imaging
            imaging_fields = imaging.model_fields_set

            merged_imaging = ImagingStep(
                channels=(
                    imaging.channels
                    if "channels" in imaging_fields and imaging.channels
                    else defaults.channels
                ),
                z_planes=(
                    imaging.z_planes
                    if "z_planes" in imaging_fields
                    else defaults.z_planes
                ),
                z_step_um=(
                    imaging.z_step_um
                    if "z_step_um" in imaging_fields
                    else defaults.z_step_um
                ),
                use_autofocus=(
                    imaging.use_autofocus
                    if "use_autofocus" in imaging_fields
                    else defaults.use_autofocus
                ),
                use_focus_lock=(
                    imaging.use_focus_lock
                    if "use_focus_lock" in imaging_fields
                    else defaults.use_focus_lock
                ),
                exposure_time_ms=imaging.exposure_time_ms,
                skip_saving=imaging.skip_saving,
            )

        return Round(
            name=round_.name,
            type=round_.type,
            fluidics_protocol=round_.fluidics_protocol,
            imaging=merged_imaging,
            requires_intervention=round_.requires_intervention,
            intervention_message=round_.intervention_message,
            metadata=round_.metadata,
        )
