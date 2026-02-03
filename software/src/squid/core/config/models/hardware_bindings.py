"""
Hardware bindings configuration models.

This module defines the bindings between hardware components, such as
which filter wheel is associated with which camera.

Uses source-qualified references to allow each hardware source (confocal,
standalone) to have its own namespace, enabling true separation of concerns.

Ported from upstream commit 171aed9b with import path adjustments.
"""

import logging
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

logger = logging.getLogger(__name__)


class FilterWheelSource(str, Enum):
    """Source of a filter wheel - confocal system or standalone."""

    CONFOCAL = "confocal"
    STANDALONE = "standalone"


# Legacy constants for backwards compatibility
FILTER_WHEEL_SOURCE_CONFOCAL = FilterWheelSource.CONFOCAL.value
FILTER_WHEEL_SOURCE_STANDALONE = FilterWheelSource.STANDALONE.value


class FilterWheelReference(BaseModel):
    """
    Reference to a filter wheel with source qualification.

    A reference must specify exactly one of 'id' or 'name' (not both).
    This type is immutable (frozen) to prevent post-construction invariant violations.
    """

    source: FilterWheelSource = Field(..., description="Source: 'confocal' or 'standalone'")
    id: Optional[int] = Field(None, ge=1, description="Filter wheel ID (mutually exclusive with name)")
    name: Optional[str] = Field(None, min_length=1, description="Filter wheel name (mutually exclusive with id)")

    model_config = {"extra": "forbid", "frozen": True}

    @field_validator("source", mode="before")
    @classmethod
    def coerce_source(cls, v: Any) -> FilterWheelSource:
        """Allow string input for source field, converting to enum."""
        if isinstance(v, str):
            try:
                return FilterWheelSource(v)
            except ValueError:
                valid = [s.value for s in FilterWheelSource]
                raise ValueError(f"Invalid source '{v}'. Must be one of: {sorted(valid)}")
        return v

    @model_validator(mode="after")
    def validate_reference(self) -> "FilterWheelReference":
        """Validate that exactly one of id or name is specified."""
        if self.id is None and self.name is None:
            raise ValueError("Either 'id' or 'name' must be specified")
        if self.id is not None and self.name is not None:
            raise ValueError("Cannot specify both 'id' and 'name' - use one or the other")
        return self

    @classmethod
    def parse(cls, ref: str) -> "FilterWheelReference":
        """Parse 'source.identifier' format."""
        if "." not in ref:
            raise ValueError(
                f"Invalid reference '{ref}'. Expected 'source.id' or 'source.name' "
                f"(e.g., 'confocal.1' or 'standalone.Emission Wheel')"
            )

        source, identifier = ref.split(".", 1)
        source = source.strip()
        identifier = identifier.strip()

        if not source:
            raise ValueError(f"Invalid reference '{ref}': source is empty")
        if not identifier:
            raise ValueError(f"Invalid reference '{ref}': identifier is empty")

        if identifier.isdigit():
            return cls(source=source, id=int(identifier))
        return cls(source=source, name=identifier)

    def to_string(self) -> str:
        """Convert back to string format."""
        identifier = self.id if self.id is not None else self.name
        return f"{self.source.value}.{identifier}"

    def __str__(self) -> str:
        return self.to_string()

    def __hash__(self) -> int:
        return hash((self.source, self.id, self.name))


class HardwareBindingsConfig(BaseModel):
    """
    Hardware bindings configuration.

    Defines relationships between hardware components using source-qualified
    references. Each source (confocal, standalone) has its own ID namespace.

    Location: machine_configs/hardware_bindings.yaml
    """

    version: float = Field(1.0, description="Configuration format version")

    emission_filter_wheels: Dict[int, FilterWheelReference] = Field(
        default_factory=dict,
        description="Camera ID -> source-qualified wheel reference",
    )

    model_config = {"extra": "forbid"}

    @field_validator("emission_filter_wheels", mode="before")
    @classmethod
    def parse_reference_strings(cls, v: Any) -> Dict[int, FilterWheelReference]:
        """Parse string references from YAML into FilterWheelReference objects."""
        if not isinstance(v, dict):
            return v

        result = {}
        errors = []
        for camera_id, ref in v.items():
            try:
                camera_id = int(camera_id)
            except (ValueError, TypeError):
                errors.append(f"Invalid camera ID '{camera_id}': must be an integer")
                continue

            if isinstance(ref, str):
                try:
                    result[camera_id] = FilterWheelReference.parse(ref)
                except ValueError as e:
                    errors.append(f"Camera {camera_id}: {e}")
            elif isinstance(ref, FilterWheelReference):
                result[camera_id] = ref
            elif isinstance(ref, dict):
                try:
                    result[camera_id] = FilterWheelReference(**ref)
                except Exception as e:
                    errors.append(f"Camera {camera_id}: {e}")
            else:
                errors.append(f"Camera {camera_id}: expected string or dict, got {type(ref).__name__}")

        if errors:
            raise ValueError("Invalid emission wheel references:\n  " + "\n  ".join(errors))
        return result

    @field_serializer("emission_filter_wheels")
    def serialize_references(self, refs: Dict[int, FilterWheelReference]) -> Dict[int, str]:
        """Serialize FilterWheelReference objects to strings for YAML output."""
        return {camera_id: ref.to_string() for camera_id, ref in refs.items()}

    def get_emission_wheel_ref(self, camera_id: int) -> Optional[FilterWheelReference]:
        """Get emission filter wheel reference for a camera."""
        return self.emission_filter_wheels.get(camera_id)

    def get_all_emission_wheel_refs(self) -> Dict[int, FilterWheelReference]:
        """Get all emission wheel bindings."""
        return dict(self.emission_filter_wheels)

    def set_emission_wheel_binding(
        self,
        camera_id: int,
        source: FilterWheelSource,
        wheel_id: Optional[int] = None,
        wheel_name: Optional[str] = None,
    ) -> None:
        """Set emission wheel binding for a camera."""
        ref = FilterWheelReference(source=source, id=wheel_id, name=wheel_name)
        self.emission_filter_wheels[camera_id] = ref

    def remove_emission_wheel_binding(self, camera_id: int) -> bool:
        """Remove emission wheel binding for a camera."""
        if camera_id in self.emission_filter_wheels:
            del self.emission_filter_wheels[camera_id]
            return True
        return False
