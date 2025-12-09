# Refactoring Plan: Clean Architecture Migration

This document provides a detailed, step-by-step plan for migrating the Squid microscopy software to the clean architecture described in `CLEAN_ARCHITECTURE.md`.

## Overview

The migration is divided into 6 phases:

| Phase | Description | Risk Level |
|-------|-------------|------------|
| 1 | Create new Services (stateless) | Low |
| 2 | Create new Controllers with state | Medium |
| 3 | Update Events | Low |
| 4 | Refactor Widgets | High |
| 5 | Remove legacy code | Medium |
| 6 | Add comprehensive tests | Low |

**Guiding principles:**
- Each task should be independently committable
- Maintain backwards compatibility during migration
- Test after each step
- Old and new code can coexist during transition

---

## Phase 1: Create New Services (Stateless Hardware API)

Services are the foundation. They wrap hardware with a clean API but have no EventBus integration.

### Task 1.1: Create Service Base Infrastructure

**Create:** `squid/services/base.py` (update existing)

The existing `BaseService` has EventBus integration. We need a simpler base for stateless services.

```python
# squid/services/stateless_base.py
class StatelessService:
    """Base class for stateless hardware services."""

    def __init__(self):
        self._log = logging.getLogger(self.__class__.__name__)
```

**Test:** Verify import works
```bash
python -c "from squid.services.stateless_base import StatelessService; print('OK')"
```

**Commit:** `feat(services): Add StatelessService base class`

---

### Task 1.2: Create CameraService (Stateless)

**Create:** `squid/services/camera_service_v2.py`

```python
"""Stateless API for camera operations."""
from __future__ import annotations
from typing import TYPE_CHECKING
from squid.services.stateless_base import StatelessService

if TYPE_CHECKING:
    from squid.abc import AbstractCamera

class CameraServiceV2(StatelessService):
    """Stateless API for camera operations."""

    def __init__(self, camera: "AbstractCamera"):
        super().__init__()
        self._camera = camera

    # Exposure
    def set_exposure_time(self, ms: float) -> float:
        """Set exposure time, returns actual clamped value."""
        limits = self._camera.get_exposure_limits()
        clamped = max(limits[0], min(limits[1], ms))
        self._camera.set_exposure_time(clamped)
        return clamped

    def get_exposure_time(self) -> float:
        return self._camera.get_exposure_time()

    def get_exposure_limits(self) -> tuple[float, float]:
        return self._camera.get_exposure_limits()

    # Gain
    def set_analog_gain(self, gain: float) -> float:
        """Set analog gain, returns actual clamped value."""
        min_gain, max_gain, _ = self._camera.get_gain_range()
        clamped = max(min_gain, min(max_gain, gain))
        self._camera.set_analog_gain(clamped)
        return clamped

    def get_analog_gain(self) -> float:
        return self._camera.get_analog_gain()

    def get_gain_range(self) -> tuple[float, float, float]:
        return self._camera.get_gain_range()

    # ROI
    def set_roi(self, x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        self._camera.set_region_of_interest(x, y, width, height)
        return self._camera.get_region_of_interest()

    def get_roi(self) -> tuple[int, int, int, int]:
        return self._camera.get_region_of_interest()

    def clear_roi(self) -> None:
        self._camera.set_region_of_interest(0, 0, *self._camera.get_resolution())

    # Binning
    def set_binning(self, x: int, y: int) -> tuple[int, int]:
        self._camera.set_binning(x, y)
        return self._camera.get_binning()

    def get_binning(self) -> tuple[int, int]:
        return self._camera.get_binning()

    def get_binning_options(self) -> list[tuple[int, int]]:
        return self._camera.get_binning_options()

    # Pixel format
    def set_pixel_format(self, fmt: str) -> str:
        self._camera.set_pixel_format(fmt)
        return self._camera.get_pixel_format()

    def get_pixel_format(self) -> str:
        return self._camera.get_pixel_format()

    def get_available_pixel_formats(self) -> list[str]:
        return self._camera.get_available_pixel_formats()

    # Streaming
    def start_streaming(self) -> None:
        self._camera.start_streaming()

    def stop_streaming(self) -> None:
        self._camera.stop_streaming()

    def is_streaming(self) -> bool:
        return self._camera.get_is_streaming()

    # Frame capture
    def read_frame(self):
        """Read a frame from the camera."""
        return self._camera.read_camera_frame()

    def send_trigger(self, illumination_time_ms: float | None = None) -> None:
        self._camera.send_trigger(illumination_time_ms)

    def is_ready_for_trigger(self) -> bool:
        return self._camera.get_ready_for_trigger()

    # Acquisition mode
    def set_acquisition_mode(self, mode) -> None:
        self._camera.set_acquisition_mode(mode)

    def get_acquisition_mode(self):
        return self._camera.get_acquisition_mode()

    # Temperature
    def set_temperature(self, deg_c: float) -> None:
        self._camera.set_temperature(deg_c)

    def get_temperature(self) -> float | None:
        return self._camera.get_temperature()

    # Resolution & pixel size
    def get_resolution(self) -> tuple[int, int]:
        return self._camera.get_resolution()

    def get_pixel_size_um(self) -> float:
        return self._camera.get_pixel_size_binned_um()
```

**Create test:** `tests/unit/squid/services/test_camera_service_v2.py`

```python
"""Tests for CameraServiceV2."""
from unittest.mock import Mock
import pytest

class TestCameraServiceV2:
    def test_set_exposure_clamps_to_limits(self):
        from squid.services.camera_service_v2 import CameraServiceV2

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (1.0, 1000.0)

        service = CameraServiceV2(mock_camera)

        # Test upper clamp
        actual = service.set_exposure_time(5000.0)
        assert actual == 1000.0
        mock_camera.set_exposure_time.assert_called_with(1000.0)

        # Test lower clamp
        actual = service.set_exposure_time(0.1)
        assert actual == 1.0
        mock_camera.set_exposure_time.assert_called_with(1.0)

    def test_set_gain_clamps_to_range(self):
        from squid.services.camera_service_v2 import CameraServiceV2

        mock_camera = Mock()
        mock_camera.get_gain_range.return_value = (0.0, 24.0, 0.1)

        service = CameraServiceV2(mock_camera)
        actual = service.set_analog_gain(50.0)

        assert actual == 24.0
        mock_camera.set_analog_gain.assert_called_with(24.0)
```

**Verify:**
```bash
python -m pytest tests/unit/squid/services/test_camera_service_v2.py -v
```

