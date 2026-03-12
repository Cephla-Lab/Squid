"""
Protocol schema definitions for orchestrated imaging/fluidics experiments.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from squid.core.protocol.fluidics_protocol import FluidicsCommand, FluidicsProtocol
from squid.core.protocol.imaging_protocol import ImagingProtocol
from squid.core.protocol.step import FluidicsStep, ImagingStep, InterventionStep, StepFailurePolicy


class Round(BaseModel):
    """A single experimental round with ordered steps."""

    name: str
    steps: List[FluidicsStep | ImagingStep | InterventionStep] = Field(default_factory=list)
    repeat: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("repeat")
    @classmethod
    def validate_repeat(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("repeat must be >= 1")
        return value


class ProtocolResources(BaseModel):
    """External and named resources used by the orchestrator."""

    imaging_protocols: Dict[str, ImagingProtocol] = Field(default_factory=dict)
    fluidics_protocols: Dict[str, FluidicsProtocol] = Field(default_factory=dict)
    fluidics_protocols_file: Optional[str] = None
    fluidics_config_file: Optional[str] = None
    fov_sets: Dict[str, str] = Field(default_factory=dict)
    fov_file: Optional[str] = None


class ProtocolDefaults(BaseModel):
    """Default retry/escalation policy used by steps unless overridden."""

    step_failure_policy: StepFailurePolicy = Field(default_factory=StepFailurePolicy)


class ExperimentProtocol(BaseModel):
    """Top-level experiment protocol."""

    name: str
    version: str = "3.0"
    description: str = ""
    author: str = ""
    output_directory: Optional[str] = None
    resources: ProtocolResources = Field(default_factory=ProtocolResources)
    defaults: ProtocolDefaults = Field(default_factory=ProtocolDefaults)
    rounds: List[Round] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_shape(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        resources = dict(payload.get("resources") or {})
        defaults = dict(payload.get("defaults") or {})

        for field in (
            "imaging_protocols",
            "fluidics_protocols",
            "fluidics_protocols_file",
            "fluidics_config_file",
            "fov_sets",
            "fov_file",
        ):
            if field in payload:
                resources.setdefault(field, payload.pop(field))
        payload["resources"] = resources

        # Accept the old error_handling block but normalize to step defaults.
        error_handling = payload.pop("error_handling", None)
        if error_handling and "step_failure_policy" not in defaults:
            imaging_failure = error_handling.get("imaging_failure")
            if imaging_failure == "abort":
                defaults["step_failure_policy"] = {"on_fail": "abort"}
            elif imaging_failure == "skip":
                defaults["step_failure_policy"] = {"on_fail": "skip_step"}
            else:
                defaults["step_failure_policy"] = {"on_fail": "pause"}
        payload["defaults"] = defaults

        return payload

    @field_validator("rounds")
    @classmethod
    def validate_rounds(cls, value: List[Round]) -> List[Round]:
        if not value:
            raise ValueError("Protocol must have at least one round")
        return value

    @property
    def imaging_protocols(self) -> Dict[str, ImagingProtocol]:
        return self.resources.imaging_protocols

    @property
    def fluidics_protocols(self) -> Dict[str, FluidicsProtocol]:
        return self.resources.fluidics_protocols

    @property
    def fov_sets(self) -> Dict[str, str]:
        return self.resources.fov_sets

    @property
    def fluidics_protocols_file(self) -> Optional[str]:
        return self.resources.fluidics_protocols_file

    @property
    def fluidics_config_file(self) -> Optional[str]:
        return self.resources.fluidics_config_file

    @property
    def fov_file(self) -> Optional[str]:
        return self.resources.fov_file

    @property
    def step_failure_policy(self) -> StepFailurePolicy:
        return self.defaults.step_failure_policy

    def get_round_by_name(self, name: str) -> Optional[Round]:
        for round_ in self.rounds:
            if round_.name == name:
                return round_
        return None

    def get_imaging_steps(self) -> List[ImagingStep]:
        steps: List[ImagingStep] = []
        for round_ in self.rounds:
            for step in round_.steps:
                if isinstance(step, ImagingStep):
                    steps.append(step)
        return steps

    def total_imaging_steps(self) -> int:
        return len(self.get_imaging_steps())

    def validate_references(self) -> List[str]:
        errors: List[str] = []

        for round_idx, round_ in enumerate(self.rounds):
            for step_idx, step in enumerate(round_.steps):
                step_loc = f"Round '{round_.name}' step {step_idx}"

                if isinstance(step, FluidicsStep):
                    if self.fluidics_protocols and step.protocol not in self.fluidics_protocols:
                        errors.append(
                            f"{step_loc}: fluidics protocol '{step.protocol}' not found"
                        )

                elif isinstance(step, ImagingStep):
                    if step.protocol not in self.imaging_protocols:
                        errors.append(
                            f"{step_loc}: imaging protocol '{step.protocol}' not found"
                        )
                    if step.fovs not in ("current", "default") and step.fovs not in self.fov_sets:
                        errors.append(
                            f"{step_loc}: FOV set '{step.fovs}' not found"
                        )

        return errors
