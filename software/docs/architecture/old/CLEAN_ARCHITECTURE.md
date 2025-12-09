# Clean Architecture Design

This document describes the target architecture for the Squid microscopy control software. The goal is a clean, testable, maintainable system with clear separation of concerns - **without unnecessary boilerplate**.

## Design Philosophy

**Minimal complexity.** Add layers only when they provide value:
- Services exist to normalize hardware differences across implementations
- If hardware has only one implementation, controllers can call the ABC directly
- No ceremony for ceremony's sake

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         GUI Widgets                             │
│              (dumb, reactive, no business logic)                │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                             EventBus
                        (Commands ↓  State ↑)
                                  │
┌─────────────────────────────────▼───────────────────────────────┐
│                          Controllers                            │
│            (own state, handle commands, orchestrate)            │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                          direct method calls
                                  │
┌─────────────────────────────────▼───────────────────────────────┐
│                    Services (optional)                          │
│    (adapter layer for hardware with multiple implementations)   │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                          direct method calls
                                  │
┌─────────────────────────────────▼───────────────────────────────┐
│                     Hardware Abstractions                       │
│        (AbstractCamera, AbstractStage, LightSource, etc.)       │
└─────────────────────────────────────────────────────────────────┘
```

## When to Use Services

| Situation | Use Service? | Why |
|-----------|--------------|-----|
| Multiple hardware vendors (cameras) | **Yes** | Normalize feature differences |
| Hardware with optional features | **Yes** | Provide uniform API with graceful fallbacks |
| Single custom hardware (microcontroller) | **No** | Controller calls ABC/implementation directly |
| Simple, uniform hardware | **No** | No value added over ABC |

**Example: With Service (cameras vary significantly)**
```
CameraController → CameraService → AbstractCamera → TISCamera/HamamatsuCamera
```

**Example: Without Service (single implementation)**
```
PeripheralController → Microcontroller (directly)
```

## Layer Responsibilities

| Layer | Responsibilities | Communicates Via |
|-------|-----------------|------------------|
| **Widget** | Render state, emit user intent | EventBus only |
| **Controller** | Own state, handle commands, orchestrate | EventBus (pub/sub) + direct calls to Services or Hardware |
| **Service** | Normalize hardware differences, validation | Direct method calls only |
| **Hardware ABC** | Define hardware contract | Direct method calls |

## Key Principles

1. **Widgets are dumb** - They only render state and emit user intents as commands
2. **Controllers own state** - Single source of truth for each domain
3. **EventBus is the only GUI↔Controller communication** - No direct calls
4. **Services are optional** - Use only when they add value (hardware normalization)
5. **Controllers can use other controllers** - For complex orchestration
6. **Services never know about EventBus** - They are pure functions over hardware

---

## 1. Services (Optional Adapter Layer)

Services are **optional**. Use them when you need to:
- Normalize differences across hardware implementations
- Handle optional features gracefully
- Add validation/clamping logic

Services do NOT subscribe to or publish events - they are pure functions over hardware.

### When Services Add Value: CameraService Example

Cameras vary significantly across vendors. A service normalizes this:

```python
class CameraService:
    """Adapter for camera hardware differences."""

    def __init__(self, camera: AbstractCamera):
        self._camera = camera

    def set_exposure_time(self, ms: float) -> float:
        """Set exposure, clamping to valid range. Returns actual value."""
        min_exp, max_exp = self._camera.get_exposure_limits()
        clamped = max(min_exp, min(max_exp, ms))
        self._camera.set_exposure_time(clamped)
        return clamped

    def set_temperature(self, deg_c: float) -> bool:
        """Set temperature if supported. Returns False if not available."""
        if not self._camera.supports_temperature:
            return False
        self._camera.set_temperature(deg_c)
        return True

    # Pass-through methods are fine when they don't add value
    def get_exposure_time(self) -> float:
        return self._camera.get_exposure_time()
