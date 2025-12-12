# Service Layer Inventory

This document catalogs the current service layer implementation in `squid/services/`. It identifies which services are well-implemented, which need refactoring, and which are missing.

---

## Overview

| Service | File | Status | Action |
|---------|------|--------|--------|
| **BaseService** | `base.py` | Good | Keep as-is |
| **CameraService** | `camera_service.py` | Good | Keep as-is |
| **StageService** | `stage_service.py` | Good | Keep as-is |
| **PeripheralService** | `peripheral_service.py` | Good | Keep as-is |
| **LiveService** | `live_service.py` | Removed | Merged into LiveController |
| **TriggerService** | `trigger_service.py` | Removed | Merged into LiveController |
| **MicroscopeModeService** | `microscope_mode_service.py` | Removed | Replaced by MicroscopeModeController |
| **IlluminationService** | `illumination_service.py` | EventBus wired, minimal logic | Add locking and richer channel handling |
| **FluidicsService** | `fluidics_service.py` | EventBus wired, direct-call only | Add command/events when defined |
| **FilterWheelService** | N/A | Missing | Create new |

---

## BaseService (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/base.py`

**Purpose:** Abstract base class for all services.

**Key Features:**
- Subscription tracking for cleanup
- Delegated pub/sub to EventBus
- Automatic unsubscription on shutdown

**Implementation:**
```python
class BaseService(ABC):
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._log = squid.logging.get_logger(...)
        self._subscriptions: List[Tuple[Type[Event], Callable]] = []

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        self._event_bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def publish(self, event: Event) -> None:
        self._event_bus.publish(event)

    def shutdown(self) -> None:
        for event_type, handler in self._subscriptions:
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
```

**Status:** Well-designed. Keep as-is.

---

## CameraService (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/camera_service.py`

**Purpose:** Thread-safe camera operations.

**Commands Handled:**
| Command | Handler | Description |
|---------|---------|-------------|
| `SetExposureTimeCommand` | `_on_set_exposure_command` | Sets exposure time |
| `SetAnalogGainCommand` | `_on_set_gain_command` | Sets analog gain |

**Events Published:**
| Event | When |
|-------|------|
| `ExposureTimeChanged` | After exposure is set |
| `AnalogGainChanged` | After gain is set |
| `ROIChanged` | After ROI is set |
| `BinningChanged` | After binning is set |
| `PixelFormatChanged` | After pixel format is set |

**Direct Methods (for controller use):**
```python
def set_exposure_time(self, exposure_time_ms: float) -> None
def get_exposure_time(self) -> float
def get_exposure_limits(self) -> Tuple[float, float]
def set_analog_gain(self, gain: float) -> None
def get_analog_gain(self) -> float
def get_gain_range(self) -> Optional[CameraGainRange]
def set_region_of_interest(self, offset_x, offset_y, width, height) -> None
def reset_region_of_interest(self) -> None
def set_binning(self, x: int, y: int) -> None
def set_pixel_format(self, format: CameraPixelFormat) -> None
def set_temperature(self, temp_c: float) -> None
def get_temperature(self) -> float
def set_white_balance_gains(self, red, green, blue) -> None
def set_black_level(self, level: int) -> None
```

**Status:** Well-implemented with proper validation and thread safety. Keep as-is.

---

## StageService (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/stage_service.py`

**Purpose:** Thread-safe stage operations.

**Commands Handled:**
| Command | Handler | Description |
|---------|---------|-------------|
| `MoveStageCommand` | `_on_move_command` | Relative movement |
| `MoveStageToCommand` | `_on_move_to_command` | Absolute movement |
| `HomeStageCommand` | `_on_home_command` | Home axes |
| `ZeroStageCommand` | `_on_zero_command` | Zero axes |
| `MoveStageToLoadingPositionCommand` | `_on_move_to_loading_command` | Go to loading position |
| `MoveStageToScanningPositionCommand` | `_on_move_to_scanning_command` | Go to scanning position |

