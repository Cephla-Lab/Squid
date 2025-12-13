# Service Layer Architecture Implementation Guide

## Overview

This document describes how to implement a Service Layer that separates GUI widgets from hardware control. Follow these tasks in order. Each task is self-contained with tests.

## Final Target Architecture (Authoritative)

This document is subordinate to the actor-model convergence plan:
- `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_00_MASTER_PLAN.md`

Frozen invariants (acceptance criteria):
- **I1**: Only one backend control thread exists: the core queued `EventBus` dispatch thread.
- **I2**: UI thread never runs controller/service logic.
- **I3**: No control-plane callbacks exist anywhere; control-plane communication is EventBus-only.
- **I4**: Frames/images never go through EventBus; StreamHandler-only data plane.
- **I5**: Long operations never block the control thread; use workers and report via events.
- **I6**: Unsafe commands are backend-gated during acquisition/live conflicts.

### Control Plane vs Data Plane

- **Control plane**: commands + state events (EventBus only).
- **Data plane**: high-rate image frames (StreamHandler only).

### No shims / no compatibility layers

Do not add “compat”, “deprecated”, “dual path”, or “no-op” wiring to keep old flows alive. Delete old paths and update all publishers/subscribers.

**Principles:**
- **TDD**: Write tests first, then implementation
- **DRY**: Don't repeat yourself - one place for each piece of logic
- **YAGNI**: Don't build features until needed
- **Frequent commits**: One commit per task

---

## Current Problem

GUI widgets directly call hardware methods:

```python
# BAD: Widget knows about hardware internals
class CameraSettingsWidget:
    def __init__(self, camera):
        self.camera = camera

    def on_exposure_changed(self, value):
        self.camera.set_exposure_time(value)  # Direct hardware call
```

**Problems:**
1. Can't test widgets without hardware
2. Can't run headless (no GUI)
3. Changing hardware requires changing widgets
4. Business logic scattered across widgets

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  WIDGETS - Display UI, capture user input                   │
│  NO hardware calls. Calls services or publishes events.     │
├─────────────────────────────────────────────────────────────┤
│  EVENT BUS - Decoupled communication                        │
│  Commands flow down, state updates flow up                  │
├─────────────────────────────────────────────────────────────┤
│  SERVICES - Business logic, validation, hardware calls      │
│  One service per domain (camera, stage, etc.)               │
├─────────────────────────────────────────────────────────────┤
│  HARDWARE - AbstractCamera, AbstractStage, Microcontroller  │
└─────────────────────────────────────────────────────────────┘
```

---

## Existing Code Reference

Before starting, familiarize yourself with these files:

| File | Purpose |
|------|---------|
| `squid/events.py` | EventBus implementation (Phase 3) |
| `squid/abc.py` | Hardware abstract base classes |
| `squid/application.py` | ApplicationContext - manages lifecycle |
| `control/widgets/hardware.py` | DACControWidget (first refactor target) |
| `control/widgets/camera.py` | CameraSettingsWidget |
| `control/gui/qt_controllers.py` | Good pattern: Qt signal wrappers |

---

## Phase 1: Service Layer Foundation

### Task 1.1: Add Event Types to squid/events.py

**Files to modify:** `squid/events.py`
**Files to create:** `tests/squid/test_events_commands.py`

#### Step 1: Write tests first

```python
# tests/squid/test_events_commands.py
"""Tests for command and state event types."""
import pytest
from dataclasses import is_dataclass


class TestCommandEvents:
    """Test command event dataclasses."""

    def test_set_exposure_command(self):
        """SetExposureTimeCommand should be a dataclass with exposure_time_ms."""
        from squid.events import SetExposureTimeCommand

        cmd = SetExposureTimeCommand(exposure_time_ms=100.0)
        assert is_dataclass(cmd)
        assert cmd.exposure_time_ms == 100.0

    def test_set_dac_command(self):
        """SetDACCommand should have channel and value."""
        from squid.events import SetDACCommand

        cmd = SetDACCommand(channel=0, value=50.0)
        assert cmd.channel == 0
        assert cmd.value == 50.0

    def test_move_stage_command(self):
        """MoveStageCommand should have axis and distance."""
        from squid.events import MoveStageCommand

        cmd = MoveStageCommand(axis='x', distance_mm=1.5)
        assert cmd.axis == 'x'
        assert cmd.distance_mm == 1.5


