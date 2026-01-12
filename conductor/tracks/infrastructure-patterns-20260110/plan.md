# Infrastructure Patterns Implementation Plan

## Overview

This plan addresses cross-cutting infrastructure improvements for the Squid codebase. It assumes the multipoint refactor (`conductor/tracks/multipoint-refactor-20251230/`) has been completed.

---

## Phase 1: Core Infrastructure

### 1.1 Event Subscription Decorator

**File:** `software/src/squid/core/events.py`

Add at the end of the file (after `EventBus` class):

```python
from typing import Callable, Type, TypeVar, List, Tuple, Any
from functools import wraps

def handles(*event_types: Type[Event]) -> Callable:
    """
    Decorator to mark a method as an event handler.

    Usage:
        class MyService(EventSubscriberMixin):
            @handles(SomeCommand, AnotherCommand)
            def _on_commands(self, event: Event) -> None:
                ...

    The class must call _auto_subscribe(event_bus) to wire handlers.
    """
    def decorator(method: Callable) -> Callable:
        if not hasattr(method, '_handled_events'):
            method._handled_events = []
        method._handled_events.extend(event_types)
        return method
    return decorator


class EventSubscriberMixin:
    """
    Mixin for classes that use @handles decorator.

    Call _auto_subscribe(event_bus) after initialization.
    Call _auto_unsubscribe(event_bus) during cleanup.
    """
    _auto_subscriptions: List[Tuple[Type[Event], Callable]]

    def _auto_subscribe(self, event_bus: "EventBus") -> None:
        """Subscribe all @handles decorated methods to the event bus."""
        if not hasattr(self, '_auto_subscriptions'):
            self._auto_subscriptions = []

        for name in dir(self):
            if name.startswith('_'):
                try:
                    method = getattr(self, name, None)
                except Exception:
                    continue
                if callable(method) and hasattr(method, '_handled_events'):
                    for event_type in method._handled_events:
                        event_bus.subscribe(event_type, method)
                        self._auto_subscriptions.append((event_type, method))

    def _auto_unsubscribe(self, event_bus: "EventBus") -> None:
        """Unsubscribe all auto-subscribed handlers."""
        if hasattr(self, '_auto_subscriptions'):
            for event_type, handler in self._auto_subscriptions:
                try:
                    event_bus.unsubscribe(event_type, handler)
                except Exception:
                    pass
            self._auto_subscriptions.clear()

    def _get_handled_events(self) -> List[Type[Event]]:
        """Return list of event types this instance handles (for introspection)."""
        events = []
        for name in dir(self):
            if name.startswith('_'):
                try:
                    method = getattr(self, name, None)
                except Exception:
                    continue
                if callable(method) and hasattr(method, '_handled_events'):
                    events.extend(method._handled_events)
        return events
```

**Update exports** at top of file:
```python
__all__ = [
    "Event",
    "EventBus",
    "handles",
    "EventSubscriberMixin",
    # ... existing exports
]
```

**Test file:** `software/tests/unit/squid/core/test_event_decorator.py`

```python
import pytest
from squid.core.events import Event, EventBus, handles, EventSubscriberMixin
from dataclasses import dataclass

@dataclass(frozen=True)
class TestEventA(Event):
    value: int

@dataclass(frozen=True)
class TestEventB(Event):
    message: str

class TestSubscriber(EventSubscriberMixin):
    def __init__(self):
        self.received_a = []
        self.received_b = []

    @handles(TestEventA)
    def _on_event_a(self, event: TestEventA):
        self.received_a.append(event.value)

    @handles(TestEventB)
    def _on_event_b(self, event: TestEventB):
        self.received_b.append(event.message)

def test_auto_subscribe():
    bus = EventBus()
    sub = TestSubscriber()
    sub._auto_subscribe(bus)

    bus.publish(TestEventA(value=42))
    bus.publish(TestEventB(message="hello"))

    # Allow dispatch
    import time
    time.sleep(0.1)

    assert sub.received_a == [42]
    assert sub.received_b == ["hello"]

def test_auto_unsubscribe():
    bus = EventBus()
    sub = TestSubscriber()
    sub._auto_subscribe(bus)
    sub._auto_unsubscribe(bus)

    bus.publish(TestEventA(value=99))

    import time
    time.sleep(0.1)

    assert sub.received_a == []
```

