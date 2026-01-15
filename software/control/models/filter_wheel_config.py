"""
Filter wheel registry configuration models.

This module defines the filter wheel registry that maps user-friendly filter
wheel names to hardware identifiers and provides filter position mappings.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class FilterWheelType(str, Enum):
    """Type of filter wheel based on its position in the optical path."""

    EXCITATION = "excitation"  # Filters light before sample (excitation filters)
    EMISSION = "emission"  # Filters light after sample (emission filters)


# ═══════════════════════════════════════════════════════════════════════════
# SHARED FILTER WHEEL VALIDATION HELPERS
# Used by both FilterWheelRegistryConfig and ConfocalConfig
# ═══════════════════════════════════════════════════════════════════════════


def apply_single_filter_wheel_defaults(wheels: List[Any]) -> List[Any]:
    """
    Apply defaults to a single filter wheel in raw data.

    For single-wheel systems, applies default id=1 and name based on type.
    Handles both dict and FilterWheelDefinition inputs.

    Args:
        wheels: List of wheels (dicts or FilterWheelDefinition objects)

    Returns:
        Modified list with defaults applied (may contain dicts)

    Raises:
        ValueError: If wheel is not a dict or Pydantic model
    """
    if len(wheels) != 1:
        return wheels

    wheel = wheels[0]
    if isinstance(wheel, dict):
        if wheel.get("id") is None:
            wheel["id"] = 1
        if wheel.get("name") is None:
            wheel_type = wheel.get("type", "emission")
            if isinstance(wheel_type, FilterWheelType):
                wheel_type = wheel_type.value
            wheel["name"] = f"{wheel_type.title()} Wheel"
    elif hasattr(wheel, "model_dump"):  # Pydantic model
        wheel_dict = wheel.model_dump()
        if wheel_dict.get("id") is None:
            wheel_dict["id"] = 1
        if wheel_dict.get("name") is None:
            wheel_type = wheel_dict.get("type", "emission")
            if isinstance(wheel_type, FilterWheelType):
                wheel_type = wheel_type.value
            wheel_dict["name"] = f"{wheel_type.title()} Wheel"
        return [wheel_dict]
    else:
        # Unexpected type - this is a configuration error
        raise ValueError(
            f"Filter wheel definition must be a dict or Pydantic model, got {type(wheel).__name__}: {wheel!r}"
        )

    return wheels


def validate_filter_wheel_list(wheels: List["FilterWheelDefinition"], context: str = "Filter wheel") -> None:
    """
    Validate a list of filter wheels.

    Checks:
    - Multiple wheels require id and name for all
    - Names must be unique
    - IDs must be unique

    Args:
        wheels: List of FilterWheelDefinition objects
        context: Context string for error messages

    Raises:
        ValueError: If validation fails
    """
    if len(wheels) == 0:
        return

    if len(wheels) > 1:
        # Multiple wheels: require id and name for all
        for i, wheel in enumerate(wheels):
            if wheel.id is None:
                raise ValueError(
                    f"{context} at index {i} (type: {wheel.type.value}) missing required 'id' "
                    f"(required when multiple wheels exist)"
                )
            if wheel.name is None:
                raise ValueError(
                    f"{context} at index {i} (type: {wheel.type.value}) missing required 'name' "
                    f"(required when multiple wheels exist)"
                )

    # Validate uniqueness
    names = [w.name for w in wheels if w.name is not None]
    ids = [w.id for w in wheels if w.id is not None]

    if len(names) != len(set(names)):
        duplicates = [n for n in set(names) if names.count(n) > 1]
        raise ValueError(f"{context} names must be unique. Duplicates: {duplicates}")

    if len(ids) != len(set(ids)):
        duplicates = [i for i in set(ids) if ids.count(i) > 1]
        raise ValueError(f"{context} IDs must be unique. Duplicates: {duplicates}")


class FilterWheelDefinition(BaseModel):
    """A filter wheel in the system.

    For single-wheel systems, name and id are optional (defaults applied).
    For multi-wheel systems, name and id are required to distinguish wheels.
    Type is always required for UI categorization.
    """

    name: Optional[str] = Field(None, min_length=1, description="User-friendly filter wheel name")
    id: Optional[int] = Field(None, ge=1, description="Filter wheel ID for hardware bindings")
    type: FilterWheelType = Field(
        ..., description="Filter wheel type: excitation (before sample) or emission (after sample)"
    )
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
    Registry of available filter wheels (standalone, not part of confocal).

    This configuration defines standalone filter wheels in the system.
    Filter wheels that are part of confocal hardware should be defined
    in confocal_config.yaml instead.

    Location: machine_configs/filter_wheels.yaml

    Validation rules:
    - Single wheel: name and id are optional (defaults: id=1, name="{Type} Wheel")
    - Multiple wheels: name and id are required for all wheels
    - Names must be unique
    - IDs must be unique
    - Type is always required
    """

    version: float = Field(1.0, description="Configuration format version")
    filter_wheels: List[FilterWheelDefinition] = Field(default_factory=list)

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
    def validate_filter_wheels(self) -> "FilterWheelRegistryConfig":
        """Validate filter wheels after object creation."""
        validate_filter_wheel_list(self.filter_wheels, context="Filter wheel")
        return self

    def get_wheel_by_name(self, name: str) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by user-friendly name."""
        for wheel in self.filter_wheels:
            if wheel.name == name:
                return wheel
        logger.debug(f"Filter wheel not found by name: '{name}'. Available: {self.get_wheel_names()}")
        return None

    def get_wheel_by_id(self, wheel_id: int) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by hardware ID."""
        for wheel in self.filter_wheels:
            if wheel.id == wheel_id:
                return wheel
        available_ids = [w.id for w in self.filter_wheels if w.id is not None]
        logger.debug(f"Filter wheel not found by ID: {wheel_id}. Available IDs: {available_ids}")
        return None

    def get_wheel_names(self) -> List[str]:
        """Get list of all filter wheel names for UI dropdowns."""
        return [wheel.name for wheel in self.filter_wheels if wheel.name is not None]

    def get_wheel_ids(self) -> List[int]:
        """Get list of all filter wheel IDs."""
        return [wheel.id for wheel in self.filter_wheels if wheel.id is not None]

    def get_first_wheel(self) -> Optional[FilterWheelDefinition]:
        """Get the first (or only) filter wheel.

        Useful for single-wheel systems.
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

    def get_wheels_by_type(self, wheel_type: FilterWheelType) -> List[FilterWheelDefinition]:
        """Get all filter wheels of a specific type."""
        return [wheel for wheel in self.filter_wheels if wheel.type == wheel_type]

    def get_excitation_wheels(self) -> List[FilterWheelDefinition]:
        """Get all excitation filter wheels."""
        return self.get_wheels_by_type(FilterWheelType.EXCITATION)

    def get_emission_wheels(self) -> List[FilterWheelDefinition]:
        """Get all emission filter wheels."""
        return self.get_wheels_by_type(FilterWheelType.EMISSION)
