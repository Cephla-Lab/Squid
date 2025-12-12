# Service vs Controller Architecture

This document defines the distinction between Services and Controllers in the Squid architecture.

## Definitions

### Service

A **Service** is a thread-safe wrapper around hardware with a consistent API.

**Characteristics:**
- Extends `BaseService` from `squid.mcs.services.base`
- Uses `threading.RLock()` for thread safety
- Wraps hardware ABCs (e.g., `AbstractCamera`, `AbstractStage`)
- Validates inputs and clamps to hardware limits
- Publishes state events after changes (e.g., `ExposureTimeChanged`)
- Subscribes to commands (e.g., `SetExposureTimeCommand`)
- Stateless or minimal state (the hardware is the source of truth)
- Does NOT orchestrate multi-step workflows

**Naming:** `<Hardware>Service` (e.g., `CameraService`, `StageService`)

**Example pattern:**
```python
class CameraService(BaseService):
    def __init__(self, camera: AbstractCamera, event_bus: EventBus):
        self._camera = camera
        self._bus = event_bus
        self._lock = threading.RLock()

    def set_exposure_time(self, ms: float) -> None:
        with self._lock:
            clamped = self._clamp_exposure(ms)
            self._camera.set_exposure_time(clamped)
        # Publish outside lock
        self._bus.publish(ExposureTimeChanged(exposure_time_ms=clamped))
```

### Controller

A **Controller** is a state machine that orchestrates multi-step workflows using services.

**Characteristics:**
- Extends `StateMachine[StateEnum]` from `squid.core.state_machine`
- Owns a state machine with defined transitions
- Coordinates multiple services (e.g., camera + stage + illumination)
- Acquires/releases resources via `ResourceCoordinator`
- Subscribes to commands (e.g., `StartLiveCommand`, `StartAcquisitionCommand`)
- Publishes high-level state events (e.g., `LiveStateChanged`, `AcquisitionStateChanged`)
- May spawn worker threads for long-running operations
- Handles errors and cleanup

**Naming:** `<Domain>Controller` (e.g., `LiveController`, `MultiPointController`)

**Example pattern:**
```python
class LiveController(StateMachine[LiveControllerState]):
    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: IlluminationService,
        coordinator: ResourceCoordinator,
        event_bus: EventBus,
    ):
        super().__init__(
            initial_state=LiveControllerState.STOPPED,
            transitions={...},
            event_bus=event_bus,
        )
        self._coordinator = coordinator
        # ...

    def start_live(self) -> None:
        self._require_state(LiveControllerState.STOPPED)
        self.transition_to(LiveControllerState.STARTING)

        # Acquire resources
        lease = self._coordinator.acquire(
            {Resource.CAMERA_CONTROL, Resource.ILLUMINATION_CONTROL},
            owner="LiveController",
        )
        if not lease:
            self.transition_to(LiveControllerState.STOPPED)
            return

        # Start streaming via service
        self._camera_service.start_streaming()
        self.transition_to(LiveControllerState.LIVE)
```

## When to Use Which

| Scenario | Use |
|----------|-----|
| Wrapping a single hardware device | Service |
| Validating inputs and clamping values | Service |
| Publishing state changes from hardware | Service |
| Orchestrating multiple devices together | Controller |
| Managing a state machine with transitions | Controller |
| Starting/stopping live view | Controller |
| Running multi-position acquisitions | Controller |
| Handling start/stop/abort workflows | Controller |
| Resource coordination (camera busy, etc.) | Controller |

## Service Inventory

All services are in `squid.mcs.services`:

| Service | Purpose | Hardware ABC |
|---------|---------|--------------|
| `CameraService` | Camera settings and streaming | `AbstractCamera` |
| `StageService` | XYZ stage movement | `AbstractStage` |
| `IlluminationService` | Light source control | `LightSource` |
| `FilterWheelService` | Filter wheel positions | `AbstractFilterWheelController` |
| `PeripheralService` | DAC/digital I/O | `Microcontroller` |
| `PiezoService` | Piezo Z control | `Piezo` |
| `FluidicsService` | Fluidics device | `FluidicsController` |
| `ObjectiveChangerService` | Objective switching | `ObjectiveChanger` |
| `SpinningDiskService` | Spinning disk confocal | `SpinningDiskController` |
| `NL5Service` | NL5 laser controller | `NL5` |
| `MovementService` | Coordinated stage + piezo moves | (orchestrates StageService + PiezoService) |

**Note:** `MovementService` is unique - it orchestrates other services but is still considered a service because it provides a simple, stateless API without a complex state machine.

## Controller Inventory

Controllers are in `squid.mcs.controllers` and `squid.ops`:

| Controller | Purpose | Location |
|------------|---------|----------|
| `LiveController` | Live view streaming | `squid.mcs.controllers.live_controller` |
| `MicroscopeModeController` | Channel/mode switching | `squid.mcs.controllers.microscope_mode_controller` |
| `PeripheralsController` | Objective, spinning disk, piezo | `squid.mcs.controllers.peripherals_controller` |
| `AutoFocusController` | Software autofocus | `squid.mcs.controllers.autofocus.auto_focus_controller` |
| `LaserAutofocusController` | Hardware laser autofocus | `squid.mcs.controllers.autofocus.laser_auto_focus_controller` |
| `MultiPointController` | Multi-position acquisition | `squid.ops.acquisition.multi_point_controller` |
| `TrackingController` | Object tracking | `squid.ops.tracking.tracking` |
| `DisplacementMeasurementController` | Displacement measurement | `squid.ops.tracking.displacement_measurement` |
| `PlateReadingController` | Plate reading workflows | `squid.ops.acquisition.platereader` |
| `PDAFController` | Phase detection autofocus | `squid.mcs.controllers.autofocus.pdaf` |

## Drivers vs Services vs Controllers

There's sometimes confusion with **Drivers** - implementations of hardware ABCs:

| Layer | Purpose | Examples |
|-------|---------|----------|
| **Driver** | Vendor-specific hardware implementation | `ToupcamCamera`, `CephlaStage`, `Optospin` |
| **Service** | Thread-safe wrapper with events | `CameraService`, `StageService` |
| **Controller** | State machine orchestrating services | `LiveController`, `MultiPointController` |

Drivers implement ABCs like `AbstractCamera`. Services wrap drivers. Controllers use services.

## Thread Safety

- **Services:** All methods are thread-safe via `RLock`. Multiple threads can call a service simultaneously.
- **Controllers:** Command handlers run on the EventBus dispatch thread. State transitions are atomic. Worker threads communicate via events.

## Event Patterns

Services and Controllers both use events but differently:

**Service pattern:**
```
SetExposureTimeCommand → CameraService → ExposureTimeChanged
```

**Controller pattern:**
```
StartLiveCommand → LiveController
  → acquires resources
  → starts camera streaming
  → starts illumination
  → LiveStateChanged(is_live=True)
```

Controllers publish coarse-grained state events; services publish fine-grained hardware events.
