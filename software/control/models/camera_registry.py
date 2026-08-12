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

# Vocabulary matches the INI camera_type strings (see squid/config.py _old_camera_variant_to_enum)
KNOWN_CAMERA_TYPES = ["Toupcam", "FLIR", "Hamamatsu", "iDS", "TIS", "Tucsen", "Photometrics", "Andor", "Default"]
# Vocabulary matches control.utils.FlipVariant member values (use FlipVariant(value) to convert)
KNOWN_FLIP_VALUES = ["Vertical", "Horizontal", "Both"]
# Vocabulary matches squid.config.CameraPixelFormat member values
KNOWN_PIXEL_FORMATS = [
    "MONO8",
    "MONO10",
    "MONO12",
    "MONO14",
    "MONO16",
    "RGB24",
    "RGB32",
    "RGB48",
    "BAYER_RG8",
    "BAYER_RG12",
]


class CameraDefinition(BaseModel):
    """A camera in the system.

    For single-camera systems, name and id are optional (defaults applied).
    For multi-camera systems, name and id are required to distinguish cameras.
    """

    name: Optional[str] = Field(None, min_length=1, description="User-friendly camera name")
    id: Optional[int] = Field(None, ge=1, description="Camera ID for hardware bindings")
    serial_number: str = Field(..., min_length=1, description="Hardware serial number")
    model: Optional[str] = Field(None, description="Camera model for display")

    # Dual-camera fields. All optional; absent values fall back to the INI [CAMERA_CONFIG] section.
    type: Optional[str] = Field(None, description="Camera driver type (same vocabulary as INI camera_type)")
    hardware_trigger: bool = Field(True, description="Whether this camera's hardware trigger line is wired")
    rotate_image_angle: Optional[float] = Field(None, description="Per-camera rotation override")
    flip: Optional[str] = Field(None, description="Per-camera flip override (Vertical/Horizontal/Both)")
    crop_width: Optional[int] = Field(None, ge=1, description="Per-camera unbinned crop width override")
    crop_height: Optional[int] = Field(None, ge=1, description="Per-camera unbinned crop height override")
    default_pixel_format: Optional[str] = Field(None, description="Per-camera default pixel format override")
    default_binning: Optional[List[int]] = Field(None, description="Per-camera default binning override, [x, y]")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_dual_camera_fields(self) -> "CameraDefinition":
        if self.type is not None and self.type not in KNOWN_CAMERA_TYPES:
            raise ValueError(f"Unknown camera type '{self.type}'. Known: {KNOWN_CAMERA_TYPES}")
        if self.flip is not None and self.flip not in KNOWN_FLIP_VALUES:
            raise ValueError(f"Unknown flip value '{self.flip}'. Known: {KNOWN_FLIP_VALUES}")
        if self.default_pixel_format is not None and self.default_pixel_format not in KNOWN_PIXEL_FORMATS:
            raise ValueError(f"Unknown pixel format '{self.default_pixel_format}'. Known: {KNOWN_PIXEL_FORMATS}")
        if self.default_binning is not None:
            if len(self.default_binning) != 2 or any(b < 1 for b in self.default_binning):
                raise ValueError(f"default_binning must be [x, y] with positive ints, got {self.default_binning}")
        return self


class CameraRegistryConfig(BaseModel):
    """
    Registry of available cameras.

    This configuration maps user-friendly camera names to hardware identifiers,
    allowing users to configure acquisition channels by camera name rather than
    serial number.

    Location: machine_configs/cameras.yaml

    Validation rules:
    - Single camera: name and id are optional (defaults: id=1, name="Camera")
    - Multiple cameras: name, id and type are required for all cameras
      (type cannot be inferred from the single INI camera_type when cameras differ)
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
                if cam.type is None:
                    raise ValueError(
                        f"Camera at index {i} (serial: {cam.serial_number}) missing required 'type' "
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