**Commit:** `feat(services): Add stateless CameraServiceV2`

---

### Task 1.3: Create StageService (Stateless)

**Create:** `squid/services/stage_service_v2.py`

```python
"""Stateless API for stage operations."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from squid.services.stateless_base import StatelessService

if TYPE_CHECKING:
    from squid.abc import AbstractStage


@dataclass
class StageLimits:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float


class StageServiceV2(StatelessService):
    """Stateless API for stage operations."""

    def __init__(self, stage: "AbstractStage"):
        super().__init__()
        self._stage = stage

    # Position queries
    def get_position(self) -> tuple[float, float, float]:
        """Get current position as (x, y, z) in mm."""
        return self._stage.get_pos()

    def get_x(self) -> float:
        x, _, _ = self._stage.get_pos()
        return x

    def get_y(self) -> float:
        _, y, _ = self._stage.get_pos()
        return y

    def get_z(self) -> float:
        _, _, z = self._stage.get_pos()
        return z

    # Relative movement
    def move_x(self, mm: float, blocking: bool = True) -> None:
        self._stage.move_x(mm, blocking=blocking)

    def move_y(self, mm: float, blocking: bool = True) -> None:
        self._stage.move_y(mm, blocking=blocking)

    def move_z(self, mm: float, blocking: bool = True) -> None:
        self._stage.move_z(mm, blocking=blocking)

    def move_relative(
        self, dx: float = 0, dy: float = 0, dz: float = 0, blocking: bool = True
    ) -> None:
        """Move by relative amounts on all axes."""
        if dx != 0:
            self._stage.move_x(dx, blocking=blocking)
        if dy != 0:
            self._stage.move_y(dy, blocking=blocking)
        if dz != 0:
            self._stage.move_z(dz, blocking=blocking)

    # Absolute movement
    def move_to(
        self, x: float, y: float, z: float | None = None, blocking: bool = True
    ) -> None:
        """Move to absolute position."""
        self._stage.move_x_to(x, blocking=blocking)
        self._stage.move_y_to(y, blocking=blocking)
        if z is not None:
            self._stage.move_z_to(z, blocking=blocking)

    def move_x_to(self, mm: float, blocking: bool = True) -> None:
        self._stage.move_x_to(mm, blocking=blocking)

    def move_y_to(self, mm: float, blocking: bool = True) -> None:
        self._stage.move_y_to(mm, blocking=blocking)

    def move_z_to(self, mm: float, blocking: bool = True) -> None:
        self._stage.move_z_to(mm, blocking=blocking)

    # Homing & zeroing
    def home(self, x: bool = False, y: bool = False, z: bool = False) -> None:
        self._stage.home(x=x, y=y, z=z, theta=False, blocking=True)

    def zero(self, x: bool = False, y: bool = False, z: bool = False) -> None:
        self._stage.zero(x=x, y=y, z=z, theta=False, blocking=True)

    # State
    def is_busy(self) -> bool:
        state = self._stage.get_state()
        return state.is_busy if hasattr(state, 'is_busy') else False

    def wait_until_idle(self, timeout_s: float = 10.0) -> None:
        self._stage.wait_for_idle(timeout_s)

    # Configuration
    def get_config(self):
        return self._stage.get_config()
```

**Create test:** `tests/unit/squid/services/test_stage_service_v2.py`

```python
"""Tests for StageServiceV2."""
from unittest.mock import Mock

class TestStageServiceV2:
    def test_get_position_returns_tuple(self):
        from squid.services.stage_service_v2 import StageServiceV2

        mock_stage = Mock()
        mock_stage.get_pos.return_value = (1.0, 2.0, 3.0)

        service = StageServiceV2(mock_stage)
        pos = service.get_position()

        assert pos == (1.0, 2.0, 3.0)

    def test_move_relative_calls_individual_axes(self):
        from squid.services.stage_service_v2 import StageServiceV2

        mock_stage = Mock()
        service = StageServiceV2(mock_stage)

        service.move_relative(dx=1.0, dy=2.0, dz=3.0)

        mock_stage.move_x.assert_called_once_with(1.0, blocking=True)
        mock_stage.move_y.assert_called_once_with(2.0, blocking=True)
        mock_stage.move_z.assert_called_once_with(3.0, blocking=True)

    def test_move_to_absolute(self):
        from squid.services.stage_service_v2 import StageServiceV2

        mock_stage = Mock()
        service = StageServiceV2(mock_stage)

        service.move_to(10.0, 20.0, 5.0)

        mock_stage.move_x_to.assert_called_once_with(10.0, blocking=True)
        mock_stage.move_y_to.assert_called_once_with(20.0, blocking=True)
        mock_stage.move_z_to.assert_called_once_with(5.0, blocking=True)
```

**Verify:**
```bash
python -m pytest tests/unit/squid/services/test_stage_service_v2.py -v
```

**Commit:** `feat(services): Add stateless StageServiceV2`

---

### Task 1.4: Create IlluminationService (Stateless)

**Create:** `squid/services/illumination_service.py`

```python
"""Stateless API for illumination control."""
from __future__ import annotations
from typing import TYPE_CHECKING
from squid.services.stateless_base import StatelessService

if TYPE_CHECKING:
    from control.microcontroller import Microcontroller
    from squid.abc import LightSource


class IlluminationService(StatelessService):
    """Stateless API for illumination control."""

    def __init__(
        self,
        microcontroller: "Microcontroller",
        light_source: "LightSource | None" = None,
    ):
        super().__init__()
        self._mcu = microcontroller
        self._light_source = light_source

    # Basic illumination control via microcontroller
    def set_illumination(
        self, channel: int, intensity: float, on: bool = True
    ) -> None:
        """Set illumination for a channel."""
        if on and intensity > 0:
            self._mcu.set_illumination(channel, intensity)
            self._mcu.turn_on_illumination()
        else:
            self._mcu.turn_off_illumination()

    def turn_on(self) -> None:
        self._mcu.turn_on_illumination()

    def turn_off(self) -> None:
        self._mcu.turn_off_illumination()

    # Light source control (if available)
    def set_channel_intensity(self, channel: int, intensity: float) -> None:
        """Set intensity for a channel (0-100%)."""
        if self._light_source:
            self._light_source.set_intensity(channel, intensity)

    def get_channel_intensity(self, channel: int) -> float:
        if self._light_source:
            return self._light_source.get_intensity(channel)
        return 0.0

    def set_channel_state(self, channel: int, on: bool) -> None:
        """Turn a channel on or off."""
        if self._light_source:
            self._light_source.set_shutter_state(channel, on)

    def get_channel_state(self, channel: int) -> bool:
        if self._light_source:
            return self._light_source.get_shutter_state(channel)
        return False

    def turn_off_all(self) -> None:
        """Turn off all illumination."""
        self._mcu.turn_off_illumination()
        if self._light_source:
            self._light_source.shut_down()
```