class TestStateEvents:
    """Test state change event dataclasses."""

    def test_exposure_changed(self):
        """ExposureTimeChanged should have exposure_time_ms."""
        from squid.events import ExposureTimeChanged

        event = ExposureTimeChanged(exposure_time_ms=100.0)
        assert event.exposure_time_ms == 100.0

    def test_stage_position_changed(self):
        """StagePositionChanged should have x, y, z."""
        from squid.events import StagePositionChanged

        event = StagePositionChanged(x_mm=1.0, y_mm=2.0, z_mm=3.0)
        assert event.x_mm == 1.0
        assert event.y_mm == 2.0
        assert event.z_mm == 3.0
```

#### Step 2: Run tests (they should fail)

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/test_events_commands.py -v
```

#### Step 3: Implement in squid/events.py

Add after the existing `Event` class:

```python
# squid/events.py - ADD these after existing code

from dataclasses import dataclass
from typing import Optional

# ============================================================
# Command Events (GUI -> Service)
# ============================================================

@dataclass
class SetExposureTimeCommand(Event):
    """Command to set camera exposure time."""
    exposure_time_ms: float


@dataclass
class SetAnalogGainCommand(Event):
    """Command to set camera analog gain."""
    gain: float


@dataclass
class SetDACCommand(Event):
    """Command to set DAC output value."""
    channel: int
    value: float  # 0-100 percentage


@dataclass
class MoveStageCommand(Event):
    """Command to move stage by relative distance."""
    axis: str  # 'x', 'y', or 'z'
    distance_mm: float


@dataclass
class MoveStageToCommand(Event):
    """Command to move stage to absolute position."""
    x_mm: Optional[float] = None
    y_mm: Optional[float] = None
    z_mm: Optional[float] = None


@dataclass
class HomeStageCommand(Event):
    """Command to home stage axes."""
    x: bool = False
    y: bool = False
    z: bool = False


@dataclass
class SetIlluminationCommand(Event):
    """Command to set illumination."""
    channel: int
    intensity: float
    on: bool


@dataclass
class StartLiveCommand(Event):
    """Command to start live view."""
    configuration: Optional[str] = None


@dataclass
class StopLiveCommand(Event):
    """Command to stop live view."""
    pass


# ============================================================
# State Events (Service -> GUI)
# ============================================================

@dataclass
class ExposureTimeChanged(Event):
    """Notification that exposure time changed."""
    exposure_time_ms: float


@dataclass
class AnalogGainChanged(Event):
    """Notification that analog gain changed."""
    gain: float


@dataclass
class StagePositionChanged(Event):
    """Notification that stage position changed."""
    x_mm: float
    y_mm: float
    z_mm: float


@dataclass
class LiveStateChanged(Event):
    """Notification that live view state changed."""
    is_live: bool
    configuration: Optional[str] = None


@dataclass
class DACValueChanged(Event):
    """Notification that DAC value changed."""
    channel: int
    value: float
```

#### Step 4: Run tests (they should pass)

```bash
pytest tests/squid/test_events_commands.py -v
```

#### Step 5: Commit

```bash
git add squid/events.py tests/squid/test_events_commands.py
git commit -m "Add command and state event types for service layer"
```

---

### Task 1.2: Create BaseService Class

**Files to create:**
- `squid/services/__init__.py`
- `squid/services/base.py`
- `tests/squid/services/__init__.py`
- `tests/squid/services/test_base.py`

#### Step 1: Create directory structure

```bash
mkdir -p squid/services
mkdir -p tests/squid/services
touch squid/services/__init__.py
touch tests/squid/services/__init__.py
```

#### Step 2: Write tests first