```

### When to Skip Services: Direct Hardware Access

For single-implementation hardware (like your microcontroller), controllers call directly:

```python
class PeripheralController:
    """Controls DAC, triggers, etc. No service layer needed."""

    def __init__(self, microcontroller: Microcontroller, event_bus: EventBus):
        self._mcu = microcontroller  # Direct reference, no service wrapper
        self._bus = event_bus

    def _on_set_dac(self, cmd: SetDACCommand) -> None:
        self._mcu.set_dac(cmd.channel, cmd.value)  # Direct call
        self._bus.publish(DACValueChanged(channel=cmd.channel, value=cmd.value))
```

---

## 2. Controllers (State + Logic + EventBus)

Controllers own state, subscribe to commands via EventBus, call Services, and publish state changes.

### CameraController

```python
@dataclass
class CameraState:
    exposure_ms: float
    gain: float
    binning: tuple[int, int]
    roi: tuple[int, int, int, int] | None
    pixel_format: str
    is_streaming: bool
    acquisition_mode: AcquisitionMode
    temperature: float | None = None


class CameraController:
    """Owns camera state, handles camera commands."""

    def __init__(self, camera_service: CameraService, event_bus: EventBus):
        self._service = camera_service
        self._bus = event_bus
        self._state = self._read_initial_state()

        # Subscribe to commands
        self._bus.subscribe(SetExposureCommand, self._on_set_exposure)
        self._bus.subscribe(SetGainCommand, self._on_set_gain)
        self._bus.subscribe(SetBinningCommand, self._on_set_binning)
        self._bus.subscribe(SetROICommand, self._on_set_roi)
        self._bus.subscribe(SetPixelFormatCommand, self._on_set_pixel_format)
        self._bus.subscribe(SetAcquisitionModeCommand, self._on_set_acquisition_mode)
        self._bus.subscribe(RequestCameraStateQuery, self._on_request_state)

    def _on_set_exposure(self, cmd: SetExposureCommand) -> None:
        actual = self._service.set_exposure_time(cmd.exposure_ms)
        self._state.exposure_ms = actual
        self._bus.publish(CameraStateChanged(self._state))

    # ... other handlers follow same pattern

    @property
    def state(self) -> CameraState:
        return self._state
```

### StageController

```python
@dataclass
class StageState:
    x_mm: float
    y_mm: float
    z_mm: float
    is_moving: bool
    limits: StageLimits


class StageController:
    """Owns stage state, handles movement commands."""

    def __init__(self, stage_service: StageService, event_bus: EventBus):
        self._service = stage_service
        self._bus = event_bus
        self._state = self._read_initial_state()

        self._bus.subscribe(MoveStageRelativeCommand, self._on_move_relative)
        self._bus.subscribe(MoveStageToCommand, self._on_move_to)
        self._bus.subscribe(HomeStageCommand, self._on_home)
        self._bus.subscribe(ZeroStageCommand, self._on_zero)
        self._bus.subscribe(RequestStageStateQuery, self._on_request_state)

    def _on_move_relative(self, cmd: MoveStageRelativeCommand) -> None:
        self._state.is_moving = True
        self._bus.publish(StageStateChanged(self._state))

        self._service.move_relative(cmd.dx, cmd.dy, cmd.dz, blocking=True)

        x, y, z = self._service.get_position()
        self._state.x_mm, self._state.y_mm, self._state.z_mm = x, y, z
        self._state.is_moving = False
        self._bus.publish(StageStateChanged(self._state))
```

### LiveController

```python
@dataclass
class LiveState:
    is_live: bool
    trigger_mode: TriggerMode  # SOFTWARE, HARDWARE, CONTINUOUS
    fps: float
    current_configuration: ChannelConfiguration | None
    illumination_on: bool


class LiveController:
    """Owns live streaming state, orchestrates camera + illumination."""

    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: IlluminationService,
        peripheral_service: PeripheralService,
        event_bus: EventBus,
    ):
        self._camera = camera_service
        self._illumination = illumination_service
        self._peripheral = peripheral_service
        self._bus = event_bus
        self._state = LiveState(...)

        self._bus.subscribe(StartLiveCommand, self._on_start_live)
        self._bus.subscribe(StopLiveCommand, self._on_stop_live)
        self._bus.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self._bus.subscribe(SetTriggerFPSCommand, self._on_set_fps)
        self._bus.subscribe(SetMicroscopeModeCommand, self._on_set_mode)
        self._bus.subscribe(RequestLiveStateQuery, self._on_request_state)

    def _on_start_live(self, cmd: StartLiveCommand) -> None:
        if cmd.configuration:
            self._apply_configuration(cmd.configuration)

        self._camera.start_streaming()
        self._turn_on_illumination()
        self._start_triggering()

        self._state.is_live = True
        self._bus.publish(LiveStateChanged(self._state))

    def _on_stop_live(self, cmd: StopLiveCommand) -> None:
        self._stop_triggering()
        self._turn_off_illumination()
        self._camera.stop_streaming()

        self._state.is_live = False
        self._bus.publish(LiveStateChanged(self._state))