**Events Published:**
| Event | When |
|-------|------|
| `StagePositionChanged` | After any movement completes |

**Direct Methods (for controller use):**
```python
def move_x(self, distance_mm: float, callback=None) -> None
def move_y(self, distance_mm: float, callback=None) -> None
def move_z(self, distance_mm: float, callback=None) -> None
def move_to(self, x_mm=None, y_mm=None, z_mm=None, theta_rad=None, callback=None) -> None
def move_to_blocking(self, x_mm=None, y_mm=None, z_mm=None) -> Pos
def get_position(self) -> Pos
def home(self, x=False, y=False, z=False, theta=False) -> None
def zero(self, x=False, y=False, z=False, theta=False) -> None
```

**Features:**
- Tracks scanning position Z for wellplate mode
- Supports blocking/async movement with callbacks
- Publishes position updates after every movement

**Status:** Well-implemented. Keep as-is.

---

## PeripheralService (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/peripheral_service.py`

**Purpose:** Thread-safe microcontroller peripheral access.

**Commands Handled:**
| Command | Handler | Description |
|---------|---------|-------------|
| `SetDACCommand` | `_on_set_dac_command` | Set DAC output |
| `TurnOnAFLaserCommand` | `_on_turn_on_af_laser_command` | Enable AF laser |
| `TurnOffAFLaserCommand` | `_on_turn_off_af_laser_command` | Disable AF laser |

**Events Published:**
| Event | When |
|-------|------|
| `DACValueChanged` | After DAC value is set |

**Direct Methods (for controller use):**
```python
def set_dac_value(self, channel: int, value: float) -> None
def get_dac_value(self, channel: int) -> float
def start_camera_trigger(self) -> None
def stop_camera_trigger(self) -> None
def set_camera_trigger_frequency(self, freq_hz: float) -> None
def turn_on_af_laser(self, wait=True) -> None
def turn_off_af_laser(self, wait=True) -> None
def add_joystick_button_listener(self, callback: Callable) -> None
def send_trigger(self) -> None
def enable_joystick(self, enable: bool) -> None
```

**Status:** Well-implemented. Keep as-is.

---

## LiveService (Merge into LiveController)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/live_service.py`

**Purpose:** Control live camera preview.

**Commands Handled:**
| Command | Handler | Description |
|---------|---------|-------------|
| `StartLiveCommand` | `_on_start_live` | Start live view |
| `StopLiveCommand` | `_on_stop_live` | Stop live view |

**Events Published:**
| Event | When |
|-------|------|
| `LiveStateChanged` | After start/stop |

**Current Implementation (Thin Wrapper):**
```python
class LiveService(BaseService):
    def __init__(self, live_controller, event_bus):
        super().__init__(event_bus)
        self._live_controller = live_controller
        self.subscribe(StartLiveCommand, self._on_start_live)
        self.subscribe(StopLiveCommand, self._on_stop_live)

    def _on_start_live(self, event: StartLiveCommand):
        self._live_controller.start_live()  # Just delegates!
        self.publish(LiveStateChanged(is_live=True))

    def _on_stop_live(self, event: StopLiveCommand):
        self._live_controller.stop_live()  # Just delegates!
        self.publish(LiveStateChanged(is_live=False))
```

**Problem:** This is a thin wrapper that just delegates to `LiveController`. The service adds no value - it should be merged into `LiveController`.

**Action:** Delete this service. Move event handling to `LiveController`.

---

## TriggerService (Merge into LiveController)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/trigger_service.py`

**Purpose:** Control camera triggering.

**Commands Handled:**
| Command | Handler | Description |
|---------|---------|-------------|
| `SetTriggerModeCommand` | `_on_set_trigger_mode` | Set trigger mode |
| `SetTriggerFPSCommand` | `_on_set_trigger_fps` | Set trigger FPS |