---

### 1.2 Feature Flags Registry

**New file:** `software/src/squid/core/config/feature_flags.py`

```python
"""
Centralized feature flag registry.

Provides typed access to feature flags with validation.
Flags are loaded from _def.py for backwards compatibility.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, Set
from enum import Enum, auto

import squid.logging

_log = squid.logging.get_logger(__name__)


class FeatureCategory(Enum):
    """Categories for organizing feature flags."""
    HARDWARE = auto()      # Physical hardware support
    UI = auto()            # UI feature toggles
    ACQUISITION = auto()   # Acquisition workflow features
    INTEGRATION = auto()   # Third-party integrations
    DEBUG = auto()         # Debug/development features


# All known flags with their categories and defaults
FLAG_DEFINITIONS: Dict[str, tuple] = {
    # HARDWARE flags
    "SUPPORT_LASER_AUTOFOCUS": (FeatureCategory.HARDWARE, False),
    "SUPPORT_SCIMICROSCOPY_LED_ARRAY": (FeatureCategory.HARDWARE, False),
    "HAS_OBJECTIVE_PIEZO": (FeatureCategory.HARDWARE, False),

    # ENABLE flags
    "ENABLE_TRACKING": (FeatureCategory.ACQUISITION, False),
    "ENABLE_FLEXIBLE_MULTIPOINT": (FeatureCategory.ACQUISITION, True),
    "ENABLE_WELLPLATE_MULTIPOINT": (FeatureCategory.ACQUISITION, True),
    "ENABLE_SPINNING_DISK_CONFOCAL": (FeatureCategory.HARDWARE, False),
    "ENABLE_NL5": (FeatureCategory.HARDWARE, False),
    "ENABLE_RECORDING": (FeatureCategory.ACQUISITION, False),
    "ENABLE_PER_ACQUISITION_LOG": (FeatureCategory.DEBUG, False),
    "ENABLE_STROBE_OUTPUT": (FeatureCategory.HARDWARE, False),
    "ENABLE_CELLX": (FeatureCategory.INTEGRATION, False),
    "ENABLE_CLICK_TO_MOVE_BY_DEFAULT": (FeatureCategory.UI, False),

    # RUN flags
    "RUN_FLUIDICS": (FeatureCategory.HARDWARE, False),

    # USE flags
    "USE_NAPARI_FOR_LIVE_VIEW": (FeatureCategory.UI, True),
    "USE_NAPARI_FOR_LIVE_CONTROL": (FeatureCategory.UI, True),
    "USE_NAPARI_FOR_MOSAIC_DISPLAY": (FeatureCategory.UI, True),
    "USE_NAPARI_FOR_MULTIPOINT": (FeatureCategory.UI, True),
    "USE_NAPARI_WELL_SELECTION": (FeatureCategory.UI, True),
    "USE_ENCODER": (FeatureCategory.HARDWARE, False),
    "USE_ENCODER_X": (FeatureCategory.HARDWARE, False),
    "USE_ENCODER_Y": (FeatureCategory.HARDWARE, False),
    "USE_ENCODER_Z": (FeatureCategory.HARDWARE, False),
    "USE_ENCODER_THETA": (FeatureCategory.HARDWARE, False),
    "USE_PRIOR_STAGE": (FeatureCategory.HARDWARE, False),
    "USE_XERYON": (FeatureCategory.HARDWARE, False),
    "USE_DRAGONFLY": (FeatureCategory.HARDWARE, False),
    "USE_EMISSION_FILTER_WHEEL": (FeatureCategory.HARDWARE, False),
    "USE_OVERLAP_FOR_FLEXIBLE": (FeatureCategory.ACQUISITION, True),
    "USE_PIEZO_FOR_ZSTACKS": (FeatureCategory.ACQUISITION, False),
    "MULTIPOINT_USE_PIEZO_FOR_ZSTACKS": (FeatureCategory.ACQUISITION, False),
    "USE_TEMPLATE_MULTIPOINT": (FeatureCategory.ACQUISITION, False),
    "USE_JUPYTER_CONSOLE": (FeatureCategory.DEBUG, False),
    "USE_LDI_SERIAL_CONTROL": (FeatureCategory.HARDWARE, False),
    "USE_CELESTA_ETHERNET_CONTROL": (FeatureCategory.HARDWARE, False),
    "USE_ANDOR_LASER_CONTROL": (FeatureCategory.HARDWARE, False),
    "USE_AOUT": (FeatureCategory.HARDWARE, False),
    "USE_DOUT": (FeatureCategory.HARDWARE, False),
    "USE_MULTIPROCESSING": (FeatureCategory.DEBUG, False),
}


@dataclass
class FeatureFlags:
    """
    Centralized feature flag registry.

    Usage:
        flags = FeatureFlags.from_config()
        if flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            ...
    """
    _flags: Dict[str, bool] = field(default_factory=dict)
    _unknown_flags: Set[str] = field(default_factory=set)

    @classmethod
    def from_config(cls, config_module: Optional[Any] = None) -> "FeatureFlags":
        """
        Load flags from _def.py config module.

        Args:
            config_module: Optional module to load from. Defaults to _def.
        """
        if config_module is None:
            try:
                import _def as config_module
            except ImportError:
                _log.warning("Could not import _def.py, using defaults")
                config_module = None

        flags = {}
        for name, (category, default) in FLAG_DEFINITIONS.items():
            if config_module is not None:
                flags[name] = getattr(config_module, name, default)
            else:
                flags[name] = default

        return cls(_flags=flags)

    def is_enabled(self, flag_name: str) -> bool:
        """
        Check if a feature flag is enabled.

        Args:
            flag_name: Name of the flag (e.g., "SUPPORT_LASER_AUTOFOCUS")

        Returns:
            True if enabled, False otherwise

        Raises:
            KeyError: If flag_name is not a known flag
        """
        if flag_name not in FLAG_DEFINITIONS:
            if flag_name not in self._unknown_flags:
                _log.warning(f"Unknown feature flag: {flag_name}")
                self._unknown_flags.add(flag_name)
            return False
        return self._flags.get(flag_name, False)

    def get_enabled_flags(self, category: Optional[FeatureCategory] = None) -> Set[str]:
        """Get set of enabled flag names, optionally filtered by category."""
        enabled = set()
        for name, (cat, _) in FLAG_DEFINITIONS.items():
            if category is not None and cat != category:
                continue
            if self._flags.get(name, False):
                enabled.add(name)
        return enabled

    def __repr__(self) -> str:
        enabled = [k for k, v in self._flags.items() if v]
        return f"FeatureFlags(enabled={enabled})"


# Global instance (lazy-loaded)
_feature_flags: Optional[FeatureFlags] = None


def get_feature_flags() -> FeatureFlags:
    """Get the global FeatureFlags instance."""
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlags.from_config()
    return _feature_flags


def reset_feature_flags() -> None:
    """Reset the global instance (for testing)."""
    global _feature_flags
    _feature_flags = None
```