**Commit:** `feat(services): Add stateless IlluminationService`

---

### Task 1.5: Create PeripheralService (Stateless)

**Update:** `squid/services/peripheral_service.py`

The current PeripheralService has EventBus integration. Create a stateless version:

**Create:** `squid/services/peripheral_service_v2.py`

```python
"""Stateless API for DAC, triggers, and misc peripherals."""
from __future__ import annotations
from typing import TYPE_CHECKING
from squid.services.stateless_base import StatelessService

if TYPE_CHECKING:
    from control.microcontroller import Microcontroller


class PeripheralServiceV2(StatelessService):
    """Stateless API for DAC, triggers, and misc peripherals."""

    def __init__(self, microcontroller: "Microcontroller"):
        super().__init__()
        self._mcu = microcontroller

    # DAC control
    def set_dac(self, channel: int, value_percent: float) -> float:
        """Set DAC output (0-100%), returns actual value."""
        clamped = max(0.0, min(100.0, value_percent))
        # Convert percent to DAC value (assuming 16-bit DAC)
        dac_value = int(clamped / 100.0 * 65535)
        self._mcu.analog_write_onboard_DAC(channel, dac_value)
        return clamped

    # Camera trigger control
    def start_camera_trigger(self) -> None:
        self._mcu.start_camera_trigger()

    def stop_camera_trigger(self) -> None:
        self._mcu.stop_camera_trigger()

    def set_camera_trigger_frequency(self, fps: float) -> None:
        self._mcu.set_camera_trigger_frequency(fps)

    # AF Laser control
    def turn_on_af_laser(self, wait: bool = False) -> None:
        self._mcu.turn_on_AF_laser()
        if wait:
            self._mcu.wait_till_operation_is_completed()

    def turn_off_af_laser(self, wait: bool = False) -> None:
        self._mcu.turn_off_AF_laser()
        if wait:
            self._mcu.wait_till_operation_is_completed()

    # Utility
    def wait_for_operation(self) -> None:
        self._mcu.wait_till_operation_is_completed()
```

**Commit:** `feat(services): Add stateless PeripheralServiceV2`

---

### Task 1.6: Create PiezoService (Stateless)

**Create:** `squid/services/piezo_service.py`

```python
"""Stateless API for piezo stage."""
from __future__ import annotations
from typing import TYPE_CHECKING
from squid.services.stateless_base import StatelessService

if TYPE_CHECKING:
    from control.core.piezo import PiezoStage


class PiezoService(StatelessService):
    """Stateless API for piezo stage."""

    def __init__(self, piezo: "PiezoStage"):
        super().__init__()
        self._piezo = piezo

    def move_to(self, um: float) -> float:
        """Move to position in um, returns actual position."""
        range_min, range_max = self.get_range()
        clamped = max(range_min, min(range_max, um))
        self._piezo.move_to(clamped)
        return self.get_position()

    def move_relative(self, um: float) -> float:
        """Move by relative amount, returns new position."""
        current = self.get_position()
        return self.move_to(current + um)

    def get_position(self) -> float:
        """Get current position in um."""
        return self._piezo.get_position()

    def get_range(self) -> tuple[float, float]:
        """Get (min, max) range in um."""
        return self._piezo.get_range()

    def home(self) -> None:
        """Move to home position."""
        self._piezo.home()
```

**Commit:** `feat(services): Add stateless PiezoService`

---

### Task 1.7: Create FilterWheelService (Stateless)

**Create:** `squid/services/filter_wheel_service.py`

```python
"""Stateless API for filter wheel operations."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from squid.services.stateless_base import StatelessService

if TYPE_CHECKING:
    from squid.abc import AbstractFilterWheelController


@dataclass
class FilterWheelInfo:
    index: int
    num_positions: int
    filter_names: list[str]


class FilterWheelService(StatelessService):
    """Stateless API for filter wheel operations."""

    def __init__(self, controller: "AbstractFilterWheelController"):
        super().__init__()
        self._controller = controller

    def get_available_wheels(self) -> list[int]:
        return self._controller.available_filter_wheels

    def get_wheel_info(self, wheel_index: int) -> FilterWheelInfo:
        info = self._controller.get_filter_wheel_info(wheel_index)
        return FilterWheelInfo(
            index=wheel_index,
            num_positions=info.num_slots,
            filter_names=info.filter_names,
        )

    def set_position(self, wheel_index: int, position: int) -> None:
        self._controller.set_filter_wheel_position({wheel_index: position})

    def get_position(self, wheel_index: int) -> int:
        positions = self._controller.get_filter_wheel_position()
        return positions.get(wheel_index, 0)

    def home(self, wheel_index: int | None = None) -> None:
        self._controller.home(wheel_index)
```

**Commit:** `feat(services): Add stateless FilterWheelService`

---

### Task 1.8: Update Services __init__.py

**Update:** `squid/services/__init__.py`

```python
# Stateless services (new architecture)
from squid.services.stateless_base import StatelessService
from squid.services.camera_service_v2 import CameraServiceV2
from squid.services.stage_service_v2 import StageServiceV2
from squid.services.illumination_service import IlluminationService
from squid.services.peripheral_service_v2 import PeripheralServiceV2
from squid.services.piezo_service import PiezoService
from squid.services.filter_wheel_service import FilterWheelService

# Legacy services (for backwards compatibility during migration)
from squid.services.base import BaseService
from squid.services.camera_service import CameraService
from squid.services.stage_service import StageService
from squid.services.peripheral_service import PeripheralService
# ... etc

__all__ = [
    # New
    "StatelessService",
    "CameraServiceV2",
    "StageServiceV2",
    "IlluminationService",
    "PeripheralServiceV2",
    "PiezoService",
    "FilterWheelService",
    # Legacy
    "BaseService",
    "CameraService",
    "StageService",
    "PeripheralService",
    # ...
]
```

**Commit:** `feat(services): Export new stateless services`

---

## Phase 2: Create New Controllers

Controllers own state, subscribe to EventBus commands, and call Services.

