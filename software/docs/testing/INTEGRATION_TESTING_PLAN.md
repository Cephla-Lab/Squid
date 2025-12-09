# Comprehensive Integration Testing Plan for Squid Microscope Software

## Executive Summary

This document establishes a systematic integration testing framework covering all major features of the GUI, event bus layer, service layer, and hardware using simulators. The goal is comprehensive test coverage (~350+ tests) that enables confident refactoring and feature development.

### Current State Analysis

| Category | Files | Classes | Existing Tests | Coverage Gap |
|----------|-------|---------|----------------|--------------|
| Services | 7 | 7 | 3 (partial) | 4 services untested |
| Widgets | 53 | ~45 | 0 | 100% |
| Hardware Simulators | 8 | 8 | Used as fixtures | Feature completeness varies |
| Integration Tests | 19 | - | 66 functions | Limited scope |
| E2E Workflows | 0 | - | 0 | 100% |

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    HighContentScreeningGui                       │
├─────────────────────────────────────────────────────────────────┤
│  Camera  │ Stage  │ Hardware │ Wellplate │ Acquisition │ Display│
│  Widgets │ Widgets│ Widgets  │ Widgets   │ Widgets     │ Widgets│
└────┬─────┴────┬───┴────┬─────┴─────┬─────┴──────┬──────┴───┬────┘
     │          │        │           │            │          │
     ▼          ▼        ▼           ▼            ▼          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      EventBus (squid/events.py)                  │
│                    Publish/Subscribe Commands & State            │
└────┬─────────────┬─────────────┬─────────────┬─────────────┬────┘
     │             │             │             │             │
     ▼             ▼             ▼             ▼             ▼
┌─────────┐ ┌───────────┐ ┌────────────┐ ┌─────────┐ ┌───────────┐
│ Camera  │ │ Stage     │ │ Peripheral │ │ Trigger │ │ Microscope│
│ Service │ │ Service   │ │ Service    │ │ Service │ │ Mode Svc  │
└────┬────┘ └─────┬─────┘ └──────┬─────┘ └────┬────┘ └─────┬─────┘
     │            │              │            │            │
     ▼            ▼              ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Simulated Hardware Layer                      │
│  SimulatedCamera │ SimulatedStage │ SimSerial │ FilterWheel     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Infrastructure & Prerequisites

### 1.1 Existing Simulators Inventory

| Simulator | Location | Completeness | Notes |
|-----------|----------|--------------|-------|
| **SimulatedCamera** | `control/peripherals/cameras/camera_utils.py:141` | HIGH | Full AbstractCamera, threading, ROI, binning |
| **SimulatedStage** | `control/peripherals/stage/simulated.py:16` | HIGH | Direct AbstractStage, no microcontroller needed |
| **SimSerial** | `control/peripherals/stage/serial.py:104` | HIGH | Cephla MCU protocol, movement commands |
| **SimulatedFilterWheelController** | `control/peripherals/filter_wheel/utils.py:8` | MEDIUM | Multi-wheel, positioning, homing |
| **XLight_Simulation** | `control/peripherals/lighting/xlight.py` | LOW | Capability declaration only |
| **Dragonfly_Simulation** | `control/peripherals/lighting/dragonfly.py` | MEDIUM | State tracking |
| **NL5_Simulation** | `control/peripherals/nl5.py` | LOW | Stub methods |
| **ObjectiveChanger2PosController_Simulation** | `control/peripherals/objective_changer.py` | MEDIUM | Position tracking |

### 1.2 Existing Test Fixtures (tests/conftest.py)

```python
# Camera
@pytest.fixture simulated_camera(camera_config) -> SimulatedCamera
@pytest.fixture simulated_camera_streaming(simulated_camera) -> SimulatedCamera

# Stage
@pytest.fixture simulated_stage(stage_config) -> SimulatedStage
@pytest.fixture simulated_cephla_stage(simulated_microcontroller, stage_config) -> CephlaStage

# Microcontroller
@pytest.fixture sim_serial() -> SimSerial
@pytest.fixture simulated_microcontroller() -> Microcontroller

# Filter Wheel
@pytest.fixture simulated_filter_wheel() -> SimulatedFilterWheelController
@pytest.fixture simulated_filter_wheel_multi() -> SimulatedFilterWheelController

# Full System
@pytest.fixture simulated_microscope() -> Microscope
@pytest.fixture simulated_application_context() -> ApplicationContext

# Event Bus
@pytest.fixture event_bus() -> EventBus
```

### 1.3 Service Layer Status

| Service | File | Has Unit Tests | Has Integration Tests |
|---------|------|----------------|----------------------|
| CameraService | `squid/services/camera_service.py` | Yes | Yes |
| StageService | `squid/services/stage_service.py` | Yes | Yes |
| PeripheralService | `squid/services/peripheral_service.py` | Yes | Yes |
| **TriggerService** | `squid/services/trigger_service.py` | **No** | **No** |
| **MicroscopeModeService** | `squid/services/microscope_mode_service.py` | **No** | **No** |
| **LiveService** | `squid/services/live_service.py` | **No** | **No** |

### 1.4 Widget Inventory (53 files, ~45 widget classes)

#### Camera Widgets (`control/widgets/camera/`)
- `live_control.py` - LiveControlWidget
- `settings.py` - CameraSettingsWidget
- `recording.py` - RecordingWidget, MultiCameraRecordingWidget
- `_common.py` - Shared utilities