```python
# tests/squid/services/test_base.py
"""Tests for BaseService class."""
import pytest
from unittest.mock import Mock, MagicMock


class TestBaseService:
    """Test suite for BaseService."""

    def test_init_requires_event_bus(self):
        """BaseService requires an EventBus."""
        from squid.services.base import BaseService
        from squid.events import EventBus

        bus = EventBus()

        # Can't instantiate ABC directly, need concrete class
        class ConcreteService(BaseService):
            pass

        service = ConcreteService(bus)
        assert service._event_bus is bus

    def test_subscribe_registers_handler(self):
        """subscribe() should register handler with event bus."""
        from squid.services.base import BaseService
        from squid.events import EventBus, Event
        from dataclasses import dataclass

        @dataclass
        class TestEvent(Event):
            value: int

        class ConcreteService(BaseService):
            def __init__(self, bus):
                super().__init__(bus)
                self.received = []
                self.subscribe(TestEvent, self.handle_test)

            def handle_test(self, event):
                self.received.append(event)

        bus = EventBus()
        service = ConcreteService(bus)

        # Publish event
        bus.publish(TestEvent(value=42))

        assert len(service.received) == 1
        assert service.received[0].value == 42

    def test_publish_sends_event(self):
        """publish() should send event through event bus."""
        from squid.services.base import BaseService
        from squid.events import EventBus, Event
        from dataclasses import dataclass

        @dataclass
        class TestEvent(Event):
            value: int

        class ConcreteService(BaseService):
            pass

        bus = EventBus()
        service = ConcreteService(bus)

        received = []
        bus.subscribe(TestEvent, lambda e: received.append(e))

        service.publish(TestEvent(value=99))

        assert len(received) == 1
        assert received[0].value == 99

    def test_shutdown_unsubscribes(self):
        """shutdown() should unsubscribe from all events."""
        from squid.services.base import BaseService
        from squid.events import EventBus, Event
        from dataclasses import dataclass

        @dataclass
        class TestEvent(Event):
            value: int

        class ConcreteService(BaseService):
            def __init__(self, bus):
                super().__init__(bus)
                self.received = []
                self.subscribe(TestEvent, self.handle_test)

            def handle_test(self, event):
                self.received.append(event)

        bus = EventBus()
        service = ConcreteService(bus)

        # Shutdown
        service.shutdown()

        # Publish should not reach service
        bus.publish(TestEvent(value=42))
        assert len(service.received) == 0
```

#### Step 3: Implement BaseService

```python
# squid/services/base.py
"""Base class for all services."""
from abc import ABC
from typing import List, Tuple, Type, Callable

import squid.logging
from squid.events import EventBus, Event


class BaseService(ABC):
    """
    Base class for service layer implementations.

    Services orchestrate hardware operations and manage state.
    They subscribe to command events and publish state events.

    Usage:
        class CameraService(BaseService):
            def __init__(self, camera, event_bus):
                super().__init__(event_bus)
                self._camera = camera
                self.subscribe(SetExposureCommand, self._on_set_exposure)

            def _on_set_exposure(self, event):
                self._camera.set_exposure_time(event.exposure_time_ms)
                self.publish(ExposureTimeChanged(event.exposure_time_ms))
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize service with event bus.

        Args:
            event_bus: EventBus for pub/sub communication
        """
        self._event_bus = event_bus
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._subscriptions: List[Tuple[Type[Event], Callable]] = []

    def subscribe(self, event_type: Type[Event], handler: Callable[[Event], None]):
        """
        Subscribe to an event type.

        Args:
            event_type: Type of event to subscribe to
            handler: Callable to handle events
        """
        self._event_bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))
        self._log.debug(f"Subscribed to {event_type.__name__}")

    def publish(self, event: Event):
        """
        Publish an event.

        Args:
            event: Event to publish
        """
        self._log.debug(f"Publishing {type(event).__name__}")
        self._event_bus.publish(event)

    def shutdown(self):
        """Unsubscribe from all events and clean up."""
        for event_type, handler in self._subscriptions:
            self._event_bus.unsubscribe(event_type, handler)
            self._log.debug(f"Unsubscribed from {event_type.__name__}")
        self._subscriptions.clear()
```

#### Step 4: Update squid/services/__init__.py

```python
# squid/services/__init__.py
"""Service layer for hardware orchestration."""
from squid.services.base import BaseService

__all__ = ['BaseService']
```

#### Step 5: Run tests

```bash
pytest tests/squid/services/test_base.py -v
```

#### Step 6: Commit

```bash
git add squid/services/ tests/squid/services/
git commit -m "Add BaseService class for service layer"
```

---

### Task 1.3: Create ServiceRegistry

**Files to modify:** `squid/services/__init__.py`
**Files to create:** `tests/squid/services/test_registry.py`

#### Step 1: Write tests

```python
# tests/squid/services/test_registry.py
"""Tests for ServiceRegistry."""
import pytest
from unittest.mock import Mock


class TestServiceRegistry:
    """Test suite for ServiceRegistry."""

    def test_register_and_get(self):
        """Should register and retrieve services by name."""
        from squid.services import ServiceRegistry, BaseService
        from squid.events import EventBus

        class MockService(BaseService):
            pass

        bus = EventBus()
        registry = ServiceRegistry(bus)
        service = MockService(bus)

        registry.register('test', service)

        assert registry.get('test') is service

    def test_get_unknown_returns_none(self):
        """get() should return None for unknown service."""
        from squid.services import ServiceRegistry
        from squid.events import EventBus

        registry = ServiceRegistry(EventBus())

        assert registry.get('unknown') is None

    def test_shutdown_calls_all_services(self):
        """shutdown() should call shutdown on all registered services."""
        from squid.services import ServiceRegistry, BaseService
        from squid.events import EventBus

        class MockService(BaseService):
            def __init__(self, bus):
                super().__init__(bus)
                self.shutdown_called = False

            def shutdown(self):
                super().shutdown()
                self.shutdown_called = True

        bus = EventBus()
        registry = ServiceRegistry(bus)

        service1 = MockService(bus)
        service2 = MockService(bus)
        registry.register('s1', service1)
        registry.register('s2', service2)

        registry.shutdown()

        assert service1.shutdown_called
        assert service2.shutdown_called
```

