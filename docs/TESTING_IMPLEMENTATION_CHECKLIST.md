# Testing Implementation Checklist

A detailed checklist for implementing comprehensive offline testing for the Squid microscopy package.

---

## Phase 1: Core Simulation Components

### 1.1 Create SimulatedStage
- [ ] Create file: `software/squid/stage/simulated.py`
- [ ] Implement `SimulatedStage` class inheriting from `AbstractStage`
- [ ] Implement position tracking (`_x_mm`, `_y_mm`, `_z_mm`, `_theta_rad`)
- [ ] Implement `move_x(rel_mm, blocking)` - relative X movement
- [ ] Implement `move_y(rel_mm, blocking)` - relative Y movement
- [ ] Implement `move_z(rel_mm, blocking)` - relative Z movement
- [ ] Implement `move_x_to(abs_mm, blocking)` - absolute X movement
- [ ] Implement `move_y_to(abs_mm, blocking)` - absolute Y movement
- [ ] Implement `move_z_to(abs_mm, blocking)` - absolute Z movement
- [ ] Implement `get_pos()` - returns `Pos` object
- [ ] Implement `get_state()` - returns `StageStage` with busy flag
- [ ] Implement `home(x, y, z, theta, blocking)` - reset position to 0
- [ ] Implement `zero(x, y, z, theta, blocking)` - set current as zero
- [ ] Implement `set_limits(...)` - software limit enforcement
- [ ] Add limit clamping in all movement methods
- [ ] Add configurable movement delay for realistic timing
- [ ] Add helper methods for test setup (`set_position`, `set_busy`)

### 1.2 Create Stage Factory
- [ ] Create file: `software/squid/stage/utils.py`
- [ ] Implement `get_stage(config, microcontroller, simulated)` factory function
- [ ] Return `SimulatedStage` when `simulated=True`
- [ ] Return `CephlaStage` or `PriorStage` when `simulated=False`
- [ ] Update imports in `squid/stage/__init__.py` if needed

### 1.3 Fix CellX_Simulation
- [ ] Open `software/control/serial_peripherals.py`
- [ ] Find `CellX_Simulation` class (around line 1231)
- [ ] Remove serial connection from `__init__` method
- [ ] Make it a pure simulation without hardware dependencies

---

## Phase 2: Test Infrastructure

### 2.1 Create Shared Test Fixtures
- [ ] Create/expand file: `software/tests/conftest.py`
- [ ] Add `@pytest.fixture` for `sim_serial` (SimSerial instance)
- [ ] Add `@pytest.fixture` for `sim_microcontroller` (Microcontroller with SimSerial)
- [ ] Add `@pytest.fixture` for `sim_camera` (SimulatedCamera)
- [ ] Add `@pytest.fixture` for `sim_stage` (SimulatedStage)
- [ ] Add `@pytest.fixture` for `sim_filter_wheel` (SimulatedFilterWheelController)
- [ ] Add `@pytest.fixture` for `sim_microscope` (full Microscope with simulated=True)
- [ ] Add cleanup/teardown in fixtures where needed (yield + close)
- [ ] Add `@pytest.fixture` for test configuration objects

### 2.2 Create Test Directory Structure
- [ ] Create directory: `software/tests/unit/`
- [ ] Create directory: `software/tests/unit/squid/`
- [ ] Create directory: `software/tests/unit/control/`
- [ ] Create directory: `software/tests/integration/`
- [ ] Add `__init__.py` to each new directory

---

## Phase 3: Unit Tests

### 3.1 SimulatedStage Unit Tests
- [ ] Create file: `software/tests/unit/squid/test_stage_unit.py`
- [ ] Test initial position is (0, 0, 0, 0)
- [ ] Test `move_x` relative movement
- [ ] Test `move_y` relative movement
- [ ] Test `move_z` relative movement
- [ ] Test `move_x_to` absolute movement
- [ ] Test `move_y_to` absolute movement
- [ ] Test `move_z_to` absolute movement
- [ ] Test `get_pos` returns correct Pos object
- [ ] Test `get_state` returns StageStage with busy flag
- [ ] Test `home` resets position to 0
- [ ] Test `zero` sets current position as zero
- [ ] Test `set_limits` updates limits
- [ ] Test movement clamping at limits
- [ ] Test blocking vs non-blocking behavior

