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
        fluidics:
          - command: "flow"
            solution: "probe_mix_1"
            volume_ul: 100
            flow_rate_ul_per_min: 50
        imaging:
          channels: ["DAPI", "Cy3"]

      - name: "Wash"
        type: "wash"
        fluidics:
          - command: "flow"
            solution: "wash_buffer"
            volume_ul: 500
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
    1. Optional fluidics operations (buffer exchange, staining, etc.)
    2. Optional imaging acquisition

    Attributes:
        name: Human-readable name for the round
        type: Type of round (imaging, wash, bleach, custom)
        fluidics: List of fluidics operations
        imaging: Imaging configuration (None to skip imaging)
        requires_intervention: If True, pause for operator intervention
        intervention_message: Message to show during intervention pause
        metadata: Additional round-specific metadata
    """

    name: str
    type: RoundType = RoundType.IMAGING
    fluidics: List[FluidicsStep] = Field(default_factory=list)
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
        metadata: Additional protocol-level metadata
    """

    name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    created: str = ""
    defaults: ProtocolDefaults = Field(default_factory=ProtocolDefaults)
    rounds: List[Round] = Field(default_factory=list)
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

        fluidics_defaults = self.defaults.fluidics
        merged_fluidics = []
        for step in round_.fluidics:
            step_fields = step.model_fields_set
            volume_ul = step.volume_ul
            if step.command == FluidicsCommand.WASH and "volume_ul" not in step_fields:
                volume_ul = fluidics_defaults.wash_volume_ul

            flow_rate_ul_per_min = step.flow_rate_ul_per_min
            if "flow_rate_ul_per_min" not in step_fields:
                flow_rate_ul_per_min = fluidics_defaults.flow_rate_ul_per_min

            merged_fluidics.append(
                FluidicsStep(
                    command=step.command,
                    solution=step.solution,
                    volume_ul=volume_ul,
                    flow_rate_ul_per_min=flow_rate_ul_per_min,
                    duration_s=step.duration_s,
                    repeats=step.repeats,
                )
            )

        return Round(
            name=round_.name,
            type=round_.type,
            fluidics=merged_fluidics,
            imaging=merged_imaging,
            requires_intervention=round_.requires_intervention,
            intervention_message=round_.intervention_message,
            metadata=round_.metadata,
        )
