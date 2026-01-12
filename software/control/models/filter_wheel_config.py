"""
Filter wheel registry configuration models.

This module defines the filter wheel registry that maps user-friendly filter
wheel names to hardware identifiers and provides filter position mappings.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class FilterWheelDefinition(BaseModel):
    """A filter wheel in the system."""

    name: str = Field(..., description="User-friendly filter wheel name")
    id: int = Field(..., description="Hardware ID for controller")
    positions: Dict[int, str] = Field(..., description="Slot number -> filter name")

    model_config = {"extra": "forbid"}

    def get_filter_name(self, position: int) -> Optional[str]:
        """Get filter name at a position."""
        return self.positions.get(position)

    def get_position_by_filter(self, filter_name: str) -> Optional[int]:
        """Get position number for a filter name."""
        for pos, name in self.positions.items():
            if name == filter_name:
                return pos
        return None

    def get_filter_names(self) -> List[str]:
        """Get list of all filter names in this wheel."""
        return list(self.positions.values())

    def get_positions(self) -> List[int]:
        """Get list of all position numbers."""
        return sorted(self.positions.keys())


class FilterWheelRegistryConfig(BaseModel):
    """
    Registry of available filter wheels.

    This configuration defines all filter wheels in the system with their
    positions and filter names. Channels reference filter wheels by name.

    Location: machine_configs/filter_wheels.yaml
    """

    version: float = Field(1.1, description="Configuration format version")
    filter_wheels: List[FilterWheelDefinition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def get_wheel_by_name(self, name: str) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by user-friendly name."""
        for wheel in self.filter_wheels:
            if wheel.name == name:
                return wheel
        return None

    def get_wheel_by_id(self, wheel_id: int) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by hardware ID."""
        for wheel in self.filter_wheels:
            if wheel.id == wheel_id:
                return wheel
        return None

    def get_wheel_names(self) -> List[str]:
        """Get list of all filter wheel names for UI dropdowns."""
        return [wheel.name for wheel in self.filter_wheels]

    def get_hardware_id(self, wheel_name: str) -> Optional[int]:
        """Get hardware ID for a filter wheel name."""
        wheel = self.get_wheel_by_name(wheel_name)
        return wheel.id if wheel else None

    def get_filter_name(self, wheel_name: str, position: int) -> Optional[str]:
        """Get filter name for a wheel and position."""
        wheel = self.get_wheel_by_name(wheel_name)
        if wheel:
            return wheel.get_filter_name(position)
        return None
