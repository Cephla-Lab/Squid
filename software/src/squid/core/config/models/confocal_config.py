"""
Confocal unit configuration models.

These models define confocal-specific hardware settings. This configuration
file is optional - it only exists on systems with a confocal unit.

Ported from upstream commit 171aed9b.
"""

from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from squid.core.config.models.filter_wheel_config import (
    FilterWheelDefinition,
    FilterWheelType,
    apply_single_filter_wheel_defaults,
    validate_filter_wheel_list,
)


class ConfocalConfig(BaseModel):
    """
    Optional configuration for confocal unit.

    Only present if the system has a confocal unit. The presence of this
    configuration file (confocal_config.yaml) indicates that confocal
    settings should be included in acquisition configs.
    """

    version: float = Field(1.0, description="Configuration format version")

    filter_wheels: List[FilterWheelDefinition] = Field(
        default_factory=list,
        description="Filter wheels managed by the confocal unit",
    )

    # Properties that can be configured in acquisition configs
    public_properties: List[str] = Field(
        default_factory=list,
        description="Properties available in general.yaml (e.g., emission_filter_wheel_position)",
    )
    objective_specific_properties: List[str] = Field(
        default_factory=list,
        description="Properties available in objective files (e.g., illumination_iris, emission_iris)",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def apply_single_wheel_defaults(cls, data: Any) -> Any:
        """Apply defaults for single-wheel systems."""
        if not isinstance(data, dict):
            return data
        wheels = data.get("filter_wheels", [])
        data["filter_wheels"] = apply_single_filter_wheel_defaults(wheels)
        return data

    @model_validator(mode="after")
    def validate_filter_wheels(self) -> "ConfocalConfig":
        """Validate filter wheels after object creation."""
        validate_filter_wheel_list(self.filter_wheels, context="Confocal filter wheel")
        return self

    def get_filter_name(self, wheel_id: int, slot: int) -> Optional[str]:
        """Get the filter name for a given wheel and slot."""
        wheel = self.get_wheel_by_id(wheel_id)
        if wheel is None:
            return None
        return wheel.get_filter_name(slot)

    def has_property(self, property_name: str) -> bool:
        """Check if a property is available for configuration."""
        return property_name in self.public_properties or property_name in self.objective_specific_properties

    def get_wheel_by_id(self, wheel_id: int) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by hardware ID."""
        for wheel in self.filter_wheels:
            if wheel.id == wheel_id:
                return wheel
        return None

    def get_wheel_by_name(self, name: str) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by user-friendly name."""
        for wheel in self.filter_wheels:
            if wheel.name == name:
                return wheel
        return None

    def get_wheel_names(self) -> List[str]:
        """Get list of all filter wheel names."""
        return [wheel.name for wheel in self.filter_wheels if wheel.name is not None]

    def get_wheel_ids(self) -> List[int]:
        """Get list of all filter wheel IDs."""
        return [wheel.id for wheel in self.filter_wheels if wheel.id is not None]

    def get_first_wheel(self) -> Optional[FilterWheelDefinition]:
        """Get the first (or only) filter wheel."""
        return self.filter_wheels[0] if self.filter_wheels else None

    def get_wheels_by_type(self, wheel_type: FilterWheelType) -> List[FilterWheelDefinition]:
        """Get all filter wheels of a specific type."""
        return [wheel for wheel in self.filter_wheels if wheel.type == wheel_type]

    def get_emission_wheels(self) -> List[FilterWheelDefinition]:
        """Get all emission filter wheels."""
        return self.get_wheels_by_type(FilterWheelType.EMISSION)

    def get_excitation_wheels(self) -> List[FilterWheelDefinition]:
        """Get all excitation filter wheels."""
        return self.get_wheels_by_type(FilterWheelType.EXCITATION)
