# Squid Microscopy Package: Comprehensive Testing Strategy

## Goal
Enable full offline testing of all major features (multi-point acquisition, live view, autofocus) with both unit and integration tests, without requiring hardware connections.

---

## Current State

### Existing Simulation Components (Already Working)
| Component | Location | Status |
|-----------|----------|--------|
| SimulatedCamera | `squid/camera/utils.py:115` | Full AbstractCamera implementation |
| SimSerial | `control/microcontroller.py:131` | Simulates serial for CephlaStage |
| SimulatedFilterWheelController | `squid/filter_wheel_controller/utils.py:8` | Full implementation |
| XLight_Simulation | `control/serial_peripherals.py:153` | Spinning disk simulation |
| Dragonfly_Simulation | `control/serial_peripherals.py:713` | Full simulation |
| LDI_Simulation | `control/serial_peripherals.py:946` | Light source simulation |
| NL5_Simulation | `control/NL5.py:95` | Laser autofocus simulation |

### Existing Factory Pattern
```python
Microscope.build_from_global_config(simulated=True)  # Already works
```

---

## Implementation Plan

### Phase 1: Create SimulatedStage (Critical)

**Problem:** No direct `AbstractStage` simulation exists. CephlaStage requires SimSerial which is indirect.

**Create:** `/software/squid/stage/simulated.py`

```python
class SimulatedStage(AbstractStage):
    """Direct implementation of AbstractStage for testing."""
    # Track: position (x_mm, y_mm, z_mm, theta_rad), limits, busy state
    # Implement: move_x/y/z, move_x_to/y_to/z_to, get_pos, home, zero, set_limits
```

**Add factory:** `/software/squid/stage/utils.py`
```python
def get_stage(config, microcontroller=None, simulated=False) -> AbstractStage:
    if simulated:
        return SimulatedStage(config)
    return CephlaStage(microcontroller, config)
```

### Phase 2: Create Test Fixtures

**Create/Expand:** `/software/tests/conftest.py`

```python
@pytest.fixture
def sim_microscope():
    """Fully simulated microscope for integration tests."""
    return Microscope.build_from_global_config(simulated=True)

@pytest.fixture
def sim_camera():
    return get_camera(get_camera_config(), simulated=True)

@pytest.fixture
def sim_stage():
    return SimulatedStage(get_stage_config())

@pytest.fixture
def sim_filter_wheel():
    return SimulatedFilterWheelController(...)
```

### Phase 3: Unit Tests

**Create:** `/software/tests/unit/` directory

| Test File | Component | Key Tests |
|-----------|-----------|-----------|
| `unit/squid/test_camera_unit.py` | SimulatedCamera | exposure, gain, binning, ROI, triggers, streaming |
| `unit/squid/test_stage_unit.py` | SimulatedStage | move, home, zero, limits, position tracking |
| `unit/squid/test_filter_wheel_unit.py` | SimulatedFilterWheelController | position, home, delays |
| `unit/control/test_microcontroller_unit.py` | SimSerial | command/response, state |
| `unit/control/test_live_controller_unit.py` | LiveController | trigger modes, illumination |
| `unit/control/test_autofocus_unit.py` | AutoFocusController | focus algorithm |

### Phase 4: Integration Tests

**Create:** `/software/tests/integration/` directory

| Test File | Workflow |
|-----------|----------|
| `test_microscope_integration.py` | Full microscope init with all simulated components |
| `test_acquisition_workflow.py` | Single image acquisition end-to-end |
| `test_live_view_workflow.py` | Start/stop live view, frame callbacks |
| `test_autofocus_workflow.py` | Autofocus with simulated camera/stage |
| `test_multi_point_workflow.py` | Multi-point acquisition with regions, Z-stacks |
| `test_channel_switching.py` | Filter wheel + illumination changes |

### Phase 5: Fix Existing Issues

1. **Fix CellX_Simulation** (`control/serial_peripherals.py:1231`)
   - Remove real serial connection from `__init__`

2. **Enable skipped tests** in `tests/control/test_MultiPointWorker.py`
   - Currently marked `@pytest.mark.skip` due to threading issues

---

## Test Directory Structure

```
software/tests/
├── conftest.py                    # Shared fixtures
├── tools.py                       # Existing utilities
│
├── unit/                          # NEW
│   ├── squid/
│   │   ├── test_camera_unit.py
│   │   ├── test_stage_unit.py
│   │   ├── test_filter_wheel_unit.py
│   │   └── test_abc_contracts.py  # Verify all ABCs implemented
│   └── control/
│       ├── test_microcontroller_unit.py
│       ├── test_live_controller_unit.py
│       └── test_autofocus_unit.py
│
├── integration/                   # NEW
│   ├── test_microscope_integration.py
│   ├── test_acquisition_workflow.py
│   ├── test_live_view_workflow.py
│   ├── test_autofocus_workflow.py
│   ├── test_multi_point_workflow.py
│   └── test_channel_switching.py
│
├── squid/                         # Existing (keep)
└── control/                       # Existing (keep)
```

---

## Key Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `squid/stage/simulated.py` | CREATE | SimulatedStage implementation |
| `squid/stage/utils.py` | CREATE | Stage factory with simulated flag |
| `tests/conftest.py` | CREATE/EXPAND | Shared pytest fixtures |
| `tests/unit/**` | CREATE | Unit test directory and files |
| `tests/integration/**` | CREATE | Integration test directory and files |
| `control/serial_peripherals.py:1231` | FIX | CellX_Simulation serial issue |

---

## Implementation Order

1. **SimulatedStage** - Unblocks all stage-dependent tests
2. **conftest.py fixtures** - Enables consistent test setup
3. **Unit tests for existing simulations** - Verify current sims work correctly
4. **Integration tests** - Test component interactions
5. **Fix CellX_Simulation** - Complete simulation layer
6. **Enable skipped MultiPointWorker tests** - Full coverage

---

## Success Criteria

- [ ] `pytest tests/` runs fully offline (no hardware)
- [ ] All major workflows have integration test coverage
- [ ] Unit tests exist for each simulated component
- [ ] `Microscope.build_from_global_config(simulated=True)` produces fully functional microscope
- [ ] Multi-point acquisition completes in simulation mode
