# Final Architectural Strategy - Squid Microscope Software (Revised)

## 1. Executive Summary

The architectural analysis identified key pain points in the Squid software. After further review, we've adopted a **minimal refactoring approach** that addresses the core problems without introducing unnecessary abstraction layers.

**Guiding Principles:**
- Keep it minimal - simple classes, composition over abstraction layers
- Extract utility functions, not heavyweight services
- **Pure functions for stateless logic** - testable, reusable, explicit
- Software triggering by default - hardware orchestration is optional
- Only add complexity when it solves a real, demonstrated problem

## 2. Current State Findings

- **Circular Dependency:** `Microscope` (HAL layer) references `LiveController` (control layer) - this must be fixed
- **MultiPointController Complexity:** Too many concerns mixed together, hard to follow control flow
- **Pass-through Bloat:** Both `Microscope` and `MultiPointWorker` have many wrapper methods that add no value
- **Inline Logic:** Stateless logic (Z-stack math, progress calc, file paths) mixed with hardware coordination

**What We Found Was NOT a Problem:**
- `ApplicationContext` structure is actually reasonable (DI pattern, centralized init)
- ServiceRegistry is adequate as-is
- Performance bottlenecks are preventative, not urgent
- `job_processing.py` already handles image saving well

## 3. Revised Target Architecture

Rather than a complete 6-layer restructure, we maintain the current 3-layer design (Core/Backend/UI) with targeted fixes:

### Layer Structure (Unchanged)
```
     ui (Layer 2)
        │
        ▼ (events only)
    backend (Layer 1)
        │
        ▼ (implements ABCs)
     core (Layer 0)
```

### Key Changes
1. **Remove circular dependency** - Microscope no longer references LiveController
2. **Add shared utilities** - ChannelModeService, experiment IO functions
3. **Extract pure functions** - Scan planning, Z-stack math, progress, AF decisions
4. **Simplify MultiPointController** - focused methods, use pure functions
5. **Clean up Microscope** - remove pass-through methods
6. **LaserAutofocusController** - extract image processing algorithms
7. **LiveController** - extract timer/FPS and illumination logic
8. **StreamHandler** - extract rate limiting logic

## 4. Separation of Concerns

### After Refactor

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MultiPoint Acquisition System                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  multi_point_utils.py (PURE FUNCTIONS)                                       │
│  ├── generate_file_id(), generate_timepoint_path()     ─ File/Path          │
│  ├── ScanPoint, generate_scan_sequence()               ─ Scan Planning      │
│  ├── calculate_z_positions()                           ─ Z-Stack Math       │
│  ├── calculate_progress(), calculate_eta()             ─ Progress           │
│  └── should_run_autofocus()                            ─ AF Decisions       │
│                                                                               │
│  experiment_io.py (PURE FUNCTIONS)                                           │
│  ├── generate_experiment_id(), create_experiment_folders()                   │
│  ├── write_acquisition_params(), write_configurations_xml()                  │
│  ├── write_coordinates_csv(), create_done_file()                            │
│  └── setup_experiment() - convenience wrapper                                │
│                                                                               │
│  job_processing.py (EXISTING - keep as-is)                                   │
│  ├── SaveImageJob (multi-format image saving)                               │
│  ├── JobRunner (async multiprocessing)                                       │
│  └── OME-TIFF logic                                                          │
│                                                                               │
│  MultiPointController (COORDINATION)                                         │
│  ├── State machine (IDLE/RUNNING/PAUSED/etc.)                               │
│  ├── Worker lifecycle management                                             │
│  └── Event publishing                                                        │
│                                                                               │
│  MultiPointWorker (HARDWARE ORCHESTRATION)                                   │
│  ├── Main acquisition loop                                                   │
│  ├── Hardware calls (stage, camera, illumination)                           │
│  └── Uses pure functions for all logic                                       │
│                                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 5. Refactoring Roadmap (7 Phases)

| Phase | Goal | Risk |
|-------|------|------|
| 1. Break Circular Dep | Remove Microscope → LiveController reference | Low |
| 2. Shared Utilities | ChannelModeService, StageService wait param, experiment IO | Low-Medium |
| 3. MultiPoint Simplification | Extract pure functions, refactor methods, add continuous AF hook | Medium |
| 4. Microscope Cleanup | Remove pass-through methods | Low |
| 5. LaserAF Algorithms | Extract image processing to `laser_af_algorithms.py` | Medium |
| 6. LiveController Utils | Extract timer/FPS/illumination to `live_utils.py` | Low |
| 7. StreamHandler Utils | Extract rate limiting to `stream_utils.py` | Low |

Each phase can be merged independently. Phases 5-7 have no dependencies on each other.

## 6. Deferred Items

The original plan proposed several items that are **explicitly deferred**:

| Item | Reason |
|------|--------|
| Centralized StateStore / reactive state | EventBus + existing managers work fine |
| BufferManager / tiered data plane workers | Profile first if perf becomes an issue |
| Hardware-orchestrated FOV acquisition | Software triggering is easier to debug |
| Factory class hierarchies | ApplicationContext is adequate |
| 6-layer architecture | Current 3-layer is sufficient |

These can be revisited if specific problems arise.

## 7. Key Documents

- **[Refactoring Roadmap](./refactoring_roadmap.md):** Revised execution order and phases
- **[Refactoring Plan](./refactoring_plan.md):** Detailed implementation approach with code examples
- **[Target Architecture Blueprint](./target_architecture_blueprint.md):** Original aspirational design (for reference)
- **[Performance Strategy](./performance_strategy.md):** Data plane optimization (deferred)
- **[State Management Design](./state_management_design.md):** Reactive state architecture (deferred)

## 8. Expected Outcomes

After completing all 7 phases:

- **~125 net lines removed** from the codebase
- **Circular dependency eliminated** - cleaner layer separation
- **Pure functions extracted** - testable, reusable logic for:
  - Scan planning and iteration (multi_point_utils.py)
  - Z-stack position calculation
  - Progress and ETA calculation
  - Autofocus decisions
  - File path generation
  - Laser AF image processing (laser_af_algorithms.py)
  - Timer/FPS calculations (live_utils.py)
  - Frame rate limiting (stream_utils.py)
- **Experiment IO consolidated** - all metadata/folder IO in one place
- **Clearer control flow** in all major controllers
- **Continuous AF ready** - hook in place for future hardware
- **Easier testing** - pure functions are trivially unit-testable

## 9. Conclusion

This revised strategy focuses on delivering practical improvements with minimal disruption. By:
1. Extracting **pure functions** for all stateless logic
2. Consolidating **IO operations** into dedicated modules
3. Deferring heavyweight abstractions (StateStore, BufferManager, Factory hierarchies)

...we avoid adding complexity that may not be needed. The result is a cleaner, more maintainable codebase that remains simple and approachable. The MultiPointController/Worker will still be central coordinators, but their logic will be explicit and testable.