#### Stage Widgets (`control/widgets/stage/`)
- `navigation.py` - NavigationWidget
- `autofocus.py` - AutofocusWidget
- `piezo.py` - PiezoWidget
- `utils.py` - StageUtilsWidget
- `_common.py` - Shared utilities

#### Hardware Widgets (`control/widgets/hardware/`)
- `dac.py` - DACControWidget
- `trigger.py` - TriggerControlWidget
- `laser_autofocus.py` - LaserAutofocusSettingWidget, LaserAutofocusControlWidget
- `filter_controller.py` - FilterControllerWidget
- `confocal.py` - SpinningDiskConfocalWidget, DragonflyWidget
- `objectives.py` - ObjectivesWidget
- `led_matrix.py` - LedMatrixWidget
- `_common.py` - Shared utilities

#### Display Widgets (`control/widgets/display/`)
- `napari_live.py` - NapariLiveViewer
- `napari_mosaic.py` - NapariMosaicDisplayWidget
- `napari_multichannel.py` - NapariMultichannelViewer
- `focus_map.py` - FocusMapWidget
- `stats.py` - StatsWidget
- `plotting.py` - PlottingWidget
- `_common.py` - Shared utilities

#### Wellplate Widgets (`control/widgets/wellplate/`)
- `format.py` - WellplateFormatWidget
- `well_selection.py` - WellSelectionWidget
- `well_1536.py` - Well1536SelectionWidget
- `calibration.py` - CalibrationLiveViewer
- `sample_settings.py` - SampleSettingsWidget
- `_common.py` - Shared utilities

#### Acquisition Widgets (`control/widgets/acquisition/`)
- `wellplate_multipoint.py` - WellplateMultiPointWidget
- `flexible_multipoint.py` - FlexibleMultiPointWidget
- `fluidics_multipoint.py` - MultiPointWithFluidicsWidget
- `_common.py` - Shared utilities

#### Tracking Widgets (`control/widgets/tracking/`)
- `controller.py` - TrackingControllerWidget
- `plate_reader.py` - PlateReaderAcquisitionWidget, PlateReaderNavigationWidget
- `joystick.py` - JoystickWidget
- `displacement.py` - DisplacementMeasurementWidget
- `_common.py` - Shared utilities

#### Other Widgets
- `fluidics.py` - FluidicsWidget
- `config.py` - ConfigWidget
- `spectrometer.py` - SpectrometerControlWidget
- `nl5.py` - NL5Widget
- `custom_multipoint.py` - TemplateMultiPointWidget
- `base.py` - Base widget classes

---

## Part 2: Event Bus Refactoring for Testability

### 2.1 Current Problem

Widgets currently use the global `event_bus` singleton directly:

```python
# Current pattern in control/widgets/stage/navigation.py:38
from squid.events import event_bus, StagePositionChanged

class NavigationWidget(QFrame):
    def __init__(self, stage_service, ...):
        # Uses global singleton - hard to test in isolation
        event_bus.subscribe(StagePositionChanged, self._on_position_changed)
```

### 2.2 Solution: Dependency Injection

Refactor widgets to accept an optional `event_bus` parameter:

```python
# New pattern
from squid.events import event_bus as global_event_bus, StagePositionChanged

class NavigationWidget(QFrame):
    def __init__(
        self,
        stage_service: "StageService",
        event_bus: Optional[EventBus] = None,  # NEW: injectable
        main: Optional[Any] = None,
        widget_configuration: str = "full",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus or global_event_bus  # Use injected or global
        self._event_bus.subscribe(StagePositionChanged, self._on_position_changed)
```

### 2.3 Widgets Requiring Refactoring (Priority Order)

| Priority | Widget | File | Event Bus Usage |
|----------|--------|------|-----------------|
| 1 | NavigationWidget | `stage/navigation.py` | subscribe: StagePositionChanged |
| 2 | DACControWidget | `hardware/dac.py` | publish: SetDACCommand; subscribe: DACValueChanged |
| 3 | TriggerControlWidget | `hardware/trigger.py` | publish: Start/StopCameraTriggerCommand |
| 4 | CameraSettingsWidget | `camera/settings.py` | publish: SetExposure/GainCommand |
| 5 | LiveControlWidget | `camera/live_control.py` | publish: Start/StopLiveCommand |
| 6 | LaserAutofocusSettingWidget | `hardware/laser_autofocus.py` | publish: TurnOn/OffAFLaserCommand |
| 7 | WellplateFormatWidget | `wellplate/format.py` | Signal-based only |
| 8 | TrackingControllerWidget | `tracking/controller.py` | Complex event usage |

### 2.4 Test Fixture Pattern After Refactoring

```python
# tests/conftest.py additions
@pytest.fixture
def navigation_widget(stage_service, event_bus, qtbot):
    """NavigationWidget with injected event bus for isolation."""
    from control.widgets.stage.navigation import NavigationWidget
    widget = NavigationWidget(stage_service, event_bus=event_bus)
    qtbot.addWidget(widget)
    yield widget
    widget.position_update_timer.stop()

@pytest.fixture
def dac_widget(peripheral_service, event_bus, qtbot):
    """DACControWidget with injected event bus."""
    from control.widgets.hardware.dac import DACControWidget
    widget = DACControWidget(peripheral_service, event_bus=event_bus)
    qtbot.addWidget(widget)
    yield widget
```