**Create directory and __init__.py:**
```
software/src/squid/core/config/__init__.py
```

```python
from .feature_flags import (
    FeatureFlags,
    FeatureCategory,
    get_feature_flags,
    reset_feature_flags,
)

__all__ = [
    "FeatureFlags",
    "FeatureCategory",
    "get_feature_flags",
    "reset_feature_flags",
]
```

---

## Phase 2: Backend Refactors

### 2.1 Microcontroller Axis Parameterization

**File:** `software/src/squid/backend/microcontroller.py`

Add at top of file after imports:

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict

class StageAxis(Enum):
    """Enumeration of stage axes."""
    X = "x"
    Y = "y"
    Z = "z"
    THETA = "theta"
    W = "w"

@dataclass
class AxisConfig:
    """Configuration for a single axis."""
    move_cmd: int
    move_to_cmd: Optional[int]
    axis_id: int
    home_direction: int = 0  # 0 = negative, 1 = positive
```

Add axis configuration mapping in `Microcontroller.__init__`:

```python
self._axis_config: Dict[StageAxis, AxisConfig] = {
    StageAxis.X: AxisConfig(CMD_SET.MOVE_X, CMD_SET.MOVETO_X, AXIS.X),
    StageAxis.Y: AxisConfig(CMD_SET.MOVE_Y, CMD_SET.MOVETO_Y, AXIS.Y),
    StageAxis.Z: AxisConfig(CMD_SET.MOVE_Z, CMD_SET.MOVETO_Z, AXIS.Z),
    StageAxis.THETA: AxisConfig(CMD_SET.MOVE_THETA, None, AXIS.THETA),
    StageAxis.W: AxisConfig(CMD_SET.MOVE_W, CMD_SET.MOVETO_W, AXIS.W),
}
```

Add generic methods:

```python
def move_axis_usteps(self, axis: StageAxis, usteps: int) -> None:
    """Move specified axis by given microsteps."""
    config = self._axis_config[axis]
    self._move_axis_usteps(usteps, config.move_cmd)

