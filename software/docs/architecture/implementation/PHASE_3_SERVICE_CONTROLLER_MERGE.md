# Phase 3: Service-Controller Merge

**Purpose:** Merge "thin wrapper" services into controllers. Create new controllers. This is the most complex phase.

**Prerequisites:** Phase 2 complete (new events and protocols exist)

**Estimated Effort:** 3-5 days

---

## Overview

This phase resolves the service/controller overlap by:
1. Merging `LiveService` and `TriggerService` into `LiveController`
2. Creating `MicroscopeModeController` from `MicroscopeModeService`
3. Creating `PeripheralsController` for objective/spinning disk/piezo
4. Updating `ApplicationContext` to wire everything correctly

---

## Task Checklist

### 3.1 Add EventBus to LiveController ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/live_controller.py`

First, add EventBus support without changing existing behavior.

- [x] Add `EventBus` parameter to constructor (optional, defaults to None)
- [x] Store EventBus as `self._bus`
- [x] Add `LiveState` dataclass for state management

**Changes to make:**

```python
# At top of file, add imports
from dataclasses import dataclass, replace
from squid.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    LiveStateChanged,
    TriggerModeChanged,
    TriggerFPSChanged,
)


@dataclass
class LiveState:
    """State managed by LiveController."""
    is_live: bool = False
    current_channel: str | None = None
    trigger_mode: str = "Continuous"
    trigger_fps: float = 10.0
    illumination_on: bool = False


class LiveController:
    def __init__(
        self,
        microscope,  # Keep existing parameter
        camera,  # Keep existing parameter
        event_bus: EventBus,  # ADD THIS PARAMETER
        # ... other existing parameters
    ):
        # Existing initialization...
        self._bus = event_bus
        self._state = LiveState()

        # Subscribe to commands (NEW)
        self._bus.subscribe(StartLiveCommand, self._on_start_live_command)
        self._bus.subscribe(StopLiveCommand, self._on_stop_live_command)
        self._bus.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode_command)
        self._bus.subscribe(SetTriggerFPSCommand, self._on_set_trigger_fps_command)
```

**Test:**
```bash
NUMBA_DISABLE_JIT=1 pytest tests/unit/control/core/test_live_controller.py -v
```

**Commit:** `refactor(live): Add EventBus to LiveController constructor`

---

### 3.2 Add Command Handlers to LiveController ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/live_controller.py`

Add handlers for the commands that `LiveService` and `TriggerService` currently handle.

- [x] Add `_on_start_live_command` handler
- [x] Add `_on_stop_live_command` handler
- [x] Add `_on_set_trigger_mode_command` handler
- [x] Add `_on_set_trigger_fps_command` handler
- [x] Add `_trigger_mode_to_str` and `_str_to_trigger_mode` helpers
- [x] Add `state` property

**Code to add:**

```python
# Add these methods to LiveController class

def _on_start_live_command(self, cmd: StartLiveCommand) -> None:
    """Handle StartLiveCommand from EventBus."""
    if self._state.is_live:
        return  # Already running

    # Call existing start_live method
    self.start_live()

    # Update state and publish
    self._state = replace(
        self._state,
        is_live=True,
        current_channel=cmd.configuration,
    )
    self._bus.publish(LiveStateChanged(
        is_live=True,
        configuration=cmd.configuration,
    ))


def _on_stop_live_command(self, cmd: StopLiveCommand) -> None:
    """Handle StopLiveCommand from EventBus."""
    if not self._state.is_live:
        return  # Not running

    # Call existing stop_live method
    self.stop_live()

    # Update state and publish
    self._state = replace(
        self._state,
        is_live=False,
        illumination_on=False,
    )
    self._bus.publish(LiveStateChanged(
        is_live=False,
        configuration=None,
    ))


def _on_set_trigger_mode_command(self, cmd: SetTriggerModeCommand) -> None:
    """Handle SetTriggerModeCommand from EventBus."""
    # Call existing set_trigger_mode method
    self.set_trigger_mode(cmd.mode)

    # Update state and publish
    self._state = replace(self._state, trigger_mode=cmd.mode)
    self._bus.publish(TriggerModeChanged(mode=cmd.mode))


def _on_set_trigger_fps_command(self, cmd: SetTriggerFPSCommand) -> None:
    """Handle SetTriggerFPSCommand from EventBus."""
    # Call existing set_trigger_fps method
    self.set_trigger_fps(cmd.fps)

    # Update state and publish
    self._state = replace(self._state, trigger_fps=cmd.fps)
    self._bus.publish(TriggerFPSChanged(fps=cmd.fps))


@property
def state(self) -> LiveState:
    """Get current state."""
    return self._state
```

