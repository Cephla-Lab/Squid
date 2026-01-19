"""
Fluidics protocol schema definitions.

Defines named fluidics protocols that can be loaded from YAML and executed
by the FluidicsController. Protocols are sequences of operations (flow, wash,
incubate, etc.) that can be referenced by name from experiment protocols.

Example YAML (fluidics_protocols.yaml):

    protocols:
      Wash_Round1:
        description: "Standard wash after Round 1 imaging"
        steps:
          - operation: wash
            solution: wash_buffer
            volume_ul: 500
            flow_rate_ul_per_min: 100
            repeats: 3
          - operation: incubate
            duration_s: 30
          - operation: aspirate

      Probe_Delivery:
        description: "Deliver probe mix to chamber"
        steps:
          - operation: prime
            solution: probe_mix
            volume_ul: 100
          - operation: flow
            solution: probe_mix
            volume_ul: 200
            flow_rate_ul_per_min: 25
          - operation: incubate
            duration_s: 1800
            description: "30 min hybridization"
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class FluidicsCommand(str, Enum):
    """Fluidics operation commands."""

    FLOW = "flow"  # Flow solution through chamber
    ASPIRATE = "aspirate"  # Remove solution
    INCUBATE = "incubate"  # Wait with solution in place
    PRIME = "prime"  # Prime tubing
    WASH = "wash"  # Wash step (flow + aspirate)


class FluidicsProtocolStep(BaseModel):
    """A single step in a fluidics protocol.

    Attributes:
        operation: The fluidics operation to execute (flow, wash, incubate, prime, aspirate)
        solution: Name/ID of the solution (for flow/wash/prime)
        volume_ul: Volume in microliters
        flow_rate_ul_per_min: Flow rate in microliters per minute
        duration_s: Duration in seconds (for incubate)
        repeats: Number of times to repeat this step
        description: Human-readable description of what this step does
    """

    operation: FluidicsCommand
    solution: Optional[str] = None
    volume_ul: Optional[float] = None
    flow_rate_ul_per_min: Optional[float] = None
    duration_s: Optional[float] = None
    repeats: int = 1
    description: str = ""

    @field_validator("repeats")
    @classmethod
    def validate_repeats(cls, v: int) -> int:
        if v < 1:
            raise ValueError("repeats must be >= 1")
        return v

    def estimated_duration_s(self) -> float:
        """Estimate duration of this step in seconds."""
        if self.duration_s is not None:
            return self.duration_s * self.repeats
        if self.volume_ul and self.flow_rate_ul_per_min:
            # Time = volume / rate, converted to seconds
            return (self.volume_ul / self.flow_rate_ul_per_min) * 60.0 * self.repeats
        # Default estimate for operations without timing info
        return 10.0 * self.repeats

    def get_description(self) -> str:
        """Get human-readable description of this step."""
        if self.description:
            return self.description

        # Generate description from operation
        op = self.operation.value.capitalize()
        if self.solution:
            desc = f"{op} {self.solution}"
        else:
            desc = op

        if self.volume_ul:
            desc += f" ({self.volume_ul}ul"
            if self.flow_rate_ul_per_min:
                desc += f" @ {self.flow_rate_ul_per_min}ul/min"
            desc += ")"
        elif self.duration_s:
            desc += f" ({self.duration_s}s)"

        if self.repeats > 1:
            desc += f" x{self.repeats}"

        return desc


class FluidicsProtocol(BaseModel):
    """A named fluidics protocol (sequence of operations).

    Attributes:
        description: Human-readable description of what this protocol does
        steps: Ordered list of fluidics operations to execute
    """

    description: str = ""
    steps: List[FluidicsProtocolStep] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v: List[FluidicsProtocolStep]) -> List[FluidicsProtocolStep]:
        if not v:
            raise ValueError("Protocol must have at least one step")
        return v

    def estimated_duration_s(self) -> float:
        """Estimate total protocol duration in seconds."""
        return sum(step.estimated_duration_s() for step in self.steps)

    def total_steps(self) -> int:
        """Count total steps including repeats."""
        return sum(step.repeats for step in self.steps)


class FluidicsProtocolFile(BaseModel):
    """Root model for fluidics protocol YAML file.

    A single YAML file contains multiple named protocols.

    Attributes:
        protocols: Dictionary mapping protocol names to protocol definitions
    """

    protocols: Dict[str, FluidicsProtocol] = Field(default_factory=dict)

    def get_protocol(self, name: str) -> Optional[FluidicsProtocol]:
        """Get a protocol by name (case-insensitive)."""
        # Try exact match first
        if name in self.protocols:
            return self.protocols[name]
        # Try case-insensitive match
        name_lower = name.lower()
        for proto_name, proto in self.protocols.items():
            if proto_name.lower() == name_lower:
                return proto
        return None

    def list_protocols(self) -> List[str]:
        """List all protocol names."""
        return list(self.protocols.keys())

    @classmethod
    def load_from_yaml(cls, path: str) -> "FluidicsProtocolFile":
        """Load protocols from a YAML file.

        Args:
            path: Path to the YAML file

        Returns:
            FluidicsProtocolFile with loaded protocols

        Raises:
            FileNotFoundError: If file doesn't exist
            yaml.YAMLError: If YAML is malformed
            pydantic.ValidationError: If schema validation fails
        """
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if data is None:
            return cls()

        return cls.model_validate(data)
