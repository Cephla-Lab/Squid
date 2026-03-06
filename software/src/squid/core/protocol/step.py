"""
Step types for orchestrated experiments.

Each round contains ordered steps. Imaging steps reference named imaging
protocols and can optionally override local focus/capture policy. Fluidics
steps reference named fluidics protocols and can override retry/escalation
policy. Intervention steps remain explicit operator pauses.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from squid.core.protocol.imaging_protocol import CapturePolicyConfig, FocusGateConfig, FocusLockConfig


class PartialFocusGateOverride(BaseModel):
    """Partial override applied on top of an imaging protocol's focus gate."""

    mode: Optional[str] = None
    channel: Optional[str] = None
    interval_fovs: Optional[int] = None
    focus_lock: Optional[FocusLockConfig] = None
    require_in_focus: Optional[bool] = None
    max_focus_error_um: Optional[float] = None
    max_focus_attempts: Optional[int] = None
    on_focus_gate_fail: Optional[Literal["retry", "skip_fov", "fail_step"]] = None


class PartialCapturePolicyOverride(BaseModel):
    """Partial override applied on top of an imaging protocol's capture policy."""

    max_capture_attempts: Optional[int] = None
    retry_delay_s: Optional[float] = None
    on_capture_fail: Optional[Literal["retry", "skip_fov", "fail_step"]] = None


class StepFailurePolicy(BaseModel):
    """Run-level retry/escalation policy for a failing step."""

    max_attempts: int = 1
    retry_delay_s: float = 0.0
    on_fail: Literal["pause", "skip_step", "abort"] = "pause"

    @field_validator("max_attempts")
    @classmethod
    def validate_max_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_attempts must be >= 1")
        return value

    @field_validator("retry_delay_s")
    @classmethod
    def validate_retry_delay(cls, value: float) -> float:
        if value < 0:
            raise ValueError("retry_delay_s must be >= 0")
        return value


class FluidicsStep(BaseModel):
    """Execute a named fluidics protocol."""

    step_type: Literal["fluidics"] = "fluidics"
    protocol: str
    fov_scope: Literal["global", "current_fov_set"] = "global"
    failure_policy: Optional[StepFailurePolicy] = None
    label: Optional[str] = None


class ImagingStep(BaseModel):
    """Execute a named imaging protocol on a named FOV set."""

    step_type: Literal["imaging"] = "imaging"
    protocol: str = ""
    fovs: str = "current"
    label: Optional[str] = None
    output_label: Optional[str] = None
    focus_gate_override: Optional[PartialFocusGateOverride] = None
    capture_policy_override: Optional[PartialCapturePolicyOverride] = None
    failure_policy: Optional[StepFailurePolicy] = None


class InterventionStep(BaseModel):
    """Pause execution for explicit operator intervention."""

    step_type: Literal["intervention"] = "intervention"
    message: str
    require_ack: bool = True


Step = Annotated[
    Union[FluidicsStep, ImagingStep, InterventionStep],
    Field(discriminator="step_type"),
]