---

## Part 3: Simulator Enhancements

### 3.1 SimulatedCamera Enhancements Needed

Current state: HIGH completeness, but some gaps for advanced testing.

**Additions needed:**

```python
# In control/peripherals/cameras/camera_utils.py

class SimulatedCamera(AbstractCamera):
    # ... existing code ...

    # NEW: Test pattern generation
    def set_test_pattern(self, pattern: str):
        """Set test pattern: 'noise', 'gradient', 'checkerboard', 'solid', 'custom'"""
        self._test_pattern = pattern

    # NEW: Simulate defocus for autofocus testing
    def set_simulated_defocus(self, defocus_um: float):
        """Apply simulated blur based on defocus distance."""
        self._defocus_um = defocus_um

    # NEW: Frame timing statistics
    def get_frame_statistics(self) -> dict:
        """Return frame timing stats for performance testing."""
        return {
            'frames_captured': self._frame_id,
            'avg_capture_time_ms': self._avg_capture_time,
            'dropped_frames': self._dropped_frames,
        }
```

### 3.2 SimulatedStage Enhancements Needed

Current state: HIGH completeness.

**Additions needed:**

```python
# In control/peripherals/stage/simulated.py

class SimulatedStage(AbstractStage):
    # ... existing code ...

    # NEW: Movement callback for testing
    def set_movement_callback(self, callback: Callable[[Pos, Pos], None]):
        """Callback called with (old_pos, new_pos) after each move."""
        self._movement_callback = callback

    # NEW: Simulate backlash (optional, disabled by default)
    def set_backlash_simulation(self, enabled: bool, backlash_um: float = 5.0):
        """Enable backlash simulation for more realistic testing."""
        self._simulate_backlash = enabled
        self._backlash_um = backlash_um

    # NEW: Movement history for verification
    def get_movement_history(self) -> List[Tuple[str, float, float]]:
        """Return list of (axis, distance_mm, timestamp) for all movements."""
        return list(self._movement_history)

    def clear_movement_history(self):
        """Clear movement history."""
        self._movement_history.clear()
```

### 3.3 New Simulators Required

#### 3.3.1 CELESTA Illumination Simulator

```python
# NEW FILE: control/peripherals/lighting/celesta_simulation.py

class CELESTA_Simulation:
    """Simulated CELESTA spectral illumination system."""

    WAVELENGTHS = [405, 446, 477, 520, 546, 638, 749]

    def __init__(self):
        self._channels = {
            wl: {"intensity": 0.0, "enabled": False}
            for wl in self.WAVELENGTHS
        }
        self._global_shutter = False

    def set_channel_intensity(self, wavelength: int, intensity: float):
        """Set intensity (0-100%) for wavelength."""
        if wavelength in self._channels:
            self._channels[wavelength]["intensity"] = max(0, min(100, intensity))

    def enable_channel(self, wavelength: int, enabled: bool):
        """Enable/disable a wavelength channel."""
        if wavelength in self._channels:
            self._channels[wavelength]["enabled"] = enabled

    def get_channel_state(self, wavelength: int) -> dict:
        """Get state of a wavelength channel."""
        return self._channels.get(wavelength, {})

    def get_all_channel_states(self) -> dict:
        """Get states of all channels."""
        return dict(self._channels)

    def set_global_shutter(self, enabled: bool):
        """Set global shutter state."""
        self._global_shutter = enabled

    def close(self):
        """Cleanup resources."""
        pass
```

#### 3.3.2 Andor Laser Simulator

```python
# NEW FILE: control/peripherals/lighting/andor_simulation.py

class AndorLaser_Simulation:
    """Simulated Andor laser illumination system."""

    def __init__(self):
        self._lasers = {}  # wavelength -> {power, enabled, temperature}

    def add_laser(self, wavelength: int):
        """Register a laser wavelength."""
        self._lasers[wavelength] = {
            "power_mw": 0.0,
            "enabled": False,
            "temperature_c": 25.0,
        }

    def set_power(self, wavelength: int, power_mw: float):
        """Set laser power in milliwatts."""
        if wavelength in self._lasers:
            self._lasers[wavelength]["power_mw"] = max(0, power_mw)

    def enable(self, wavelength: int, enabled: bool):
        """Enable/disable a laser."""
        if wavelength in self._lasers:
            self._lasers[wavelength]["enabled"] = enabled

    def get_laser_state(self, wavelength: int) -> dict:
        """Get state of a laser."""
        return self._lasers.get(wavelength, {})

    def close(self):
        """Cleanup resources."""
        pass
```

#### 3.3.3 Fluidics Simulator Enhancement

