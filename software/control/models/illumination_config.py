"""
Illumination channel configuration models.

These models define hardware-level illumination channel settings that are
machine-specific (not user-specific). They map illumination sources to
controller ports and serial command codes.
"""

import logging
from enum import Enum
from typing import ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from control._def import ILLUMINATION_CODE

logger = logging.getLogger(__name__)


class IlluminationType(str, Enum):
    """Type of illumination source."""

    EPI_ILLUMINATION = "epi_illumination"  # Fluorescence lasers
    TRANSILLUMINATION = "transillumination"  # LED matrix, brightfield


class IlluminationChannel(BaseModel):
    """Hardware-level definition of an illumination channel.

    This defines the physical illumination hardware, NOT user-facing acquisition settings.
    Fields like display_color and enabled belong in acquisition channel configs (user_profiles/).

    Controller ports:
    - D1-D5: Laser channels (epi-illumination)
    - USB1-USB8: LED matrix patterns (transillumination)

    Excitation filter wheel (optional):
    - Most systems don't have an excitation filter wheel
    - If present, the excitation filter is paired with the illumination channel
    - If single excitation wheel: only excitation_filter_position needed
    - If multiple excitation wheels: excitation_filter_wheel_id required
    """

    name: str = Field(..., min_length=1, description="Unique name for this illumination channel")
    type: IlluminationType = Field(..., description="Type of illumination")
    controller_port: str = Field(
        ...,
        pattern=r"^(D[1-8]|USB[1-8])$",
        description="Controller port (D1-D8 for lasers, USB1-USB8 for LED patterns)",
    )
    wavelength_nm: Optional[int] = Field(
        None, gt=0, description="Wavelength in nm (for epi-illumination channels, must be positive)"
    )
    intensity_calibration_file: Optional[str] = Field(
        None, description="Reference to calibration CSV file in intensity_calibrations/"
    )

    # Excitation filter (optional)
    excitation_filter_wheel_id: Optional[int] = Field(
        None,
        ge=1,
        description="Excitation filter wheel ID (required if multiple excitation wheels exist)",
    )
    excitation_filter_position: Optional[int] = Field(
        None,
        ge=1,
        description="Position in excitation filter wheel",
    )

    # Deprecated: use excitation_filter_wheel_id instead
    excitation_filter_wheel: Optional[str] = Field(
        None,
        description="[DEPRECATED] Use excitation_filter_wheel_id instead. Name of excitation filter wheel.",
    )

    model_config = {"extra": "allow"}  # Allow extra fields during transition


class IlluminationChannelConfig(BaseModel):
    """Root configuration for all illumination channels on a machine."""

    # All available controller ports in canonical order
    ALL_PORTS: ClassVar[List[str]] = [
        "USB1",
        "USB2",
        "USB3",
        "USB4",
        "USB5",
        "USB6",
        "USB7",
        "USB8",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "D8",
    ]

    version: float = Field(1.0, description="Configuration format version")
    controller_port_mapping: Dict[str, int] = Field(
        default_factory=lambda: {
            # Laser ports - reference constants from _def.py
            "D1": ILLUMINATION_CODE.ILLUMINATION_D1,
            "D2": ILLUMINATION_CODE.ILLUMINATION_D2,
            "D3": ILLUMINATION_CODE.ILLUMINATION_D3,
            "D4": ILLUMINATION_CODE.ILLUMINATION_D4,
            "D5": ILLUMINATION_CODE.ILLUMINATION_D5,
            # LED matrix patterns - reference constants from _def.py
            "USB1": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_FULL,
            "USB2": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF,
            "USB3": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF,
            "USB4": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR,
            "USB5": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA,
            "USB7": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF,
            "USB8": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF,
        },
        description="Mapping from controller port to source code",
    )
    channels: List[IlluminationChannel] = Field(
        default_factory=list, description="List of available illumination channels"
    )

    model_config = {"extra": "allow"}  # Allow extra fields during transition

    def get_source_code(self, channel: IlluminationChannel) -> int:
        """Get the source code for a channel based on controller port mapping.

        All channels (both laser and LED) use controller_port_mapping.
        D1-D5 for lasers, USB1-USB8 for LED matrix patterns.
        """
        source_code = self.controller_port_mapping.get(channel.controller_port)
        if source_code is not None:
            return source_code
        # Fallback for old format with direct source_code or led_matrix_pattern
        if hasattr(channel, "source_code"):
            return channel.source_code
        if hasattr(channel, "led_matrix_pattern") and channel.led_matrix_pattern:
            # Legacy: try to map pattern name to source code
            legacy_patterns = {
                "full": 0,
                "left_half": 1,
                "right_half": 2,
                "dark_field": 3,
                "low_na": 4,
                "top_half": 7,
                "bottom_half": 8,
            }
            return legacy_patterns.get(channel.led_matrix_pattern, 0)
        return 0  # Default for unknown

    def get_channel_by_name(self, name: str) -> Optional[IlluminationChannel]:
        """Get an illumination channel by name."""
        for ch in self.channels:
            if ch.name == name:
                return ch
        available = [ch.name for ch in self.channels]
        logger.debug(f"Illumination channel not found by name: '{name}'. Available: {available}")
        return None

    def get_channel_by_source_code(self, source_code: int) -> Optional[IlluminationChannel]:
        """Get an illumination channel by its source code."""
        for ch in self.channels:
            if self.get_source_code(ch) == source_code:
                return ch
        logger.debug(f"Illumination channel not found by source code: {source_code}")
        return None

    def get_available_ports(self) -> List[str]:
        """Get list of controller ports that have mappings (not N/A).

        Returns ports in canonical order (USB ports first, then D ports),
        filtered to only those present in controller_port_mapping.
        """
        return [p for p in self.ALL_PORTS if p in self.controller_port_mapping]


# Default display colors based on wavelength (used when generating default acquisition configs)
DEFAULT_WAVELENGTH_COLORS: Dict[int, str] = {
    405: "#20ADF8",  # Blue-violet
    488: "#1FFF00",  # Green
    561: "#FFCF00",  # Yellow-orange
    638: "#FF0000",  # Red
    730: "#770000",  # Deep red/NIR
}

DEFAULT_LED_COLOR = "#FFFFFF"  # White for LED matrix