```

### AutofocusController

```python
@dataclass
class AutofocusState:
    is_running: bool
    n_planes: int
    delta_z_um: float
    focus_map: FocusMap | None
    use_focus_map: bool
    progress: float  # 0.0 to 1.0


class AutofocusController:
    """Owns autofocus state, runs focus algorithms."""

    def __init__(
        self,
        camera_service: CameraService,
        stage_service: StageService,
        live_controller: LiveController,  # needs to stop/start live
        event_bus: EventBus,
    ):
        self._camera = camera_service
        self._stage = stage_service
        self._live = live_controller
        self._bus = event_bus
        self._state = AutofocusState(...)

        self._bus.subscribe(StartAutofocusCommand, self._on_start_autofocus)
        self._bus.subscribe(StopAutofocusCommand, self._on_stop)
        self._bus.subscribe(SetAutofocusParamsCommand, self._on_set_params)
        self._bus.subscribe(AddFocusMapPointCommand, self._on_add_focus_point)
        self._bus.subscribe(ClearFocusMapCommand, self._on_clear_focus_map)

    def _on_start_autofocus(self, cmd: StartAutofocusCommand) -> None:
        self._state.is_running = True
        self._bus.publish(AutofocusStateChanged(self._state))

        # Stop live if running
        if self._live.state.is_live:
            self._bus.publish(StopLiveCommand())

        # Run autofocus in thread
        self._thread = Thread(target=self._run_autofocus)
        self._thread.start()

    def _run_autofocus(self) -> None:
        # ... autofocus algorithm using self._camera and self._stage
        self._state.is_running = False
        self._bus.publish(AutofocusStateChanged(self._state))
        self._bus.publish(AutofocusCompleted(z_mm=best_z))
```

### AcquisitionController

```python
@dataclass
class AcquisitionConfig:
    grid: GridConfig  # NX, NY, deltaX, deltaY
    z_stack: ZStackConfig | None  # NZ, deltaZ
    time_series: TimeSeriesConfig | None  # Nt, deltaT
    channels: list[ChannelConfiguration]
    use_autofocus: bool
    use_focus_map: bool
    save_path: Path
    experiment_id: str


@dataclass
class AcquisitionState:
    is_running: bool
    is_paused: bool
    config: AcquisitionConfig | None
    current_position: int
    total_positions: int
    current_timepoint: int
    current_channel: str
    progress: float
    estimated_time_remaining_s: float


class AcquisitionController:
    """Orchestrates multi-point acquisition across all hardware."""

    def __init__(
        self,
        camera_service: CameraService,
        stage_service: StageService,
        illumination_service: IlluminationService,
        piezo_service: PiezoService | None,
        autofocus_controller: AutofocusController,
        live_controller: LiveController,
        event_bus: EventBus,
    ):
        self._camera = camera_service
        self._stage = stage_service
        self._illumination = illumination_service
        self._piezo = piezo_service
        self._autofocus = autofocus_controller
        self._live = live_controller
        self._bus = event_bus
        self._state = AcquisitionState(...)

        self._bus.subscribe(StartAcquisitionCommand, self._on_start)
        self._bus.subscribe(StopAcquisitionCommand, self._on_stop)
        self._bus.subscribe(PauseAcquisitionCommand, self._on_pause)
        self._bus.subscribe(ResumeAcquisitionCommand, self._on_resume)
        self._bus.subscribe(SetAcquisitionConfigCommand, self._on_set_config)

    def _on_start(self, cmd: StartAcquisitionCommand) -> None:
        self._state.config = cmd.config
        self._state.is_running = True
        self._bus.publish(AcquisitionStateChanged(self._state))

        self._thread = Thread(target=self._run_acquisition)
        self._thread.start()

    def _run_acquisition(self) -> None:
        positions = self._generate_positions()

        for t in range(self._state.config.time_series.nt if self._state.config.time_series else 1):
            for i, pos in enumerate(positions):
                if not self._state.is_running:
                    break

                while self._state.is_paused:
                    time.sleep(0.1)

                self._stage.move_to(pos.x, pos.y, pos.z)

                if self._state.config.use_autofocus:
                    self._run_autofocus_at_position()

                for channel in self._state.config.channels:
                    self._acquire_channel(pos, channel)

                self._update_progress(i, t)

        self._state.is_running = False
        self._bus.publish(AcquisitionStateChanged(self._state))
        self._bus.publish(AcquisitionCompleted())