```python
# Enhancement to control/peripherals/fluidics.py

class FluidicsSimulation:
    """Enhanced fluidics simulation for testing."""

    def __init__(self):
        self._valve_position = 0
        self._pump_speed = 0
        self._volume_dispensed_ul = 0
        self._is_initialized = False
        self._sequence_running = False
        self._current_step = 0
        self._error_simulation = None

    def initialize(self) -> bool:
        """Initialize the fluidics system."""
        self._is_initialized = True
        return True

    def set_valve_position(self, position: int):
        """Set valve position (1-8 typically)."""
        if self._error_simulation == "valve_stuck":
            raise RuntimeError("Valve stuck simulation")
        self._valve_position = position

    def prime_port(self, port: int, volume_ul: float):
        """Prime a specific port."""
        self._volume_dispensed_ul += volume_ul

    def run_sequence(self, sequence: list) -> bool:
        """Run a fluidics sequence."""
        self._sequence_running = True
        for i, step in enumerate(sequence):
            self._current_step = i
            # Simulate step execution
        self._sequence_running = False
        return True

    def get_status(self) -> dict:
        """Get current fluidics status."""
        return {
            "initialized": self._is_initialized,
            "valve_position": self._valve_position,
            "sequence_running": self._sequence_running,
            "current_step": self._current_step,
        }

    def simulate_error(self, error_type: str):
        """Simulate an error for testing error handling."""
        self._error_simulation = error_type

    def close(self):
        """Cleanup."""
        pass
```

### 3.4 SimSerial Verification Checklist

Verify all MCU commands are implemented in `control/peripherals/stage/serial.py:104`:

| Command | Implemented | Notes |
|---------|-------------|-------|
| MOVE_X, MOVE_Y, MOVE_Z | Yes | Relative movement |
| MOVETO_X, MOVETO_Y, MOVETO_Z | Yes | Absolute movement |
| HOME_X, HOME_Y, HOME_Z | Yes | Homing |
| ZERO_X, ZERO_Y, ZERO_Z | Yes | Zeroing |
| SET_DAC0, SET_DAC1 | Needs verification | DAC output |
| TRIGGER_START/STOP | Needs verification | Camera trigger |
| LED_ON/OFF | Needs verification | LED control |
| AF_LASER_ON/OFF | Needs verification | Autofocus laser |
| JOYSTICK_BUTTON | Yes | Button state |
| GET_POSITION | Yes | Position query |

---

## Part 4: Test Infrastructure Improvements

### 4.1 Create Test Utilities Module

```python
# NEW FILE: tests/utils/wait_helpers.py
"""Utilities for async/event-based testing."""

import threading
import time
from typing import Callable, Type, List, Any
from contextlib import contextmanager
from squid.events import Event, EventBus


def wait_for_event(
    event_bus: EventBus,
    event_type: Type[Event],
    timeout_ms: int = 1000,
    condition: Callable[[Event], bool] = None,
) -> Event:
    """
    Block until an event is received or timeout.

    Args:
        event_bus: The event bus to listen on
        event_type: The event type to wait for
        timeout_ms: Maximum time to wait
        condition: Optional predicate to filter events

    Returns:
        The received event

    Raises:
        TimeoutError: If no matching event received within timeout
    """
    received = []
    event_received = threading.Event()

    def handler(event):
        if condition is None or condition(event):
            received.append(event)
            event_received.set()

    event_bus.subscribe(event_type, handler)
    try:
        if event_received.wait(timeout_ms / 1000):
            return received[0]
        raise TimeoutError(f"No {event_type.__name__} received within {timeout_ms}ms")
    finally:
        event_bus.unsubscribe(event_type, handler)


def wait_for_condition(
    condition_fn: Callable[[], bool],
    timeout_ms: int = 1000,
    poll_interval_ms: int = 10,
) -> bool:
    """
    Poll until condition returns True or timeout.

    Args:
        condition_fn: Function that returns True when condition is met
        timeout_ms: Maximum time to wait
        poll_interval_ms: Time between polls

    Returns:
        True if condition was met, False if timed out
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if condition_fn():
            return True
        time.sleep(poll_interval_ms / 1000)
    return False


@contextmanager
def capture_events(
    event_bus: EventBus,
    event_type: Type[Event],
) -> List[Event]:
    """
    Context manager to capture all events of a type.

    Usage:
        with capture_events(bus, ExposureTimeChanged) as events:
            service.set_exposure_time(50)
        assert len(events) == 1
        assert events[0].exposure_time_ms == 50
    """
    captured = []

    def handler(event):
        captured.append(event)

    event_bus.subscribe(event_type, handler)
    try:
        yield captured
    finally:
        event_bus.unsubscribe(event_type, handler)


class EventCapture:
    """Helper class to capture events during tests."""

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self._handlers = {}
        self._captured = {}

    def capture(self, *event_types: Type[Event]):
        """Start capturing specified event types."""
        for event_type in event_types:
            self._captured[event_type] = []

            def make_handler(et):
                def handler(event):
                    self._captured[et].append(event)
                return handler

            handler = make_handler(event_type)
            self._handlers[event_type] = handler
            self._bus.subscribe(event_type, handler)

    def stop(self):
        """Stop capturing and unsubscribe."""
        for event_type, handler in self._handlers.items():
            self._bus.unsubscribe(event_type, handler)
        self._handlers.clear()

    def get(self, event_type: Type[Event]) -> List[Event]:
        """Get captured events of a type."""
        return self._captured.get(event_type, [])

    def clear(self):
        """Clear all captured events."""
        for key in self._captured:
            self._captured[key] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()
```

### 4.2 Enhanced pytest Configuration

```toml
# pyproject.toml additions

[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]

# Markers for categorizing tests
markers = [
    "unit: Unit tests (no hardware simulation, fast)",
    "integration: Integration tests with simulated hardware",
    "qt: Tests requiring Qt event loop",
    "slow: Tests taking > 5 seconds",
    "e2e: End-to-end workflow tests",
    "manual: Tests requiring manual verification",
    "service: Service layer tests",
    "widget: Widget layer tests",
    "simulator: Simulator-specific tests",
]

# Default options
addopts = [
    "-v",
    "--tb=short",
    "-ra",
    "--strict-markers",
    "--ignore=tests/manual",
]

# Timeout for stuck tests
timeout = 60

# Filter warnings
filterwarnings = [
    "ignore::DeprecationWarning:pyqtgraph.*",
    "ignore::PendingDeprecationWarning",
]
```