#### Step 2: Implement ServiceRegistry

```python
# squid/services/__init__.py
"""Service layer for hardware orchestration."""
from typing import Dict, Optional

from squid.services.base import BaseService
from squid.events import EventBus


class ServiceRegistry:
    """
    Central registry for all services.

    Usage:
        from squid.events import event_bus
        from squid.services import ServiceRegistry

        registry = ServiceRegistry(event_bus)
        registry.register('camera', CameraService(camera, event_bus))

        # Access services
        registry.camera.set_exposure_time(100)
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize registry.

        Args:
            event_bus: EventBus for service communication
        """
        self._event_bus = event_bus
        self._services: Dict[str, BaseService] = {}

    def register(self, name: str, service: BaseService):
        """
        Register a service.

        Args:
            name: Service name (e.g., 'camera', 'stage')
            service: Service instance
        """
        self._services[name] = service

    def get(self, name: str) -> Optional[BaseService]:
        """
        Get a service by name.

        Args:
            name: Service name

        Returns:
            Service instance or None if not found
        """
        return self._services.get(name)

    def shutdown(self):
        """Shutdown all services."""
        for service in self._services.values():
            service.shutdown()
        self._services.clear()


__all__ = ['BaseService', 'ServiceRegistry']
```

#### Step 3: Run tests and commit

```bash
pytest tests/squid/services/test_registry.py -v
git add squid/services/__init__.py tests/squid/services/test_registry.py
git commit -m "Add ServiceRegistry for service management"
```

---

## Phase 2: Create First Services

### Task 2.1: Create PeripheralService (Simplest First)

**Files to create:**
- `squid/services/peripheral_service.py`
- `tests/squid/services/test_peripheral_service.py`

This is the simplest service - only 2 methods to start.

#### Step 1: Write tests

```python
# tests/squid/services/test_peripheral_service.py
"""Tests for PeripheralService."""
import pytest
from unittest.mock import Mock, MagicMock


class TestPeripheralService:
    """Test suite for PeripheralService."""

    def test_set_dac_calls_hardware(self):
        """set_dac should call microcontroller.analog_write_onboard_DAC."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()

        service = PeripheralService(mock_mcu, bus)
        service.set_dac(channel=0, percentage=50.0)

        # 50% of 65535 = 32767 (rounded)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(0, 32768)

    def test_set_dac_clamps_percentage(self):
        """set_dac should clamp percentage to 0-100."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        # Over 100%
        service.set_dac(channel=0, percentage=150.0)
        mock_mcu.analog_write_onboard_DAC.assert_called_with(0, 65535)

        # Under 0%
        service.set_dac(channel=1, percentage=-10.0)
        mock_mcu.analog_write_onboard_DAC.assert_called_with(1, 0)

    def test_set_dac_publishes_event(self):
        """set_dac should publish DACValueChanged event."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus, DACValueChanged

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        received = []
        bus.subscribe(DACValueChanged, lambda e: received.append(e))

        service.set_dac(channel=0, percentage=50.0)

        assert len(received) == 1
        assert received[0].channel == 0
        assert received[0].value == 50.0

    def test_handles_set_dac_command(self):
        """Should respond to SetDACCommand events."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus, SetDACCommand

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        # Publish command
        bus.publish(SetDACCommand(channel=1, value=75.0))

        # Should have called hardware
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(1, 49152)
```

#### Step 2: Implement PeripheralService