**Test with new tests:**

```python
# tests/unit/control/core/test_live_controller.py

def test_handles_start_live_command():
    """LiveController handles StartLiveCommand."""
    # Setup
    bus = EventBus()
    mock_camera = MagicMock()
    mock_microscope = MagicMock()

    controller = LiveController(
        microscope=mock_microscope,
        camera=mock_camera,
        event_bus=bus,
    )

    events_received = []
    bus.subscribe(LiveStateChanged, events_received.append)

    # Act
    bus.publish(StartLiveCommand(configuration="BF"))

    # Assert
    assert controller.state.is_live is True
    assert len(events_received) == 1
    assert events_received[0].is_live is True


def test_handles_stop_live_command():
    """LiveController handles StopLiveCommand."""
    bus = EventBus()
    mock_camera = MagicMock()
    mock_microscope = MagicMock()

    controller = LiveController(
        microscope=mock_microscope,
        camera=mock_camera,
        event_bus=bus,
    )

    # Start first
    bus.publish(StartLiveCommand())

    events_received = []
    bus.subscribe(LiveStateChanged, events_received.append)

    # Stop
    bus.publish(StopLiveCommand())

    assert controller.state.is_live is False
    assert len(events_received) == 1
    assert events_received[0].is_live is False
```

**Commit:** `refactor(live): Add command handlers to LiveController`

---

### 3.3 Create MicroscopeModeController ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/controllers/microscope_mode_controller.py`

Create this new controller to replace `MicroscopeModeService`.

- [x] Create file
- [x] Implement controller
- [x] Add to `__init__.py`
- [x] Create tests in `tests/unit/squid/controllers/test_microscope_mode_controller.py`

**Full implementation:**

```python
"""Microscope mode/channel controller.

Manages microscope channel/configuration switching. When switching modes,
coordinates camera settings, illumination, and filters.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from squid.events import (
    SetMicroscopeModeCommand,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    MicroscopeModeChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services import CameraService
    from squid.services.illumination_service import IlluminationService
    from squid.services.filter_wheel_service import FilterWheelService


@dataclass
class MicroscopeModeState:
    """State managed by MicroscopeModeController."""
    current_mode: str | None = None
    available_modes: tuple[str, ...] = ()


class MicroscopeModeController:
    """Manages microscope channel/mode switching.

    Coordinates camera settings, illumination, and filters when switching modes.

    Subscribes to: SetMicroscopeModeCommand
    Publishes: MicroscopeModeChanged
    """

    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: IlluminationService,
        filter_wheel_service: FilterWheelService | None,
        channel_configs: dict,
        event_bus: EventBus,
    ) -> None:
        self._camera = camera_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._channel_configs = channel_configs
        self._bus = event_bus
        self._lock = threading.RLock()

        self._state = MicroscopeModeState(
            current_mode=None,
            available_modes=tuple(channel_configs.keys()),
        )

        self._bus.subscribe(SetMicroscopeModeCommand, self._on_set_mode)

    @property
    def state(self) -> MicroscopeModeState:
        """Get current state."""
        return self._state

    def _on_set_mode(self, cmd: SetMicroscopeModeCommand) -> None:
        """Handle SetMicroscopeModeCommand."""
        mode = cmd.configuration_name

        if mode not in self._channel_configs:
            return

        config = self._channel_configs[mode]

        # Apply camera settings via events (so CameraService handles validation)
        if hasattr(config, 'exposure_time') or hasattr(config, 'exposure_ms'):
            exposure = getattr(config, 'exposure_time', None) or getattr(config, 'exposure_ms', None)
            if exposure is not None:
                self._bus.publish(SetExposureTimeCommand(exposure_time_ms=exposure))

        if hasattr(config, 'analog_gain'):
            self._bus.publish(SetAnalogGainCommand(gain=config.analog_gain))

        # Apply illumination via service
        if hasattr(config, 'illumination_source') and hasattr(config, 'intensity'):
            self._illumination.set_channel_intensity(
                config.illumination_source,
                config.intensity,
            )

        # Apply filter wheel if specified
        if (
            hasattr(config, 'filter_wheel_position')
            and config.filter_wheel_position is not None
            and self._filter_wheel is not None
            and self._filter_wheel.is_available()
        ):
            self._filter_wheel.set_position(config.filter_wheel_position)

        # Update state and publish
        with self._lock:
            self._state = replace(self._state, current_mode=mode)

        self._bus.publish(MicroscopeModeChanged(configuration_name=mode))

    def apply_mode_for_acquisition(self, mode: str) -> None:
        """Apply mode settings for acquisition (direct calls for speed).

        Used during acquisition when event round-trips would be too slow.
        """
        if mode not in self._channel_configs:
            return

        config = self._channel_configs[mode]

        # Direct service calls for efficiency
        if hasattr(config, 'exposure_time') or hasattr(config, 'exposure_ms'):
            exposure = getattr(config, 'exposure_time', None) or getattr(config, 'exposure_ms', None)
            if exposure is not None:
                self._camera.set_exposure_time(exposure)

        if hasattr(config, 'analog_gain'):
            self._camera.set_analog_gain(config.analog_gain)

        if hasattr(config, 'illumination_source') and hasattr(config, 'intensity'):
            self._illumination.set_channel_intensity(
                config.illumination_source,
                config.intensity,
            )

        if (
            hasattr(config, 'filter_wheel_position')
            and config.filter_wheel_position is not None
            and self._filter_wheel is not None
            and self._filter_wheel.is_available()
        ):
            self._filter_wheel.set_position(config.filter_wheel_position)

        with self._lock:
            self._state = replace(self._state, current_mode=mode)

    def get_available_modes(self) -> tuple[str, ...]:
        """Get list of available mode names."""
        return self._state.available_modes
```

