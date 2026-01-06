"""
Confocal unit configuration models.

These models define confocal-specific hardware settings. This configuration
file is optional - it only exists on systems with a confocal unit.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ConfocalConfig(BaseModel):
    """
    Optional configuration for confocal unit.

    Only present if the system has a confocal unit. The presence of this
    configuration file (confocal_config.yaml) indicates that confocal
    settings should be included in acquisition configs.
    """

    version: int = Field(1, description="Configuration format version")

    filter_wheel_mappings: Optional[Dict[int, Dict[int, str]]] = Field(
        None,
        description="Filter wheel ID -> slot number -> filter name mapping",
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

    def get_filter_name(self, wheel_id: int, slot: int) -> Optional[str]:
        """Get the filter name for a given wheel and slot."""
        if self.filter_wheel_mappings is None:
            return None
        wheel = self.filter_wheel_mappings.get(wheel_id)
        if wheel is None:
            return None
        return wheel.get(slot)

    def has_property(self, property_name: str) -> bool:
        """Check if a property is available for configuration."""
        return property_name in self.public_properties or property_name in self.objective_specific_properties