### Task 2.1: Create State Dataclasses

**Create:** `squid/state/__init__.py`

```python
"""State dataclasses for controllers."""
from squid.state.camera import CameraState
from squid.state.stage import StageState
from squid.state.live import LiveState
from squid.state.autofocus import AutofocusState
from squid.state.acquisition import AcquisitionState, AcquisitionConfig

__all__ = [
    "CameraState",
    "StageState",
    "LiveState",
    "AutofocusState",
    "AcquisitionState",
    "AcquisitionConfig",
]
```

**Create:** `squid/state/camera.py`

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class CameraState:
    exposure_ms: float
    gain: float
    binning: tuple[int, int]
    roi: tuple[int, int, int, int] | None
    pixel_format: str
    is_streaming: bool
    acquisition_mode: Any  # AcquisitionMode enum
    temperature: float | None = None
    resolution: tuple[int, int] = (0, 0)
```

**Create:** `squid/state/stage.py`

```python
from dataclasses import dataclass

@dataclass
class StageLimits:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

@dataclass
class StageState:
    x_mm: float
    y_mm: float
    z_mm: float
    is_moving: bool
    limits: StageLimits | None = None
```

**Create:** `squid/state/live.py`

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any

class TriggerMode(Enum):
    SOFTWARE = "Software"
    HARDWARE = "Hardware"
    CONTINUOUS = "Continuous"

@dataclass
class LiveState:
    is_live: bool
    trigger_mode: TriggerMode
    fps: float
    current_configuration: Any | None  # ChannelConfiguration
    illumination_on: bool
```

**Create:** `squid/state/autofocus.py`

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class AutofocusState:
    is_running: bool
    n_planes: int
    delta_z_um: float
    focus_map: Any | None  # FocusMap
    use_focus_map: bool
    progress: float  # 0.0 to 1.0
```

**Create:** `squid/state/acquisition.py`

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class GridConfig:
    nx: int
    ny: int
    delta_x_mm: float
    delta_y_mm: float

@dataclass
class ZStackConfig:
    nz: int
    delta_z_um: float

@dataclass
class TimeSeriesConfig:
    nt: int
    delta_t_s: float

@dataclass
class AcquisitionConfig:
    grid: GridConfig
    z_stack: ZStackConfig | None = None
    time_series: TimeSeriesConfig | None = None
    channels: list[Any] = field(default_factory=list)  # ChannelConfiguration
    use_autofocus: bool = False
    use_focus_map: bool = False
    save_path: Path = Path(".")
    experiment_id: str = ""

@dataclass
class AcquisitionState:
    is_running: bool
    is_paused: bool
    config: AcquisitionConfig | None
    current_position: int
    total_positions: int
    current_timepoint: int
    total_timepoints: int
    current_channel: str
    progress: float
    estimated_time_remaining_s: float
```

**Commit:** `feat(state): Add state dataclasses for controllers`

---

### Task 2.2: Create Controller Base Class

**Create:** `squid/controllers/base.py`

```python
"""Base class for controllers."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Callable, Type, TypeVar

if TYPE_CHECKING:
    from squid.events import Event, EventBus

E = TypeVar("E", bound="Event")


class BaseController:
    """Base class for controllers with EventBus integration."""

    def __init__(self, event_bus: "EventBus"):
        self._bus = event_bus
        self._log = logging.getLogger(self.__class__.__name__)
        self._subscriptions: list[tuple[Type[E], Callable]] = []

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """Subscribe to an event type."""
        self._bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def publish(self, event: "Event") -> None:
        """Publish an event."""
        self._bus.publish(event)

    def shutdown(self) -> None:
        """Unsubscribe from all events."""
        for event_type, handler in self._subscriptions:
            self._bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
```

**Commit:** `feat(controllers): Add BaseController class`

---

### Task 2.3: Create CameraController

**Create:** `squid/controllers/camera_controller.py`

```python
"""Camera controller - owns camera state and handles commands."""
from __future__ import annotations
from copy import deepcopy
from typing import TYPE_CHECKING

from squid.controllers.base import BaseController
from squid.state.camera import CameraState
from squid.events import (
    SetExposureCommand,
    SetGainCommand,
    SetBinningCommand,
    SetROICommand,
    SetPixelFormatCommand,
    RequestCameraStateQuery,
    CameraStateChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services.camera_service_v2 import CameraServiceV2


class CameraController(BaseController):
    """Owns camera state, handles camera commands."""

    def __init__(self, camera_service: "CameraServiceV2", event_bus: "EventBus"):
        super().__init__(event_bus)
        self._service = camera_service
        self._state = self._read_initial_state()

        # Subscribe to commands
        self.subscribe(SetExposureCommand, self._on_set_exposure)
        self.subscribe(SetGainCommand, self._on_set_gain)
        self.subscribe(SetBinningCommand, self._on_set_binning)
        self.subscribe(SetROICommand, self._on_set_roi)
        self.subscribe(SetPixelFormatCommand, self._on_set_pixel_format)
        self.subscribe(RequestCameraStateQuery, self._on_request_state)

    def _read_initial_state(self) -> CameraState:
        """Read current state from hardware."""
        return CameraState(
            exposure_ms=self._service.get_exposure_time(),
            gain=self._service.get_analog_gain(),
            binning=self._service.get_binning(),
            roi=self._service.get_roi(),
            pixel_format=self._service.get_pixel_format(),
            is_streaming=self._service.is_streaming(),
            acquisition_mode=self._service.get_acquisition_mode(),
            temperature=self._service.get_temperature(),
            resolution=self._service.get_resolution(),
        )

    def _publish_state(self) -> None:
        """Publish current state."""
        self.publish(CameraStateChanged(state=deepcopy(self._state)))

    def _on_set_exposure(self, cmd: SetExposureCommand) -> None:
        actual = self._service.set_exposure_time(cmd.exposure_ms)
        self._state.exposure_ms = actual
        self._publish_state()

    def _on_set_gain(self, cmd: SetGainCommand) -> None:
        actual = self._service.set_analog_gain(cmd.gain)
        self._state.gain = actual
        self._publish_state()

    def _on_set_binning(self, cmd: SetBinningCommand) -> None:
        actual = self._service.set_binning(cmd.x, cmd.y)
        self._state.binning = actual
        self._state.resolution = self._service.get_resolution()
        self._publish_state()

    def _on_set_roi(self, cmd: SetROICommand) -> None:
        actual = self._service.set_roi(cmd.x, cmd.y, cmd.width, cmd.height)
        self._state.roi = actual
        self._publish_state()

    def _on_set_pixel_format(self, cmd: SetPixelFormatCommand) -> None:
        actual = self._service.set_pixel_format(cmd.pixel_format)
        self._state.pixel_format = actual
        self._publish_state()

    def _on_request_state(self, query: RequestCameraStateQuery) -> None:
        self._publish_state()

    @property
    def state(self) -> CameraState:
        return deepcopy(self._state)
```