### 3.2 SimulatedCamera Unit Tests
- [ ] Create file: `software/tests/unit/squid/test_camera_unit.py`
- [ ] Test `set_exposure_time` / `get_exposure_time`
- [ ] Test `set_analog_gain` / `get_analog_gain`
- [ ] Test `set_binning` / `get_binning`
- [ ] Test `get_resolution` with different binning
- [ ] Test `set_pixel_format` / `get_pixel_format`
- [ ] Test `set_region_of_interest` / `get_region_of_interest`
- [ ] Test `start_streaming` / `stop_streaming` / `get_is_streaming`
- [ ] Test `send_trigger` generates frame
- [ ] Test `read_camera_frame` returns CameraFrame
- [ ] Test frame callbacks are called with `add_frame_callback`
- [ ] Test `remove_frame_callback` works
- [ ] Test acquisition modes (SOFTWARE_TRIGGER, CONTINUOUS)

### 3.3 SimulatedFilterWheelController Unit Tests
- [ ] Create file: `software/tests/unit/squid/test_filter_wheel_unit.py`
- [ ] Test `set_filter_wheel_position` / `get_filter_wheel_position`
- [ ] Test `home` operation
- [ ] Test `initialize` with different wheel indices
- [ ] Test `available_filter_wheels` property
- [ ] Test `get_filter_wheel_info` returns correct info
- [ ] Test `set_delay_ms` / `get_delay_ms`
- [ ] Test `close` cleanup

### 3.4 Microcontroller/SimSerial Unit Tests
- [ ] Create file: `software/tests/unit/control/test_microcontroller_unit.py`
- [ ] Test SimSerial command parsing
- [ ] Test SimSerial response generation
- [ ] Test position tracking in SimSerial
- [ ] Test Microcontroller with SimSerial backend
- [ ] Test `move_x_usteps` / `move_y_usteps` / `move_z_usteps`
- [ ] Test `get_pos` returns position tuple
- [ ] Test `is_busy` state tracking
- [ ] Test `wait_till_operation_is_completed`

### 3.5 LiveController Unit Tests
- [ ] Create file: `software/tests/unit/control/test_live_controller_unit.py`
- [ ] Test start/stop live view
- [ ] Test `is_live` property
- [ ] Test trigger mode switching
- [ ] Test illumination control integration

### 3.6 AutoFocusController Unit Tests
- [ ] Create file: `software/tests/unit/control/test_autofocus_unit.py`
- [ ] Test autofocus algorithm with synthetic images
- [ ] Test focus map functionality
- [ ] Test reflection-based autofocus logic

### 3.7 ABC Contract Verification Tests
- [ ] Create file: `software/tests/unit/squid/test_abc_contracts.py`
- [ ] Verify SimulatedStage implements all AbstractStage methods
- [ ] Verify SimulatedCamera implements all AbstractCamera methods
- [ ] Verify SimulatedFilterWheelController implements all AbstractFilterWheelController methods
- [ ] Test that abstract methods raise NotImplementedError if not implemented

---

## Phase 4: Integration Tests

### 4.1 Microscope Integration Tests
- [ ] Create file: `software/tests/integration/test_microscope_integration.py`
- [ ] Test `Microscope.build_from_global_config(simulated=True)` creates all components
- [ ] Test microscope has non-null camera, stage, illumination_controller
- [ ] Test microscope components are simulated versions
- [ ] Test microscope cleanup on close

### 4.2 Acquisition Workflow Tests
- [ ] Create file: `software/tests/integration/test_acquisition_workflow.py`
- [ ] Test single image acquisition end-to-end
- [ ] Test camera trigger -> frame received flow
- [ ] Test stage movement -> image capture sequence
- [ ] Test illumination -> camera -> save flow

### 4.3 Live View Workflow Tests
- [ ] Create file: `software/tests/integration/test_live_view_workflow.py`
- [ ] Test start live view with simulated microscope
- [ ] Test frame callbacks receive frames during live view
- [ ] Test stop live view cleans up properly
- [ ] Test live view with different acquisition modes

### 4.4 Autofocus Workflow Tests
- [ ] Create file: `software/tests/integration/test_autofocus_workflow.py`
- [ ] Test autofocus controller with simulated camera and stage
- [ ] Test Z-stack acquisition for focus finding
- [ ] Test focus quality metric calculation

### 4.5 Multi-Point Acquisition Tests
- [ ] Create file: `software/tests/integration/test_multi_point_workflow.py`
- [ ] Test MultiPointController configuration
- [ ] Test grid scan (NX, NY positions)
- [ ] Test Z-stack acquisition (NZ levels)
- [ ] Test multi-channel acquisition
- [ ] Test progress callbacks during acquisition
- [ ] Test acquisition completion

### 4.6 Channel Switching Tests
- [ ] Create file: `software/tests/integration/test_channel_switching.py`
- [ ] Test filter wheel position changes
- [ ] Test illumination intensity changes
- [ ] Test channel configuration loading
- [ ] Test multi-channel sequence

---

## Phase 5: Enable Skipped Tests

