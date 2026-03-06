"""
Confocal unit configuration models.

These models define confocal-specific hardware settings. This configuration
file is optional - it only exists on systems with a confocal unit.
"""

import logging
from typing import Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from control.models.confocal_models import ConfocalModelDef

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

from control.models.filter_wheel_config import (
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

    Filter wheels built into the confocal unit are defined here, not in
    filter_wheels.yaml. This keeps the confocal configuration self-contained.
    """

    version: float = Field(1.0, description="Configuration format version")

    # Confocal model name (e.g. "xlight_v3", "cicero") — drives which properties are generated
    model: Optional[str] = Field(None, description="Confocal model (e.g. 'xlight_v3', 'cicero')")

    # Filter wheels that are part of the confocal unit
    filter_wheels: List[FilterWheelDefinition] = Field(
        default_factory=list,
        description="Filter wheels built into the confocal unit",
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
        """Apply defaults for single-wheel systems before object creation."""
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
        for wheel in self.filter_wheels:
            if wheel.id == wheel_id:
                return wheel.get_filter_name(slot)
        return None

    def get_wheel_by_id(self, wheel_id: int) -> Optional[FilterWheelDefinition]:
        """Get a confocal filter wheel by ID."""
        for wheel in self.filter_wheels:
            if wheel.id == wheel_id:
                return wheel
        return None

    def get_wheel_by_name(self, name: str) -> Optional[FilterWheelDefinition]:
        """Get a confocal filter wheel by name."""
        for wheel in self.filter_wheels:
            if wheel.name == name:
                return wheel
        return None

    def get_wheel_names(self) -> List[str]:
        """Get list of all confocal filter wheel names for UI dropdowns."""
        return [wheel.name for wheel in self.filter_wheels if wheel.name is not None]

    def get_wheel_ids(self) -> List[int]:
        """Get list of all confocal filter wheel IDs."""
        return [wheel.id for wheel in self.filter_wheels if wheel.id is not None]

    def get_first_wheel(self) -> Optional[FilterWheelDefinition]:
        """Get the first (or only) confocal filter wheel.

        Useful for single-wheel confocal systems.
        """
        return self.filter_wheels[0] if self.filter_wheels else None

    def get_wheels_by_type(self, wheel_type: FilterWheelType) -> List[FilterWheelDefinition]:
        """Get all confocal filter wheels of a specific type."""
        return [wheel for wheel in self.filter_wheels if wheel.type == wheel_type]

    def get_emission_wheels(self) -> List[FilterWheelDefinition]:
        """Get all confocal emission filter wheels."""
        return self.get_wheels_by_type(FilterWheelType.EMISSION)

    def get_excitation_wheels(self) -> List[FilterWheelDefinition]:
        """Get all confocal excitation filter wheels."""
        return self.get_wheels_by_type(FilterWheelType.EXCITATION)

    def get_model_def(self) -> Optional["ConfocalModelDef"]:
        """Look up the model definition from the registry.

        Returns None if model is not set or not found in the registry.
        Logs a warning if model is set but not found (possible typo).
        """
        if self.model is None:
            return None
        from control.models.confocal_models import CONFOCAL_MODELS

        model_def = CONFOCAL_MODELS.get(self.model)
        if model_def is None:
            logger.warning(
                f"Confocal model '{self.model}' not found in registry. "
                f"Known models: {sorted(CONFOCAL_MODELS.keys())}"
            )
        return model_def

    def has_property(self, property_name: str) -> bool:
        """Check if a property is available for configuration."""
        return property_name in self.public_properties or property_name in self.objective_specific_properties