def move_axis_to_usteps(self, axis: StageAxis, usteps: int) -> None:
    """Move specified axis to absolute position in microsteps."""
    config = self._axis_config[axis]
    if config.move_to_cmd is None:
        raise ValueError(f"Axis {axis} does not support move_to")
    self._move_axis_to_usteps(usteps, config.move_to_cmd)

def home_axis(self, axis: StageAxis, direction: Optional[int] = None) -> None:
    """Home specified axis."""
    config = self._axis_config[axis]
    dir_to_use = direction if direction is not None else config.home_direction
    self._home(config.axis_id, dir_to_use)

def zero_axis(self, axis: StageAxis) -> None:
    """Zero specified axis at current position."""
    config = self._axis_config[axis]
    self._zero(config.axis_id)
```

Update legacy methods to use generic (keep for backwards compatibility):

```python
def move_x_usteps(self, usteps: int) -> None:
    self.move_axis_usteps(StageAxis.X, usteps)

def move_y_usteps(self, usteps: int) -> None:
    self.move_axis_usteps(StageAxis.Y, usteps)

# ... etc for all legacy methods
```

---

### 2.2 ScanCoordinates Decomposition

Create new directory structure:

```
software/src/squid/backend/managers/scan_coordinates/
├── __init__.py
├── region_store.py
├── grid_generator.py
└── wellplate_generator.py
```

**region_store.py:**
```python
"""Region data storage without generation logic."""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import numpy as np

@dataclass
class Region:
    """A scan coordinate region."""
    id: str
    center: Tuple[float, float, float]  # x, y, z in mm
    shape: str  # "Square", "Circle", "Rectangle"
    fov_coordinates: List[Tuple[float, ...]] = field(default_factory=list)

class RegionStore:
    """Manages scan coordinate regions."""

    def __init__(self):
        self._regions: Dict[str, Region] = {}
        self._region_order: List[str] = []

    def add_region(self, region: Region) -> None:
        """Add a region to the store."""
        self._regions[region.id] = region
        if region.id not in self._region_order:
            self._region_order.append(region.id)

    def remove_region(self, region_id: str) -> None:
        """Remove a region by ID."""
        if region_id in self._regions:
            del self._regions[region_id]
            self._region_order.remove(region_id)

    def get_region(self, region_id: str) -> Optional[Region]:
        """Get a region by ID."""
        return self._regions.get(region_id)

    def get_all_regions(self) -> List[Region]:
        """Get all regions in order."""
        return [self._regions[rid] for rid in self._region_order if rid in self._regions]

    def clear(self) -> None:
        """Clear all regions."""
        self._regions.clear()
        self._region_order.clear()

    def get_total_fovs(self) -> int:
        """Get total FOV count across all regions."""
        return sum(len(r.fov_coordinates) for r in self._regions.values())

    def get_all_fov_coordinates(self) -> List[Tuple[str, float, float, float]]:
        """Get all FOV coordinates as (region_id, x, y, z) tuples."""
        result = []
        for region in self.get_all_regions():
            for coord in region.fov_coordinates:
                result.append((region.id, coord[0], coord[1], coord[2]))
        return result