### 5.1 MultiPointWorker Tests
- [ ] Open `software/tests/control/test_MultiPointWorker.py`
- [ ] Identify tests marked with `@pytest.mark.skip`
- [ ] Fix threading issues causing test failures
- [ ] Remove skip markers from fixed tests
- [ ] Verify tests pass with simulated hardware

---

## Phase 6: CI/CD Integration (Optional)

### 6.1 Pytest Configuration
- [ ] Add pytest markers in `pytest.ini` or `pyproject.toml`
  - [ ] `@pytest.mark.unit` for unit tests
  - [ ] `@pytest.mark.integration` for integration tests
  - [ ] `@pytest.mark.slow` for long-running tests
- [ ] Configure test discovery for new directories

### 6.2 GitHub Actions (if applicable)
- [ ] Create `.github/workflows/tests.yml`
- [ ] Configure Python environment setup
- [ ] Run `pytest tests/` on push/PR
- [ ] Add coverage reporting

---

## Phase 7: Flakiness Prevention Infrastructure

### 7.1 Create Test Utilities Module
- [ ] Create file: `tests/fixtures/__init__.py`
- [ ] Create file: `tests/fixtures/builders.py`
  - [ ] Implement `MicroscopeBuilder` class
  - [ ] Implement `with_piezo()` method
  - [ ] Implement `with_autofocus_camera()` method
  - [ ] Implement `build()` method
- [ ] Create file: `tests/fixtures/waiters.py`
  - [ ] Implement `wait_for_condition(fn, timeout_s)` function
  - [ ] Implement `DeterministicEventWaiter` class
  - [ ] Implement `AcquisitionTracker` class

### 7.2 Add Isolation Fixtures
- [ ] Add `isolated_event_bus` fixture to `conftest.py`
- [ ] Add `isolated_config` fixture to `conftest.py`
- [ ] Add `apply_timeout` autouse fixture to `conftest.py`
- [ ] Add pytest-timeout to dependencies in `pyproject.toml`

### 7.3 Replace time.sleep() in Tests
- [ ] Audit existing tests for `time.sleep()` usage
- [ ] Replace with `wait_for_condition()` or event waits
- [ ] Add explicit timeout assertions

---

## Phase 8: GUI Widget Testing

### 8.1 Camera Settings Widget Tests
- [ ] Create file: `tests/gui/__init__.py`
- [ ] Create file: `tests/gui/test_camera_settings_widget.py`
  - [ ] Test widget initialization with mock camera
  - [ ] Test exposure time spinbox updates camera
  - [ ] Test gain spinbox updates camera
  - [ ] Test event subscription (ExposureTimeChanged updates UI)
  - [ ] Test binning dropdown changes

### 8.2 Stage Widget Tests
- [ ] Create file: `tests/gui/test_stage_widget.py`
  - [ ] Test widget initialization with mock stage service
  - [ ] Test movement button clicks publish commands
  - [ ] Test position display updates from events
  - [ ] Test home button functionality

### 8.3 Navigation Widget Tests
- [ ] Create file: `tests/gui/test_navigation_widget.py`
  - [ ] Test widget initialization
  - [ ] Test FOV navigation
  - [ ] Test coordinate display updates

---

## Phase 9: E2E Workflow Tests

### 9.1 Multi-Point Acquisition E2E
- [ ] Create file: `tests/e2e/__init__.py`
- [ ] Create file: `tests/e2e/test_multipoint_acquisition.py`
  - [ ] Test single region acquisition completes
  - [ ] Test multi-region acquisition completes
  - [ ] Test Z-stack acquisition creates correct number of images
  - [ ] Test time-lapse acquisition
  - [ ] Test acquisition abort mid-run
  - [ ] Test output files are created correctly

### 9.2 Autofocus Workflow E2E
- [ ] Create file: `tests/e2e/test_autofocus_workflow.py`
  - [ ] Test contrast autofocus completes
  - [ ] Test focus map generation with 3 points
  - [ ] Test focus map interpolation
  - [ ] Test autofocus during acquisition

### 9.3 Live View E2E
- [ ] Create file: `tests/e2e/test_live_view.py`
  - [ ] Test start/stop live view cycle
  - [ ] Test frame callbacks receive frames
  - [ ] Test channel switching during live view

---

## Phase 10: Simulation Enhancements

### 10.1 Deterministic Frame Generation
- [ ] Modify `SimulatedCamera` in `control/peripherals/cameras/camera_utils.py`
  - [ ] Add `_deterministic_seed` parameter
  - [ ] Add `set_frame_generator(fn)` method
  - [ ] Add `_frame_generator` callback support
  - [ ] Ensure reproducible frames when seed is set

### 10.2 Error Injection for SimulatedStage
- [ ] Modify `SimulatedStage` in `control/peripherals/stage/simulated.py`
  - [ ] Add `inject_failure_on_next_move()` method
  - [ ] Add `inject_failure_after_n_moves(n)` method
  - [ ] Add `_fail_next_move` flag
  - [ ] Add `_move_count` tracking
  - [ ] Raise RuntimeError when failure is injected

