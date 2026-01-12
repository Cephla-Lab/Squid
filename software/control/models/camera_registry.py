"""
Camera registry configuration models.

This module defines the camera registry that maps user-friendly camera names
to hardware identifiers (serial numbers). This allows users to configure
channels using camera names instead of serial numbers.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class CameraDefinition(BaseModel):
    """A camera in the system."""

    name: str = Field(..., description="User-friendly camera name")
    serial_number: str = Field(..., description="Hardware serial number")
    model: Optional[str] = Field(None, description="Camera model for display")

    model_config = {"extra": "forbid"}


class CameraRegistryConfig(BaseModel):
    """
    Registry of available cameras.

    This configuration maps user-friendly camera names to hardware identifiers,
    allowing users to configure acquisition channels by camera name rather than
    serial number.

    Location: machine_configs/cameras.yaml
    """

    version: float = Field(1.1, description="Configuration format version")
    cameras: List[CameraDefinition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def get_camera_by_name(self, name: str) -> Optional[CameraDefinition]:
        """Get camera definition by user-friendly name."""
        for camera in self.cameras:
            if camera.name == name:
                return camera
        return None

    def get_camera_by_sn(self, serial_number: str) -> Optional[CameraDefinition]:
        """Get camera definition by serial number."""
        for camera in self.cameras:
            if camera.serial_number == serial_number:
                return camera
        return None

    def get_camera_names(self) -> List[str]:
        """Get list of all camera names for UI dropdowns."""
        return [camera.name for camera in self.cameras]

    def get_serial_number(self, camera_name: str) -> Optional[str]:
        """Get serial number for a camera name."""
        camera = self.get_camera_by_name(camera_name)
        return camera.serial_number if camera else None