**Commit:** `feat(controllers): Add CameraController`

---

### Task 2.4: Create StageController

**Create:** `squid/controllers/stage_controller.py`

```python
"""Stage controller - owns stage state and handles movement commands."""
from __future__ import annotations
from copy import deepcopy
from typing import TYPE_CHECKING

from squid.controllers.base import BaseController
from squid.state.stage import StageState
from squid.events import (
    MoveStageRelativeCommand,
    MoveStageToCommand,
    HomeStageCommand,
    ZeroStageCommand,
    RequestStageStateQuery,
    StageStateChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services.stage_service_v2 import StageServiceV2


class StageController(BaseController):
    """Owns stage state, handles movement commands."""

    def __init__(self, stage_service: "StageServiceV2", event_bus: "EventBus"):
        super().__init__(event_bus)
        self._service = stage_service
        self._state = self._read_initial_state()

        self.subscribe(MoveStageRelativeCommand, self._on_move_relative)
        self.subscribe(MoveStageToCommand, self._on_move_to)
        self.subscribe(HomeStageCommand, self._on_home)
        self.subscribe(ZeroStageCommand, self._on_zero)
        self.subscribe(RequestStageStateQuery, self._on_request_state)

    def _read_initial_state(self) -> StageState:
        x, y, z = self._service.get_position()
        return StageState(
            x_mm=x,
            y_mm=y,
            z_mm=z,
            is_moving=False,
            limits=None,
        )

    def _update_position(self) -> None:
        x, y, z = self._service.get_position()
        self._state.x_mm = x
        self._state.y_mm = y
        self._state.z_mm = z

    def _publish_state(self) -> None:
        self.publish(StageStateChanged(state=deepcopy(self._state)))

    def _on_move_relative(self, cmd: MoveStageRelativeCommand) -> None:
        self._state.is_moving = True
        self._publish_state()

        self._service.move_relative(cmd.dx, cmd.dy, cmd.dz, blocking=True)

        self._update_position()
        self._state.is_moving = False
        self._publish_state()

    def _on_move_to(self, cmd: MoveStageToCommand) -> None:
        self._state.is_moving = True
        self._publish_state()

        self._service.move_to(cmd.x, cmd.y, cmd.z, blocking=True)

        self._update_position()
        self._state.is_moving = False
        self._publish_state()

    def _on_home(self, cmd: HomeStageCommand) -> None:
        self._state.is_moving = True
        self._publish_state()

        self._service.home(x=cmd.x, y=cmd.y, z=cmd.z)

        self._update_position()
        self._state.is_moving = False
        self._publish_state()

    def _on_zero(self, cmd: ZeroStageCommand) -> None:
        self._service.zero(x=cmd.x, y=cmd.y, z=cmd.z)
        self._update_position()
        self._publish_state()

    def _on_request_state(self, query: RequestStageStateQuery) -> None:
        self._update_position()
        self._publish_state()

    @property
    def state(self) -> StageState:
        return deepcopy(self._state)
```

**Commit:** `feat(controllers): Add StageController`

---

### Task 2.5: Create LiveController (New Version)

**Create:** `squid/controllers/live_controller.py`

```python
"""Live controller - orchestrates live streaming."""
from __future__ import annotations
from copy import deepcopy
from threading import Timer
from typing import TYPE_CHECKING, Any

from squid.controllers.base import BaseController
from squid.state.live import LiveState, TriggerMode
from squid.events import (
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    SetMicroscopeModeCommand,
    RequestLiveStateQuery,
    LiveStateChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services.camera_service_v2 import CameraServiceV2
    from squid.services.illumination_service import IlluminationService
    from squid.services.peripheral_service_v2 import PeripheralServiceV2


class LiveControllerV2(BaseController):
    """Owns live streaming state, orchestrates camera + illumination."""

    def __init__(
        self,
        camera_service: "CameraServiceV2",
        illumination_service: "IlluminationService",
        peripheral_service: "PeripheralServiceV2",
        event_bus: "EventBus",
    ):
        super().__init__(event_bus)
        self._camera = camera_service
        self._illumination = illumination_service
        self._peripheral = peripheral_service

        self._state = LiveState(
            is_live=False,
            trigger_mode=TriggerMode.SOFTWARE,
            fps=10.0,
            current_configuration=None,
            illumination_on=False,
        )

        self._trigger_timer: Timer | None = None

        self.subscribe(StartLiveCommand, self._on_start_live)
        self.subscribe(StopLiveCommand, self._on_stop_live)
        self.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self.subscribe(SetTriggerFPSCommand, self._on_set_fps)
        self.subscribe(SetMicroscopeModeCommand, self._on_set_mode)
        self.subscribe(RequestLiveStateQuery, self._on_request_state)

    def _publish_state(self) -> None:
        self.publish(LiveStateChanged(state=deepcopy(self._state)))

    def _on_start_live(self, cmd: StartLiveCommand) -> None:
        if self._state.is_live:
            return

        if cmd.configuration:
            self._apply_configuration(cmd.configuration)

        self._camera.start_streaming()
        self._turn_on_illumination()
        self._start_triggering()

        self._state.is_live = True
        self._publish_state()

    def _on_stop_live(self, cmd: StopLiveCommand) -> None:
        if not self._state.is_live:
            return

        self._stop_triggering()
        self._turn_off_illumination()
        self._camera.stop_streaming()

        self._state.is_live = False
        self._publish_state()

    def _on_set_trigger_mode(self, cmd: SetTriggerModeCommand) -> None:
        was_live = self._state.is_live
        if was_live:
            self._stop_triggering()

        self._state.trigger_mode = cmd.mode

        if was_live:
            self._start_triggering()

        self._publish_state()

    def _on_set_fps(self, cmd: SetTriggerFPSCommand) -> None:
        self._state.fps = cmd.fps

        if self._state.is_live:
            self._stop_triggering()
            self._start_triggering()

        self._publish_state()

    def _on_set_mode(self, cmd: SetMicroscopeModeCommand) -> None:
        self._apply_configuration(cmd.configuration)
        self._publish_state()

    def _on_request_state(self, query: RequestLiveStateQuery) -> None:
        self._publish_state()

    def _apply_configuration(self, config: Any) -> None:
        """Apply a channel configuration."""
        self._state.current_configuration = config
        # Set camera exposure/gain from config
        if hasattr(config, 'exposure_time'):
            self._camera.set_exposure_time(config.exposure_time)
        if hasattr(config, 'analog_gain'):
            self._camera.set_analog_gain(config.analog_gain)
        # Set illumination from config
        if hasattr(config, 'illumination_source'):
            self._illumination.set_channel_intensity(
                config.illumination_source,
                config.illumination_intensity
            )

    def _turn_on_illumination(self) -> None:
        if self._state.current_configuration:
            self._illumination.turn_on()
        self._state.illumination_on = True

    def _turn_off_illumination(self) -> None:
        self._illumination.turn_off()
        self._state.illumination_on = False

    def _start_triggering(self) -> None:
        if self._state.trigger_mode == TriggerMode.HARDWARE:
            self._peripheral.set_camera_trigger_frequency(self._state.fps)
            self._peripheral.start_camera_trigger()
        elif self._state.trigger_mode == TriggerMode.SOFTWARE:
            self._schedule_software_trigger()
        # CONTINUOUS mode doesn't need explicit triggering

    def _stop_triggering(self) -> None:
        if self._trigger_timer:
            self._trigger_timer.cancel()
            self._trigger_timer = None
        self._peripheral.stop_camera_trigger()

    def _schedule_software_trigger(self) -> None:
        if not self._state.is_live:
            return
        interval = 1.0 / self._state.fps
        self._trigger_timer = Timer(interval, self._software_trigger_tick)
        self._trigger_timer.start()

    def _software_trigger_tick(self) -> None:
        if self._state.is_live and self._camera.is_ready_for_trigger():
            self._camera.send_trigger()
        self._schedule_software_trigger()

    @property
    def state(self) -> LiveState:
        return deepcopy(self._state)
```