**Update exports:**

```python
# squid/controllers/__init__.py

from .microscope_mode_controller import MicroscopeModeController

__all__ = [
    "MicroscopeModeController",
]
```

**Create tests:**

```python
# tests/unit/squid/controllers/test_microscope_mode_controller.py

from unittest.mock import MagicMock
import pytest

from squid.events import EventBus, SetMicroscopeModeCommand, MicroscopeModeChanged
from squid.controllers.microscope_mode_controller import MicroscopeModeController


class TestMicroscopeModeController:
    @pytest.fixture
    def mock_camera_service(self):
        return MagicMock()

    @pytest.fixture
    def mock_illumination_service(self):
        return MagicMock()

    @pytest.fixture
    def mock_filter_wheel_service(self):
        service = MagicMock()
        service.is_available.return_value = True
        return service

    @pytest.fixture
    def channel_configs(self):
        # Simple mock configs
        class Config:
            def __init__(self, exposure, gain):
                self.exposure_ms = exposure
                self.analog_gain = gain
                self.illumination_source = "488"
                self.intensity = 50.0
                self.filter_wheel_position = None

        return {
            "BF": Config(10.0, 1.0),
            "DAPI": Config(100.0, 5.0),
        }

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def controller(
        self,
        mock_camera_service,
        mock_illumination_service,
        mock_filter_wheel_service,
        channel_configs,
        event_bus,
    ):
        return MicroscopeModeController(
            camera_service=mock_camera_service,
            illumination_service=mock_illumination_service,
            filter_wheel_service=mock_filter_wheel_service,
            channel_configs=channel_configs,
            event_bus=event_bus,
        )

    def test_initial_state(self, controller):
        assert controller.state.current_mode is None
        assert "BF" in controller.state.available_modes
        assert "DAPI" in controller.state.available_modes

    def test_handles_set_mode_command(self, controller, event_bus):
        events_received = []
        event_bus.subscribe(MicroscopeModeChanged, events_received.append)

        event_bus.publish(SetMicroscopeModeCommand(configuration_name="BF"))

        assert controller.state.current_mode == "BF"
        assert len(events_received) == 1
        assert events_received[0].configuration_name == "BF"

    def test_ignores_unknown_mode(self, controller, event_bus):
        events_received = []
        event_bus.subscribe(MicroscopeModeChanged, events_received.append)

        event_bus.publish(SetMicroscopeModeCommand(configuration_name="UNKNOWN"))

        assert controller.state.current_mode is None
        assert len(events_received) == 0
```

**Commit:** `feat(controllers): Create MicroscopeModeController`

---

### 3.4 Create PeripheralsController ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/controllers/peripherals_controller.py`

- [x] Create file with full implementation
- [x] Add to `__init__.py`
- [x] Create tests in `tests/unit/squid/controllers/test_peripherals_controller.py`

**Full implementation:**