### 4.3 Enhanced qtbot Fixture

```python
# Addition to tests/conftest.py

@pytest.fixture
def qtbot():
    """
    Enhanced qtbot stub for widget testing.

    For full Qt testing, install pytest-qt and this will be overridden.
    This stub provides basic functionality for headless testing.
    """
    class _QtBotStub:
        def __init__(self):
            self._widgets = []

        def add_widget(self, widget):
            """Register widget for cleanup."""
            self._widgets.append(widget)
            return widget

        def wait(self, timeout_ms: int = 100):
            """Wait for Qt events to process."""
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import QCoreApplication
            import time

            end_time = time.time() + timeout_ms / 1000
            while time.time() < end_time:
                QCoreApplication.processEvents()
                time.sleep(0.01)

        def wait_signal(self, signal, timeout: int = 1000):
            """Wait for a Qt signal."""
            from PyQt5.QtCore import QEventLoop, QTimer

            loop = QEventLoop()
            signal.connect(loop.quit)
            QTimer.singleShot(timeout, loop.quit)
            loop.exec_()

        def mouse_click(self, widget, button=None, pos=None):
            """Simulate mouse click on widget."""
            from PyQt5.QtTest import QTest
            from PyQt5.QtCore import Qt
            button = button or Qt.LeftButton
            if pos:
                QTest.mouseClick(widget, button, Qt.NoModifier, pos)
            else:
                QTest.mouseClick(widget, button)

        def key_click(self, widget, key, modifier=None):
            """Simulate key press on widget."""
            from PyQt5.QtTest import QTest
            from PyQt5.QtCore import Qt
            modifier = modifier or Qt.NoModifier
            QTest.keyClick(widget, key, modifier)

        def cleanup(self):
            """Cleanup all registered widgets."""
            for widget in reversed(self._widgets):
                try:
                    widget.close()
                    widget.deleteLater()
                except:
                    pass
            self._widgets.clear()

    bot = _QtBotStub()
    yield bot
    bot.cleanup()
```

---

## Part 5: Test Implementation Plan

### 5.1 Directory Structure

```
tests/
├── conftest.py                              # Shared fixtures
├── utils/
│   ├── __init__.py
│   ├── wait_helpers.py                      # Async/event utilities
│   └── widget_test_helpers.py               # Widget testing utilities
│
├── unit/
│   ├── squid/
│   │   ├── services/
│   │   │   ├── test_base.py                 # BaseService tests
│   │   │   ├── test_camera_service.py       # EXISTING - enhance
│   │   │   ├── test_stage_service.py        # EXISTING - enhance
│   │   │   ├── test_peripheral_service.py   # EXISTING - enhance
│   │   │   ├── test_trigger_service.py      # NEW
│   │   │   ├── test_microscope_mode_service.py # NEW
│   │   │   ├── test_live_service.py         # NEW
│   │   │   └── test_registry.py             # Service registry tests
│   │   ├── test_events.py                   # EXISTING
│   │   └── test_config.py                   # EXISTING
│   │
│   └── control/
│       └── widgets/
│           ├── camera/
│           │   ├── test_live_control.py     # NEW
│           │   ├── test_settings.py         # NEW
│           │   └── test_recording.py        # NEW
│           ├── stage/
│           │   ├── test_navigation.py       # NEW
│           │   ├── test_autofocus.py        # NEW
│           │   ├── test_piezo.py            # NEW
│           │   └── test_utils.py            # NEW
│           ├── hardware/
│           │   ├── test_dac.py              # NEW
│           │   ├── test_trigger.py          # NEW
│           │   ├── test_laser_autofocus.py  # NEW
│           │   ├── test_filter_controller.py # NEW
│           │   ├── test_confocal.py         # NEW
│           │   └── test_objectives.py       # NEW
│           ├── wellplate/
│           │   ├── test_format.py           # NEW
│           │   ├── test_calibration.py      # NEW
│           │   └── test_well_selection.py   # NEW
│           ├── acquisition/
│           │   ├── test_wellplate_multipoint.py # NEW
│           │   └── test_flexible_multipoint.py  # NEW
│           ├── display/
│           │   ├── test_napari_live.py      # NEW
│           │   ├── test_napari_mosaic.py    # NEW
│           │   └── test_plotting.py         # NEW
│           └── tracking/
│               ├── test_controller.py       # NEW
│               └── test_joystick.py         # NEW
│
├── integration/
│   ├── squid/
│   │   ├── services/
│   │   │   ├── test_camera_service_integration.py    # EXISTING
│   │   │   ├── test_stage_service_integration.py     # EXISTING
│   │   │   ├── test_peripheral_service_integration.py # EXISTING
│   │   │   ├── test_trigger_service_integration.py    # NEW
│   │   │   ├── test_microscope_mode_integration.py    # NEW
│   │   │   └── test_live_service_integration.py       # NEW
│   │   ├── test_camera.py                   # EXISTING
│   │   ├── test_stage.py                    # EXISTING
│   │   ├── test_filter_wheel.py             # EXISTING
│   │   └── test_application.py              # EXISTING
│   │
│   └── control/
│       ├── widgets/
│       │   ├── test_camera_widget_integration.py      # NEW
│       │   ├── test_stage_widget_integration.py       # NEW
│       │   ├── test_hardware_widget_integration.py    # NEW
│       │   ├── test_wellplate_widget_integration.py   # NEW
│       │   └── test_acquisition_widget_integration.py # NEW
│       ├── test_HighContentScreeningGui.py  # EXISTING - enhance
│       ├── test_MultiPointController.py     # EXISTING
│       └── test_microscope.py               # EXISTING
│
├── e2e/
│   ├── workflows/
│   │   ├── test_live_acquisition.py         # NEW
│   │   ├── test_multipoint_acquisition.py   # NEW
│   │   ├── test_wellplate_scanning.py       # NEW
│   │   └── test_autofocus_workflow.py       # NEW
│   └── test_full_acquisition_pipeline.py    # NEW
│
└── data/
    ├── test_configurations/                 # Test configuration files
    └── test_images/                         # Reference images for testing
```