```python
# squid/services/peripheral_service.py
"""Service for peripheral hardware (DAC, pins, etc.)."""
from squid.services.base import BaseService
from squid.events import EventBus, SetDACCommand, DACValueChanged


class PeripheralService(BaseService):
    """
    Service layer for peripheral hardware operations.

    Handles DAC control, pin settings, and other microcontroller peripherals.
    Widgets should use this service instead of calling microcontroller directly.
    """

    def __init__(self, microcontroller, event_bus: EventBus):
        """
        Initialize peripheral service.

        Args:
            microcontroller: Microcontroller instance
            event_bus: EventBus for communication
        """
        super().__init__(event_bus)
        self._microcontroller = microcontroller

        # Subscribe to commands
        self.subscribe(SetDACCommand, self._on_set_dac_command)

    def _on_set_dac_command(self, event: SetDACCommand):
        """Handle SetDACCommand event."""
        self.set_dac(event.channel, event.value)

    def set_dac(self, channel: int, percentage: float):
        """
        Set DAC output value.

        Args:
            channel: DAC channel (0 or 1)
            percentage: Output value as percentage (0-100)
        """
        # Clamp to valid range
        percentage = max(0.0, min(100.0, percentage))

        # Convert percentage to 16-bit value
        value = round(percentage * 65535 / 100)

        self._log.debug(f"Setting DAC{channel} to {percentage}% ({value})")
        self._microcontroller.analog_write_onboard_DAC(channel, value)

        # Notify listeners
        self.publish(DACValueChanged(channel=channel, value=percentage))
```

#### Step 3: Update squid/services/__init__.py

```python
# squid/services/__init__.py
"""Service layer for hardware orchestration."""
from typing import Dict, Optional

from squid.services.base import BaseService
from squid.services.peripheral_service import PeripheralService
from squid.events import EventBus

# ... rest of ServiceRegistry code ...

__all__ = ['BaseService', 'ServiceRegistry', 'PeripheralService']
```

#### Step 4: Run tests and commit

```bash
pytest tests/squid/services/test_peripheral_service.py -v
git add squid/services/ tests/squid/services/
git commit -m "Add PeripheralService for DAC control"
```

---

### Task 2.2: Create CameraService

**Files to create:**
- `squid/services/camera_service.py`
- `tests/squid/services/test_camera_service.py`

#### Step 1: Write tests

```python
# tests/squid/services/test_camera_service.py
"""Tests for CameraService."""
import pytest
from unittest.mock import Mock, MagicMock, PropertyMock


class TestCameraService:
    """Test suite for CameraService."""

    def test_set_exposure_time_calls_camera(self):
        """set_exposure_time should call camera.set_exposure_time."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)
        service.set_exposure_time(100.0)

        mock_camera.set_exposure_time.assert_called_once_with(100.0)

    def test_set_exposure_clamps_to_limits(self):
        """set_exposure_time should clamp to camera limits."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (1.0, 500.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        # Over max
        service.set_exposure_time(1000.0)
        mock_camera.set_exposure_time.assert_called_with(500.0)

        # Under min
        service.set_exposure_time(0.1)
        mock_camera.set_exposure_time.assert_called_with(1.0)

    def test_set_exposure_publishes_event(self):
        """set_exposure_time should publish ExposureTimeChanged."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, ExposureTimeChanged

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(ExposureTimeChanged, lambda e: received.append(e))

        service.set_exposure_time(100.0)

        assert len(received) == 1
        assert received[0].exposure_time_ms == 100.0

    def test_handles_set_exposure_command(self):
        """Should respond to SetExposureTimeCommand events."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, SetExposureTimeCommand

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        bus.publish(SetExposureTimeCommand(exposure_time_ms=200.0))

        mock_camera.set_exposure_time.assert_called_once_with(200.0)

    def test_get_exposure_time(self):
        """get_exposure_time should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_time.return_value = 50.0
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        assert service.get_exposure_time() == 50.0

    def test_set_analog_gain(self):
        """set_analog_gain should call camera and publish event."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, AnalogGainChanged

        mock_camera = Mock()
        mock_gain_range = Mock()
        mock_gain_range.min_gain = 0.0
        mock_gain_range.max_gain = 24.0
        mock_camera.get_gain_range.return_value = mock_gain_range
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(AnalogGainChanged, lambda e: received.append(e))

        service.set_analog_gain(12.0)

        mock_camera.set_analog_gain.assert_called_once_with(12.0)
        assert len(received) == 1
        assert received[0].gain == 12.0
```

#### Step 2: Implement CameraService

