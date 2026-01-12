"""Centralized feature flag access with validation.

Provides a unified interface for accessing feature flags defined in _def.py.
This module consolidates the scattered flag access patterns across the codebase
into a single, validated interface.

Usage:
    from squid.core.config.feature_flags import get_feature_flags

    flags = get_feature_flags()
    if flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
        # laser autofocus code
        ...

    # Get a flag with a default if not defined
    if flags.get("NEW_FEATURE", default=False):
        ...
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


@dataclass(frozen=True)
class FeatureFlag:
    """Metadata about a feature flag."""

    name: str
    category: str
    default: bool
    description: str


class FeatureFlags:
    """Centralized feature flag access with validation.

    Loads flag values from _def.py at initialization and provides
    validated access to feature flags.

    Categories:
        - hardware: Hardware-related features (laser AF, spinning disk, etc.)
        - ui: UI feature toggles (napari, display options)
        - acquisition: Acquisition behavior (autofocus, tracking)
        - debug: Debug/development features
        - encoder: Stage encoder settings
    """

    # Standard categories
    HARDWARE = "hardware"
    UI = "ui"
    ACQUISITION = "acquisition"
    DEBUG = "debug"
    ENCODER = "encoder"

    def __init__(self):
        """Initialize and load flag values from _def.py."""
        self._flags: Dict[str, FeatureFlag] = {}
        self._values: Dict[str, bool] = {}
        self._accessed: Set[str] = set()  # Track which flags are accessed
        self._register_known_flags()
        self._load_from_def()

    def _register_known_flags(self) -> None:
        """Register known feature flags with metadata."""
        # Hardware flags
        self._register("SUPPORT_LASER_AUTOFOCUS", self.HARDWARE, False, "Enable laser autofocus hardware support")
        self._register("ENABLE_SPINNING_DISK_CONFOCAL", self.HARDWARE, False, "Enable spinning disk confocal support")
        self._register("USE_LDI_SERIAL_CONTROL", self.HARDWARE, False, "Use LDI serial control for laser")
        self._register("USE_CELESTA_ETHERNET_CONTROL", self.HARDWARE, False, "Use Celesta ethernet control")
        self._register("USE_ANDOR_LASER_CONTROL", self.HARDWARE, False, "Use Andor laser control")
        self._register("USE_DRAGONFLY", self.HARDWARE, False, "Enable Dragonfly confocal support")
        self._register("ENABLE_CELLX", self.HARDWARE, False, "Enable CellX hardware")
        self._register("USE_SEPARATE_MCU_FOR_DAC", self.HARDWARE, False, "Use separate MCU for DAC")
        self._register("ENABLE_STROBE_OUTPUT", self.HARDWARE, False, "Enable strobe output")
        self._register("INVERTED_OBJECTIVE", self.HARDWARE, False, "Objective is inverted")

        # UI flags
        self._register("USE_NAPARI_FOR_LIVE_VIEW", self.UI, False, "Use napari for live view display")
        self._register("USE_NAPARI_FOR_MULTIPOINT", self.UI, True, "Use napari for multipoint display")
        self._register("USE_NAPARI_FOR_MOSAIC_DISPLAY", self.UI, True, "Use napari for mosaic display")
        self._register("USE_NAPARI_WELL_SELECTION", self.UI, False, "Use napari for well selection")
        self._register("SHOW_DAC_CONTROL", self.UI, False, "Show DAC control panel")
        self._register("SHOW_AUTOLEVEL_BTN", self.UI, False, "Show auto-level button")
        self._register("SHOW_LEGACY_DISPLACEMENT_MEASUREMENT_WINDOWS", self.UI, False, "Show legacy displacement windows")
        self._register("LASER_AF_DISPLAY_SPOT_IMAGE", self.UI, True, "Display laser AF spot image")

        # Acquisition flags
        self._register("ENABLE_TRACKING", self.ACQUISITION, False, "Enable object tracking")
        self._register("ENABLE_FLEXIBLE_MULTIPOINT", self.ACQUISITION, True, "Enable flexible multipoint widget")
        self._register("USE_OVERLAP_FOR_FLEXIBLE", self.ACQUISITION, True, "Use overlap for flexible multipoint")
        self._register("ENABLE_WELLPLATE_MULTIPOINT", self.ACQUISITION, True, "Enable wellplate multipoint widget")
        self._register("ENABLE_RECORDING", self.ACQUISITION, False, "Enable video recording")
        self._register("RESUME_LIVE_AFTER_ACQUISITION", self.ACQUISITION, True, "Resume live after acquisition")
        self._register("MULTIPOINT_DISPLAY_IMAGES", self.ACQUISITION, False, "Display images during multipoint")
        self._register("SORT_DURING_MULTIPOINT", self.ACQUISITION, False, "Sort coordinates during multipoint")
        self._register("MULTIPOINT_AUTOFOCUS_ENABLE_BY_DEFAULT", self.ACQUISITION, False, "Enable AF by default in multipoint")
        self._register(
            "MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT",
            self.ACQUISITION,
            False,
            "Enable reflection AF by default",
        )
        self._register(
            "MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT",
            self.ACQUISITION,
            False,
            "Enable contrast AF by default",
        )
        self._register(
            "RETRACT_OBJECTIVE_BEFORE_MOVING_TO_LOADING_POSITION",
            self.ACQUISITION,
            True,
            "Retract objective before loading position",
        )
        self._register("FOCUS_LOCK_AUTO_SEARCH_ENABLED", self.ACQUISITION, False, "Enable focus lock auto-search on loss")
        self._register("ENABLE_SEGMENTATION", self.ACQUISITION, True, "Enable image segmentation")
        self._register("USE_TRT_SEGMENTATION", self.ACQUISITION, False, "Use TensorRT for segmentation")

        # Debug flags
        self._register("PRINT_CAMERA_FPS", self.DEBUG, True, "Print camera FPS to console")
        self._register("ENABLE_PER_ACQUISITION_LOG", self.DEBUG, False, "Enable per-acquisition logging")
        self._register("LASER_AF_CHARACTERIZATION_MODE", self.DEBUG, False, "Enable laser AF characterization mode")
        self._register("CLASSIFICATION_TEST_MODE", self.DEBUG, False, "Enable classification test mode")
        self._register("AUTOLEVEL_DEFAULT_SETTING", self.DEBUG, False, "Default autolevel setting")
        self._register("TWO_CLASSIFICATION_MODELS", self.DEBUG, False, "Use two classification models")

        # Encoder flags (many of these)
        for axis in ["X", "Y", "Z", "THETA", "W"]:
            self._register(f"USE_ENCODER_{axis}", self.ENCODER, False, f"Use encoder for {axis} axis")
            self._register(f"HAS_ENCODER_{axis}", self.ENCODER, False, f"Has encoder for {axis} axis")

        for axis in ["X", "Y", "Z", "W"]:
            self._register(f"ENABLE_PID_{axis}", self.ENCODER, False, f"Enable PID for {axis} axis")
            self._register(f"ENCODER_FLIP_DIR_{axis}", self.ENCODER, False, f"Flip encoder direction for {axis}")

        self._register("HOMING_ENABLED_X", self.ENCODER, True, "Enable homing for X axis")
        self._register("HOMING_ENABLED_Y", self.ENCODER, True, "Enable homing for Y axis")
        self._register("HOMING_ENABLED_Z", self.ENCODER, False, "Enable homing for Z axis")

    def _register(self, name: str, category: str, default: bool, description: str) -> None:
        """Register a feature flag with metadata."""
        self._flags[name] = FeatureFlag(name, category, default, description)

    def _load_from_def(self) -> None:
        """Load flag values from _def.py for backwards compatibility."""
        try:
            import _def

            # Load all registered flags from _def
            for name, flag in self._flags.items():
                self._values[name] = getattr(_def, name, flag.default)

            # Also load any boolean attributes from _def that aren't registered
            # This ensures we don't break access to flags we haven't catalogued
            for attr_name in dir(_def):
                if attr_name.startswith("_"):
                    continue
                value = getattr(_def, attr_name, None)
                if isinstance(value, bool) and attr_name not in self._flags:
                    self._values[attr_name] = value
        except ImportError:
            _log.warning("Could not import _def.py; using default flag values")
            for name, flag in self._flags.items():
                self._values[name] = flag.default

    def refresh_from_def(self) -> None:
        """Reload flag values from _def.py to pick up runtime changes."""
        self._load_from_def()

    def is_enabled(self, flag_name: str) -> bool:
        """Check if a flag is enabled.

        Args:
            flag_name: Name of the feature flag

        Returns:
            True if the flag is enabled, False otherwise.
            Returns False for unknown flags (with a warning).
        """
        self.refresh_from_def()
        self._accessed.add(flag_name)

        if flag_name not in self._values:
            if flag_name not in self._flags:
                _log.warning(f"Unknown feature flag: {flag_name}")
            return False

        return self._values[flag_name]

    def get(self, flag_name: str, default: bool = False) -> bool:
        """Get a flag value with an explicit default.

        Args:
            flag_name: Name of the feature flag
            default: Default value if flag is not defined

        Returns:
            The flag value, or default if not defined.
        """
        self.refresh_from_def()
        self._accessed.add(flag_name)
        return self._values.get(flag_name, default)

    def get_category(self, flag_name: str) -> Optional[str]:
        """Get the category of a registered flag.

        Args:
            flag_name: Name of the feature flag

        Returns:
            Category string or None if not registered.
        """
        flag = self._flags.get(flag_name)
        return flag.category if flag else None

    def list_flags(self, category: Optional[str] = None) -> Dict[str, bool]:
        """List all flags, optionally filtered by category.

        Args:
            category: If provided, only return flags in this category

        Returns:
            Dictionary of flag names to current values.
        """
        if category is None:
            return dict(self._values)

        return {
            name: value for name, value in self._values.items() if self._flags.get(name, FeatureFlag(name, "", False, "")).category == category
        }

    def get_accessed_flags(self) -> Set[str]:
        """Get the set of flags that have been accessed during this session.

        Useful for debugging and understanding which flags are actually used.
        """
        return self._accessed.copy()


# Singleton instance
_feature_flags: Optional[FeatureFlags] = None


def get_feature_flags() -> FeatureFlags:
    """Get the global FeatureFlags instance.

    Returns:
        The singleton FeatureFlags instance.
    """
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlags()
    return _feature_flags


def reset_feature_flags() -> None:
    """Reset the feature flags singleton. Primarily for testing."""
    global _feature_flags
    _feature_flags = None
