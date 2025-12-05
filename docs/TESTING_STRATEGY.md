# Squid Microscopy Package: Comprehensive Testing Strategy

## Goal
Enable full offline testing of all major features (multi-point acquisition, live view, autofocus) with both unit and integration tests, without requiring hardware connections.

---

## Current State (Updated)

### Test Infrastructure
- **Test directory structure**: `tests/unit/`, `tests/integration/`, `tests/manual/`
- **Shared fixtures**: `tests/conftest.py` with simulated camera, stage, filter wheel, microscope fixtures
- **Pytest configuration**: `pyproject.toml` with markers (@unit, @integration, @slow, @qt, @manual)

### Existing Simulation Components
| Component | Location | Status |
|-----------|----------|--------|
| SimulatedCamera | `control/peripherals/cameras/camera_utils.py:125` | Full AbstractCamera implementation |
| SimulatedStage | `control/peripherals/stage/simulated.py` | **NEW** - Full AbstractStage implementation |
| SimSerial | `control/peripherals/stage/serial.py:104` | Simulates serial for CephlaStage |
| SimulatedFilterWheelController | `control/peripherals/filter_wheel/utils.py:8` | Full implementation |
| XLight_Simulation | `control/peripherals/lighting/xlight.py` | Spinning disk simulation |
| Dragonfly_Simulation | `control/peripherals/lighting/dragonfly.py` | Full simulation |
| LDI_Simulation | `control/peripherals/lighting/ldi.py` | Light source simulation |
| CellX_Simulation | `control/peripherals/lighting/cellx.py` | Laser simulation |
| NL5_Simulation | `control/peripherals/nl5.py` | Laser autofocus simulation |

### Factory Functions
```python
# Camera
from control.peripherals.cameras.camera_utils import get_camera
camera = get_camera(config, simulated=True)

# Stage
from control.peripherals.stage.stage_utils import get_stage
stage = get_stage(stage_config, microcontroller=None, simulated=True)

# Filter Wheel
from control.peripherals.filter_wheel.utils import get_filter_wheel_controller
controller = get_filter_wheel_controller(config, simulated=True)

# Full Microscope
from control.microscope import Microscope
scope = Microscope.build_from_global_config(simulated=True)
```

---

## Test Directory Structure

```
software/tests/
├── conftest.py                    # Shared fixtures
├── tools.py                       # Test utilities
├── unit/                          # Unit tests (no hardware simulation)
│   ├── squid/
│   │   ├── config/
│   │   ├── services/
│   │   ├── utils/
│   │   ├── hardware/              # Simulated hardware unit tests
│   │   └── abc/                   # ABC contract verification
│   └── control/
│       └── core/
├── integration/                   # Integration tests (simulated hardware)
│   ├── squid/
│   │   ├── test_camera.py
│   │   ├── test_stage.py
│   │   ├── test_filter_wheel.py
│   │   ├── test_application.py
│   │   └── ...
│   └── control/
│       ├── test_microcontroller.py
│       ├── test_microscope.py
│       └── ...
├── manual/                        # Manual/visual verification tests
│   ├── test_spot_detection_manual.py
│   └── test_ome_tiff_saving.py
└── data/                          # Test data files
```

---

## Using Test Fixtures

### Available Fixtures (from conftest.py)

```python
# Camera
def test_camera(simulated_camera):
    simulated_camera.start_streaming()
    simulated_camera.send_trigger()
    frame = simulated_camera.read_frame()

# Stage (SimulatedStage - fast, no microcontroller)
def test_stage(simulated_stage):
    simulated_stage.move_x_to(50.0)
    pos = simulated_stage.get_pos()

# Stage (CephlaStage with SimSerial - for testing microcontroller interactions)
def test_cephla_stage(simulated_cephla_stage):
    simulated_cephla_stage.home(x=True, y=True, z=True, theta=False)

# Filter Wheel
def test_filter_wheel(simulated_filter_wheel):
    simulated_filter_wheel.set_filter_wheel_position({1: 3})

# Full Microscope
def test_microscope(simulated_microscope):
    scope = simulated_microscope
    scope.camera.start_streaming()
    scope.stage.move_x_to(50.0)

# Microcontroller
def test_micro(simulated_microcontroller):
    simulated_microcontroller.move_x_usteps(1000)
```

---

## Running Tests

```bash
# Run all unit tests (fast, no hardware simulation)
pytest tests/unit/ -m unit

# Run all integration tests
pytest tests/integration/ -m integration

# Run everything offline
pytest tests/

# Run specific test file
pytest tests/integration/squid/test_stage.py -v

# Run tests with specific marker
pytest -m "not slow"

# Run with coverage
pytest --cov=squid --cov=control tests/
```

---

## Test Markers

Add markers to test functions for filtering:

```python
import pytest

@pytest.mark.unit
def test_pure_logic():
    """Unit test without hardware."""
    pass

@pytest.mark.integration
def test_with_simulated_hardware(simulated_microscope):
    """Integration test using simulated hardware."""
    pass

@pytest.mark.slow
def test_long_running():
    """Test that takes >5 seconds."""
    pass

@pytest.mark.qt
def test_gui_component(qtbot):
    """Test requiring Qt."""
    pass

@pytest.mark.e2e
def test_full_workflow(simulated_microscope):
    """End-to-end workflow test."""
    pass
```

---

## Flakiness Prevention

### Use Event-Based Synchronization

Replace `time.sleep()` with explicit wait conditions:

```python
import time

# BAD - flaky
time.sleep(1.0)
assert camera.frame_count > 0

# GOOD - deterministic
def wait_for_condition(fn, timeout_s=5.0, poll_interval=0.01):
    start = time.time()
    while time.time() - start < timeout_s:
        if fn():
            return True
        time.sleep(poll_interval)
    raise TimeoutError(f"Condition not met within {timeout_s}s")

wait_for_condition(lambda: camera.frame_count > 0)
```

### Isolation Fixtures

Ensure each test starts with clean state:

```python
@pytest.fixture
def isolated_event_bus():
    """Fresh EventBus per test."""
    from squid.events import EventBus
    bus = EventBus()
    yield bus
    bus.clear()

@pytest.fixture
def isolated_config():
    """Save/restore control._def state."""
    import control._def as _def
    original = {k: getattr(_def, k) for k in dir(_def) if not k.startswith('_')}
    yield
    for k, v in original.items():
        setattr(_def, k, v)
```

### Test Timeouts

Apply automatic timeouts based on test type:

```python
# In conftest.py
@pytest.fixture(autouse=True)
def apply_timeout(request):
    markers = [m.name for m in request.node.iter_markers()]
    if 'e2e' in markers:
        request.node.add_marker(pytest.mark.timeout(300))
    elif 'integration' in markers:
        request.node.add_marker(pytest.mark.timeout(60))
    else:
        request.node.add_marker(pytest.mark.timeout(10))
```

---

## GUI Testing

### Widget Unit Tests with Mocked Services

The service layer enables clean widget testing by mocking services instead of hardware:

```python
@pytest.mark.qt
def test_camera_settings_widget_with_service(qtbot):
    from unittest.mock import Mock
    from control.widgets.camera import CameraSettingsWidget
    from squid.events import EventBus

    # Mock the camera (for getters used in initialization)
    mock_camera = Mock()
    mock_camera.get_exposure_limits.return_value = (1.0, 1000.0)
    mock_camera.get_gain_range.return_value = Mock(min_gain=0, max_gain=100, gain_step=1)
    mock_camera.get_resolution.return_value = (1920, 1080)
    mock_camera.get_region_of_interest.return_value = (0, 0, 1920, 1080)
    mock_camera.get_pixel_format.return_value = None
    mock_camera.get_available_pixel_formats.return_value = []
    mock_camera.get_binning.return_value = (1, 1)
    mock_camera.get_binning_options.return_value = [(1, 1)]

    # Mock the service
    mock_service = Mock()

    widget = CameraSettingsWidget(
        camera=mock_camera,
        camera_service=mock_service
    )
    qtbot.addWidget(widget)

    # Test that widget uses service for operations
    widget.entry_exposureTime.setValue(100.0)
    mock_service.set_exposure_time.assert_called_with(100.0)
```

### Testing with Real Services and Mocked Hardware

For integration-style tests, use real services with mocked hardware:

```python
@pytest.mark.qt
def test_camera_widget_integration(qtbot):
    from unittest.mock import Mock
    from control.widgets.camera import CameraSettingsWidget
    from squid.services import CameraService
    from squid.events import EventBus

    # Create mock camera
    mock_camera = Mock()
    mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
    # ... setup other mock returns

    # Create real service with mock camera
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    widget = CameraSettingsWidget(
        camera=mock_camera,
        camera_service=service
    )
    qtbot.addWidget(widget)

    # Test that setting exposure goes through service to camera
    widget.entry_exposureTime.setValue(50.0)

    # Verify the camera received the call
    mock_camera.set_exposure_time.assert_called_with(50.0)
```

### Testing Event-Based Synchronization

Test that widgets respond to service events:

```python
@pytest.mark.qt
def test_widget_responds_to_events(qtbot):
    from squid.events import EventBus, ExposureTimeChanged
    from control.widgets.camera import CameraSettingsWidget

    bus = EventBus()
    # ... setup widget with bus

    # Simulate event from another source
    bus.publish(ExposureTimeChanged(exposure_time_ms=250.0))

    # Widget should update its display
    qtbot.waitUntil(lambda: widget.entry_exposureTime.value() == 250.0, timeout=1000)
```

### Legacy Widget Tests (Backward Compatibility)

Widgets still work without services for backward compatibility:

```python
@pytest.mark.qt
def test_camera_settings_widget_legacy(qtbot):
    from unittest.mock import Mock
    from control.widgets.camera import CameraSettingsWidget

    mock_camera = Mock()
    mock_camera.get_exposure_limits.return_value = (1.0, 1000.0)
    mock_camera.get_gain_range.return_value = Mock(min_gain=0, max_gain=100, gain_step=1)
    mock_camera.get_resolution.return_value = (1920, 1080)
    mock_camera.get_region_of_interest.return_value = (0, 0, 1920, 1080)

    # Create widget without service (legacy mode)
    widget = CameraSettingsWidget(camera=mock_camera)
    qtbot.addWidget(widget)

    # Widget should still work
    widget.entry_exposureTime.setValue(100.0)
```

### Signal/Slot Testing

Test event-driven updates:

```python
@pytest.mark.qt
def test_exposure_event_updates_widget(qtbot, isolated_event_bus):
    from squid.events import ExposureTimeChanged

    widget = CameraSettingsWidget(camera=mock_camera)
    qtbot.addWidget(widget)

    isolated_event_bus.publish(ExposureTimeChanged(exposure_time_ms=250.0))

    qtbot.waitUntil(lambda: widget.entry_exposureTime.value() == 250.0, timeout=1000)
```

### Running GUI Tests

```bash
# Run Qt tests (requires display or Xvfb)
pytest -m qt tests/

# Run with xvfb (CI/headless)
pytest -m qt tests/ --xvfb
```

---

## E2E Workflow Testing

### AcquisitionTracker Pattern

Use a tracker to monitor acquisition state:

```python
import threading

class AcquisitionTracker:
    """Track acquisition events for E2E testing."""
    def __init__(self, timeout_s=30.0):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.image_count = 0
        self.errors = []
        self._timeout = timeout_s

    def on_start(self, params):
        self.started.set()

    def on_finish(self):
        self.finished.set()

    def on_image(self, frame, info):
        self.image_count += 1

    def wait_for_completion(self):
        if not self.finished.wait(timeout=self._timeout):
            raise TimeoutError("Acquisition did not complete")
```

### Example E2E Test

```python
@pytest.mark.e2e
@pytest.mark.slow
def test_multipoint_acquisition(simulated_microscope, tmp_path):
    from control.core.multi_point_controller import MultiPointController

    tracker = AcquisitionTracker()
    mpc = MultiPointController(simulated_microscope)

    # Configure
    mpc.set_base_path(str(tmp_path))
    mpc.set_NZ(2)
    mpc.set_Nt(1)
    mpc.scanCoordinates.add_single_fov_region("R1", 1.0, 1.0, 1.0)

    # Connect tracker
    mpc.signal_acquisition_started.connect(tracker.on_start)
    mpc.signal_acquisition_finished.connect(tracker.on_finish)

    # Run
    mpc.run_acquisition()
    tracker.wait_for_completion()

    # Verify
    assert tracker.image_count > 0
    assert (tmp_path / "coordinates.csv").exists()
```

---

## Test Utilities

### Builder Pattern for Test Objects

Create configurable test microscopes:

```python
# tests/fixtures/builders.py
from control.microscope import Microscope
import control._def as _def

class MicroscopeBuilder:
    def __init__(self):
        self._with_piezo = False
        self._with_af_camera = False

    def with_piezo(self):
        self._with_piezo = True
        return self

    def with_autofocus_camera(self):
        self._with_af_camera = True
        return self

    def build(self):
        _def.HAS_OBJECTIVE_PIEZO = self._with_piezo
        _def.SUPPORT_LASER_AUTOFOCUS = self._with_af_camera
        return Microscope.build_from_global_config(simulated=True)

# Usage
scope = MicroscopeBuilder().with_piezo().build()
```

### DeterministicEventWaiter

Wait for events with timeout:

```python
# tests/fixtures/waiters.py
import threading

class DeterministicEventWaiter:
    def __init__(self, timeout_s=5.0):
        self.timeout_s = timeout_s

    def wait_for(self, event: threading.Event, message=""):
        if not event.wait(timeout=self.timeout_s):
            raise TimeoutError(f"Timeout waiting for: {message}")
```

---

## Success Criteria

- [x] Test infrastructure reorganized into unit/integration/manual directories
- [x] Shared fixtures in conftest.py
- [x] SimulatedStage implements AbstractStage directly
- [x] Stage factory function (get_stage) works
- [x] Microscope.build_from_global_config(simulated=True) uses SimulatedStage
- [x] pytest configuration with markers in pyproject.toml
- [x] Service layer unit tests (43 tests for CameraService, StageService, PeripheralService)
- [x] Widget testing patterns documented for service layer
- [ ] All major workflows have integration test coverage (in progress)
- [ ] Multi-point acquisition completes in simulation mode
- [ ] Flakiness prevention patterns documented and in use
- [ ] GUI widget tests exist for major widgets
- [ ] E2E workflow tests for multi-point acquisition
- [ ] Test utilities (builders, waiters) available in fixtures
- [ ] All tests pass with `pytest --timeout=60`