### 10.3 Error Injection for SimulatedCamera
- [ ] Modify `SimulatedCamera`
  - [ ] Add `inject_read_timeout()` method
  - [ ] Add `inject_streaming_failure()` method

---

## Phase 11: pytest Configuration Updates

### 11.1 Add New Markers
- [ ] Add `e2e` marker to `pyproject.toml`
- [ ] Add `gui` marker alias for `qt`
- [ ] Update marker descriptions

### 11.2 Add Dependencies
- [ ] Add `pytest-timeout` to test dependencies
- [ ] Verify `pytest-qt` is in dependencies
- [ ] Verify `pytest-xvfb` is in dependencies

### 11.3 Add testpaths
- [ ] Add `tests/gui` to testpaths if not auto-discovered
- [ ] Add `tests/e2e` to testpaths if not auto-discovered

---

## Verification Checklist

After implementation, verify:

- [ ] `pytest software/tests/unit/` passes without hardware
- [ ] `pytest software/tests/integration/` passes without hardware
- [ ] `pytest software/tests/` runs fully offline
- [ ] `Microscope.build_from_global_config(simulated=True)` works
- [ ] No import errors for simulation classes
- [ ] All AbstractStage methods implemented in SimulatedStage
- [ ] All AbstractCamera methods implemented in SimulatedCamera
- [ ] Multi-point acquisition completes in simulation mode
- [ ] No flaky tests (run 5x without failures)
- [ ] GUI tests pass with `pytest -m qt --xvfb`
- [ ] E2E tests pass with `pytest -m e2e`
- [ ] All tests complete within timeout limits

---

## Files to Create Summary

| File | Purpose |
|------|---------|
| `squid/stage/simulated.py` | SimulatedStage implementation |
| `squid/stage/utils.py` | Stage factory function |
| `tests/conftest.py` | Shared pytest fixtures |
| `tests/unit/__init__.py` | Unit test package |
| `tests/unit/squid/__init__.py` | Squid unit tests package |
| `tests/unit/squid/test_stage_unit.py` | Stage unit tests |
| `tests/unit/squid/test_camera_unit.py` | Camera unit tests |
| `tests/unit/squid/test_filter_wheel_unit.py` | Filter wheel unit tests |
| `tests/unit/squid/test_abc_contracts.py` | ABC verification tests |
| `tests/unit/control/__init__.py` | Control unit tests package |
| `tests/unit/control/test_microcontroller_unit.py` | Microcontroller unit tests |
| `tests/unit/control/test_live_controller_unit.py` | LiveController unit tests |
| `tests/unit/control/test_autofocus_unit.py` | AutoFocus unit tests |
| `tests/integration/__init__.py` | Integration tests package |
| `tests/integration/test_microscope_integration.py` | Microscope integration tests |
| `tests/integration/test_acquisition_workflow.py` | Acquisition workflow tests |
| `tests/integration/test_live_view_workflow.py` | Live view workflow tests |
| `tests/integration/test_autofocus_workflow.py` | Autofocus workflow tests |
| `tests/integration/test_multi_point_workflow.py` | Multi-point acquisition tests |
| `tests/integration/test_channel_switching.py` | Channel switching tests |
| `tests/fixtures/__init__.py` | Fixtures package |
| `tests/fixtures/builders.py` | MicroscopeBuilder, test object factories |
| `tests/fixtures/waiters.py` | wait_for_condition, DeterministicEventWaiter, AcquisitionTracker |
| `tests/gui/__init__.py` | GUI tests package |
| `tests/gui/test_camera_settings_widget.py` | Camera widget tests |
| `tests/gui/test_stage_widget.py` | Stage widget tests |
| `tests/gui/test_navigation_widget.py` | Navigation widget tests |
| `tests/e2e/__init__.py` | E2E tests package |
| `tests/e2e/test_multipoint_acquisition.py` | Multi-point E2E tests |
| `tests/e2e/test_autofocus_workflow.py` | Autofocus E2E tests |
| `tests/e2e/test_live_view.py` | Live view E2E tests |

## Files to Modify Summary

| File | Change |
|------|--------|
| `control/serial_peripherals.py` | Fix CellX_Simulation (remove serial in __init__) |
| `tests/control/test_MultiPointWorker.py` | Enable skipped tests |
| `control/peripherals/cameras/camera_utils.py` | Add deterministic frame generation, error injection |
| `control/peripherals/stage/simulated.py` | Add error injection methods |
| `tests/conftest.py` | Add isolation fixtures, timeout fixture |
| `pyproject.toml` | Add e2e marker, pytest-timeout dependency |
