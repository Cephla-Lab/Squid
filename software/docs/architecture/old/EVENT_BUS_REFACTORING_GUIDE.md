# Event Bus Refactoring Guide

## Overview

This guide walks you through refactoring GUI widgets to use the event bus instead of directly accessing hardware. The goal is to decouple the GUI from hardware, making the system more testable and maintainable.

**Time estimate**: Plan for multiple sessions. Each task is designed to be completed and committed independently.

---

## Architecture: Before and After

### Before (BAD)
```
┌────────────────┐
│  Widget        │
│                │───────────────────▶ Hardware
│  (direct call) │
└────────────────┘
```

Widget calls `self.liveController.start_live()` directly.

### After (GOOD)
```
┌────────────────┐     Command Event      ┌─────────────┐
│  Widget        │ ──────────────────────▶│  Service    │───▶ Hardware
└────────────────┘                        └──────┬──────┘
         ▲                                       │
         │            State Event                │
         └───────────────────────────────────────┘
```

Widget publishes `StartLiveCommand`. Service handles it, calls hardware, publishes `LiveStateChanged`. Widget subscribes to update UI.

---

## Key Files Reference

Before starting, familiarize yourself with these files:

| File | Purpose | Read First? |
|------|---------|-------------|
| `squid/events.py` | Event definitions and EventBus | YES |
| `squid/services/base.py` | BaseService class pattern | YES |
| `squid/services/camera_service.py` | Example service implementation | YES |
| `squid/services/live_service.py` | Simple service example | YES |
| `squid/application.py` | Where services are registered | YES |
| `tests/unit/squid/services/test_camera_service.py` | Test patterns | YES |

---

## Principles

1. **DRY**: Don't repeat yourself. If you see duplicate event handling, extract it.
2. **YAGNI**: You Aren't Gonna Need It. Only add events/services that are actually used.
3. **TDD**: Write the test first. It clarifies what you're building.
4. **Small commits**: One logical change per commit. Makes review easier.

---

## Task 1: Add Trigger Events

### Goal
Add events for trigger mode and FPS control.

### Files to Modify
- `squid/events.py`