```

**grid_generator.py:**
```python
"""Pure functions for generating FOV grids."""
from typing import List, Tuple
import numpy as np

class GridGenerator:
    """Static methods for generating FOV coordinate grids."""

    @staticmethod
    def generate_rectangular_grid(
        center_x: float,
        center_y: float,
        nx: int,
        ny: int,
        dx: float,
        dy: float,
        s_pattern: bool = True,
    ) -> List[Tuple[float, float]]:
        """
        Generate rectangular grid of FOV positions.

        Args:
            center_x, center_y: Grid center in mm
            nx, ny: Number of FOVs in x and y
            dx, dy: FOV spacing in mm
            s_pattern: If True, alternate row directions

        Returns:
            List of (x, y) positions in mm
        """
        positions = []
        start_x = center_x - (nx - 1) * dx / 2
        start_y = center_y - (ny - 1) * dy / 2

        for j in range(ny):
            row_positions = []
            for i in range(nx):
                x = start_x + i * dx
                y = start_y + j * dy
                row_positions.append((x, y))

            if s_pattern and j % 2 == 1:
                row_positions.reverse()

            positions.extend(row_positions)

        return positions

    @staticmethod
    def generate_circular_grid(
        center_x: float,
        center_y: float,
        radius: float,
        dx: float,
        dy: float,
    ) -> List[Tuple[float, float]]:
        """
        Generate circular grid of FOV positions.

        Args:
            center_x, center_y: Circle center in mm
            radius: Circle radius in mm
            dx, dy: FOV spacing in mm

        Returns:
            List of (x, y) positions within the circle
        """
        # Calculate bounding box
        nx = int(np.ceil(2 * radius / dx)) + 1
        ny = int(np.ceil(2 * radius / dy)) + 1

        positions = []
        for j in range(ny):
            for i in range(nx):
                x = center_x - radius + i * dx
                y = center_y - radius + j * dy
                # Check if within circle
                if (x - center_x)**2 + (y - center_y)**2 <= radius**2:
                    positions.append((x, y))

        return positions
```

**__init__.py (facade):**
```python
"""
ScanCoordinates facade.

Maintains backward compatibility while delegating to focused components.
"""
from .region_store import RegionStore, Region
from .grid_generator import GridGenerator

# Re-export for external use
__all__ = ["ScanCoordinates", "RegionStore", "Region", "GridGenerator"]

# Import original class and extend or replace
# This allows gradual migration
```

---

### 2.3 LaserSpotDetector Extraction

**New file:** `software/src/squid/backend/processing/laser_spot_detection.py`

```python
"""
Laser spot detection and displacement measurement.

Extracted from LaserAutofocusController for testability.
"""
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np

@dataclass
class SpotDetectionResult:
    """Result of spot detection."""
    x_px: float
    y_px: float
    intensity: float
    valid: bool
    error: Optional[str] = None

@dataclass
class DisplacementResult:
    """Result of displacement measurement."""
    displacement_px: float
    displacement_um: float
    valid: bool
    error: Optional[str] = None