**Commit:** `feat(controllers): Add LiveControllerV2`

---

### Task 2.6: Create AutofocusController (New Version)

**Create:** `squid/controllers/autofocus_controller.py`

Similar pattern - subscribes to autofocus commands, runs algorithm, publishes state.

**Commit:** `feat(controllers): Add AutofocusControllerV2`

---

### Task 2.7: Create AcquisitionController (New Version)

**Create:** `squid/controllers/acquisition_controller.py`

The most complex controller - orchestrates multi-point acquisition.

**Commit:** `feat(controllers): Add AcquisitionControllerV2`

---

### Task 2.8: Update Controllers __init__.py

**Create:** `squid/controllers/__init__.py`

```python
"""Controllers for microscope operations."""
from squid.controllers.base import BaseController
from squid.controllers.camera_controller import CameraController
from squid.controllers.stage_controller import StageController
from squid.controllers.live_controller import LiveControllerV2
# from squid.controllers.autofocus_controller import AutofocusControllerV2
# from squid.controllers.acquisition_controller import AcquisitionControllerV2

__all__ = [
    "BaseController",
    "CameraController",
    "StageController",
    "LiveControllerV2",
]
```

**Commit:** `feat(controllers): Export controllers`

---

## Phase 3: Update Events

### Task 3.1: Reorganize Events

**Update:** `squid/events.py`

Organize events into clear sections and add any missing events:

```python
"""Event definitions for the Squid microscope control software."""
from dataclasses import dataclass
from typing import Any, TypeVar

# ... existing EventBus code ...

# =============================================================================
# Base Event
# =============================================================================

@dataclass
class Event:
    """Base class for all events."""
    pass

E = TypeVar("E", bound=Event)

# =============================================================================
# QUERY EVENTS (Request current state)
# =============================================================================

@dataclass
class RequestCameraStateQuery(Event):
    """Request current camera state."""
    pass

@dataclass
class RequestStageStateQuery(Event):
    """Request current stage state."""
    pass

@dataclass
class RequestLiveStateQuery(Event):
    """Request current live state."""
    pass

@dataclass
class RequestAutofocusStateQuery(Event):
    """Request current autofocus state."""
    pass

@dataclass
class RequestAcquisitionStateQuery(Event):
    """Request current acquisition state."""
    pass

# =============================================================================
# CAMERA COMMANDS
# =============================================================================

@dataclass
class SetExposureCommand(Event):
    """Command to set camera exposure time."""
    exposure_ms: float

@dataclass
class SetGainCommand(Event):
    """Command to set camera analog gain."""
    gain: float

@dataclass
class SetBinningCommand(Event):
    """Command to set camera binning."""
    x: int
    y: int

@dataclass
class SetROICommand(Event):
    """Command to set camera region of interest."""
    x: int
    y: int
    width: int
    height: int

@dataclass
class SetPixelFormatCommand(Event):
    """Command to set camera pixel format."""
    pixel_format: str

# =============================================================================
# STAGE COMMANDS
# =============================================================================

@dataclass
class MoveStageRelativeCommand(Event):
    """Command to move stage by relative amount."""
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

@dataclass
class MoveStageToCommand(Event):
    """Command to move stage to absolute position."""
    x: float
    y: float
    z: float | None = None

@dataclass
class HomeStageCommand(Event):
    """Command to home stage axes."""
    x: bool = False
    y: bool = False
    z: bool = False

@dataclass
class ZeroStageCommand(Event):
    """Command to zero stage axes."""
    x: bool = False
    y: bool = False
    z: bool = False

# =============================================================================
# LIVE COMMANDS
# =============================================================================

@dataclass
class StartLiveCommand(Event):
    """Command to start live streaming."""
    configuration: Any | None = None  # ChannelConfiguration

@dataclass
class StopLiveCommand(Event):
    """Command to stop live streaming."""
    pass

@dataclass
class SetTriggerModeCommand(Event):
    """Command to set trigger mode."""
    mode: Any  # TriggerMode enum

@dataclass
class SetTriggerFPSCommand(Event):
    """Command to set trigger FPS."""
    fps: float

@dataclass
class SetMicroscopeModeCommand(Event):
    """Command to set microscope mode/channel configuration."""
    configuration: Any  # ChannelConfiguration

# =============================================================================
# AUTOFOCUS COMMANDS
# =============================================================================

@dataclass
class StartAutofocusCommand(Event):
    """Command to start autofocus."""
    use_focus_map: bool = True

@dataclass
class StopAutofocusCommand(Event):
    """Command to stop autofocus."""
    pass

@dataclass
class SetAutofocusParamsCommand(Event):
    """Command to set autofocus parameters."""
    n_planes: int | None = None
    delta_z_um: float | None = None

@dataclass
class AddFocusMapPointCommand(Event):
    """Command to add a point to focus map."""
    x: float
    y: float
    z: float

@dataclass
class ClearFocusMapCommand(Event):
    """Command to clear focus map."""
    pass

# =============================================================================
# ACQUISITION COMMANDS
# =============================================================================

@dataclass
class StartAcquisitionCommand(Event):
    """Command to start acquisition."""
    config: Any  # AcquisitionConfig

@dataclass
class StopAcquisitionCommand(Event):
    """Command to stop acquisition."""
    pass

@dataclass
class PauseAcquisitionCommand(Event):
    """Command to pause acquisition."""
    pass

@dataclass
class ResumeAcquisitionCommand(Event):
    """Command to resume acquisition."""
    pass

# =============================================================================
# PERIPHERAL COMMANDS
# =============================================================================

@dataclass
class SetDACCommand(Event):
    """Command to set DAC output."""
    channel: int
    value_percent: float

@dataclass
class TurnOnAFLaserCommand(Event):
    """Command to turn on autofocus laser."""
    wait: bool = False

@dataclass
class TurnOffAFLaserCommand(Event):
    """Command to turn off autofocus laser."""
    wait: bool = False

# =============================================================================
# STATE EVENTS (Controller -> GUI)
# =============================================================================

@dataclass
class CameraStateChanged(Event):
    """Notification that camera state changed."""
    state: Any  # CameraState

@dataclass
class StageStateChanged(Event):
    """Notification that stage state changed."""
    state: Any  # StageState

@dataclass
class LiveStateChanged(Event):
    """Notification that live state changed."""
    state: Any  # LiveState

@dataclass
class AutofocusStateChanged(Event):
    """Notification that autofocus state changed."""
    state: Any  # AutofocusState

@dataclass
class AutofocusCompleted(Event):
    """Notification that autofocus completed."""
    z_mm: float
    success: bool = True

@dataclass
class AcquisitionStateChanged(Event):
    """Notification that acquisition state changed."""
    state: Any  # AcquisitionState

@dataclass
class AcquisitionCompleted(Event):
    """Notification that acquisition completed."""
    success: bool = True
    error: str | None = None

@dataclass
class DACValueChanged(Event):
    """Notification that DAC value changed."""
    channel: int
    value_percent: float

@dataclass
class NewFrameAvailable(Event):
    """Notification that a new frame is available for display."""
    frame_id: int
    channel: str | None = None
```