**Events Published:**
| Event | When |
|-------|------|
| `TriggerModeChanged` | After mode is set |
| `TriggerFPSChanged` | After FPS is set |

**Current Implementation (Thin Wrapper):**
```python
class TriggerService(BaseService):
    def __init__(self, live_controller, event_bus):
        super().__init__(event_bus)
        self._live_controller = live_controller
        self.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self.subscribe(SetTriggerFPSCommand, self._on_set_trigger_fps)

    def _on_set_trigger_mode(self, event: SetTriggerModeCommand):
        self._live_controller.set_trigger_mode(event.mode)  # Just delegates!
        self.publish(TriggerModeChanged(mode=event.mode))

    def _on_set_trigger_fps(self, event: SetTriggerFPSCommand):
        self._live_controller.set_trigger_fps(event.fps)  # Just delegates!
        self.publish(TriggerFPSChanged(fps=event.fps))
```

**Problem:** Same as LiveService - thin wrapper that just delegates.

**Action:** Delete this service. Move event handling to `LiveController`.

---

## MicroscopeModeService (Rename to Controller)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/microscope_mode_service.py`

**Purpose:** Handle microscope channel/configuration switching.

**Commands Handled:**
| Command | Handler | Description |
|---------|---------|-------------|
| `SetMicroscopeModeCommand` | `_on_set_mode` | Switch microscope mode |

**Events Published:**
| Event | When |
|-------|------|
| `MicroscopeModeChanged` | After mode is applied |

**Current Implementation:**
```python
class MicroscopeModeService(BaseService):
    def __init__(self, live_controller, channel_config_manager, event_bus):
        super().__init__(event_bus)
        self._live_controller = live_controller
        self._config_manager = channel_config_manager
        self.subscribe(SetMicroscopeModeCommand, self._on_set_mode)

    def _on_set_mode(self, event: SetMicroscopeModeCommand):
        config = self._config_manager.get_configuration(
            event.configuration_name,
            event.objective
        )
        self._live_controller.set_microscope_mode(config)  # Coordinates!
        self.publish(MicroscopeModeChanged(configuration_name=event.configuration_name))
```

**Analysis:** This service does more than just delegate - it retrieves configuration and coordinates between components. This is **controller behavior**, not service behavior.

**Action:** Rename to `MicroscopeModeController` and move to `squid/controllers/`.

---

## IlluminationService (EventBus wired - basic)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/illumination_service.py`

**Purpose:** Control illumination sources.

**Current Implementation:**
```python
class IlluminationService(BaseService):
    def __init__(self, illumination: LightSource, event_bus: EventBus):
        super().__init__(event_bus)
        self._illumination = illumination
        self.subscribe(SetIlluminationCommand, self._on_set_illumination)

    def _on_set_illumination(self, cmd: SetIlluminationCommand) -> None:
        self.set_channel_power(cmd.channel, cmd.intensity)
        if cmd.on:
            self.turn_on_channel(cmd.channel)
        else:
            self.turn_off_channel(cmd.channel)
        self.publish(IlluminationStateChanged(channel=cmd.channel, intensity=cmd.intensity, on=cmd.on))
```

**Status:** EventBus integration is present; publishes `IlluminationStateChanged`. No locking and only supports a single `LightSource` instance (no multi-source routing).

**Action:** Add locking if hardware is not thread-safe; extend to multi-source routing and MCU-backed devices as needed.

---

## FluidicsService (EventBus wired - direct call only)

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/fluidics_service.py`

**Purpose:** Execute fluidics protocols.

**Current Implementation:**
```python
class FluidicsService(BaseService):
    def __init__(self, fluidics: Fluidics, event_bus: EventBus):
        super().__init__(event_bus)
        self._fluidics = fluidics

    def run_protocol(self, proto: FluidicsProtocol) -> None:
        self._fluidics.run_protocol(proto)
    # ... direct passthrough methods ...
