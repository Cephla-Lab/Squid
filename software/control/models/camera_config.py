"""
Camera mappings configuration models.

These models define camera-to-hardware bindings, including dichroic positions
and filter wheel associations.
"""

from typing import Dict, Optional

from pydantic import BaseModel, Field


class ConfocalCameraSettings(BaseModel):
    """Confocal-specific settings for a camera."""

    filter_wheel_id: Optional[int] = Field(None, description="Filter wheel ID associated with this camera")
    dichroic_position: Optional[int] = Field(None, description="Dichroic position when this camera is selected")

    model_config = {"extra": "allow"}  # Allow additional confocal-specific settings


class CameraHardwareInfo(BaseModel):
    """Hardware connections for a camera."""

    filter_wheel_id: Optional[int] = Field(None, description="Filter wheel ID (for systems without confocal)")
    confocal_settings: Optional[ConfocalCameraSettings] = Field(
        None, description="Confocal-specific settings (only if confocal present)"
    )

    model_config = {"extra": "forbid"}


class CameraPropertyBindings(BaseModel):
    """Properties set when a camera combination is selected."""

    dichroic_position: Optional[int] = Field(None, description="Dichroic position (for systems without confocal)")
    confocal_settings: Optional[ConfocalCameraSettings] = Field(
        None, description="Confocal-specific bindings (only if confocal present)"
    )

    model_config = {"extra": "forbid"}


class CameraMappingsConfig(BaseModel):
    """
    Camera selection to hardware bindings.

    Maps camera combinations (camera_1, camera_2, camera_1_2) to their
    associated hardware settings like dichroic positions and filter wheels.
    """

    version: int = Field(1, description="Configuration format version")

    hardware_connection_info: Dict[str, CameraHardwareInfo] = Field(
        default_factory=dict,
        description="Camera ID -> hardware connection info",
    )
    property_bindings: Dict[str, CameraPropertyBindings] = Field(
        default_factory=dict,
        description="Camera ID -> properties to set when selected",
    )

    model_config = {"extra": "forbid"}

    def get_hardware_info(self, camera_id: str) -> Optional[CameraHardwareInfo]:
        """Get hardware info for a camera."""
        return self.hardware_connection_info.get(camera_id)

    def get_bindings(self, camera_id: str) -> Optional[CameraPropertyBindings]:
        """Get property bindings for a camera combination."""
        return self.property_bindings.get(camera_id)

    def has_confocal_in_light_path(self, camera_id: str = "camera_1") -> bool:
        """
        Check if confocal is in the light path for a given camera.

        Returns True if the camera has confocal_settings defined in either
        hardware_connection_info or property_bindings.
        """
        hw_info = self.hardware_connection_info.get(camera_id)
        if hw_info and hw_info.confocal_settings:
            return True
        bindings = self.property_bindings.get(camera_id)
        if bindings and bindings.confocal_settings:
            return True
        return False