### Test First
Create `tests/unit/squid/test_events.py` (if it doesn't exist):

```python
"""Tests for event definitions."""
from dataclasses import fields

def test_trigger_events_are_dataclasses():
    """Trigger events should be proper dataclasses."""
    from squid.events import SetTriggerModeCommand, SetTriggerFPSCommand
    from squid.events import TriggerModeChanged, TriggerFPSChanged

    # Commands have required fields
    assert 'mode' in [f.name for f in fields(SetTriggerModeCommand)]
    assert 'fps' in [f.name for f in fields(SetTriggerFPSCommand)]

    # State events have required fields
    assert 'mode' in [f.name for f in fields(TriggerModeChanged)]
    assert 'fps' in [f.name for f in fields(TriggerFPSChanged)]
```

### Implementation

Add to `squid/events.py` after the existing command events section:

```python
# ============================================================
# Trigger Control Commands
# ============================================================

@dataclass
class SetTriggerModeCommand(Event):
    """Command to set camera trigger mode."""
    mode: str  # "Software", "Hardware", "Continuous"


@dataclass
class SetTriggerFPSCommand(Event):
    """Command to set trigger frequency."""
    fps: float


# ============================================================
# Trigger State Events
# ============================================================

@dataclass
class TriggerModeChanged(Event):
    """Notification that trigger mode changed."""
    mode: str


@dataclass
class TriggerFPSChanged(Event):
    """Notification that trigger FPS changed."""
    fps: float
```

### Verify
```bash
python -m pytest tests/unit/squid/test_events.py -v
```

### Commit
```bash
git add squid/events.py tests/unit/squid/test_events.py
git commit -m "feat(events): Add trigger mode and FPS events"
```

---

## Task 2: Add Microscope Mode Events

### Goal
Add events for channel/microscope mode configuration.

### Files to Modify
- `squid/events.py`

### Test First
Add to `tests/unit/squid/test_events.py`:

```python
def test_microscope_mode_events():
    """Microscope mode events should have required fields."""
    from squid.events import SetMicroscopeModeCommand, MicroscopeModeChanged

    cmd = SetMicroscopeModeCommand(configuration_name="GFP", objective="20x")
    assert cmd.configuration_name == "GFP"
    assert cmd.objective == "20x"

    evt = MicroscopeModeChanged(configuration_name="GFP")
    assert evt.configuration_name == "GFP"
```

### Implementation

Add to `squid/events.py`:

```python
@dataclass
class SetMicroscopeModeCommand(Event):
    """Command to set microscope mode/channel configuration."""
    configuration_name: str
    objective: str


@dataclass
class MicroscopeModeChanged(Event):
    """Notification that microscope mode changed."""
    configuration_name: str
```

### Verify & Commit
```bash
python -m pytest tests/unit/squid/test_events.py -v
git add squid/events.py tests/unit/squid/test_events.py
git commit -m "feat(events): Add microscope mode events"
```

---

## Task 3: Create TriggerService

### Goal
Create a service that handles trigger mode and FPS commands.

### Files to Create
- `squid/services/trigger_service.py`
- `tests/unit/squid/services/test_trigger_service.py`

### Test First

Create `tests/unit/squid/services/test_trigger_service.py`:

```python
"""Tests for TriggerService."""
from unittest.mock import Mock


class TestTriggerService:
    """Test suite for TriggerService."""

    def test_handles_set_trigger_mode_command(self):
        """Should respond to SetTriggerModeCommand."""
        from squid.services.trigger_service import TriggerService
        from squid.events import EventBus, SetTriggerModeCommand

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)
        bus.publish(SetTriggerModeCommand(mode="Hardware"))

        mock_controller.set_trigger_mode.assert_called_once_with("Hardware")

    def test_publishes_trigger_mode_changed(self):
        """Should publish TriggerModeChanged after setting mode."""
        from squid.services.trigger_service import TriggerService
        from squid.events import EventBus, SetTriggerModeCommand, TriggerModeChanged

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)

        received = []
        bus.subscribe(TriggerModeChanged, lambda e: received.append(e))
        bus.publish(SetTriggerModeCommand(mode="Software"))

        assert len(received) == 1
        assert received[0].mode == "Software"

    def test_handles_set_trigger_fps_command(self):
        """Should respond to SetTriggerFPSCommand."""
        from squid.services.trigger_service import TriggerService
        from squid.events import EventBus, SetTriggerFPSCommand

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)
        bus.publish(SetTriggerFPSCommand(fps=30.0))

        mock_controller.set_trigger_fps.assert_called_once_with(30.0)

    def test_publishes_trigger_fps_changed(self):
        """Should publish TriggerFPSChanged after setting FPS."""
        from squid.services.trigger_service import TriggerService
        from squid.events import EventBus, SetTriggerFPSCommand, TriggerFPSChanged

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)

        received = []
        bus.subscribe(TriggerFPSChanged, lambda e: received.append(e))
        bus.publish(SetTriggerFPSCommand(fps=15.0))

        assert len(received) == 1
        assert received[0].fps == 15.0
```

### Implementation

Create `squid/services/trigger_service.py`:

```python
"""Service for camera trigger control."""
from __future__ import annotations
from typing import TYPE_CHECKING

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
)

if TYPE_CHECKING:
    from control.core.display import LiveController


class TriggerService(BaseService):
    """
    Service for camera trigger operations.

    Handles trigger mode (Software/Hardware/Continuous) and FPS settings.
    """

    def __init__(self, live_controller: "LiveController", event_bus: EventBus):
        super().__init__(event_bus)
        self._live_controller = live_controller

        self.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self.subscribe(SetTriggerFPSCommand, self._on_set_trigger_fps)

    def _on_set_trigger_mode(self, event: SetTriggerModeCommand) -> None:
        """Handle SetTriggerModeCommand."""
        self._log.info(f"Setting trigger mode to {event.mode}")
        self._live_controller.set_trigger_mode(event.mode)
        self.publish(TriggerModeChanged(mode=event.mode))

    def _on_set_trigger_fps(self, event: SetTriggerFPSCommand) -> None:
        """Handle SetTriggerFPSCommand."""
        self._log.info(f"Setting trigger FPS to {event.fps}")
        self._live_controller.set_trigger_fps(event.fps)
        self.publish(TriggerFPSChanged(fps=event.fps))
```

### Verify
```bash
python -m pytest tests/unit/squid/services/test_trigger_service.py -v
```

### Commit
```bash
git add squid/services/trigger_service.py tests/unit/squid/services/test_trigger_service.py
git commit -m "feat(services): Add TriggerService for trigger mode/FPS control"
```

---

## Task 4: Create MicroscopeModeService

### Goal
Create a service that handles microscope mode/channel configuration.

### Files to Create
- `squid/services/microscope_mode_service.py`
- `tests/unit/squid/services/test_microscope_mode_service.py`

### Test First

Create `tests/unit/squid/services/test_microscope_mode_service.py`:

```python
"""Tests for MicroscopeModeService."""
from unittest.mock import Mock


class TestMicroscopeModeService:
    """Test suite for MicroscopeModeService."""

    def test_handles_set_microscope_mode_command(self):
        """Should respond to SetMicroscopeModeCommand."""
        from squid.services.microscope_mode_service import MicroscopeModeService
        from squid.events import EventBus, SetMicroscopeModeCommand

        mock_controller = Mock()
        mock_config_manager = Mock()
        mock_config = Mock()
        mock_config_manager.get_channel_configuration_by_name.return_value = mock_config
        bus = EventBus()

        MicroscopeModeService(mock_controller, mock_config_manager, bus)
        bus.publish(SetMicroscopeModeCommand(configuration_name="GFP", objective="20x"))

        mock_config_manager.get_channel_configuration_by_name.assert_called_once_with("20x", "GFP")
        mock_controller.set_microscope_mode.assert_called_once_with(mock_config)

    def test_publishes_microscope_mode_changed(self):
        """Should publish MicroscopeModeChanged after setting mode."""
        from squid.services.microscope_mode_service import MicroscopeModeService
        from squid.events import EventBus, SetMicroscopeModeCommand, MicroscopeModeChanged

        mock_controller = Mock()
        mock_config_manager = Mock()
        mock_config = Mock()
        mock_config_manager.get_channel_configuration_by_name.return_value = mock_config
        bus = EventBus()

        MicroscopeModeService(mock_controller, mock_config_manager, bus)

        received = []
        bus.subscribe(MicroscopeModeChanged, lambda e: received.append(e))
        bus.publish(SetMicroscopeModeCommand(configuration_name="mCherry", objective="10x"))

        assert len(received) == 1
        assert received[0].configuration_name == "mCherry"
```

### Implementation

Create `squid/services/microscope_mode_service.py`:

```python
"""Service for microscope mode configuration."""
from __future__ import annotations
from typing import TYPE_CHECKING

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    SetMicroscopeModeCommand,
    MicroscopeModeChanged,
)

if TYPE_CHECKING:
    from control.core.display import LiveController
    from control.core.configuration import ChannelConfigurationManager


class MicroscopeModeService(BaseService):
    """
    Service for microscope mode/channel configuration.

    Handles setting the active channel configuration (exposure, gain, illumination).
    """

    def __init__(
        self,
        live_controller: "LiveController",
        channel_config_manager: "ChannelConfigurationManager",
        event_bus: EventBus,
    ):
        super().__init__(event_bus)
        self._live_controller = live_controller
        self._channel_config_manager = channel_config_manager

        self.subscribe(SetMicroscopeModeCommand, self._on_set_mode)

    def _on_set_mode(self, event: SetMicroscopeModeCommand) -> None:
        """Handle SetMicroscopeModeCommand."""
        self._log.info(f"Setting microscope mode to {event.configuration_name}")

        config = self._channel_config_manager.get_channel_configuration_by_name(
            event.objective, event.configuration_name
        )
        self._live_controller.set_microscope_mode(config)

        self.publish(MicroscopeModeChanged(configuration_name=event.configuration_name))
```

### Verify & Commit
```bash
python -m pytest tests/unit/squid/services/test_microscope_mode_service.py -v
git add squid/services/microscope_mode_service.py tests/unit/squid/services/test_microscope_mode_service.py
git commit -m "feat(services): Add MicroscopeModeService for channel configuration"
```

---

## Task 5: Register New Services

### Goal
Export and register the new services.

### Files to Modify
- `squid/services/__init__.py`
- `squid/application.py`

### Implementation

Update `squid/services/__init__.py`:

```python
from squid.services.trigger_service import TriggerService
from squid.services.microscope_mode_service import MicroscopeModeService

__all__ = [
    # ... existing exports ...
    "TriggerService",
    "MicroscopeModeService",
]
```

Update `squid/application.py` in `_build_services()`:

```python
from squid.services import (
    # ... existing imports ...
    TriggerService,
    MicroscopeModeService,
)

# In _build_services():
self._services.register(
    "trigger", TriggerService(self._microscope.live_controller, event_bus)
)

self._services.register(
    "microscope_mode",
    MicroscopeModeService(
        self._microscope.live_controller,
        self._microscope.channel_configuration_manager,
        event_bus,
    ),
)
```

### Verify
```bash
python -c "from squid.services import TriggerService, MicroscopeModeService; print('OK')"
```

### Commit
```bash
git add squid/services/__init__.py squid/application.py
git commit -m "feat(services): Register TriggerService and MicroscopeModeService"
```

---

## Task 6: Refactor LiveControlWidget

### Goal
Remove direct `liveController` calls from `LiveControlWidget`.

### Files to Modify
- `control/widgets/camera/_common.py` (add imports)
- `control/widgets/camera/live_control.py`

### What to Change

The widget currently has these direct calls:
```python
self.liveController.set_trigger_mode(...)
self.liveController.set_trigger_fps(...)
self.liveController.set_microscope_mode(...)
```

Replace with:
```python
event_bus.publish(SetTriggerModeCommand(mode=...))
event_bus.publish(SetTriggerFPSCommand(fps=...))
event_bus.publish(SetMicroscopeModeCommand(configuration_name=..., objective=...))
```

### Step-by-Step

1. **Add imports** to `control/widgets/camera/_common.py`:
```python
from squid.events import (
    # ... existing imports ...
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    SetMicroscopeModeCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
    MicroscopeModeChanged,
)
```

2. **Subscribe to state events** in `LiveControlWidget.__init__()`:
```python
event_bus.subscribe(TriggerModeChanged, self._on_trigger_mode_changed)
event_bus.subscribe(TriggerFPSChanged, self._on_trigger_fps_changed)
event_bus.subscribe(MicroscopeModeChanged, self._on_microscope_mode_changed)
```

3. **Add handlers**:
```python
def _on_trigger_mode_changed(self, event: TriggerModeChanged) -> None:
    """Handle trigger mode change from service."""
    self.dropdown_triggerManu.blockSignals(True)
    self.dropdown_triggerManu.setCurrentText(event.mode)
    self.dropdown_triggerManu.blockSignals(False)

def _on_trigger_fps_changed(self, event: TriggerFPSChanged) -> None:
    """Handle trigger FPS change from service."""
    self.entry_triggerFPS.blockSignals(True)
    self.entry_triggerFPS.setValue(event.fps)
    self.entry_triggerFPS.blockSignals(False)

def _on_microscope_mode_changed(self, event: MicroscopeModeChanged) -> None:
    """Handle microscope mode change from service."""
    self.dropdown_modeSelection.blockSignals(True)
    self.dropdown_modeSelection.setCurrentText(event.configuration_name)
    self.dropdown_modeSelection.blockSignals(False)
```

4. **Replace direct calls** in signal connections:

Before:
```python
self.dropdown_triggerManu.currentTextChanged.connect(
    self.liveController.set_trigger_mode
)
self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)
```

After:
```python
self.dropdown_triggerManu.currentTextChanged.connect(
    lambda mode: event_bus.publish(SetTriggerModeCommand(mode=mode))
)
self.entry_triggerFPS.valueChanged.connect(
    lambda fps: event_bus.publish(SetTriggerFPSCommand(fps=fps))
)
```

5. **Update `update_configuration()`**:

Before:
```python
self.liveController.set_microscope_mode(self.currentConfiguration)
```

After:
```python
event_bus.publish(SetMicroscopeModeCommand(
    configuration_name=self.currentConfiguration.name,
    objective=self.objectiveStore.current_objective,
))
```

### How to Test
```bash
# Run the app in simulation mode
python main_hcs.py --simulation

# Click through UI:
# 1. Change trigger mode dropdown - verify it works
# 2. Change FPS spinner - verify it works
# 3. Change microscope mode dropdown - verify it works
# 4. Start/stop live - verify it still works
```

### Commit
```bash
git add control/widgets/camera/_common.py control/widgets/camera/live_control.py
git commit -m "refactor(widgets): LiveControlWidget uses event bus for trigger/mode"
```

---

## Task 7: Refactor NapariLiveWidget

### Goal
Remove direct `liveController` calls from `NapariLiveWidget`.

### Files to Modify
- `control/widgets/display/napari_live.py`

### What to Change

Similar pattern to Task 6. Find all `self.liveController.*` calls and replace with event bus publishes.

Key methods to update:
- `toggle_live()` - already done via LiveService
- Direct `set_trigger_fps()` calls
- Direct `set_microscope_mode()` calls
- Direct `update_illumination()` calls

### Commit
```bash
git add control/widgets/display/napari_live.py
git commit -m "refactor(widgets): NapariLiveWidget uses event bus"
```

---

## Task 8: Refactor CalibrationWidget

### Goal
Remove direct `liveController` calls from wellplate calibration.

### Files to Modify
- `control/widgets/wellplate/calibration.py`

### What to Change

Replace:
```python
if self.liveController.is_live:
    self.liveController.stop_live()
```

With:
```python
# Track live state via subscription
if self._is_live:
    event_bus.publish(StopLiveCommand())
```

Add state tracking:
```python
def __init__(self, ...):
    # ...
    self._is_live = False
    event_bus.subscribe(LiveStateChanged, self._on_live_state_changed)

def _on_live_state_changed(self, event: LiveStateChanged) -> None:
    self._is_live = event.is_live
```

### Commit
```bash
git add control/widgets/wellplate/calibration.py
git commit -m "refactor(widgets): CalibrationWidget uses event bus for live control"
```

---

## Remaining Tasks (Same Pattern)

Apply the same pattern to these widgets:

| Task | Widget File | Direct Access to Remove |
|------|-------------|------------------------|
| 9 | `widgets/hardware/laser_autofocus.py` | `liveController.*` |
| 10 | `widgets/hardware/filter_controller.py` | `filterController.*` |
| 11 | `widgets/hardware/led_matrix.py` | `led_array.*` |
| 12 | `widgets/stage/autofocus.py` | `autofocusController.*` |
| 13 | `widgets/stage/utils.py` | `live_controller.*` |
| 14 | `widgets/acquisition/wellplate_multipoint.py` | `liveController.*` |

For each:
1. Create events if needed (FilterWheelService, LEDMatrixService, AutofocusService)
2. Create service if needed
3. Register service
4. Update widget to publish events and subscribe to state
5. Test manually
6. Commit

---

## Testing Checklist

After each refactor, verify:

- [ ] Unit tests pass: `python -m pytest tests/unit/squid/services/ -v`
- [ ] App starts: `python main_hcs.py --simulation`
- [ ] Widget functions correctly in UI
- [ ] Events appear in debug log: `event_bus.set_debug(True)`

### Enable Event Bus Debug Mode

Add to `main_hcs.py` temporarily:
```python
from squid.events import event_bus
event_bus.set_debug(True)
```

This will print every event to the console.

---

## Common Mistakes to Avoid

### 1. Forgetting to Block Signals
When updating UI from event handler, block signals to avoid loops:
```python
def _on_trigger_fps_changed(self, event):
    self.entry_triggerFPS.blockSignals(True)  # Block!
    self.entry_triggerFPS.setValue(event.fps)
    self.entry_triggerFPS.blockSignals(False)  # Unblock!
```

### 2. Not Unsubscribing
Services handle this via `BaseService.shutdown()`. Widgets should unsubscribe in `closeEvent()`:
```python
def closeEvent(self, event):
    event_bus.unsubscribe(LiveStateChanged, self._on_live_state_changed)
    super().closeEvent(event)
```

### 3. Heavy Objects in Events
Don't put numpy arrays or large objects in events. Use IDs and look up from a cache:
```python
# BAD
@dataclass
class ImageCaptured(Event):
    frame: np.ndarray  # Big!

# GOOD
@dataclass
class ImageCaptured(Event):
    frame_id: int  # Small, look up frame elsewhere
```

### 4. Circular Dependencies
Use `TYPE_CHECKING` for imports only needed for type hints:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control.core.display import LiveController
```

---

## Reference: Service Template

Copy this template for new services:

```python
"""Service for [description]."""
from __future__ import annotations
from typing import TYPE_CHECKING

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    # Import command events
    # Import state events
)