**Commit:** `refactor(events): Reorganize and complete event definitions`

---

## Phase 4: Refactor Widgets

This is the most extensive phase. Each widget needs to be updated to:
1. Only communicate via EventBus
2. Subscribe to state events
3. Publish commands on user action
4. Remove direct controller/service references

### Task 4.1: Create Widget Base Helper

**Create:** `control/widgets/base_widget.py`

```python
"""Base class for reactive widgets."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Type, TypeVar
from PyQt5.QtWidgets import QWidget

if TYPE_CHECKING:
    from squid.events import Event, EventBus

E = TypeVar("E", bound="Event")


class ReactiveWidget(QWidget):
    """Base class for widgets that use EventBus."""

    def __init__(self, event_bus: "EventBus", parent: QWidget | None = None):
        super().__init__(parent)
        self._bus = event_bus
        self._subscriptions: list[tuple[Type[E], Callable]] = []

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """Subscribe to an event type."""
        self._bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def publish(self, event: "Event") -> None:
        """Publish an event."""
        self._bus.publish(event)

    def closeEvent(self, event) -> None:
        """Clean up subscriptions on close."""
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except ValueError:
                pass  # Already unsubscribed
        self._subscriptions.clear()
        super().closeEvent(event)
```

**Commit:** `feat(widgets): Add ReactiveWidget base class`

---

### Task 4.2: Refactor CameraSettingsWidget

**File:** `control/widgets/camera/settings.py`

Update to use only EventBus:

```python
class CameraSettingsWidget(ReactiveWidget):
    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self._build_ui()

        # Subscribe to state
        self.subscribe(CameraStateChanged, self._on_state_changed)

        # Request initial state
        self.publish(RequestCameraStateQuery())

    def _build_ui(self) -> None:
        # ... existing UI code ...

        # Connect signals to publish commands
        self.entry_exposureTime.valueChanged.connect(
            lambda v: self.publish(SetExposureCommand(exposure_ms=v))
        )
        self.entry_analogGain.valueChanged.connect(
            lambda v: self.publish(SetGainCommand(gain=v))
        )

    def _on_state_changed(self, event: CameraStateChanged) -> None:
        state = event.state

        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(state.exposure_ms)
        self.entry_exposureTime.blockSignals(False)

        self.entry_analogGain.blockSignals(True)
        self.entry_analogGain.setValue(state.gain)
        self.entry_analogGain.blockSignals(False)
```

**Commit:** `refactor(widgets): CameraSettingsWidget uses EventBus only`

---

### Task 4.3: Refactor NavigationWidget

**File:** `control/widgets/stage/navigation.py`

**Commit:** `refactor(widgets): NavigationWidget uses EventBus only`

---

### Task 4.4: Refactor LiveControlWidget

**File:** `control/widgets/camera/live_control.py`

**Commit:** `refactor(widgets): LiveControlWidget uses EventBus only`

---

### Task 4.5-4.N: Refactor Remaining Widgets

Continue for each widget category:

| Widget | File | Commit Message |
|--------|------|----------------|
| AutoFocusWidget | `stage/autofocus.py` | `refactor(widgets): AutoFocusWidget uses EventBus only` |
| TriggerControlWidget | `hardware/trigger.py` | `refactor(widgets): TriggerControlWidget uses EventBus only` |
| DACControlWidget | `hardware/dac.py` | `refactor(widgets): DACControlWidget uses EventBus only` |
| LaserAutofocusControlWidget | `hardware/laser_autofocus.py` | `refactor(widgets): LaserAutofocusControlWidget uses EventBus only` |
| WellplateMultiPointWidget | `acquisition/wellplate_multipoint.py` | `refactor(widgets): WellplateMultiPointWidget uses EventBus only` |
| NapariLiveWidget | `display/napari_live.py` | `refactor(widgets): NapariLiveWidget uses EventBus only` |
| FocusMapWidget | `display/focus_map.py` | `refactor(widgets): FocusMapWidget uses EventBus only` |
| TrackingControllerWidget | `tracking/controller.py` | `refactor(widgets): TrackingControllerWidget uses EventBus only` |
| WellplateCalibration | `wellplate/calibration.py` | `refactor(widgets): WellplateCalibration uses EventBus only` |