### 5.2 Service Layer Test Specifications

#### test_trigger_service.py (~15 tests)

```python
"""Unit tests for TriggerService."""

class TestTriggerService:
    # Command handling
    def test_set_trigger_mode_command_handled(self): ...
    def test_set_trigger_fps_command_handled(self): ...
    def test_start_camera_trigger_command_handled(self): ...
    def test_stop_camera_trigger_command_handled(self): ...

    # State events
    def test_trigger_mode_changed_published(self): ...
    def test_trigger_fps_changed_published(self): ...

    # Mode transitions
    def test_switch_software_to_hardware_mode(self): ...
    def test_switch_hardware_to_continuous_mode(self): ...
    def test_switch_continuous_to_software_mode(self): ...

    # FPS validation
    def test_fps_clamped_to_min(self): ...
    def test_fps_clamped_to_max(self): ...

    # Error handling
    def test_invalid_mode_raises_error(self): ...
    def test_service_handles_exception_gracefully(self): ...

    # Cleanup
    def test_shutdown_stops_trigger(self): ...
    def test_unsubscribes_on_shutdown(self): ...
```

#### test_microscope_mode_service.py (~12 tests)

```python
"""Unit tests for MicroscopeModeService."""

class TestMicroscopeModeService:
    # Command handling
    def test_set_microscope_mode_command_handled(self): ...

    # State events
    def test_microscope_mode_changed_published(self): ...

    # Configuration switching
    def test_switch_to_brightfield(self): ...
    def test_switch_to_fluorescence(self): ...
    def test_switch_to_confocal(self): ...

    # Objective coordination
    def test_objective_change_triggers_mode_update(self): ...
    def test_mode_change_validates_objective_compatibility(self): ...

    # Invalid configurations
    def test_invalid_configuration_raises_error(self): ...
    def test_missing_configuration_handled(self): ...

    # Cleanup
    def test_shutdown_restores_default_mode(self): ...
    def test_unsubscribes_on_shutdown(self): ...
```

#### test_live_service.py (~18 tests)

```python
"""Unit tests for LiveService."""

class TestLiveService:
    # Command handling
    def test_start_live_command_handled(self): ...
    def test_stop_live_command_handled(self): ...

    # State events
    def test_live_state_changed_published_on_start(self): ...
    def test_live_state_changed_published_on_stop(self): ...

    # Configuration handling
    def test_start_live_with_configuration(self): ...
    def test_start_live_without_configuration(self): ...
    def test_configuration_change_during_live(self): ...

    # State management
    def test_start_when_already_live(self): ...
    def test_stop_when_not_live(self): ...
    def test_is_live_property(self): ...

    # Frame callbacks
    def test_frame_callback_called(self): ...
    def test_frame_callback_with_exposure_change(self): ...

    # Concurrent operations
    def test_start_stop_rapid_succession(self): ...
    def test_multiple_start_commands(self): ...

    # Error handling
    def test_camera_error_during_live(self): ...
    def test_graceful_recovery_from_error(self): ...

    # Cleanup
    def test_shutdown_stops_live(self): ...
    def test_unsubscribes_on_shutdown(self): ...
```

### 5.3 Widget Unit Test Specifications

#### test_navigation.py (~20 tests)

```python
"""Unit tests for NavigationWidget."""

class TestNavigationWidget:
    # Button actions
    def test_forward_x_button_publishes_move_command(self): ...
    def test_backward_x_button_publishes_negative_move(self): ...
    def test_forward_y_button_publishes_move_command(self): ...
    def test_backward_y_button_publishes_negative_move(self): ...
    def test_forward_z_button_publishes_move_command(self): ...
    def test_backward_z_button_publishes_negative_move(self): ...

    # Delta value handling
    def test_delta_x_spinbox_affects_move_distance(self): ...
    def test_delta_y_spinbox_affects_move_distance(self): ...
    def test_delta_z_spinbox_converts_um_to_mm(self): ...
    def test_delta_rounds_to_microstep_boundary(self): ...

    # Position updates
    def test_position_changed_event_updates_labels(self): ...
    def test_timer_updates_position_display(self): ...
    def test_z_label_displays_in_um(self): ...

    # Click-to-move
    def test_click_to_move_checkbox_default_unchecked(self): ...
    def test_click_to_move_checkbox_toggle(self): ...

    # Event isolation
    def test_uses_injected_event_bus(self): ...
    def test_unsubscribes_on_close(self): ...

    # Widget configuration
    def test_full_configuration_shows_all_controls(self): ...
    def test_minimal_configuration_hides_extras(self): ...

    # Cleanup
    def test_timer_stopped_on_cleanup(self): ...
```