```

### LaserAutofocusController

```python
@dataclass
class LaserAFState:
    is_initialized: bool
    is_tracking: bool
    displacement_um: float
    reference_set: bool
    cross_correlation: float


class LaserAutofocusController:
    """Reflection-based autofocus using laser spot tracking."""

    def __init__(
        self,
        focus_camera_service: CameraService,  # separate focus camera
        stage_service: StageService,
        piezo_service: PiezoService | None,
        peripheral_service: PeripheralService,
        event_bus: EventBus,
    ):
        # ...
        self._bus.subscribe(InitializeLaserAFCommand, self._on_initialize)
        self._bus.subscribe(SetLaserAFReferenceCommand, self._on_set_reference)
        self._bus.subscribe(MeasureDisplacementCommand, self._on_measure)
        self._bus.subscribe(MoveToTargetDisplacementCommand, self._on_move_to_target)
```

### TrackingController

```python
@dataclass
class TrackingState:
    is_tracking: bool
    tracker_type: str
    interval_s: float
    stage_tracking_enabled: bool
    autofocus_enabled: bool
    save_images: bool


class TrackingController:
    """Real-time object tracking with stage following."""

    def __init__(
        self,
        camera_service: CameraService,
        stage_service: StageService,
        autofocus_controller: AutofocusController,
        live_controller: LiveController,
        event_bus: EventBus,
    ):
        # ...
        self._bus.subscribe(StartTrackingCommand, self._on_start)
        self._bus.subscribe(StopTrackingCommand, self._on_stop)
        self._bus.subscribe(SetTrackingParamsCommand, self._on_set_params)
```

---

## 3. Events

### Commands (GUI → Controller)

```python
# Camera Commands
@dataclass
class SetExposureCommand(Event):
    exposure_ms: float

@dataclass
class SetGainCommand(Event):
    gain: float

@dataclass
class SetBinningCommand(Event):
    x: int
    y: int

@dataclass
class SetROICommand(Event):
    x: int
    y: int
    width: int
    height: int

@dataclass
class SetPixelFormatCommand(Event):
    pixel_format: str

@dataclass
class SetAcquisitionModeCommand(Event):
    mode: AcquisitionMode


# Stage Commands
@dataclass
class MoveStageRelativeCommand(Event):
    dx: float = 0
    dy: float = 0
    dz: float = 0

@dataclass
class MoveStageToCommand(Event):
    x: float
    y: float
    z: float | None = None

@dataclass
class HomeStageCommand(Event):
    x: bool = False
    y: bool = False
    z: bool = False

@dataclass
class ZeroStageCommand(Event):
    x: bool = False
    y: bool = False
    z: bool = False


# Live Commands
@dataclass
class StartLiveCommand(Event):
    configuration: ChannelConfiguration | None = None

@dataclass
class StopLiveCommand(Event):
    pass

@dataclass
class SetTriggerModeCommand(Event):
    mode: TriggerMode

@dataclass
class SetTriggerFPSCommand(Event):
    fps: float

@dataclass
class SetMicroscopeModeCommand(Event):
    configuration: ChannelConfiguration


# Autofocus Commands
@dataclass
class StartAutofocusCommand(Event):
    use_focus_map: bool = True

