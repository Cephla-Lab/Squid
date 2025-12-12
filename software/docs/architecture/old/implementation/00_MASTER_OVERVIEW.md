# Squid Architecture Refactoring - Master Implementation Guide

This is the central hub for the Squid microscopy software architecture refactoring. The goal is to achieve clean separation of concerns with ~90% code reuse.

---

## Quick Reference

| Item | Value |
|------|-------|
| **Repository Root** | `/Users/wea/src/allenlab/Squid/software` |
| **Working Branch** | `arch_v2` |
| **Architecture Doc** | `docs/architecture/REVISED_ARCHITECTURE_V3.md` |
| **Run Tests** | `NUMBA_DISABLE_JIT=1 pytest tests/ -v` |
| **Run GUI (Simulation)** | `python main_hcs.py --simulation` |

---

## Project Summary

**Problem:** The current architecture has three main tangles:
1. Services vs Controllers overlap - unclear who owns domain logic
2. Live path split three ways - LiveService, LiveController, and StreamHandler share responsibility
3. Acquisition tightly coupled to hardware - MultiPointWorker bypasses services

**Solution:** Clarify responsibilities:
- **Services** = thread-safe hardware access only (CameraService, StageService, etc.)
- **Controllers** = orchestration and state (LiveController, MicroscopeModeController, etc.)
- **EventBus** = control plane (commands, state changes)
- **StreamHandler** = data plane (camera frames)
- **Widgets** = render state, emit commands (no business logic)

**Result:** ~90% code reuse with clear boundaries.

---

## Phase Overview

| Phase | Title | Prerequisites | Status |
|-------|-------|---------------|--------|
| 1 | [Establish Boundaries](./PHASE_1_ESTABLISH_BOUNDARIES.md) | None | [ ] Not Started |
| 2 | [Create Infrastructure](./PHASE_2_CREATE_INFRASTRUCTURE.md) | Phase 1 | [ ] Not Started |
| 3 | [Service-Controller Merge](./PHASE_3_SERVICE_CONTROLLER_MERGE.md) | Phase 2 | [ ] Not Started |
| 4 | [Acquisition Service Usage](./PHASE_4_ACQUISITION_SERVICE_USAGE.md) | Phase 3 | [ ] Not Started |
| 5 | [Widget Updates](./PHASE_5_WIDGET_UPDATES.md) | Phase 3 | [ ] Not Started |
| 6 | [Cleanup](./PHASE_6_CLEANUP.md) | Phase 4, 5 | [ ] Not Started |

---

## Supporting Documents

### Foundation (Read First)
- [01_CODING_STANDARDS.md](./01_CODING_STANDARDS.md) - Patterns, conventions, DRY/YAGNI/TDD
- [02_TESTING_GUIDE.md](./02_TESTING_GUIDE.md) - TDD workflow, test commands, mock patterns

### Inventory (Phase 1 Deliverables)
- [inventory/SERVICE_INVENTORY.md](./inventory/SERVICE_INVENTORY.md) - Current service layer documentation
- [inventory/CONTROLLER_INVENTORY.md](./inventory/CONTROLLER_INVENTORY.md) - Current controller layer documentation
- [inventory/HARDWARE_ACCESS_MAP.md](./inventory/HARDWARE_ACCESS_MAP.md) - Direct hardware access to fix

---

## Architecture Diagrams

### Before: Current State

```
┌─────────────────────────────────────────────────────────────────┐
│                         GUI Widgets                             │
│     (some use events, some call services directly, some         │
│      call controllers, some call hardware)                      │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                    mixed paths (messy)
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
         EventBus      Services      Controllers
          (good)      (thin wrappers)  (do everything)
              │             │             │
              └─────────────┼─────────────┘
                            │
                    direct calls (bypasses services)
                            │
                            ▼
                    Hardware Drivers
```

### After: Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         GUI Widgets                             │
│              (render state, emit commands, no logic)            │
│                       control/widgets/                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
          EventBus                   StreamHandler
     (commands ↓ state ↑)          (frames → display)
       squid/events.py         control/core/display/
              │                           │
              │                           │
┌─────────────▼───────────────────────────┴───────────────────────┐
│                         Controllers                             │
│           (orchestration, state machines, workflows)            │
│                                                                 │
│   LiveController          MultiPointController (Acquisition)   │
│   AutoFocusController     MicroscopeModeController             │
│   TrackingController      PeripheralsController                │
│   LaserAFController                                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                      direct method calls
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Hardware Services                          │
│            (thread-safe device access, validation)              │
│                                                                 │
│   CameraService       StageService       IlluminationService   │
│   PeripheralService   FilterWheelService                       │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                      direct method calls
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                   Hardware Abstractions                         │
│        (AbstractCamera, AbstractStage, LightSource, etc.)       │
│                         squid/abc.py                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Hardware Drivers                           │
│              (vendor-specific implementations)                  │
│                    control/peripherals/                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Files by Phase