#### test_dac.py (~15 tests)

```python
"""Unit tests for DACControWidget."""

class TestDACControWidget:
    # Slider actions
    def test_channel0_slider_publishes_set_dac_command(self): ...
    def test_channel1_slider_publishes_set_dac_command(self): ...

    # Spinbox actions
    def test_channel0_spinbox_publishes_set_dac_command(self): ...
    def test_channel1_spinbox_publishes_set_dac_command(self): ...

    # Value synchronization
    def test_slider_change_updates_spinbox(self): ...
    def test_spinbox_change_updates_slider(self): ...

    # Event handling
    def test_dac_value_changed_updates_ui(self): ...
    def test_dac_value_changed_blocks_signals(self): ...
    def test_external_change_doesnt_trigger_command(self): ...

    # Value range
    def test_value_clamped_to_0_100(self): ...
    def test_slider_range_0_100(self): ...

    # Event isolation
    def test_uses_injected_event_bus(self): ...
    def test_unsubscribes_on_close(self): ...

    # Independence
    def test_channel0_and_channel1_independent(self): ...
```

### 5.4 Widget-Service Integration Test Specifications

#### test_camera_widget_integration.py (~25 tests)

```python
"""Integration tests for camera widgets with services."""

class TestCameraWidgetIntegration:
    # LiveControlWidget + CameraService
    def test_exposure_change_updates_camera_and_ui(self): ...
    def test_gain_change_updates_camera_and_ui(self): ...
    def test_live_start_triggers_camera_streaming(self): ...
    def test_live_stop_stops_camera_streaming(self): ...

    # CameraSettingsWidget + CameraService
    def test_exposure_spinbox_round_trip(self): ...
    def test_gain_spinbox_round_trip(self): ...
    def test_binning_dropdown_round_trip(self): ...

    # Full flow tests
    def test_widget_exposure_to_camera_to_event_to_widget(self): ...
    def test_widget_gain_to_camera_to_event_to_widget(self): ...

    # Concurrent updates
    def test_programmatic_and_ui_exposure_change(self): ...
    def test_rapid_exposure_changes(self): ...

    # Error scenarios
    def test_camera_error_reflected_in_widget(self): ...
    def test_widget_disabled_during_acquisition(self): ...
```

#### test_stage_widget_integration.py (~20 tests)

```python
"""Integration tests for stage widgets with services."""

class TestStageWidgetIntegration:
    # NavigationWidget + StageService
    def test_button_click_moves_stage(self): ...
    def test_stage_position_updates_label(self): ...
    def test_click_to_move_triggers_stage_movement(self): ...

    # Full flow tests
    def test_button_to_stage_to_event_to_label(self): ...
    def test_home_button_homes_stage(self): ...
    def test_zero_button_zeros_stage(self): ...

    # Loading/Scanning position
    def test_loading_position_button_workflow(self): ...
    def test_scanning_position_button_workflow(self): ...

    # Coordinate conversion
    def test_um_to_mm_conversion_accurate(self): ...
    def test_position_display_precision(self): ...

    # Concurrent operations
    def test_rapid_button_clicks(self): ...
    def test_movement_during_position_update(self): ...
```

### 5.5 E2E Workflow Test Specifications

#### test_live_acquisition.py (~12 tests)

```python
"""End-to-end tests for live acquisition workflow."""

class TestLiveAcquisitionWorkflow:
    # Basic live workflow
    def test_start_live_receives_frames(self): ...
    def test_stop_live_stops_frames(self): ...

    # Configuration during live
    def test_exposure_change_during_live(self): ...
    def test_gain_change_during_live(self): ...
    def test_configuration_switch_during_live(self): ...

    # Frame rate
    def test_frame_rate_matches_trigger_fps(self): ...
    def test_frame_rate_with_different_exposures(self): ...

    # Display integration
    def test_frames_displayed_in_viewer(self): ...
    def test_stats_updated_during_live(self): ...

    # Error recovery
    def test_recovery_from_camera_timeout(self): ...
    def test_restart_after_error(self): ...
```

#### test_multipoint_acquisition.py (~15 tests)

```python
"""End-to-end tests for multipoint acquisition workflow."""

class TestMultipointAcquisitionWorkflow:
    # Basic acquisition
    def test_single_point_acquisition(self): ...
    def test_multiple_point_acquisition(self): ...

    # Z-stack
    def test_z_stack_acquisition(self): ...
    def test_z_stack_with_autofocus(self): ...

    # Multi-channel
    def test_multi_channel_acquisition(self): ...
    def test_channel_switching_timing(self): ...

    # Progress tracking
    def test_progress_events_emitted(self): ...
    def test_image_count_matches_expected(self): ...

    # Abort handling
    def test_abort_mid_acquisition(self): ...
    def test_resume_after_abort(self): ...

    # File output
    def test_images_saved_to_correct_location(self): ...
    def test_metadata_saved_correctly(self): ...

    # Error handling
    def test_camera_error_during_acquisition(self): ...
    def test_stage_error_during_acquisition(self): ...
```