---

## Phase 5: Wire Up Application

### Task 5.1: Create Application Bootstrap

**Update:** `squid/application.py`

```python
class SquidApplication:
    def __init__(self, microscope: Microscope, event_bus: EventBus):
        self._microscope = microscope
        self._event_bus = event_bus

        # Create services (stateless)
        self._services = self._create_services()

        # Create controllers (stateful, on EventBus)
        self._controllers = self._create_controllers()

    def _create_services(self) -> dict:
        return {
            "camera": CameraServiceV2(self._microscope.camera),
            "stage": StageServiceV2(self._microscope.stage),
            "illumination": IlluminationService(
                self._microscope.microcontroller,
                self._microscope.light_source,
            ),
            "peripheral": PeripheralServiceV2(self._microscope.microcontroller),
            "piezo": PiezoService(self._microscope.piezo) if self._microscope.piezo else None,
        }

    def _create_controllers(self) -> dict:
        s = self._services
        bus = self._event_bus

        camera_ctrl = CameraController(s["camera"], bus)
        stage_ctrl = StageController(s["stage"], bus)
        live_ctrl = LiveControllerV2(
            s["camera"], s["illumination"], s["peripheral"], bus
        )
        # ... etc

        return {
            "camera": camera_ctrl,
            "stage": stage_ctrl,
            "live": live_ctrl,
        }

    def shutdown(self) -> None:
        for controller in self._controllers.values():
            controller.shutdown()
```

**Commit:** `feat(app): Create application bootstrap with new architecture`

---

### Task 5.2: Update Main Entry Point

**Update:** `main_hcs.py`

Wire up the new application structure.

**Commit:** `refactor(main): Use new application bootstrap`

---

## Phase 6: Remove Legacy Code

### Task 6.1: Remove Old Services

Once all widgets are migrated, remove:
- `squid/services/camera_service.py` (old version)
- `squid/services/trigger_service.py`
- `squid/services/microscope_mode_service.py`
- `squid/services/live_service.py`

Rename V2 services to remove suffix.

**Commit:** `refactor(services): Remove legacy services, rename V2 to final names`

---

### Task 6.2: Remove Old Controllers

Remove from `control/core/`:
- Old `LiveController` (replaced by new one)
- Callback-based patterns

**Commit:** `refactor(controllers): Remove legacy controller patterns`

---

### Task 6.3: Clean Up Unused Events

Remove any events that are no longer used.

**Commit:** `refactor(events): Remove unused legacy events`

---

## Phase 7: Add Comprehensive Tests

### Task 7.1: Service Unit Tests

Create/update tests for each service:

```
tests/unit/squid/services/
├── test_camera_service.py
├── test_stage_service.py
├── test_illumination_service.py
├── test_peripheral_service.py
├── test_piezo_service.py
└── test_filter_wheel_service.py
```

### Task 7.2: Controller Unit Tests

Create tests for each controller:

```
tests/unit/squid/controllers/
├── test_camera_controller.py
├── test_stage_controller.py
├── test_live_controller.py
├── test_autofocus_controller.py
└── test_acquisition_controller.py
```

### Task 7.3: Integration Tests

Create integration tests that verify the full flow:

```
tests/integration/
├── test_camera_flow.py      # Widget -> Controller -> Service
├── test_stage_flow.py
├── test_live_flow.py
└── test_acquisition_flow.py
```

---

## Migration Checklist

Use this checklist to track progress:

### Phase 1: Services
- [ ] StatelessService base class
- [ ] CameraServiceV2
- [ ] StageServiceV2
- [ ] IlluminationService
- [ ] PeripheralServiceV2
- [ ] PiezoService
- [ ] FilterWheelService
- [ ] Update __init__.py exports

### Phase 2: Controllers
- [ ] State dataclasses
- [ ] BaseController
- [ ] CameraController
- [ ] StageController
- [ ] LiveControllerV2
- [ ] AutofocusControllerV2
- [ ] AcquisitionControllerV2
- [ ] Update __init__.py exports

### Phase 3: Events
- [ ] Reorganize event definitions
- [ ] Add missing query events
- [ ] Add missing state events

### Phase 4: Widgets
- [ ] ReactiveWidget base class
- [ ] CameraSettingsWidget
- [ ] NavigationWidget
- [ ] LiveControlWidget
- [ ] AutoFocusWidget
- [ ] TriggerControlWidget
- [ ] DACControlWidget
- [ ] LaserAutofocusControlWidget
- [ ] WellplateMultiPointWidget
- [ ] NapariLiveWidget
- [ ] FocusMapWidget
- [ ] TrackingControllerWidget
- [ ] WellplateCalibration
- [ ] WellplateFormatWidget
- [ ] ObjectivesWidget

### Phase 5: Application
- [ ] Application bootstrap
- [ ] Main entry point update
- [ ] GUI factory update

### Phase 6: Cleanup
- [ ] Remove legacy services
- [ ] Remove legacy controllers
- [ ] Remove unused events
- [ ] Rename V2 classes

### Phase 7: Tests
- [ ] Service unit tests
- [ ] Controller unit tests
- [ ] Integration tests
- [ ] Manual testing checklist

---

## Rollback Plan

If issues arise during migration:

1. **Services**: Old and new services can coexist. Revert widget changes to use old services.
2. **Controllers**: Keep old controllers alongside new ones during transition.
3. **Widgets**: Each widget can be reverted independently.
4. **Git tags**: Create tags before each phase for easy rollback:
   - `pre-phase-1-services`
   - `pre-phase-2-controllers`
   - `pre-phase-4-widgets`

---

## Testing During Migration

After each task:

1. **Unit test**: `python -m pytest tests/unit/ -v`
2. **Integration test**: `python -m pytest tests/integration/ -v`
3. **Manual test**: `python main_hcs.py --simulation`
   - Verify affected widget works
   - Check console for errors
   - Test basic interactions

Enable EventBus debug mode during development:
```python
from squid.events import event_bus
event_bus.set_debug(True)
```
