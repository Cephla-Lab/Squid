# Testing Guide for Squid Refactoring

This document covers the TDD workflow, test commands, and mock patterns for the architecture refactoring.

---

## Table of Contents

1. [Environment Setup](#environment-setup)
2. [Test Directory Structure](#test-directory-structure)
3. [TDD Workflow](#tdd-workflow)
4. [Running Tests](#running-tests)
5. [Mock Patterns](#mock-patterns)
6. [Test Templates](#test-templates)
7. [Coverage Expectations](#coverage-expectations)

---

## Environment Setup

### Required Environment Variable

Numba JIT compilation must be disabled for tests to run correctly:

```bash
export NUMBA_DISABLE_JIT=1
```

Or prefix every pytest command:

```bash
NUMBA_DISABLE_JIT=1 pytest tests/ -v
```

### Virtual Environment

```bash
cd /Users/wea/src/allenlab/Squid/software
source venv/bin/activate  # Or your venv path
```

### Install Test Dependencies

```bash
pip install pytest pytest-cov pytest-qt
```

---

## Test Directory Structure

```
tests/
├── conftest.py                          # Shared fixtures
├── unit/
│   ├── squid/
│   │   ├── services/
│   │   │   ├── test_camera_service.py
│   │   │   ├── test_stage_service.py
│   │   │   ├── test_peripheral_service.py
│   │   │   └── ...
│   │   ├── controllers/                 # NEW - add tests here
│   │   │   ├── test_microscope_mode_controller.py
│   │   │   └── test_peripherals_controller.py
│   │   └── test_events.py
│   └── control/
│       ├── core/
│       │   ├── test_live_controller.py
│       │   └── test_multi_point_worker.py
│       └── widgets/
│           ├── test_camera_settings.py
│           └── ...
├── integration/
│   ├── squid/
│   │   └── test_application_services.py
│   └── control/
│       └── test_HighContentScreeningGui.py
└── fixtures/
    ├── configs/                         # Test configuration files
    └── images/                          # Test images
```

---

## TDD Workflow

### The Red-Green-Refactor Cycle

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   1. RED                                                        │
│   Write a failing test                                          │
│   - Test should fail for the right reason                       │
│   - Test should be specific and focused                         │
│                                                                 │
│   ↓                                                             │
│                                                                 │
│   2. GREEN                                                      │
│   Write minimum code to pass                                    │
│   - Don't over-engineer                                         │
│   - Don't add extra features                                    │
│   - Just make the test pass                                     │
│                                                                 │
│   ↓                                                             │
│                                                                 │
│   3. REFACTOR                                                   │
│   Improve code while keeping tests green                        │
│   - Remove duplication                                          │
│   - Improve naming                                              │
│   - Run tests after each change                                 │
│                                                                 │
│   ↓                                                             │
│                                                                 │
│   4. COMMIT                                                     │
│   Save your progress                                            │
│   - Small, focused commits                                      │
│   - Run all related tests before commit                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Example TDD Session

**Task:** Add `SetFilterPositionCommand` handling to a new `FilterWheelService`

```python
# Step 1: RED - Write failing test
# tests/unit/squid/services/test_filter_wheel_service.py

def test_handles_set_filter_position_command():
    """FilterWheelService handles SetFilterPositionCommand."""
    # Arrange
    mock_wheel = MagicMock()
    mock_wheel.get_filter_wheel_position.return_value = 3
    bus = EventBus()

    # This import will fail - service doesn't exist yet
    from squid.services.filter_wheel_service import FilterWheelService

    service = FilterWheelService(mock_wheel, bus)

    # Act
    bus.publish(SetFilterPositionCommand(position=3, wheel_index=0))

    # Assert
    mock_wheel.set_filter_wheel_position.assert_called_once_with(3, 0)
```

```bash
# Run test - it fails (RED)
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_filter_wheel_service.py -v
# ModuleNotFoundError: No module named 'squid.services.filter_wheel_service'
```

```python
# Step 2: GREEN - Create minimum implementation
# squid/services/filter_wheel_service.py

from squid.services.base import BaseService
from squid.events import SetFilterPositionCommand


class FilterWheelService(BaseService):
    def __init__(self, filter_wheel, event_bus):
        super().__init__(event_bus)
        self._wheel = filter_wheel
        self.subscribe(SetFilterPositionCommand, self._on_set_position)

    def _on_set_position(self, cmd):
        self._wheel.set_filter_wheel_position(cmd.position, cmd.wheel_index)
```

```bash
# Run test - it passes (GREEN)
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_filter_wheel_service.py -v
# PASSED
```

```python
# Step 3: REFACTOR - Add thread safety, event publishing
# squid/services/filter_wheel_service.py

import threading
from squid.services.base import BaseService
from squid.events import SetFilterPositionCommand, FilterPositionChanged


class FilterWheelService(BaseService):
    def __init__(self, filter_wheel, event_bus):
        super().__init__(event_bus)
        self._wheel = filter_wheel
        self._lock = threading.RLock()
        self.subscribe(SetFilterPositionCommand, self._on_set_position)

    def _on_set_position(self, cmd):
        with self._lock:
            self._wheel.set_filter_wheel_position(cmd.position, cmd.wheel_index)
            actual = self._wheel.get_filter_wheel_position(cmd.wheel_index)
        self.publish(FilterPositionChanged(position=actual, wheel_index=cmd.wheel_index))
```

```bash
# Run test - still passes (GREEN)
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_filter_wheel_service.py -v
# PASSED
```

```bash
# Step 4: COMMIT
git add squid/services/filter_wheel_service.py tests/unit/squid/services/test_filter_wheel_service.py
git commit -m "feat(services): Add FilterWheelService with SetFilterPositionCommand handling"
```

---

## Running Tests

### All Tests

```bash
NUMBA_DISABLE_JIT=1 pytest tests/ -v
```

### Service Tests Only

```bash
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v
```

### Controller Tests Only

```bash
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/controllers -v
```

### Single Test File

```bash
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_camera_service.py -v
```

### Single Test Function

```bash
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_camera_service.py::test_handles_set_exposure_command -v
```

### Tests with Coverage

```bash
NUMBA_DISABLE_JIT=1 pytest tests/ -v --cov=squid --cov-report=term-missing
```

### Tests Matching Pattern

```bash
NUMBA_DISABLE_JIT=1 pytest tests/ -v -k "exposure"  # All tests with "exposure" in name
```

### Integration Tests Only

```bash
NUMBA_DISABLE_JIT=1 pytest tests/integration -v
```

### GUI Tests (Requires Display)

```bash
NUMBA_DISABLE_JIT=1 pytest tests/integration/control/test_HighContentScreeningGui.py -v
```

### Failed Tests Only (Re-run)

```bash
NUMBA_DISABLE_JIT=1 pytest tests/ -v --lf  # --last-failed
```

---

## Mock Patterns

### Mocking Hardware (MagicMock)

```python
from unittest.mock import MagicMock

def test_camera_service_sets_exposure():
    # Create mock camera
    mock_camera = MagicMock()
    mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)

    # Create service with mock
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    # Act
    bus.publish(SetExposureTimeCommand(exposure_time_ms=100.0))

    # Assert hardware was called correctly
    mock_camera.set_exposure_time.assert_called_once_with(100.0)
```

### Using Simulated Hardware

For more realistic tests, use the simulated hardware classes:

```python
from control.peripherals.cameras.camera_utils import SimulatedCamera
from control.peripherals.stage.simulated import SimulatedStage
from squid.config import CameraConfig

def test_with_simulated_camera():
    # Create simulated camera (more realistic than mock)
    config = CameraConfig(...)
    camera = SimulatedCamera(config)

    bus = EventBus()
    service = CameraService(camera, bus)

    # Act
    bus.publish(SetExposureTimeCommand(exposure_time_ms=100.0))

    # Assert
    assert camera.get_exposure_time() == 100.0
```

### Capturing Published Events

```python
def test_publishes_state_change_event():
    mock_camera = MagicMock()
    mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)

    bus = EventBus()
    service = CameraService(mock_camera, bus)

    # Capture published events
    published_events = []
    bus.subscribe(ExposureTimeChanged, published_events.append)

    # Act
    bus.publish(SetExposureTimeCommand(exposure_time_ms=100.0))

    # Assert event was published
    assert len(published_events) == 1
    assert published_events[0].exposure_time_ms == 100.0
```

### Testing Event Subscriptions

```python
def test_subscribes_to_correct_commands():
    mock_camera = MagicMock()
    bus = EventBus()

    service = CameraService(mock_camera, bus)

    # Check subscriptions exist
    assert SetExposureTimeCommand in bus._subscribers
    assert SetAnalogGainCommand in bus._subscribers
```

### Testing Controllers with Mock Services

```python
def test_live_controller_starts_camera_streaming():
    # Mock services
    mock_camera_service = MagicMock(spec=CameraService)
    mock_illumination_service = MagicMock(spec=IlluminationService)
    mock_stream_handler = MagicMock(spec=StreamHandler)

    bus = EventBus()

    controller = LiveController(
        camera_service=mock_camera_service,
        illumination_service=mock_illumination_service,
        stream_handler=mock_stream_handler,
        event_bus=bus,
    )

    # Act
    bus.publish(StartLiveCommand())

    # Assert
    mock_camera_service.start_streaming.assert_called_once()
```

### Testing Widgets with Qt (pytest-qt)

```python
import pytest
from pytestqt.qtbot import QtBot

def test_exposure_spinbox_publishes_command(qtbot):
    bus = EventBus()

    # Create widget
    widget = CameraSettingsWidget(event_bus=bus)
    qtbot.addWidget(widget)

    # Capture published commands
    commands = []
    bus.subscribe(SetExposureTimeCommand, commands.append)

    # Simulate user input
    widget._exposure_spinbox.setValue(50.0)

    # Assert command was published
    assert len(commands) == 1
    assert commands[0].exposure_time_ms == 50.0


def test_exposure_changed_event_updates_spinbox(qtbot):
    bus = EventBus()

    widget = CameraSettingsWidget(event_bus=bus)
    qtbot.addWidget(widget)

    # Publish state change
    bus.publish(ExposureTimeChanged(exposure_time_ms=100.0))

    # Assert UI updated
    assert widget._exposure_spinbox.value() == 100.0
```

---

## Test Templates

### Service Test Template

```python
# tests/unit/squid/services/test_{name}_service.py

from unittest.mock import MagicMock
import pytest

from squid.events import EventBus, {CommandEvent}, {StateEvent}
from squid.services.{name}_service import {Name}Service


class Test{Name}Service:
    """Tests for {Name}Service."""

    @pytest.fixture
    def mock_hardware(self):
        """Create mock hardware."""
        mock = MagicMock()
        # Configure mock return values
        mock.get_limits.return_value = (0, 100)
        return mock

    @pytest.fixture
    def event_bus(self):
        """Create fresh EventBus."""
        return EventBus()

    @pytest.fixture
    def service(self, mock_hardware, event_bus):
        """Create service instance."""
        return {Name}Service(mock_hardware, event_bus)

    def test_subscribes_to_commands(self, service, event_bus):
        """Service subscribes to expected commands."""
        assert {CommandEvent} in event_bus._subscribers

    def test_handles_{command}_command(self, service, event_bus, mock_hardware):
        """Service handles {CommandEvent}."""
        # Arrange
        events_received = []
        event_bus.subscribe({StateEvent}, events_received.append)

        # Act
        event_bus.publish({CommandEvent}(value=50))

        # Assert
        mock_hardware.set_value.assert_called_once_with(50)
        assert len(events_received) == 1
        assert events_received[0].value == 50

    def test_validates_input(self, service, event_bus, mock_hardware):
        """Service validates and clamps input values."""
        mock_hardware.get_limits.return_value = (0, 100)

        event_bus.publish({CommandEvent}(value=150))  # Over limit

        mock_hardware.set_value.assert_called_once_with(100)  # Clamped

    def test_direct_method_access(self, service, mock_hardware):
        """Service provides direct methods for controllers."""
        mock_hardware.get_value.return_value = 42

        result = service.get_value()

        assert result == 42
        mock_hardware.get_value.assert_called_once()
```

### Controller Test Template

```python
# tests/unit/squid/controllers/test_{name}_controller.py

from unittest.mock import MagicMock
import pytest

from squid.events import EventBus, {CommandEvent}, {StateEvent}
from squid.controllers.{name}_controller import {Name}Controller


class Test{Name}Controller:
    """Tests for {Name}Controller."""

    @pytest.fixture
    def mock_service_a(self):
        """Create mock ServiceA."""
        return MagicMock()

    @pytest.fixture
    def mock_service_b(self):
        """Create mock ServiceB."""
        return MagicMock()

    @pytest.fixture
    def event_bus(self):
        """Create fresh EventBus."""
        return EventBus()

    @pytest.fixture
    def controller(self, mock_service_a, mock_service_b, event_bus):
        """Create controller instance."""
        return {Name}Controller(
            service_a=mock_service_a,
            service_b=mock_service_b,
            event_bus=event_bus,
        )

    def test_initial_state(self, controller):
        """Controller starts with correct initial state."""
        assert controller.state.is_running == False
        assert controller.state.value == 0

    def test_handles_start_command(self, controller, event_bus, mock_service_a):
        """Controller handles start command."""
        events_received = []
        event_bus.subscribe({StateEvent}, events_received.append)

        event_bus.publish({StartCommand}())

        assert controller.state.is_running == True
        mock_service_a.start.assert_called_once()
        assert len(events_received) == 1

    def test_handles_stop_command(self, controller, event_bus, mock_service_a):
        """Controller handles stop command."""
        # Start first
        event_bus.publish({StartCommand}())

        # Then stop
        event_bus.publish({StopCommand}())

        assert controller.state.is_running == False
        mock_service_a.stop.assert_called_once()

    def test_ignores_duplicate_start(self, controller, event_bus, mock_service_a):
        """Controller ignores start when already running."""
        event_bus.publish({StartCommand}())
        event_bus.publish({StartCommand}())  # Duplicate

        # Should only start once
        assert mock_service_a.start.call_count == 1
```

### Widget Test Template

```python
# tests/unit/control/widgets/test_{name}_widget.py

import pytest
from pytestqt.qtbot import QtBot

from squid.events import EventBus, {CommandEvent}, {StateEvent}
from control.widgets.{category}.{name} import {Name}Widget


class Test{Name}Widget:
    """Tests for {Name}Widget."""

    @pytest.fixture
    def event_bus(self):
        """Create fresh EventBus."""
        return EventBus()

    @pytest.fixture
    def widget(self, event_bus, qtbot):
        """Create widget instance."""
        widget = {Name}Widget(event_bus=event_bus)
        qtbot.addWidget(widget)
        return widget

    def test_publishes_command_on_user_input(self, widget, event_bus):
        """Widget publishes command when user changes value."""
        commands = []
        event_bus.subscribe({CommandEvent}, commands.append)

        widget._value_spinbox.setValue(50)

        assert len(commands) == 1
        assert commands[0].value == 50

    def test_updates_ui_on_state_event(self, widget, event_bus):
        """Widget updates UI when state event received."""
        event_bus.publish({StateEvent}(value=100))

        assert widget._value_spinbox.value() == 100

    def test_no_feedback_loop_on_update(self, widget, event_bus):
        """Widget doesn't publish command when updated from event."""
        commands = []
        event_bus.subscribe({CommandEvent}, commands.append)

        event_bus.publish({StateEvent}(value=100))

        # Should not publish command back
        assert len(commands) == 0
```

---

## Coverage Expectations

### Service Layer
- **Target:** 90%+ coverage on event handlers
- **Focus:** Command handling, validation, event publishing
- **Mock:** Hardware interfaces

### Controller Layer
- **Target:** 80%+ coverage on state transitions
- **Focus:** State management, service coordination
- **Mock:** Services (not hardware directly)

### Widget Layer
- **Target:** 70%+ coverage on event round-trips
- **Focus:** Command publishing, state event handling
- **Skip:** Complex Qt layout code

### What to Skip Testing
- Trivial getters/setters
- Qt layout code
- Hardware driver internals (test with simulated hardware instead)

### Running Coverage Report

```bash
# Coverage for service layer
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v --cov=squid.services --cov-report=term-missing

# Coverage for specific file
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_camera_service.py -v --cov=squid.services.camera_service --cov-report=term-missing

# HTML report
NUMBA_DISABLE_JIT=1 pytest tests/ -v --cov=squid --cov-report=html
# Open htmlcov/index.html in browser
```

---

## Troubleshooting

### Test Won't Run (Import Error)

```bash
# Check if module exists
python -c "from squid.services.camera_service import CameraService"

# Check PYTHONPATH
echo $PYTHONPATH
export PYTHONPATH=/Users/wea/src/allenlab/Squid/software:$PYTHONPATH
```

### Numba Error

```bash
# Always set this
export NUMBA_DISABLE_JIT=1

# Or prefix command
NUMBA_DISABLE_JIT=1 pytest tests/ -v
```

### Qt Display Error

```bash
# For headless testing
export QT_QPA_PLATFORM=offscreen
NUMBA_DISABLE_JIT=1 pytest tests/integration/control -v
```

### Slow Tests

```bash
# Run only fast unit tests
NUMBA_DISABLE_JIT=1 pytest tests/unit -v

# Skip slow integration tests
NUMBA_DISABLE_JIT=1 pytest tests/ -v -m "not slow"
```