```

**Status:** Takes `event_bus` but has no command subscriptions or published events; functions purely as a direct-call wrapper.

**Action:** Add command/event handling when fluidics events are defined; consider locking if the hardware driver is not thread-safe.

---

## FilterWheelService (Missing - Create New)

**File:** Does not exist.

**Purpose:** Thread-safe filter wheel operations.

**Events Needed:**
| Type | Event |
|------|-------|
| Command | `SetFilterPositionCommand(position: int, wheel_index: int)` |
| State | `FilterPositionChanged(position: int, wheel_index: int)` |

**Expected Implementation:**
```python
# squid/services/filter_wheel_service.py

class FilterWheelService(BaseService):
    def __init__(
        self,
        filter_wheel: AbstractFilterWheelController | None,
        event_bus: EventBus
    ):
        super().__init__(event_bus)
        self._wheel = filter_wheel
        self._lock = threading.RLock()

        if filter_wheel:
            self.subscribe(SetFilterPositionCommand, self._on_set_position)

    def _on_set_position(self, cmd: SetFilterPositionCommand) -> None:
        if not self._wheel:
            return
        with self._lock:
            self._wheel.set_filter_wheel_position(cmd.position, cmd.wheel_index)
            actual = self._wheel.get_filter_wheel_position(cmd.wheel_index)
        self.publish(FilterPositionChanged(position=actual, wheel_index=cmd.wheel_index))

    def set_position(self, position: int, wheel_index: int = 0) -> int:
        """Direct access for controllers."""
        if not self._wheel:
            raise HardwareNotAvailable("filter_wheel")
        with self._lock:
            self._wheel.set_filter_wheel_position(position, wheel_index)
            return self._wheel.get_filter_wheel_position(wheel_index)

    def get_position(self, wheel_index: int = 0) -> int:
        if not self._wheel:
            raise HardwareNotAvailable("filter_wheel")
        with self._lock:
            return self._wheel.get_filter_wheel_position(wheel_index)

    def is_available(self) -> bool:
        return self._wheel is not None
```

**Action:** Create this service in Phase 2.

---

## ServiceRegistry

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/__init__.py`

**Purpose:** Central registry for all services.

**Current Registration (in ApplicationContext):**
```python
self._services.register("camera", CameraService(camera, event_bus))
self._services.register("stage", StageService(stage, event_bus))
self._services.register("peripheral", PeripheralService(mcu, event_bus))
self._services.register("live", LiveService(live_controller, event_bus))
self._services.register("trigger", TriggerService(live_controller, event_bus))
self._services.register("microscope_mode", MicroscopeModeService(live_controller, config_manager, event_bus))
```

**After Refactoring:**
```python
# Keep these
self._services.register("camera", CameraService(camera, event_bus))
self._services.register("stage", StageService(stage, event_bus))
self._services.register("peripheral", PeripheralService(mcu, event_bus))

# Add new
self._services.register("illumination", IlluminationService(light_sources, mcu, event_bus))
self._services.register("filter_wheel", FilterWheelService(filter_wheel, event_bus))

# Remove (merged into controllers)
# - LiveService
# - TriggerService
# - MicroscopeModeService
```

---

## Summary: Service Layer Changes

| Service | Current | Target | Action |
|---------|---------|--------|--------|
| BaseService | Good | Keep | None |
| CameraService | Good | Keep | None |
| StageService | Good | Keep | None |
| PeripheralService | Good | Keep | None |
| LiveService | Thin wrapper | Delete | Merge into LiveController |
| TriggerService | Thin wrapper | Delete | Merge into LiveController |
| MicroscopeModeService | Smart service | Controller | Move to `squid/controllers/` |
| IlluminationService | Incomplete | Fix | Add EventBus integration |
| FluidicsService | Incomplete | Fix | Add EventBus integration |
| FilterWheelService | Missing | Create | New service |