```python
# squid/services/camera_service.py
"""Service for camera operations."""
from typing import Tuple

from squid.services.base import BaseService
from squid.abc import AbstractCamera
from squid.events import (
    EventBus,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    ExposureTimeChanged,
    AnalogGainChanged,
)


class CameraService(BaseService):
    """
    Service layer for camera operations.

    Handles exposure, gain, binning, ROI, etc.
    Widgets should use this service instead of calling camera directly.
    """

    def __init__(self, camera: AbstractCamera, event_bus: EventBus):
        """
        Initialize camera service.

        Args:
            camera: AbstractCamera implementation
            event_bus: EventBus for communication
        """
        super().__init__(event_bus)
        self._camera = camera

        # Subscribe to commands
        self.subscribe(SetExposureTimeCommand, self._on_set_exposure_command)
        self.subscribe(SetAnalogGainCommand, self._on_set_gain_command)

    def _on_set_exposure_command(self, event: SetExposureTimeCommand):
        """Handle SetExposureTimeCommand event."""
        self.set_exposure_time(event.exposure_time_ms)

    def _on_set_gain_command(self, event: SetAnalogGainCommand):
        """Handle SetAnalogGainCommand event."""
        self.set_analog_gain(event.gain)

    def set_exposure_time(self, exposure_time_ms: float):
        """
        Set camera exposure time.

        Args:
            exposure_time_ms: Exposure time in milliseconds
        """
        # Get limits and clamp
        limits = self._camera.get_exposure_limits()
        exposure_time_ms = max(limits[0], min(limits[1], exposure_time_ms))

        self._log.debug(f"Setting exposure time to {exposure_time_ms} ms")
        self._camera.set_exposure_time(exposure_time_ms)

        self.publish(ExposureTimeChanged(exposure_time_ms=exposure_time_ms))

    def get_exposure_time(self) -> float:
        """Get current exposure time in milliseconds."""
        return self._camera.get_exposure_time()

    def get_exposure_limits(self) -> Tuple[float, float]:
        """Get exposure time limits (min, max) in milliseconds."""
        return self._camera.get_exposure_limits()

    def set_analog_gain(self, gain: float):
        """
        Set camera analog gain.

        Args:
            gain: Analog gain value
        """
        try:
            gain_range = self._camera.get_gain_range()
            gain = max(gain_range.min_gain, min(gain_range.max_gain, gain))

            self._log.debug(f"Setting analog gain to {gain}")
            self._camera.set_analog_gain(gain)

            self.publish(AnalogGainChanged(gain=gain))
        except NotImplementedError:
            self._log.warning("Camera does not support analog gain")

    def get_analog_gain(self) -> float:
        """Get current analog gain."""
        return self._camera.get_analog_gain()
```

#### Step 3: Update exports and run tests

```bash
# Add to squid/services/__init__.py exports
pytest tests/squid/services/test_camera_service.py -v
git add squid/services/ tests/squid/services/
git commit -m "Add CameraService for camera control"
```

---

### Task 2.3: Create StageService

**Files to create:**
- `squid/services/stage_service.py`
- `tests/squid/services/test_stage_service.py`

(Similar pattern - write tests first, then implement)

```python
# squid/services/stage_service.py
"""Service for stage operations."""
from typing import Optional

from squid.services.base import BaseService
from squid.abc import AbstractStage, Pos
from squid.events import (
    EventBus,
    MoveStageCommand,
    MoveStageToCommand,
    HomeStageCommand,
    StagePositionChanged,
)


class StageService(BaseService):
    """
    Service layer for stage operations.

    Handles movement, homing, zeroing.
    Widgets should use this service instead of calling stage directly.
    """

    def __init__(self, stage: AbstractStage, event_bus: EventBus):
        super().__init__(event_bus)
        self._stage = stage

        self.subscribe(MoveStageCommand, self._on_move_command)
        self.subscribe(MoveStageToCommand, self._on_move_to_command)
        self.subscribe(HomeStageCommand, self._on_home_command)

    def _on_move_command(self, event: MoveStageCommand):
        if event.axis == 'x':
            self.move_x(event.distance_mm)
        elif event.axis == 'y':
            self.move_y(event.distance_mm)
        elif event.axis == 'z':
            self.move_z(event.distance_mm)

    def _on_move_to_command(self, event: MoveStageToCommand):
        self.move_to(event.x_mm, event.y_mm, event.z_mm)

    def _on_home_command(self, event: HomeStageCommand):
        self.home(event.x, event.y, event.z)

    def move_x(self, distance_mm: float, blocking: bool = True):
        """Move X axis by relative distance."""
        self._stage.move_x(distance_mm, blocking)
        self._publish_position()

    def move_y(self, distance_mm: float, blocking: bool = True):
        """Move Y axis by relative distance."""
        self._stage.move_y(distance_mm, blocking)
        self._publish_position()

    def move_z(self, distance_mm: float, blocking: bool = True):
        """Move Z axis by relative distance."""
        self._stage.move_z(distance_mm, blocking)
        self._publish_position()

    def move_to(
        self,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        z_mm: Optional[float] = None,
        blocking: bool = True
    ):
        """Move to absolute position."""
        if x_mm is not None:
            self._stage.move_x_to(x_mm, blocking)
        if y_mm is not None:
            self._stage.move_y_to(y_mm, blocking)
        if z_mm is not None:
            self._stage.move_z_to(z_mm, blocking)
        self._publish_position()

    def get_position(self) -> Pos:
        """Get current position."""
        return self._stage.get_pos()

    def home(self, x: bool = False, y: bool = False, z: bool = False):
        """Home specified axes."""
        self._stage.home(x, y, z)
        self._publish_position()

    def zero(self, x: bool = False, y: bool = False, z: bool = False):
        """Zero specified axes."""
        self._stage.zero(x, y, z)
        self._publish_position()

    def _publish_position(self):
        """Publish current position."""
        pos = self._stage.get_pos()
        self.publish(StagePositionChanged(
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
            z_mm=pos.z_mm
        ))
```

