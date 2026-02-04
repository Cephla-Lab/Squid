"""
Protocol schema definitions for experiment orchestration (V2).

Defines the YAML structure for step-based multi-round experiments.
Uses Pydantic for validation and type safety.

Example V2 protocol:
    name: "Multi-Round FISH"
    version: "2.0"

    error_handling:
      focus_failure: skip
      fluidics_failure: abort

    fluidics_protocols:
      wash:
        description: "Standard wash"
        steps:
          - operation: flow
            solution: wash_buffer
            volume_ul: 500

    imaging_protocols:
      fish_standard:
        channels: [DAPI, Cy5]
        z_stack:
          planes: 5
        focus:
          enabled: true
          method: laser

    fov_sets:
      main_grid: positions/main.csv

    rounds:
      - name: "Round 1"
        steps:
          - step_type: fluidics
            protocol: wash
          - step_type: imaging
            protocol: fish_standard
            fovs: main_grid
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from squid.core.protocol.step import Step, FluidicsStep, ImagingStep, InterventionStep
from squid.core.protocol.imaging_protocol import ImagingProtocol
from squid.core.protocol.error_handling import ErrorHandlingConfig, FailureAction
from squid.core.protocol.fluidics_protocol import FluidicsProtocol, FluidicsCommand


class Round(BaseModel):
    """A single experimental round with ordered steps.

    V2 rounds use a step-based model where each round contains an ordered
    list of steps (fluidics, imaging, intervention) that execute in sequence.

    Attributes:
        name: Human-readable name for the round (supports {i} substitution)
        steps: Ordered list of steps to execute
        repeat: Optional repeat count (expands to N rounds with {i} substitution)
        metadata: Additional round-specific metadata
    """

    name: str
    steps: List[Step] = Field(default_factory=list)
    repeat: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("repeat")
    @classmethod
    def validate_repeat(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("repeat must be >= 1")
        return v


class ExperimentProtocol(BaseModel):
    """Complete V2 experiment protocol definition.

    V2 protocols use named resources (fluidics_protocols, imaging_protocols, fov_sets)
    that are referenced by steps within rounds. This enables reuse and flexible
    step ordering.

    Attributes:
        name: Human-readable protocol name
        version: Protocol version string (should be "2.0" for V2)
        description: Detailed description of the protocol
        author: Protocol author (optional)
        output_directory: Optional default output directory for experiment data

        error_handling: Protocol-level error handling configuration
        fluidics_protocols: Named fluidics protocols (inline or file: reference)
        imaging_protocols: Named imaging protocols (inline or file: reference)
        fov_sets: Named FOV sets mapping to CSV file paths
        rounds: List of experimental rounds
    """

    name: str
    version: str = "2.0"
    description: str = ""
    author: str = ""
    output_directory: Optional[str] = None

    # Error handling
    error_handling: ErrorHandlingConfig = Field(default_factory=ErrorHandlingConfig)

    # Named resources
    fluidics_protocols: Dict[str, FluidicsProtocol] = Field(default_factory=dict)
    imaging_protocols: Dict[str, ImagingProtocol] = Field(default_factory=dict)
    fov_sets: Dict[str, str] = Field(default_factory=dict)  # name -> CSV path

    # Rounds
    rounds: List[Round] = Field(default_factory=list)

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

    def get_imaging_steps(self) -> List[ImagingStep]:
        """Get all imaging steps across all rounds."""
        steps = []
        for round_ in self.rounds:
            for step in round_.steps:
                if isinstance(step, ImagingStep):
                    steps.append(step)
        return steps

    def total_imaging_steps(self) -> int:
        """Count imaging steps across all rounds."""
        return len(self.get_imaging_steps())

    def validate_references(self) -> List[str]:
        """Validate that all step references exist in their respective resource dicts.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        for round_idx, round_ in enumerate(self.rounds):
            for step_idx, step in enumerate(round_.steps):
                step_loc = f"Round '{round_.name}' step {step_idx}"

                if isinstance(step, FluidicsStep):
                    if self.fluidics_protocols:
                        if step.protocol not in self.fluidics_protocols:
                            errors.append(
                                f"{step_loc}: fluidics protocol '{step.protocol}' not found"
                            )

                elif isinstance(step, ImagingStep):
                    if step.protocol not in self.imaging_protocols:
                        errors.append(
                            f"{step_loc}: imaging protocol '{step.protocol}' not found"
                        )
                    if step.fovs != "current" and step.fovs not in self.fov_sets:
                        errors.append(
                            f"{step_loc}: FOV set '{step.fovs}' not found"
                        )

        return errors