@dataclass
class StopAutofocusCommand(Event):
    pass

@dataclass
class SetAutofocusParamsCommand(Event):
    n_planes: int | None = None
    delta_z_um: float | None = None

@dataclass
class AddFocusMapPointCommand(Event):
    x: float
    y: float
    z: float

@dataclass
class ClearFocusMapCommand(Event):
    pass


# Acquisition Commands
@dataclass
class StartAcquisitionCommand(Event):
    config: AcquisitionConfig

@dataclass
class StopAcquisitionCommand(Event):
    pass

@dataclass
class PauseAcquisitionCommand(Event):
    pass

@dataclass
class ResumeAcquisitionCommand(Event):
    pass


# Peripheral Commands
@dataclass
class SetDACCommand(Event):
    channel: int
    value_percent: float

@dataclass
class TurnOnAFLaserCommand(Event):
    pass

@dataclass
class TurnOffAFLaserCommand(Event):
    pass


# Query Commands (request current state)
@dataclass
class RequestCameraStateQuery(Event):
    pass

@dataclass
class RequestStageStateQuery(Event):
    pass

@dataclass
class RequestLiveStateQuery(Event):
    pass
```

### State Events (Controller → GUI)

```python
@dataclass
class CameraStateChanged(Event):
    state: CameraState

@dataclass
class StageStateChanged(Event):
    state: StageState

@dataclass
class LiveStateChanged(Event):
    state: LiveState

@dataclass
class AutofocusStateChanged(Event):
    state: AutofocusState

@dataclass
class AutofocusCompleted(Event):
    z_mm: float
    success: bool = True

@dataclass
class AcquisitionStateChanged(Event):
    state: AcquisitionState

@dataclass
class AcquisitionCompleted(Event):
    success: bool = True
    error: str | None = None

@dataclass
class LaserAFStateChanged(Event):
    state: LaserAFState

@dataclass
class TrackingStateChanged(Event):
    state: TrackingState

@dataclass
class DACValueChanged(Event):
    channel: int
    value_percent: float

# For image display (lightweight - just ID, not data)
@dataclass
class NewFrameAvailable(Event):
    frame_id: int
    channel: str | None = None
