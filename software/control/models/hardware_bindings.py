"""
Hardware bindings configuration models.

This module defines the bindings between hardware components, such as
which filter wheel is associated with which camera.

Uses source-qualified references to allow each hardware source (confocal,
standalone) to have its own namespace, enabling true separation of concerns.
"""

import logging
from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# Source identifiers
FILTER_WHEEL_SOURCE_CONFOCAL = "confocal"
FILTER_WHEEL_SOURCE_STANDALONE = "standalone"

VALID_SOURCES = {FILTER_WHEEL_SOURCE_CONFOCAL, FILTER_WHEEL_SOURCE_STANDALONE}


class FilterWheelReference(BaseModel):
    """
    Reference to a filter wheel with source qualification.

    A reference must specify exactly one of 'id' or 'name' (not both).

    Examples:
        - FilterWheelReference(source="confocal", id=1)
        - FilterWheelReference(source="standalone", name="Emission Wheel")
        - FilterWheelReference.parse("confocal.1")
        - FilterWheelReference.parse("standalone.Emission Wheel")
    """

    source: str = Field(..., description="Source: 'confocal' or 'standalone'")
    id: Optional[int] = Field(None, ge=1, description="Filter wheel ID (mutually exclusive with name)")
    name: Optional[str] = Field(None, min_length=1, description="Filter wheel name (mutually exclusive with id)")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_reference(self) -> "FilterWheelReference":
        """Validate that source is valid and exactly one of id or name is specified."""
        if self.source not in VALID_SOURCES:
            raise ValueError(f"Invalid source '{self.source}'. Must be one of: {sorted(VALID_SOURCES)}")
        if self.id is None and self.name is None:
            raise ValueError("Either 'id' or 'name' must be specified")
        if self.id is not None and self.name is not None:
            raise ValueError("Cannot specify both 'id' and 'name' - use one or the other")
        return self

    @classmethod
    def parse(cls, ref: str) -> "FilterWheelReference":
        """
        Parse 'source.identifier' format.

        Args:
            ref: Reference string like 'confocal.1' or 'standalone.Emission Wheel'

        Returns:
            FilterWheelReference instance

        Raises:
            ValueError: If format is invalid
        """
        if "." not in ref:
            raise ValueError(
                f"Invalid reference '{ref}'. Expected 'source.id' or 'source.name' "
                f"(e.g., 'confocal.1' or 'standalone.Emission Wheel')"
            )

        # Split on first dot only (name might contain dots)
        source, identifier = ref.split(".", 1)
        source = source.strip()
        identifier = identifier.strip()

        if not source:
            raise ValueError(f"Invalid reference '{ref}': source is empty")
        if not identifier:
            raise ValueError(f"Invalid reference '{ref}': identifier is empty")

        # Check if identifier is a number (ID) or string (name)
        if identifier.isdigit():
            return cls(source=source, id=int(identifier))
        return cls(source=source, name=identifier)

    def to_string(self) -> str:
        """Convert back to string format."""
        identifier = self.id if self.id is not None else self.name
        return f"{self.source}.{identifier}"

    def __str__(self) -> str:
        return self.to_string()


class HardwareBindingsConfig(BaseModel):
    """
    Hardware bindings configuration.

    Defines relationships between hardware components using source-qualified
    references. Each source (confocal, standalone) has its own ID namespace,
    so there are no global ID conflicts.

    Location: machine_configs/hardware_bindings.yaml

    Example:
        ```yaml
        version: 1.1
        emission_filter_wheels:
          1: confocal.1           # camera 1 → confocal's wheel 1
          2: standalone.1         # camera 2 → standalone's wheel 1
        ```

    Or with names:
        ```yaml
        emission_filter_wheels:
          1: "confocal.Emission"
          2: "standalone.Side Emission"
        ```
    """

    version: float = Field(1.1, description="Configuration format version")

    emission_filter_wheels: Dict[int, str] = Field(
        default_factory=dict,
        description="Camera ID -> source-qualified wheel reference (e.g., 'confocal.1')",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_references(self) -> "HardwareBindingsConfig":
        """Validate all reference strings at load time.

        This catches configuration errors early rather than when the
        reference is first accessed.
        """
        errors = []
        for camera_id, ref_str in self.emission_filter_wheels.items():
            try:
                FilterWheelReference.parse(ref_str)
            except ValueError as e:
                errors.append(f"Camera {camera_id}: {e}")

        if errors:
            raise ValueError(f"Invalid emission wheel references:\n  " + "\n  ".join(errors))
        return self

    def get_emission_wheel_ref(self, camera_id: int) -> Optional[FilterWheelReference]:
        """
        Get emission filter wheel reference for a camera.

        Args:
            camera_id: Camera ID

        Returns:
            FilterWheelReference if binding exists, None otherwise
        """
        ref_str = self.emission_filter_wheels.get(camera_id)
        if ref_str is None:
            return None
        try:
            return FilterWheelReference.parse(ref_str)
        except ValueError as e:
            logger.warning(f"Invalid emission wheel reference for camera {camera_id}: {e}")
            return None

    def get_all_emission_wheel_refs(self) -> Dict[int, FilterWheelReference]:
        """
        Get all emission wheel bindings.

        Returns:
            Dict mapping camera ID to FilterWheelReference
        """
        result = {}
        for camera_id, ref_str in self.emission_filter_wheels.items():
            try:
                result[camera_id] = FilterWheelReference.parse(ref_str)
            except ValueError as e:
                logger.warning(f"Skipping invalid reference for camera {camera_id}: {e}")
        return result

    def set_emission_wheel_binding(
        self,
        camera_id: int,
        source: str,
        wheel_id: Optional[int] = None,
        wheel_name: Optional[str] = None,
    ) -> None:
        """
        Set emission wheel binding for a camera.

        Args:
            camera_id: Camera ID
            source: Filter wheel source ('confocal' or 'standalone')
            wheel_id: Filter wheel ID (mutually exclusive with wheel_name)
            wheel_name: Filter wheel name (mutually exclusive with wheel_id)
        """
        ref = FilterWheelReference(source=source, id=wheel_id, name=wheel_name)
        self.emission_filter_wheels[camera_id] = ref.to_string()