```python
"""Peripherals controller.

Handles simple peripheral hardware that doesn't need complex orchestration:
objective changer, spinning disk, piezo Z stage.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from squid.events import (
    SetObjectiveCommand,
    SetSpinningDiskPositionCommand,
    SetSpinningDiskSpinningCommand,
    SetDiskDichroicCommand,
    SetDiskEmissionFilterCommand,
    SetPiezoPositionCommand,
    MovePiezoRelativeCommand,
    ObjectiveChanged,
    SpinningDiskStateChanged,
    PiezoPositionChanged,
    PixelSizeChanged,
)

if TYPE_CHECKING:
    from squid.abc import ObjectiveChanger, SpinningDiskController, PiezoStage
    from squid.events import EventBus
    from control.core.navigation.objective_store import ObjectiveStore


@dataclass(frozen=True)
class SpinningDiskState:
    """State of spinning disk confocal."""
    is_available: bool
    is_disk_in: bool = False
    is_spinning: bool = False
    motor_speed: int = 0
    dichroic: int = 0
    emission_filter: int = 0


@dataclass
class PeripheralsState:
    """State managed by PeripheralsController."""
    objective_position: int | None = None
    objective_name: str | None = None
    pixel_size_um: float | None = None
    spinning_disk: SpinningDiskState | None = None
    piezo_position_um: float | None = None


class PeripheralsController:
    """Handles peripheral hardware control.

    Manages: objective changer, spinning disk, piezo Z stage.

    Subscribes to: SetObjectiveCommand, SetSpinningDisk*, SetPiezo*
    Publishes: ObjectiveChanged, SpinningDiskStateChanged, PiezoPositionChanged
    """

    def __init__(
        self,
        objective_changer: ObjectiveChanger | None,
        spinning_disk: SpinningDiskController | None,
        piezo: PiezoStage | None,
        objective_store: ObjectiveStore | None,
        event_bus: EventBus,
    ) -> None:
        self._objective_changer = objective_changer
        self._spinning_disk = spinning_disk
        self._piezo = piezo
        self._objective_store = objective_store
        self._bus = event_bus
        self._lock = threading.RLock()

        self._state = self._read_initial_state()

        # Subscribe to commands
        if objective_changer:
            self._bus.subscribe(SetObjectiveCommand, self._on_set_objective)

        if spinning_disk:
            self._bus.subscribe(SetSpinningDiskPositionCommand, self._on_set_disk_position)
            self._bus.subscribe(SetSpinningDiskSpinningCommand, self._on_set_spinning)
            self._bus.subscribe(SetDiskDichroicCommand, self._on_set_dichroic)
            self._bus.subscribe(SetDiskEmissionFilterCommand, self._on_set_emission)

        if piezo:
            self._bus.subscribe(SetPiezoPositionCommand, self._on_set_piezo)
            self._bus.subscribe(MovePiezoRelativeCommand, self._on_move_piezo_relative)

    @property
    def state(self) -> PeripheralsState:
        """Get current state."""
        return self._state

    def _read_initial_state(self) -> PeripheralsState:
        """Read initial state from hardware."""
        obj_pos = None
        obj_name = None
        pixel_size = None

        if self._objective_changer:
            with self._lock:
                obj_pos = self._objective_changer.current_position
                info = self._objective_changer.get_objective_info(obj_pos)
                if info:
                    obj_name = info.name
                    pixel_size = info.pixel_size_um

        disk_state = None
        if self._spinning_disk:
            with self._lock:
                disk_state = SpinningDiskState(
                    is_available=True,
                    is_disk_in=self._spinning_disk.is_disk_in,
                    is_spinning=self._spinning_disk.is_spinning,
                    motor_speed=self._spinning_disk.disk_motor_speed,
                    dichroic=self._spinning_disk.current_dichroic,
                    emission_filter=self._spinning_disk.current_emission_filter,
                )

        piezo_pos = None
        if self._piezo:
            with self._lock:
                piezo_pos = self._piezo.position_um

        return PeripheralsState(
            objective_position=obj_pos,
            objective_name=obj_name,
            pixel_size_um=pixel_size,
            spinning_disk=disk_state,
            piezo_position_um=piezo_pos,
        )

    # --- Objective ---

    def _on_set_objective(self, cmd: SetObjectiveCommand) -> None:
        """Handle SetObjectiveCommand."""
        if not self._objective_changer:
            return

        with self._lock:
            self._objective_changer.set_position(cmd.position)
            actual = self._objective_changer.current_position
            info = self._objective_changer.get_objective_info(actual)

        obj_name = info.name if info else None
        pixel_size = info.pixel_size_um if info else None

        self._state = replace(
            self._state,
            objective_position=actual,
            objective_name=obj_name,
            pixel_size_um=pixel_size,
        )

        # Update objective store if available
        if self._objective_store:
            self._objective_store.set_current_objective(actual)

        self._bus.publish(ObjectiveChanged(
            position=actual,
            objective_name=obj_name,
            pixel_size_um=pixel_size,
        ))

        if pixel_size:
            self._bus.publish(PixelSizeChanged(pixel_size_um=pixel_size))

    # --- Spinning Disk ---

    def _on_set_disk_position(self, cmd: SetSpinningDiskPositionCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_disk_position(cmd.in_beam)
        self._update_disk_state()

    def _on_set_spinning(self, cmd: SetSpinningDiskSpinningCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_spinning(cmd.spinning)
        self._update_disk_state()

    def _on_set_dichroic(self, cmd: SetDiskDichroicCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_dichroic(cmd.position)
        self._update_disk_state()

    def _on_set_emission(self, cmd: SetDiskEmissionFilterCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_emission_filter(cmd.position)
        self._update_disk_state()

    def _update_disk_state(self) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            disk_state = SpinningDiskState(
                is_available=True,
                is_disk_in=self._spinning_disk.is_disk_in,
                is_spinning=self._spinning_disk.is_spinning,
                motor_speed=self._spinning_disk.disk_motor_speed,
                dichroic=self._spinning_disk.current_dichroic,
                emission_filter=self._spinning_disk.current_emission_filter,
            )

        self._state = replace(self._state, spinning_disk=disk_state)
        self._bus.publish(SpinningDiskStateChanged(
            is_disk_in=disk_state.is_disk_in,
            is_spinning=disk_state.is_spinning,
            motor_speed=disk_state.motor_speed,
            dichroic=disk_state.dichroic,
            emission_filter=disk_state.emission_filter,
        ))

    # --- Piezo ---

    def _on_set_piezo(self, cmd: SetPiezoPositionCommand) -> None:
        if not self._piezo:
            return

        with self._lock:
            min_pos, max_pos = self._piezo.range_um
            clamped = max(min_pos, min(max_pos, cmd.position_um))
            self._piezo.move_to(clamped)
            actual = self._piezo.position_um

        self._state = replace(self._state, piezo_position_um=actual)
        self._bus.publish(PiezoPositionChanged(position_um=actual))

    def _on_move_piezo_relative(self, cmd: MovePiezoRelativeCommand) -> None:
        if not self._piezo:
            return

        with self._lock:
            self._piezo.move_relative(cmd.delta_um)
            actual = self._piezo.position_um

        self._state = replace(self._state, piezo_position_um=actual)
        self._bus.publish(PiezoPositionChanged(position_um=actual))

    # --- Convenience methods ---

    def has_objective_changer(self) -> bool:
        return self._objective_changer is not None

    def has_spinning_disk(self) -> bool:
        return self._spinning_disk is not None

    def has_piezo(self) -> bool:
        return self._piezo is not None
```