class LaserSpotDetector:
    """
    Detects laser spots and measures displacement.

    This is pure image processing with no hardware dependencies.
    """

    def __init__(
        self,
        um_per_px: float = 1.0,
        spot_search_radius: int = 50,
        min_spot_intensity: float = 100.0,
    ):
        self._um_per_px = um_per_px
        self._spot_search_radius = spot_search_radius
        self._min_spot_intensity = min_spot_intensity
        self._reference_centroid: Optional[Tuple[float, float]] = None

    def detect_spot(self, frame: np.ndarray) -> SpotDetectionResult:
        """
        Detect laser spot in frame using centroid.

        Args:
            frame: Grayscale image as numpy array

        Returns:
            SpotDetectionResult with centroid coordinates
        """
        if frame is None or frame.size == 0:
            return SpotDetectionResult(0, 0, 0, False, "Empty frame")

        # Find brightest region
        max_val = np.max(frame)
        if max_val < self._min_spot_intensity:
            return SpotDetectionResult(0, 0, max_val, False, "Spot too dim")

        # Compute centroid of bright pixels
        threshold = max_val * 0.5
        bright_mask = frame > threshold
        y_coords, x_coords = np.nonzero(bright_mask)

        if len(x_coords) == 0:
            return SpotDetectionResult(0, 0, max_val, False, "No bright pixels")

        weights = frame[bright_mask]
        x_centroid = np.average(x_coords, weights=weights)
        y_centroid = np.average(y_coords, weights=weights)

        return SpotDetectionResult(
            x_px=x_centroid,
            y_px=y_centroid,
            intensity=max_val,
            valid=True,
        )

    def set_reference(self, frame: np.ndarray) -> bool:
        """
        Set reference position from current frame.

        Returns:
            True if reference was set successfully
        """
        result = self.detect_spot(frame)
        if result.valid:
            self._reference_centroid = (result.x_px, result.y_px)
            return True
        return False

    def measure_displacement(self, frame: np.ndarray) -> DisplacementResult:
        """
        Measure displacement from reference position.

        Args:
            frame: Current frame

        Returns:
            DisplacementResult with displacement in pixels and um
        """
        if self._reference_centroid is None:
            return DisplacementResult(0, 0, False, "No reference set")

        result = self.detect_spot(frame)
        if not result.valid:
            return DisplacementResult(0, 0, False, result.error)

        dx = result.x_px - self._reference_centroid[0]
        dy = result.y_px - self._reference_centroid[1]
        displacement_px = np.sqrt(dx**2 + dy**2)
        displacement_um = displacement_px * self._um_per_px

        return DisplacementResult(
            displacement_px=displacement_px,
            displacement_um=displacement_um,
            valid=True,
        )
```

---

## Phase 3: Application Layer

### 3.1 Controller Factory

**New file:** `software/src/squid/backend/factories/controller_factory.py`

```python
"""
Factory for creating controllers with feature flag awareness.
"""
from typing import Optional, TYPE_CHECKING

from squid.core.config import get_feature_flags, FeatureFlags
import squid.logging

if TYPE_CHECKING:
    from squid.backend.microscope import Microscope
    from squid.backend.services import ServiceRegistry
    from squid.core.mode_gate import GlobalModeGate

_log = squid.logging.get_logger(__name__)


class ControllerFactory:
    """
    Factory for creating controllers.

    Centralizes feature flag checks and dependency wiring.
    """

    def __init__(
        self,
        microscope: "Microscope",
        services: "ServiceRegistry",
        mode_gate: "GlobalModeGate",
        feature_flags: Optional[FeatureFlags] = None,
    ):
        self._microscope = microscope
        self._services = services
        self._mode_gate = mode_gate
        self._flags = feature_flags or get_feature_flags()

    def create_laser_autofocus(self):
        """Create laser autofocus controller if enabled."""
        if not self._flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            _log.debug("Laser autofocus disabled by feature flag")
            return None

        camera_focus = self._services.get("camera_focus")
        if camera_focus is None:
            _log.warning("No focus camera available for laser autofocus")
            return None

        from squid.backend.controllers.autofocus.laser_auto_focus_controller import (
            LaserAutofocusController,
        )

        return LaserAutofocusController(
            camera_service=camera_focus,
            stage_service=self._services.stage,
            piezo_service=self._services.piezo,
            peripheral_service=self._services.peripheral,
            event_bus=self._services.event_bus,
            # ... other dependencies
        )

    def create_tracking(self):
        """Create tracking controller if enabled."""
        if not self._flags.is_enabled("ENABLE_TRACKING"):
            _log.debug("Tracking disabled by feature flag")
            return None

        from squid.backend.controllers.tracking import TrackingControllerCore

        return TrackingControllerCore(
            # ... dependencies
        )

    # Add more factory methods as needed
