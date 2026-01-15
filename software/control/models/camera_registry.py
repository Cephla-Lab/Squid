"""
Camera registry configuration models.

This module defines the camera registry that maps user-friendly camera names
to hardware identifiers (serial numbers). This allows users to configure
channels using camera names instead of serial numbers.
"""

import logging
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class CameraDefinition(BaseModel):
    """A camera in the system.

    For single-camera systems, name and id are optional (defaults applied).
    For multi-camera systems, name and id are required to distinguish cameras.
    """

    name: Optional[str] = Field(None, min_length=1, description="User-friendly camera name")
    id: Optional[int] = Field(None, ge=1, description="Camera ID for hardware bindings")
    serial_number: str = Field(..., min_length=1, description="Hardware serial number")
    model: Optional[str] = Field(None, description="Camera model for display")

    model_config = {"extra": "forbid"}


class CameraRegistryConfig(BaseModel):
    """
    Registry of available cameras.

    This configuration maps user-friendly camera names to hardware identifiers,
    allowing users to configure acquisition channels by camera name rather than
    serial number.

    Location: machine_configs/cameras.yaml

    Validation rules:
    - Single camera: name and id are optional (defaults: id=1, name="Camera")
    - Multiple cameras: name and id are required for all cameras
    - Names must be unique
    - IDs must be unique
    - Serial numbers must be unique
    """

    version: float = Field(1.0, description="Configuration format version")
    cameras: List[CameraDefinition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def apply_single_camera_defaults(cls, data: Any) -> Any:
        """Apply defaults for single-camera systems before object creation.

        This transforms raw data before CameraDefinition objects are created,
        avoiding mutation of input objects. Handles both dict data (from YAML)
        and Pydantic model instances (from code).
        """
        if not isinstance(data, dict):
            return data

        cameras = data.get("cameras", [])
        if len(cameras) == 1:
            camera = cameras[0]
            # Handle both dict and Pydantic model inputs
            if isinstance(camera, dict):
                if camera.get("id") is None:
                    camera["id"] = 1
                if camera.get("name") is None:
                    camera["name"] = "Camera"
            elif isinstance(camera, CameraDefinition):
                # Convert to dict to apply defaults, avoiding mutation of original
                camera_dict = camera.model_dump()
                if camera_dict.get("id") is None:
                    camera_dict["id"] = 1
                if camera_dict.get("name") is None:
                    camera_dict["name"] = "Camera"
                data["cameras"] = [camera_dict]

        return data

    @model_validator(mode="after")
    def validate_cameras(self) -> "CameraRegistryConfig":
        """Validate cameras after object creation."""
        if len(self.cameras) == 0:
            return self

        if len(self.cameras) > 1:
            # Multiple cameras: require id and name for all
            for i, cam in enumerate(self.cameras):
                if cam.id is None:
                    raise ValueError(
                        f"Camera at index {i} (serial: {cam.serial_number}) missing required 'id' "
                        f"(required when multiple cameras exist)"
                    )
                if cam.name is None:
                    raise ValueError(
                        f"Camera at index {i} (serial: {cam.serial_number}) missing required 'name' "
                        f"(required when multiple cameras exist)"
                    )

        # Validate uniqueness
        names = [c.name for c in self.cameras if c.name is not None]
        ids = [c.id for c in self.cameras if c.id is not None]
        serials = [c.serial_number for c in self.cameras]

        if len(names) != len(set(names)):
            duplicates = [n for n in set(names) if names.count(n) > 1]
            raise ValueError(f"Camera names must be unique. Duplicates: {duplicates}")

        if len(ids) != len(set(ids)):
            duplicates = [i for i in set(ids) if ids.count(i) > 1]
            raise ValueError(f"Camera IDs must be unique. Duplicates: {duplicates}")

        if len(serials) != len(set(serials)):
            duplicates = [s for s in set(serials) if serials.count(s) > 1]
            raise ValueError(f"Camera serial numbers must be unique. Duplicates: {duplicates}")

        return self

    def get_camera_by_name(self, name: str) -> Optional[CameraDefinition]:
        """Get camera definition by user-friendly name."""
        for camera in self.cameras:
            if camera.name == name:
                return camera
        logger.debug(f"Camera not found by name: '{name}'. Available: {self.get_camera_names()}")
        return None

    def get_camera_by_id(self, camera_id: int) -> Optional[CameraDefinition]:
        """Get camera definition by ID."""
        for camera in self.cameras:
            if camera.id == camera_id:
                return camera
        available_ids = [c.id for c in self.cameras if c.id is not None]
        logger.debug(f"Camera not found by ID: {camera_id}. Available IDs: {available_ids}")
        return None

    def get_camera_by_sn(self, serial_number: str) -> Optional[CameraDefinition]:
        """Get camera definition by serial number."""
        for camera in self.cameras:
            if camera.serial_number == serial_number:
                return camera
        logger.debug(f"Camera not found by serial number: '{serial_number}'")
        return None

    def get_camera_names(self) -> List[str]:
        """Get list of all camera names for UI dropdowns."""
        return [camera.name for camera in self.cameras if camera.name is not None]

    def get_camera_ids(self) -> List[int]:
        """Get list of all camera IDs."""
        return [camera.id for camera in self.cameras if camera.id is not None]

    def get_serial_number(self, camera_name: str) -> Optional[str]:
        """Get serial number for a camera name."""
        camera = self.get_camera_by_name(camera_name)
        return camera.serial_number if camera else None