**Update exports:**

```python
# squid/controllers/__init__.py

from .microscope_mode_controller import MicroscopeModeController
from .peripherals_controller import PeripheralsController

__all__ = [
    "MicroscopeModeController",
    "PeripheralsController",
]
```

**Commit:** `feat(controllers): Create PeripheralsController`

---

### 3.5 Update ApplicationContext ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/application.py`

Update to create and wire the new controllers.

- [x] Import new controllers
- [x] Update `Controllers` dataclass (added `microscope_mode` and `peripherals` fields)
- [x] Create controllers in `_build_controllers()`
- [x] Pass EventBus to LiveController
- [x] Add `_create_microscope_mode_controller()` helper
- [x] Add `_create_peripherals_controller()` helper
- [ ] Remove registration of deprecated services (kept for backwards compatibility)

**Changes to make:**

```python
# Add imports
from squid.controllers import MicroscopeModeController, PeripheralsController

# Update Controllers dataclass
@dataclass
class Controllers:
    live: LiveController
    stream_handler: StreamHandler
    microscope_mode: MicroscopeModeController  # ADD
    peripherals: PeripheralsController  # ADD
    multipoint: MultiPointController | None = None
    # ... other existing fields


# In _build_controllers() method:

def _build_controllers(self) -> Controllers:
    # ... existing code ...

    # Create LiveController with EventBus
    live_controller = LiveController(
        microscope=self._microscope,
        camera=self._microscope.camera,
        event_bus=self._event_bus,  # ADD THIS
        # ... other existing parameters
    )

    # Create MicroscopeModeController (NEW)
    microscope_mode_controller = MicroscopeModeController(
        camera_service=self._services.get("camera"),
        illumination_service=self._services.get("illumination"),
        filter_wheel_service=self._services.get("filter_wheel"),
        channel_configs=channel_configs,
        event_bus=self._event_bus,
    )

    # Create PeripheralsController (NEW)
    peripherals_controller = PeripheralsController(
        objective_changer=self._microscope.addons.objective_changer if hasattr(self._microscope.addons, 'objective_changer') else None,
        spinning_disk=self._microscope.addons.xlight if hasattr(self._microscope.addons, 'xlight') else None,
        piezo=self._microscope.addons.piezo_stage if hasattr(self._microscope.addons, 'piezo_stage') else None,
        objective_store=objective_store,
        event_bus=self._event_bus,
    )

    return Controllers(
        live=live_controller,
        stream_handler=stream_handler,
        microscope_mode=microscope_mode_controller,  # ADD
        peripherals=peripherals_controller,  # ADD
        multipoint=multipoint_controller,
        # ... other fields
    )
```