if TYPE_CHECKING:
    pass  # Add hardware types here


class MyService(BaseService):
    """
    Service for [description].

    Handles [what commands].
    """

    def __init__(self, hardware: "HardwareType", event_bus: EventBus):
        super().__init__(event_bus)
        self._hardware = hardware

        self.subscribe(SomeCommand, self._on_some_command)

    def _on_some_command(self, event: SomeCommand) -> None:
        """Handle SomeCommand."""
        self._log.info(f"Handling {event}")
        self._hardware.do_thing(event.value)
        self.publish(SomeStateChanged(value=event.value))
```

---

## Reference: Test Template

Copy this template for service tests:

```python
"""Tests for MyService."""
from unittest.mock import Mock


class TestMyService:
    """Test suite for MyService."""

    def test_handles_command(self):
        """Should respond to SomeCommand."""
        from squid.services.my_service import MyService
        from squid.events import EventBus, SomeCommand

        mock_hardware = Mock()
        bus = EventBus()

        MyService(mock_hardware, bus)
        bus.publish(SomeCommand(value=42))

        mock_hardware.do_thing.assert_called_once_with(42)

    def test_publishes_state_changed(self):
        """Should publish SomeStateChanged after handling command."""
        from squid.services.my_service import MyService
        from squid.events import EventBus, SomeCommand, SomeStateChanged

        mock_hardware = Mock()
        bus = EventBus()

        MyService(mock_hardware, bus)

        received = []
        bus.subscribe(SomeStateChanged, lambda e: received.append(e))
        bus.publish(SomeCommand(value=42))

        assert len(received) == 1
        assert received[0].value == 42
```