---

## Part 6: Test Metrics & Success Criteria

### Target Test Counts

| Category | Target | Priority |
|----------|--------|----------|
| Service Unit Tests | 75 | High |
| Service Integration Tests | 40 | High |
| Widget Unit Tests | 150 | High |
| Widget-Service Integration | 95 | Medium |
| E2E Workflow Tests | 57 | Medium |
| **Total** | **~420** | |

### Success Criteria

- [ ] All existing tests pass (0 regressions)
- [ ] All 3 currently broken tests fixed
- [ ] All new services have unit and integration tests
- [ ] All widgets refactored for event bus injection
- [ ] All widget categories have representative unit tests
- [ ] Key workflows have E2E coverage
- [ ] All tests run in CI (headless/offscreen)
- [ ] Test execution time < 5 minutes for unit tests
- [ ] Test execution time < 15 minutes for full suite
- [ ] No flaky tests (deterministic, isolated)
- [ ] Test coverage report shows > 70% line coverage for services
- [ ] Test coverage report shows > 50% line coverage for widgets

### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Qt event loop issues | Use `threading.Event` for async waits, avoid `processEvents()` |
| Flaky timing tests | Use generous timeouts, mock time where possible |
| CI environment differences | Use offscreen platform, headless mode |
| Simulator incompleteness | Create minimal stubs, document gaps |
| Large scope | Prioritize service and core widget tests first |

---

## Appendix A: Critical File Paths

### Files to Modify
- `tests/conftest.py` - Enhanced fixtures
- `control/widgets/stage/navigation.py` - Event bus injection
- `control/widgets/hardware/dac.py` - Event bus injection
- `control/widgets/hardware/trigger.py` - Event bus injection
- `control/widgets/camera/settings.py` - Event bus injection
- `control/widgets/camera/live_control.py` - Event bus injection
- `pyproject.toml` - pytest configuration

### Files to Create
- `tests/utils/__init__.py`
- `tests/utils/wait_helpers.py`
- `tests/utils/widget_test_helpers.py`
- `tests/unit/squid/services/test_trigger_service.py`
- `tests/unit/squid/services/test_microscope_mode_service.py`
- `tests/unit/squid/services/test_live_service.py`
- All widget test files (see directory structure)
- All integration test files (see directory structure)
- All E2E test files (see directory structure)
- `control/peripherals/lighting/celesta_simulation.py`
- `control/peripherals/lighting/andor_simulation.py`

### Existing Files to Enhance
- `control/peripherals/cameras/camera_utils.py` - SimulatedCamera enhancements
- `control/peripherals/stage/simulated.py` - SimulatedStage enhancements
- `control/peripherals/stage/serial.py` - SimSerial command verification
- `control/peripherals/fluidics.py` - Enhanced simulation

---

## Appendix B: Event Bus Commands and State Events Reference

### Commands (GUI -> Service)

| Event | Parameters | Handler Service |
|-------|------------|-----------------|
| `SetExposureTimeCommand` | exposure_time_ms: float | CameraService |
| `SetAnalogGainCommand` | gain: float | CameraService |
| `SetDACCommand` | channel: int, value: float | PeripheralService |
| `StartCameraTriggerCommand` | - | TriggerService |
| `StopCameraTriggerCommand` | - | TriggerService |
| `SetCameraTriggerFrequencyCommand` | fps: float | TriggerService |
| `TurnOnAFLaserCommand` | wait_for_completion: bool | PeripheralService |
| `TurnOffAFLaserCommand` | wait_for_completion: bool | PeripheralService |
| `MoveStageCommand` | axis: str, distance_mm: float | StageService |
| `MoveStageToCommand` | x/y/z_mm: Optional[float] | StageService |
| `HomeStageCommand` | x/y/z/theta: bool | StageService |
| `ZeroStageCommand` | x/y/z/theta: bool | StageService |
| `MoveStageToLoadingPositionCommand` | blocking, callback, is_wellplate | StageService |
| `MoveStageToScanningPositionCommand` | blocking, callback, is_wellplate | StageService |
| `StartLiveCommand` | configuration: Optional[str] | LiveService |
| `StopLiveCommand` | - | LiveService |
| `SetTriggerModeCommand` | mode: str | TriggerService |
| `SetTriggerFPSCommand` | fps: float | TriggerService |
| `SetMicroscopeModeCommand` | configuration_name, objective | MicroscopeModeService |

### State Events (Service -> GUI)

| Event | Parameters | Publishing Service |
|-------|------------|-------------------|
| `ExposureTimeChanged` | exposure_time_ms: float | CameraService |
| `AnalogGainChanged` | gain: float | CameraService |
| `StagePositionChanged` | x/y/z_mm, theta_rad | StageService |
| `LiveStateChanged` | is_live: bool, configuration | LiveService |
| `DACValueChanged` | channel: int, value: float | PeripheralService |
| `ROIChanged` | x_offset, y_offset, width, height | CameraService |
| `BinningChanged` | binning_x, binning_y | CameraService |
| `PixelFormatChanged` | pixel_format | CameraService |
| `TriggerModeChanged` | mode: str | TriggerService |
| `TriggerFPSChanged` | fps: float | TriggerService |
| `MicroscopeModeChanged` | configuration_name: str | MicroscopeModeService |
