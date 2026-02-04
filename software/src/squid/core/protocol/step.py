"""
Step types for V2 protocol schema.

Defines the discriminated union of step types that can appear in a round:
- FluidicsStep: Execute a named fluidics protocol
- ImagingStep: Execute imaging with a named protocol
- InterventionStep: Pause for operator intervention
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class FluidicsStep(BaseModel):
    """Fluidics step referencing a named protocol.

    References a protocol defined in the protocol's fluidics_protocols section.

    Example:
        step_type: fluidics
        protocol: wash
    """

    step_type: Literal["fluidics"] = "fluidics"
    protocol: str  # Name from fluidics_protocols


class ImagingStep(BaseModel):
    """Imaging step referencing named resources.

    References an imaging protocol and optional FOV set defined in the protocol.

    Example:
        step_type: imaging
        protocol: fish_standard
        fovs: main_grid
    """

    step_type: Literal["imaging"] = "imaging"
    protocol: str = ""  # Name from imaging_protocols dict or stored profile protocol
    fovs: str = "current"  # Name from fov_sets, or "current" for loaded FOVs


class InterventionStep(BaseModel):
    """Intervention step requiring operator action.

    Pauses execution and displays a message to the operator.

    Example:
        step_type: intervention
        message: "Replace slide"
    """

    step_type: Literal["intervention"] = "intervention"
    message: str


# Discriminated union of all step types
Step = Annotated[
    Union[FluidicsStep, ImagingStep, InterventionStep],
    Field(discriminator="step_type"),
]
