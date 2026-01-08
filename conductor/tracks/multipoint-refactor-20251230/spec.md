# Multipoint Controller Refactoring

## Status: Planning Complete

## Problem
The acquisition controllers have become God objects:

| File | Lines | Issues |
|------|-------|--------|
| `multi_point_controller.py` | 1,264 | 295-line `run_acquisition()`, 7+ responsibilities |
| `multi_point_worker.py` | 1,690 | 21 thin wrappers, duplicated patterns |
| `live_controller.py` | 655 | Duplicated illumination/channel logic |

**Core Issues:**
- Duplicated illumination/channel logic between live and multipoint
- Z-movement + stabilization pattern repeated 4x
- 21 thin service wrapper methods adding no value
- Mixed abstraction levels (hardware waits mixed with business logic)

## Solution
Create 7 focused components across service and controller layers:

### Service Layer (shared)
1. **`AcquisitionService`** - Hardware orchestration primitives (apply config, trigger, illumination)

### Controller Layer (multipoint-specific)
2. **`ExperimentManager`** - Folder creation, metadata, logging
3. **`AcquisitionPlanner`** - Estimation logic (disk, RAM, image count)
4. **`PositionController` + `ZStackExecutor`** - Stage movement, z-stack sequences
5. **`FocusMapGenerator` + `AutofocusExecutor`** - Focus map, autofocus
6. **`ProgressTracker` + `CoordinateTracker`** - Events, coordinates
7. **`ImageCaptureExecutor`** - Multipoint-specific capture (CaptureInfo, NL5)

## Expected Outcome

| Metric | Before | After |
|--------|--------|-------|
| Controller | 1,264 lines | ~700 lines |
| Worker | 1,690 lines | ~900 lines |
| Live | 655 lines | ~450 lines |
| Service wrappers | 21 | 0 |
| Duplicated patterns | 6+ | 0 |

## Implementation Phases

1. **Foundation** - Create `AcquisitionService`, migrate `LiveController`
2. **Controller Extraction** - `ExperimentManager`, `AcquisitionPlanner`
3. **Worker Domain Modules** - progress, position, focus, capture
4. **Integration** - Wire up domain objects
5. **Cleanup** - Remove wrappers, dead code
6. **Stretch** - Task-based loop decomposition

## Documents
- [plan.md](plan.md) - Detailed implementation plan with interfaces

## Related
- Supersedes `conductor/tracks/refactor_controllers_20251230/` (merged both analyses)
