"""
Filter wheel registry configuration models.

This module defines the filter wheel registry that maps user-friendly filter
wheel names to hardware identifiers and provides filter position mappings.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class FilterWheelDefinition(BaseModel):
    """A filter wheel in the system.

    For single-wheel systems, name and id can be omitted (None).
    For multi-wheel systems, name and id are required to distinguish wheels.
    """

    name: Optional[str] = Field(
        None, min_length=1, description="User-friendly filter wheel name (optional for single wheel)"
    )
    id: Optional[int] = Field(None, ge=0, description="Hardware ID for controller (optional for single wheel)")
    positions: Dict[int, str] = Field(..., description="Slot number -> filter name")

    model_config = {"extra": "forbid"}

    @field_validator("positions")
    @classmethod
    def validate_positions(cls, v: Dict[int, str]) -> Dict[int, str]:
        """Validate that position numbers are >= 1 and filter names are non-empty."""
        for pos, name in v.items():
            if pos < 1:
                raise ValueError(f"Position {pos} must be >= 1")
            if not name or not name.strip():
                raise ValueError(f"Filter name at position {pos} cannot be empty")
        return v

    @model_validator(mode="after")
    def validate_name_id_consistency(self) -> "FilterWheelDefinition":
        """Ensure name and id are either both present or both absent."""
        if (self.name is None) != (self.id is None):
            raise ValueError("name and id must both be present or both be absent")
        return self

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

    @field_validator("filter_wheels")
    @classmethod
    def validate_filter_wheels(cls, v: List[FilterWheelDefinition]) -> List[FilterWheelDefinition]:
        """Validate filter wheel collection rules.

        Rules:
        1. Multi-wheel systems require name and id for each wheel
        2. Names must be unique (excluding None for single-wheel)
        3. IDs must be unique (excluding None for single-wheel)
        """
        # Rule 1: Multi-wheel systems require name and id for each wheel
        if len(v) > 1:
            for i, wheel in enumerate(v):
                if wheel.name is None or wheel.id is None:
                    raise ValueError(
                        f"Multi-wheel systems require name and id for each wheel. "
                        f"Wheel at index {i} is missing name or id."
                    )

        # Rule 2: Names must be unique (filter out None for single-wheel case)
        names = [w.name for w in v if w.name is not None]
        unique_names = set(names)
        if len(names) != len(unique_names):
            duplicates = [n for n in unique_names if names.count(n) > 1]
            raise ValueError(f"Filter wheel names must be unique. Duplicates: {duplicates}")

        # Rule 3: IDs must be unique (filter out None for single-wheel case)
        ids = [w.id for w in v if w.id is not None]
        unique_ids = set(ids)
        if len(ids) != len(unique_ids):
            duplicates = [i for i in unique_ids if ids.count(i) > 1]
            raise ValueError(f"Filter wheel IDs must be unique. Duplicates: {duplicates}")
        return v

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
        """Get list of all filter wheel names for UI dropdowns.

        Only returns named wheels - unnamed single-wheel systems return empty list.
        """
        return [wheel.name for wheel in self.filter_wheels if wheel.name is not None]

    def get_first_wheel(self) -> Optional[FilterWheelDefinition]:
        """Get the first (or only) filter wheel, regardless of name.

        Useful for single-wheel systems where the wheel may be unnamed.
        """
        return self.filter_wheels[0] if self.filter_wheels else None

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
