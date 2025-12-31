# Architecture Mapping: master -> arch_v2

Reference for translating file paths from the legacy structure (master) to the new 3-layer architecture (arch_v2).

## Directory Mapping

| Old Location (master) | New Location (arch_v2) |
|-----------------------|------------------------|
| `control/camera.py` | `src/squid/backend/drivers/cameras/` |
| `control/camera_*.py` | `src/squid/backend/drivers/cameras/<vendor>.py` |
| `control/microcontroller.py` | `src/squid/backend/microcontroller.py` |
| `control/core.py` | Split: `backend/controllers/`, `backend/managers/`, `backend/services/` |
| `control/widgets.py` | `src/squid/ui/widgets/<domain>/` |
| `control/gui_hcs.py` | `src/squid/ui/main_window.py` |
| `control/mcs/services/` | `src/squid/backend/services/` |
| `control/mcs/controllers/` | `src/squid/backend/controllers/` |
| `control/mcs/drivers/` | `src/squid/backend/drivers/` |
| `control/ops/acquisition/` | `src/squid/backend/controllers/multipoint/` |
| `control/ops/configuration/` | `src/squid/backend/managers/` |
| `control/ops/processing/` | `src/squid/backend/processing/` |
| `control/ops/tracking/` | `src/squid/backend/processing/` |
| `control/storage/` | `src/squid/backend/io/` |
| `configurations/` | `configurations/` (unchanged) |

## Driver Mapping

| Old Driver | New Location |
|------------|--------------|
| `camera_hamamatsu.py` | `backend/drivers/cameras/hamamatsu.py` |
| `camera_toupcam.py` | `backend/drivers/cameras/toupcam.py` |
| `camera_flir.py` | `backend/drivers/cameras/flir.py` |
| `camera_ids.py` | `backend/drivers/cameras/ids.py` |
| `Xeryon.py` | `backend/drivers/stages/xeryon.py` |
| `celesta.py` | `backend/drivers/lighting/celesta.py` |
| `illumination_andor.py` | `backend/drivers/lighting/andor.py` |
| `filterwheel_*.py` | `backend/drivers/filter_wheels/<vendor>.py` |

## Widget Domain Mapping

The monolithic `widgets.py` was split by domain:

| Widget Type | New Location |
|-------------|--------------|
| Camera controls | `ui/widgets/camera/` |
| Stage controls | `ui/widgets/stage/` |
| Display/visualization | `ui/widgets/display/` |
| Hardware controls (objectives, filters, lasers) | `ui/widgets/hardware/` |
| Acquisition dialogs | `ui/widgets/acquisition/` |
| Wellplate selection | `ui/widgets/wellplate/` |
| Tracking controls | `ui/widgets/tracking/` |

## core.py Decomposition

The large `core.py` (~2000 lines) was split into:

| Functionality | New Location |
|---------------|--------------|
| Live view logic | `backend/controllers/live_controller.py` |
| Multi-point acquisition | `backend/controllers/multipoint/` |
| Autofocus algorithms | `backend/controllers/autofocus/` |
| Channel configuration | `backend/managers/channel_configuration_manager.py` |
| Scan coordinates | `backend/managers/scan_coordinates.py` |
| Objective management | `backend/managers/objective_store.py` |
| Focus map | `backend/managers/focus_map.py` |
| Contrast settings | `backend/managers/contrast_manager.py` |

## Service Layer

New services wrap hardware with thread-safety:

| Service | Purpose |
|---------|---------|
| `CameraService` | Camera operations (exposure, gain, streaming) |
| `StageService` | Stage movement (XY, Z, theta) |
| `IlluminationService` | Light source control |
| `FilterWheelService` | Filter wheel positions |
| `PiezoService` | Piezo Z control |
| `FluidicsService` | Fluidics pump control |
| `PeripheralService` | Misc peripherals |

## Import Path Changes

| Old Import | New Import |
|------------|------------|
| `from control.camera import *` | `from squid.backend.drivers.cameras import *` |
| `from control.widgets import *` | `from squid.ui.widgets.<domain> import *` |
| `from control.core import *` | `from squid.backend.controllers import *` |
| `from control.mcs.services import *` | `from squid.backend.services import *` |

## 3-Layer Architecture

```
     ui (Layer 2)
        |
        v (events only)
    backend (Layer 1)
        |
        v (implements ABCs)
     core (Layer 0)
```

**Layer 0 - Core** (`squid/core/`):
- Hardware ABCs (AbstractCamera, AbstractStage, LightSource)
- EventBus and typed events
- Pydantic config models
- Utilities (ThreadSafeValue, logging)

**Layer 1 - Backend** (`squid/backend/`):
- `drivers/` - Vendor-specific hardware implementations
- `services/` - Thread-safe wrappers with business logic
- `controllers/` - Workflow orchestration (state machines)
- `managers/` - Stateful configuration
- `processing/` - Image processing and tracking algorithms
- `io/` - Frame streaming and file writers

**Layer 2 - UI** (`squid/ui/`):
- Pure PyQt5 widgets
- No business logic
- Communicate only via EventBus (Commands up, State events down)

## Key Rules When Porting

1. **No upward dependencies**: UI never imports from backend directly (uses EventBus)
2. **Services own hardware**: All hardware access goes through services
3. **Controllers orchestrate**: State machines for multi-step workflows
4. **Thread safety**: Services use `RLock()`, release before publishing events
5. **Pure functions**: Extract stateless logic to utility modules