```

---

## Phase 4: UI Architecture

### 4.1 Presenter Base Class

**New file:** `software/src/squid/ui/presenters/__init__.py`

```python
from .base import Presenter

__all__ = ["Presenter"]
```

**New file:** `software/src/squid/ui/presenters/base.py`

```python
"""
Base presenter class for MVP pattern.
"""
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, TYPE_CHECKING

from squid.core.events import EventSubscriberMixin

if TYPE_CHECKING:
    from squid.core.events import EventBus

ViewT = TypeVar("ViewT")


class Presenter(ABC, Generic[ViewT], EventSubscriberMixin):
    """
    Base presenter for MVP pattern.

    Presenters handle:
    - Event bus subscriptions
    - Business logic
    - Updating view state

    Views handle:
    - Layout and rendering
    - User input signals
    """

    def __init__(self, view: ViewT, event_bus: "EventBus"):
        self._view = view
        self._event_bus = event_bus

    def attach(self) -> None:
        """
        Called when view is ready.

        Override to:
        - Connect view signals to presenter methods
        - Set initial view state
        """
        self._auto_subscribe(self._event_bus)

    def detach(self) -> None:
        """
        Called when view is closing.

        Override to clean up resources.
        """
        self._auto_unsubscribe(self._event_bus)

    def _publish(self, event) -> None:
        """Convenience method to publish events."""
        self._event_bus.publish(event)
```

---

## Verification

### Unit Tests

Run after each phase:
```bash
cd software
pytest tests/unit/squid/core/test_event_decorator.py -v
pytest tests/unit/squid/core/config/test_feature_flags.py -v
pytest tests/unit/squid/backend/processing/test_laser_spot_detection.py -v
```

### Integration Tests

```bash
cd software
pytest tests/integration -v
```

### Manual Testing

```bash
cd software
python main_hcs.py --simulation
```

Verify:
1. Live view starts and displays frames
2. Multipoint widget loads without errors
3. Laser autofocus panel appears (if enabled in config)
4. Acquisition can be started/stopped

---

## Migration Guide

### Adopting @handles Decorator

Before:
```python
class MyService:
    def __init__(self, event_bus):
        self._event_bus = event_bus
        self._event_bus.subscribe(CommandA, self._on_command_a)
        self._event_bus.subscribe(CommandB, self._on_command_b)

    def _on_command_a(self, cmd): ...
    def _on_command_b(self, cmd): ...
```

After:
```python
class MyService(EventSubscriberMixin):
    def __init__(self, event_bus):
        self._event_bus = event_bus
        self._auto_subscribe(event_bus)

    @handles(CommandA)
    def _on_command_a(self, cmd): ...

    @handles(CommandB)
    def _on_command_b(self, cmd): ...
```

### Adopting FeatureFlags

Before:
```python
import _def as _config
if getattr(_config, "SUPPORT_LASER_AUTOFOCUS", False):
    ...
```

After:
```python
from squid.core.config import get_feature_flags
if get_feature_flags().is_enabled("SUPPORT_LASER_AUTOFOCUS"):
    ...
```

### Adopting Generic Axis Methods

Before:
```python
mc.move_x_usteps(1000)
mc.home_x()
```

After:
```python
mc.move_axis_usteps(StageAxis.X, 1000)
mc.home_axis(StageAxis.X)
```

(Legacy methods still work for backwards compatibility)
