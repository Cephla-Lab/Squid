# Application.py Decomposition Plan

## Goal
Extract the 970-line `ApplicationContext` class into focused modules for better maintainability and testability. **No changes to `_def.py`** - this is a pure extraction refactor.

## Scope
- **In scope**: `application.py` decomposition only
- **Out of scope**: `_def.py`, widget system, INI format changes

---

## New Files

| File | Lines | Purpose |
|------|-------|---------|
| `squid/backend/services/service_builder.py` | ~120 | `build_services()` function |
| `squid/backend/controllers/controller_builder.py` | ~300 | `build_controllers()` + helper functions |
| `squid/backend/initialization.py` | ~150 | `initialize_hardware()`, `shutdown_hardware()` |

---

## Step 1: Create `service_builder.py`

**File:** `software/src/squid/backend/services/service_builder.py`

Extract `_build_services()` method (~120 lines) into a standalone function:

```python
def build_services(
    microscope: "Microscope",
    event_bus: EventBus,
    mode_gate: Optional["GlobalModeGate"] = None,
) -> ServiceRegistry:
    """Build all services for the application."""
    registry = ServiceRegistry(event_bus)

    # Core services (camera, stage, peripheral)
    _register_core_services(registry, microscope, event_bus, mode_gate)

    # Optional services (illumination, filter wheel, piezo, fluidics, etc.)
    _register_optional_services(registry, microscope, event_bus, mode_gate)

    # Movement service for position polling
    _register_movement_service(registry, microscope, event_bus)

    return registry
```

**What moves:**
- Lines 626-743 from `application.py`
- All service registration logic
- The `try/import _def` pattern stays (we're not changing that)

---

## Step 2: Create `controller_builder.py`

**File:** `software/src/squid/backend/controllers/controller_builder.py`

Extract controller creation methods:

```python
def build_controllers(
    microscope: "Microscope",
    services: "ServiceRegistry",
    event_bus: EventBus,
    mode_gate: Optional["GlobalModeGate"] = None,
) -> Controllers:
    """Build all controllers for the application."""
    # Core controllers
    live, stream_handler = _build_live_controller(microscope, services, event_bus, mode_gate)
    live_focus, stream_handler_focus = _build_focus_controllers(microscope, services, event_bus, mode_gate)

    # Mode controllers
    microscope_mode = _build_microscope_mode_controller(microscope, services, event_bus)
    peripherals = _build_peripherals_controller(microscope, services, event_bus)

    # Optional controllers (feature-gated via _def)
    autofocus = _build_autofocus_controller(microscope, services, event_bus, mode_gate)
    laser_autofocus = _build_laser_autofocus_controller(microscope, services, event_bus)
    # ... etc

    return Controllers(live=live, stream_handler=stream_handler, ...)
```

**What moves:**
- `Controllers` dataclass (lines 41-63)
- `_create_controllers_externally()` (lines 276-374)
- `_build_tracking_controller()` (lines 388-416)
- `_build_autofocus_controller()` (lines 480-502)
- `_build_laser_autofocus_controller()` (lines 504-531)
- `_build_multipoint_controller()` (lines 533-571)
- `_build_image_click_controller()` (lines 589-618)
- `_build_scan_coordinates()` (lines 469-478)
- `_create_microscope_mode_controller()` (lines 418-440)
- `_create_peripherals_controller()` (lines 452-467)

---

## Step 3: Create `initialization.py`

**File:** `software/src/squid/backend/initialization.py`

Extract hardware initialization and shutdown:

```python
def initialize_hardware(
    microscope: "Microscope",
    services: "ServiceRegistry",
    stream_handler,
    stream_handler_focus=None,
) -> tuple[Optional[str], Optional[str]]:
    """One-time hardware initialization. Returns callback IDs."""
    _initialize_stage(services)
    camera_id = _wire_camera_callbacks(services, stream_handler)
    focus_id = _wire_focus_camera_callbacks(services, stream_handler_focus)
    _home_objective_changer(services)
    return camera_id, focus_id

def shutdown_hardware(microscope: "Microscope", services: "ServiceRegistry") -> None:
    """Best-effort hardware shutdown. Must not raise."""
    _cache_stage_position(services)
    _retract_z(services)
    _reset_filter_wheel(services)
    _stop_cameras(services)
    _reset_peripherals(microscope, services)
```

**What moves:**
- `_initialize_hardware()` (lines 132-249)
- `_shutdown_hardware()` (lines 879-969)

---

## Step 4: Refactor `application.py`

After extraction, `ApplicationContext` becomes ~200 lines:

```python
from squid.backend.services.service_builder import build_services
from squid.backend.controllers.controller_builder import build_controllers, Controllers
from squid.backend.initialization import initialize_hardware, shutdown_hardware

class ApplicationContext:
    def __init__(self, simulation: bool = False):
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._simulation = simulation
        # ... instance variables

        event_bus.start()
        self._mode_gate = GlobalModeGate(event_bus)
        self._build_components()
        event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

    def _build_components(self) -> None:
        # Microscope
        self._microscope = Microscope.build_from_global_config(
            simulated=self._simulation, skip_controller_creation=True
        )
        if self._microscope.objective_store:
            self._microscope.objective_store._event_bus = event_bus

        # Services (delegated to builder)
        self._services = build_services(self._microscope, event_bus, self._mode_gate)

        # Controllers (delegated to builder)
        self._controllers = build_controllers(
            self._microscope, self._services, event_bus, self._mode_gate
        )

        # Hardware init (delegated to initialization module)
        self._camera_callback_id, self._camera_focus_callback_id = initialize_hardware(
            self._microscope, self._services,
            self._controllers.stream_handler,
            self._controllers.stream_handler_focus,
        )

    def shutdown(self) -> None:
        # ... GUI cleanup
        if self._controllers:
            if self._controllers.live:
                self._controllers.live.stop_live()

        # Hardware shutdown (delegated)
        if self._microscope and self._services:
            shutdown_hardware(self._microscope, self._services)

        # ... service/microscope cleanup
        event_bus.stop()
        event_bus.clear()

    # Properties stay in ApplicationContext
    @property
    def microscope(self) -> Microscope: ...
    @property
    def controllers(self) -> Controllers: ...
    @property
    def services(self) -> ServiceRegistry: ...
    # ... etc
```

---

## Files Modified

| File | Change |
|------|--------|
| `software/src/squid/application.py` | Reduce from 970 → ~200 lines |
| `software/src/squid/backend/services/service_builder.py` | **NEW** - ~120 lines |
| `software/src/squid/backend/controllers/controller_builder.py` | **NEW** - ~300 lines |
| `software/src/squid/backend/initialization.py` | **NEW** - ~150 lines |
| `software/src/squid/backend/services/__init__.py` | Add `service_builder` export |
| `software/src/squid/backend/controllers/__init__.py` | Add `controller_builder` export |

---

## Testing

1. **Run existing tests** - No behavior changes, tests should pass
2. **Add unit tests for builders** - Test `build_services()`, `build_controllers()` with mocked dependencies
3. **Integration test** - Verify full `ApplicationContext` still works in simulation mode

---

## Benefits

1. **Easier to understand** - Each module has one clear purpose
2. **Easier to test** - Can test builders in isolation
3. **Easier to modify** - Adding a new service? Edit `service_builder.py`
4. **No risk to _def.py** - Config system stays untouched
5. **~1-2 days effort** - Pure extraction, no logic changes