**Commit:**
```bash
git add squid/services/ tests/squid/services/
git commit -m "Add StageService for stage control"
```

---

## Phase 3: Wire Up in ApplicationContext

### Task 3.1: Integrate Services into ApplicationContext

**Files to modify:** `squid/application.py`
**Files to create:** `tests/squid/test_application_services.py`

#### Step 1: Write tests

```python
# tests/squid/test_application_services.py
"""Tests for ApplicationContext service integration."""
import pytest


class TestApplicationContextServices:
    """Test ApplicationContext service integration."""

    def test_context_has_services(self):
        """ApplicationContext should create services."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services is not None
        context.shutdown()

    def test_services_has_camera(self):
        """Services should include camera service."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services.camera is not None
        context.shutdown()

    def test_services_has_stage(self):
        """Services should include stage service."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services.stage is not None
        context.shutdown()

    def test_services_has_peripheral(self):
        """Services should include peripheral service."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services.peripheral is not None
        context.shutdown()
```

#### Step 2: Update ApplicationContext

Add to `squid/application.py`:

```python
# In ApplicationContext.__init__, after _build_controllers():
self._build_services()

# Add new method:
def _build_services(self):
    """Build service layer."""
    from squid.services import ServiceRegistry, CameraService, StageService, PeripheralService
    from squid.events import event_bus  # global event bus

    self._log.info("Building services...")

    self._services = ServiceRegistry(event_bus)

    self._services.register('camera',
        CameraService(self._microscope.camera, event_bus))

    self._services.register('stage',
        StageService(self._microscope.stage, event_bus))

    self._services.register('peripheral',
        PeripheralService(
            self._microscope.low_level_drivers.microcontroller,
            event_bus
        ))

    self._log.info("Services built successfully")

# Add property:
@property
def services(self) -> "ServiceRegistry":
    """Get the service registry."""
    return self._services

# Update shutdown():
def shutdown(self):
    # ... existing code ...
    if self._services:
        self._services.shutdown()
        self._services = None
```

#### Step 3: Run tests and commit

```bash
pytest tests/squid/test_application_services.py -v
git add squid/application.py tests/squid/
git commit -m "Integrate services into ApplicationContext"
```

---

## Phase 4: Refactor Widgets

### Task 4.1: Refactor DACControWidget (Simplest)

**Files to modify:** `control/widgets/hardware.py`

This is the simplest widget - only 2 direct hardware calls.

#### Current code (lines ~875-895):

```python
# BEFORE - Direct hardware calls
class DACControWidget(QFrame):
    def __init__(self, microcontroller, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.microcontroller = microcontroller
        # ...

    def set_DAC0(self, value):
        self.microcontroller.analog_write_onboard_DAC(0, round(value * 65535 / 100))

    def set_DAC1(self, value):
        self.microcontroller.analog_write_onboard_DAC(1, round(value * 65535 / 100))
```

#### Refactored code:

```python
# AFTER - Uses service
from squid.services import PeripheralService
from squid.events import event_bus, DACValueChanged

class DACControWidget(QFrame):
    def __init__(
        self,
        microcontroller=None,  # Legacy - keep for backward compat
        peripheral_service: PeripheralService = None,
        *args, **kwargs
    ):
        super().__init__(*args, **kwargs)

        # Use service if provided, otherwise create from legacy param
        if peripheral_service is not None:
            self._service = peripheral_service
        elif microcontroller is not None:
            # Legacy mode - create service wrapper
            self._service = PeripheralService(microcontroller, event_bus)
        else:
            raise ValueError("Either peripheral_service or microcontroller required")

        # Subscribe to state updates
        event_bus.subscribe(DACValueChanged, self._on_dac_changed)

        # ... rest of __init__ ...

    def set_DAC0(self, value):
        """Set DAC0 output (0-100%)."""
        self._service.set_dac(channel=0, percentage=value)

    def set_DAC1(self, value):
        """Set DAC1 output (0-100%)."""
        self._service.set_dac(channel=1, percentage=value)

    def _on_dac_changed(self, event: DACValueChanged):
        """Handle DAC value changed event."""
        # Update UI without triggering signal loops
        if event.channel == 0:
            self.entry_DAC0.blockSignals(True)
            self.entry_DAC0.setValue(event.value)
            self.entry_DAC0.blockSignals(False)
        elif event.channel == 1:
            self.entry_DAC1.blockSignals(True)
            self.entry_DAC1.setValue(event.value)
            self.entry_DAC1.blockSignals(False)
```

#### Test the refactored widget:

```python
# tests/control/widgets/test_dac_widget.py
"""Tests for refactored DACControWidget."""
import pytest
from unittest.mock import Mock, patch


class TestDACControWidget:
    """Test DACControWidget uses service."""

    @pytest.fixture
    def mock_service(self):
        from squid.services import PeripheralService
        return Mock(spec=PeripheralService)

    def test_set_dac0_uses_service(self, mock_service):
        """set_DAC0 should call service.set_dac."""
        # Would need to mock Qt - simplified test
        pass  # Full test requires pytest-qt
```

#### Commit:

```bash
git add control/widgets/hardware.py tests/control/widgets/
git commit -m "Refactor DACControWidget to use PeripheralService"
```

---

### Task 4.2-4.5: Refactor Other Widgets

Follow the same pattern for:

1. **PiezoWidget** (`control/widgets/stage.py`)
2. **NavigationWidget** (`control/widgets/navigation.py` or wherever it is)
3. **CameraSettingsWidget** (`control/widgets/camera.py`)
4. **StageUtils** (`control/widgets/stage.py`)

Each widget:
1. Accept service in constructor
2. Replace direct hardware calls with service calls
3. Subscribe to state events for UI updates
4. Keep backward compatibility with legacy constructor

---

## Phase 5: Update Entry Point

### Task 5.1: Pass Services to GUI

**Files to modify:** `main_hcs.py`, `control/gui_hcs.py`

Update GUI initialization to use services:

```python
# main_hcs.py
context = ApplicationContext(simulation=args.simulation)

win = gui.HighContentScreeningGui(
    microscope=context.microscope,
    services=context.services,  # NEW
    is_simulation=args.simulation,
    live_only_mode=args.live_only
)
```

```python
# control/gui_hcs.py
class HighContentScreeningGui(QMainWindow):
    def __init__(
        self,
        microscope,
        services=None,  # NEW - ServiceRegistry
        is_simulation=False,
        live_only_mode=False,
        *args, **kwargs
    ):
        # ...
        self._services = services

        # Pass services to widgets
        self.dacWidget = DACControWidget(
            peripheral_service=services.peripheral if services else None,
            microcontroller=microscope.low_level_drivers.microcontroller
        )
```

---

## Testing Strategy

### Unit Tests
- Each service has its own test file
- Mock hardware, test service logic
- Test event publishing/subscribing

### Integration Tests
- Test services with simulated hardware
- Test event flow between services and widgets

### Manual Testing
```bash
# Run in simulation mode
python main_hcs.py --simulation

# Verify:
# 1. DAC sliders work
# 2. Camera settings work
# 3. Stage navigation works
# 4. No errors in console
```

---

## Commit Summary

| Task | Commit Message |
|------|----------------|
| 1.1 | Add command and state event types for service layer |
| 1.2 | Add BaseService class for service layer |
| 1.3 | Add ServiceRegistry for service management |
| 2.1 | Add PeripheralService for DAC control |
| 2.2 | Add CameraService for camera control |
| 2.3 | Add StageService for stage control |
| 3.1 | Integrate services into ApplicationContext |
| 4.1 | Refactor DACControWidget to use PeripheralService |
| 4.2 | Refactor PiezoWidget to use services |
| 4.3 | Refactor NavigationWidget to use StageService |
| 4.4 | Refactor CameraSettingsWidget to use CameraService |
| 4.5 | Refactor StageUtils to use StageService |
| 5.1 | Pass services to GUI from ApplicationContext |