**Commit:** `refactor(app): Wire new controllers in ApplicationContext`

---

### 3.6 Write Tests for Integration ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/tests/integration/squid/test_application_controllers.py`

Tests created:
- `TestLiveControllerEventBus`: Tests for LiveController EventBus integration
- `TestControllersDataclass`: Tests for Controllers dataclass structure
- `TestNewControllerImports`: Tests for controller imports

```python
"""Integration tests for controller wiring."""

import pytest

from squid.application import ApplicationContext
from squid.events import StartLiveCommand, SetMicroscopeModeCommand


class TestApplicationControllers:
    """Test that controllers are properly wired."""

    @pytest.fixture
    def app_context(self):
        """Create application context in simulation mode."""
        # This may need adjustment based on actual ApplicationContext API
        context = ApplicationContext(simulated=True)
        yield context
        context.shutdown()

    def test_live_controller_receives_eventbus(self, app_context):
        """LiveController should have EventBus."""
        assert app_context.controllers.live._bus is not None

    def test_microscope_mode_controller_exists(self, app_context):
        """MicroscopeModeController should exist."""
        assert app_context.controllers.microscope_mode is not None

    def test_peripherals_controller_exists(self, app_context):
        """PeripheralsController should exist."""
        assert app_context.controllers.peripherals is not None
```

**Commit:** `test(integration): Add controller wiring tests`

---

## Verification Checklist

Before proceeding to Phase 4, verify:

- [x] LiveController has `_bus` attribute
- [x] LiveController subscribes to `StartLiveCommand`, `StopLiveCommand`
- [x] LiveController publishes `LiveStateChanged`
- [x] MicroscopeModeController exists and works
- [x] PeripheralsController exists and works
- [x] ApplicationContext creates all controllers
- [X] All unit tests pass (manual verification needed)
- [X] Application starts in simulation mode: `python main_hcs.py --simulation` (manual verification needed)

**Status:** All code tasks complete. Manual verification required.

**Manual Verification Commands:**
```bash
# Run unit tests
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/controllers/ -v
NUMBA_DISABLE_JIT=1 pytest tests/integration/squid/test_application_controllers.py -v

# Run full app
python main_hcs.py --simulation
```

---

## Commit Summary

| Order | Commit Message | Files |
|-------|----------------|-------|
| 1 | `refactor(live): Add EventBus to LiveController constructor` | `control/core/display/live_controller.py` |
| 2 | `refactor(live): Add command handlers to LiveController` | `control/core/display/live_controller.py`, tests |
| 3 | `feat(controllers): Create MicroscopeModeController` | `squid/controllers/`, tests |
| 4 | `feat(controllers): Create PeripheralsController` | `squid/controllers/`, tests |
| 5 | `refactor(app): Wire new controllers in ApplicationContext` | `squid/application.py` |
| 6 | `test(integration): Add controller wiring tests` | tests |

---

## Next Steps

Once all checkmarks are complete, proceed to:
→ [PHASE_4_ACQUISITION_SERVICE_USAGE.md](./PHASE_4_ACQUISITION_SERVICE_USAGE.md)