### Phase 2: New Infrastructure
| File | Changes |
|------|---------|
| `squid/events.py` | Add ~20 new event types |
| `squid/abc.py` | Add 3-4 new protocols |
| `squid/controllers/__init__.py` | Create new directory |

### Phase 3: Service-Controller Merge
| File | Changes |
|------|---------|
| `control/core/display/live_controller.py` | Add EventBus, absorb LiveService |
| `squid/controllers/microscope_mode_controller.py` | Create from MicroscopeModeService |
| `squid/controllers/peripherals_controller.py` | Create new |
| `squid/application.py` | Wire new controllers |

### Phase 4: Acquisition Service Usage
| File | Changes |
|------|---------|
| `control/core/acquisition/multi_point_worker.py` | Replace direct hardware calls |
| `control/core/acquisition/multi_point_controller.py` | Pass services to worker |

### Phase 5: Widget Updates
| File | Changes |
|------|---------|
| `control/widgets/stage/navigation.py` | Use EventBus only |
| `control/widgets/camera/settings.py` | Use EventBus only |
| `control/widgets/camera/live_control.py` | Use EventBus only |
| `control/widgets/hardware/*.py` | Use EventBus only |

### Phase 6: Cleanup
| File | Changes |
|------|---------|
| `squid/services/live_service.py` | Delete |
| `squid/services/trigger_service.py` | Delete |
| `squid/services/microscope_mode_service.py` | Delete |

---

## Commit Message Convention

Use conventional commits format:

```
type(scope): description

[optional body]

[optional footer]
```

**Types:**
- `feat` - New feature or capability
- `fix` - Bug fix
- `refactor` - Code restructuring (no behavior change)
- `test` - Adding or updating tests
- `docs` - Documentation only
- `chore` - Maintenance tasks

**Scopes:**
- `events` - Event definitions
- `abc` - Hardware abstractions
- `services` - Service layer
- `controllers` - Controller layer
- `widgets` - GUI widgets
- `acquisition` - Acquisition system
- `live` - Live view system
- `app` - ApplicationContext

**Examples:**
```
feat(events): Add peripheral command and state events
refactor(live): Merge LiveService into LiveController
test(controllers): Add MicroscopeModeController unit tests
docs(inventory): Add service responsibility inventory
chore: Remove deprecated LiveService
```

---

## Progress Tracking

Update this section as you complete phases:

```
[ ] Phase 1: Establish Boundaries
    [ ] SERVICE_INVENTORY.md complete
    [ ] CONTROLLER_INVENTORY.md complete
    [ ] HARDWARE_ACCESS_MAP.md complete

[ ] Phase 2: Create Infrastructure
    [ ] New events added to squid/events.py
    [ ] New protocols added to squid/abc.py
    [ ] squid/controllers/ directory created

[ ] Phase 3: Service-Controller Merge
    [ ] LiveController handles StartLiveCommand/StopLiveCommand
    [ ] MicroscopeModeController created
    [ ] PeripheralsController created
    [ ] ApplicationContext updated

[ ] Phase 4: Acquisition Service Usage
    [ ] MultiPointWorker uses CameraService
    [ ] MultiPointWorker uses StageService
    [ ] MultiPointWorker uses IlluminationService

[ ] Phase 5: Widget Updates
    [ ] Stage widgets use EventBus only
    [ ] Camera widgets use EventBus only
    [ ] Hardware widgets use EventBus only

[ ] Phase 6: Cleanup
    [ ] Deprecated services removed
    [ ] All tests passing
    [ ] Documentation updated
```

---

## Getting Started

1. **Read the foundation documents:**
   - [01_CODING_STANDARDS.md](./01_CODING_STANDARDS.md)
   - [02_TESTING_GUIDE.md](./02_TESTING_GUIDE.md)

2. **Understand the target architecture:**
   - Read `docs/architecture/REVISED_ARCHITECTURE_V3.md`

3. **Start with Phase 1:**
   - [PHASE_1_ESTABLISH_BOUNDARIES.md](./PHASE_1_ESTABLISH_BOUNDARIES.md)

4. **Work through phases in order:**
   - Each phase builds on the previous
   - Don't skip phases
   - Run tests after each subtask
   - Commit frequently