```

---

## 4. Widget Pattern

All widgets follow this pattern - they are purely reactive and contain no business logic:

```python
class CameraSettingsWidget(QWidget):
    """Displays and controls camera settings. No business logic."""

    def __init__(self, event_bus: EventBus):
        super().__init__()
        self._bus = event_bus

        # Build UI
        self._build_ui()

        # Subscribe to state changes
        self._bus.subscribe(CameraStateChanged, self._on_state_changed)

        # Request initial state
        self._bus.publish(RequestCameraStateQuery())

    def _build_ui(self) -> None:
        self._exposure_spinbox = QDoubleSpinBox()
        self._exposure_spinbox.valueChanged.connect(self._on_exposure_input)

        self._gain_spinbox = QDoubleSpinBox()
        self._gain_spinbox.valueChanged.connect(self._on_gain_input)
        # ... etc

    # User input → publish command
    def _on_exposure_input(self, value: float) -> None:
        self._bus.publish(SetExposureCommand(exposure_ms=value))

    def _on_gain_input(self, value: float) -> None:
        self._bus.publish(SetGainCommand(gain=value))

    # State change → update UI
    def _on_state_changed(self, event: CameraStateChanged) -> None:
        state = event.state

        # Block signals to prevent feedback loops
        self._exposure_spinbox.blockSignals(True)
        self._exposure_spinbox.setValue(state.exposure_ms)
        self._exposure_spinbox.blockSignals(False)

        self._gain_spinbox.blockSignals(True)
        self._gain_spinbox.setValue(state.gain)
        self._gain_spinbox.blockSignals(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Clean up subscriptions
        self._bus.unsubscribe(CameraStateChanged, self._on_state_changed)
        super().closeEvent(event)
```

---

## 5. Controller Dependency Graph

```
                    ┌─────────────────────┐
                    │ AcquisitionController│
                    └──────────┬──────────┘
                               │ uses
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ LiveController  │  │AutofocusController│ │TrackingController│
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                    │                    │
         │ uses               │ uses               │ uses
         ▼                    ▼                    ▼
    ┌─────────┐          ┌─────────┐          ┌─────────┐
    │Services │          │Services │          │Services │
    └─────────┘          └─────────┘          └─────────┘
```

**Key rules:**
- Controllers can depend on other controllers (e.g., AcquisitionController uses LiveController)
- Controllers call Services directly (not through EventBus)
- Controllers communicate with GUI only via EventBus
- Services never know about Controllers or EventBus

---

## 6. File Organization

```
squid/
├── events.py                    # All event definitions
├── services/
│   ├── __init__.py
│   ├── camera_service.py        # Stateless camera API
│   ├── stage_service.py         # Stateless stage API
│   ├── illumination_service.py  # Stateless illumination API
│   ├── peripheral_service.py    # Stateless DAC/trigger API
│   ├── filter_wheel_service.py  # Stateless filter wheel API
│   └── piezo_service.py         # Stateless piezo API
├── controllers/
│   ├── __init__.py
│   ├── camera_controller.py     # Camera state + commands
│   ├── stage_controller.py      # Stage state + commands
│   ├── live_controller.py       # Live streaming orchestration
│   ├── autofocus_controller.py  # Autofocus algorithms
│   ├── acquisition_controller.py # Multi-point acquisition
│   ├── laser_af_controller.py   # Reflection autofocus
│   └── tracking_controller.py   # Object tracking
├── state/
│   ├── __init__.py
│   ├── camera_state.py          # CameraState dataclass
│   ├── stage_state.py           # StageState dataclass
│   ├── live_state.py            # LiveState dataclass
│   ├── autofocus_state.py       # AutofocusState dataclass
│   └── acquisition_state.py     # AcquisitionState dataclass
└── abc.py                       # Hardware abstractions (existing)

control/
├── widgets/                     # All widgets (dumb, reactive)
│   ├── camera/
│   ├── stage/
│   ├── acquisition/
│   ├── display/
│   └── ...
└── gui/                         # Main GUI assembly
```

---

## 7. What This Architecture Eliminates

- **GUI calling hardware directly** - All hardware access goes through Services
- **GUI calling controllers directly** - All communication via EventBus
- **Services on EventBus** - Services are pure, stateless APIs
- **Confusion about where state lives** - Controllers own all state
- **Callback spaghetti** - Clean pub/sub pattern
- **Circular dependencies** - Clear layered architecture
- **Difficult testing** - Each layer is independently testable

---

## 8. Testing Strategy

### Service Tests
```python
def test_camera_service_clamps_exposure():
    mock_camera = Mock(spec=AbstractCamera)
    mock_camera.get_exposure_limits.return_value = (1.0, 1000.0)

    service = CameraService(mock_camera)
    actual = service.set_exposure_time(5000.0)  # Over limit

    assert actual == 1000.0
    mock_camera.set_exposure_time.assert_called_with(1000.0)
```

### Controller Tests
```python
def test_camera_controller_publishes_state_on_exposure_change():
    mock_service = Mock(spec=CameraService)
    mock_service.set_exposure_time.return_value = 50.0
    bus = EventBus()

    controller = CameraController(mock_service, bus)

    received = []
    bus.subscribe(CameraStateChanged, lambda e: received.append(e))

    bus.publish(SetExposureCommand(exposure_ms=50.0))

    assert len(received) == 1
    assert received[0].state.exposure_ms == 50.0
```

### Widget Tests
```python
def test_widget_updates_on_state_change():
    bus = EventBus()
    widget = CameraSettingsWidget(bus)

    state = CameraState(exposure_ms=100.0, gain=1.0, ...)
    bus.publish(CameraStateChanged(state))

    assert widget._exposure_spinbox.value() == 100.0
```

---

## 9. Migration Path

1. **Phase 1**: Create new Services (stateless, no EventBus)
2. **Phase 2**: Create new Controllers with proper state management
3. **Phase 3**: Update Events to match new architecture
4. **Phase 4**: Refactor widgets to use EventBus only
5. **Phase 5**: Remove old controller/service code
6. **Phase 6**: Add comprehensive tests

See `REFACTORING_PLAN.md` for detailed migration steps.
