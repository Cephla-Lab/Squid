# Squid Stability & Extensibility Implementation Guide

This guide provides step-by-step instructions for implementing stability and extensibility improvements to Squid.

## Background

Squid crashes frequently despite having modern, clean code. Analysis of storm-control (which rarely crashes despite 15-year-old code) revealed that stability comes from **architectural patterns**, not code quality.

See:
- [IMPROVEMENTS_V2.md](../IMPROVEMENTS_V2.md) - Stability patterns from storm-control
- [EXTENSIBILITY.md](../EXTENSIBILITY.md) - Extensibility patterns

## Principles

- **DRY**: Don't Repeat Yourself - extract common patterns into utilities
- **YAGNI**: You Aren't Gonna Need It - only build what's needed now
- **TDD**: Test-Driven Development - write tests first, then implementation
- **Frequent Commits**: One logical change per commit

## Phase Overview

| Phase | Focus | Impact | Estimated Effort |
|-------|-------|--------|------------------|
| 0 | Codebase Refactoring | Modular, navigable codebase | 1-2 weeks |
| 1 | Safety Foundation | Immediate crash reduction | 1 week |
| 2 | Worker Management | Prevents hangs | 3 days |
| 3 | Extensibility | Future-proofing | 1 week |
| 4 | Configuration | Cleaner code | 3 days |
| 5 | GUI Decoupling | Long-term stability | 2 weeks |

## Priority Order

Complete phases in order. Each builds on the previous.

0. **Phase 0**: Split large files, reorganize folders - makes subsequent phases easier
1. **Phase 1**: Create utilities (safe_callback, thread_safe_state) - foundation with no risk
2. **Phase 2**: Add worker timeouts - prevents application hangs
3. **Phase 3**: Registry and EventBus - enables extensibility
4. **Phase 4**: Configuration objects - cleaner, validated config
5. **Phase 5**: GUI decoupling - largest refactor, biggest long-term benefit

## Phase Documents

- [PHASE_0_REFACTORING.md](./PHASE_0_REFACTORING.md) - Split large files, reorganize folders
- [PHASE_1_SAFETY_FOUNDATION.md](./PHASE_1_SAFETY_FOUNDATION.md) - Error containment, thread safety
- [PHASE_2_WORKER_MANAGEMENT.md](./PHASE_2_WORKER_MANAGEMENT.md) - Timeouts, debugging hangs
- [PHASE_3_EXTENSIBILITY.md](./PHASE_3_EXTENSIBILITY.md) - Registry, EventBus
- [PHASE_4_CONFIGURATION.md](./PHASE_4_CONFIGURATION.md) - Pydantic config objects
- [PHASE_5_GUI_DECOUPLING.md](./PHASE_5_GUI_DECOUPLING.md) - ApplicationContext refactor

## Testing

After each phase:
```bash
cd /Users/wea/src/allenlab/Squid/software
pytest --tb=short -v
```

Manual smoke test:
```bash
python main_hcs.py --simulation
# Run a simple acquisition
# Verify no crashes
```

## Files Created/Modified by This Implementation

### Phase 0: New Directories
```
software/control/cameras/          # Camera drivers
software/control/peripherals/      # Hardware peripherals
software/control/peripherals/lighting/
software/control/widgets/          # UI widgets
software/control/gui/              # GUI components
software/control/stage/            # Stage controllers
software/control/processing/       # Image processing
```

### Phase 1-5: New Files
```
software/squid/utils/__init__.py
software/squid/utils/safe_callback.py
software/squid/utils/thread_safe_state.py
software/squid/utils/worker_manager.py
software/squid/registry.py
software/squid/events.py
software/squid/config/acquisition.py
software/squid/application.py

software/tests/squid/utils/__init__.py
software/tests/squid/utils/test_safe_callback.py
software/tests/squid/utils/test_thread_safe_state.py
software/tests/squid/utils/test_worker_manager.py
software/tests/squid/test_registry.py
software/tests/squid/test_events.py
software/tests/squid/config/test_acquisition.py
```

### Files Split by Phase 0
```
software/control/widgets.py → software/control/widgets/*.py (10 files)
software/control/core/core.py → software/control/core/*.py (4 files)
software/control/gui_hcs.py → software/control/gui/*.py (4 files)
software/control/serial_peripherals.py → software/control/peripherals/lighting/*.py (7 files)
software/control/stitcher.py → software/control/processing/*.py (2 files)
software/control/microcontroller.py → software/control/stage/*.py (2 files)
software/control/camera_*.py → software/control/cameras/*.py (9 files)
```

### Modified Files (Phases 1-5)
```
software/control/core/multi_point_worker.py
software/control/_def.py
software/squid/camera/utils.py
software/main_hcs.py
```
