# Phase 5: Widget Updates

**Purpose:** Update widgets to use EventBus for all communication. Widgets should not access hardware, services, or controllers directly.

**Prerequisites:** Phase 4 and 4B complete (acquisition and autofocus use services)

**Estimated Effort:** 2-3 days

---

## Overview

Widgets currently have a mix of:
- Direct hardware access (bad)
- Direct service calls (acceptable but not ideal)
- Direct controller method calls (bad)
- Event publishing/subscribing (good)

This phase standardizes all widgets to use **events only**.

**Target Pattern:**
```
User clicks button
    → Widget publishes Command event
    → Service/Controller handles event
    → Service/Controller publishes State event
    → Widget updates UI from State event
```

---

## Widgets to Update

Based on `inventory/HARDWARE_ACCESS_MAP.md`:

| Widget | File | Issue | Priority |
|--------|------|-------|----------|
| NavigationWidget | `stage/navigation.py` | Direct stage calls | High |
| StageUtils | `stage/utils.py` | Direct stage calls | High |
| AutoFocusWidget | `stage/autofocus.py` | Direct controller calls | Medium |
| LiveControlWidget | `camera/live_control.py` | Direct LiveController | High |
| CameraSettingsWidget | `camera/settings.py` | May have direct calls | Medium |
| TriggerControlWidget | `hardware/trigger.py` | Direct trigger calls | Medium |
| WellplateCalibration | `wellplate/calibration.py` | Direct stage calls | Medium |
| DACWidget | `hardware/dac.py` | Direct peripheral calls | Low |

---

## Task Checklist

### 5.1 Create Widget Base Pattern

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/base.py` (create if doesn't exist)

- [ ] Create base widget class with EventBus support
- [ ] Add helper methods for event subscription cleanup

```python
"""Base widget with EventBus support."""

from typing import TYPE_CHECKING, Callable, Any
from qtpy.QtWidgets import QWidget

if TYPE_CHECKING:
    from squid.events import EventBus, Event


class EventBusWidget(QWidget):
    """Base widget that communicates via EventBus only.

    Widgets extending this class should:
    1. Publish Command events when user interacts
    2. Subscribe to State events to update UI
    3. Never access hardware, services, or controllers directly
    """

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(parent)
        self._bus = event_bus
        self._subscriptions: list[tuple[type, Callable]] = []

    def _subscribe(self, event_type: type, handler: Callable) -> None:
        """Subscribe to an event and track for cleanup."""
        self._bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def _publish(self, event: "Event") -> None:
        """Publish an event."""
        self._bus.publish(event)

    def closeEvent(self, event) -> None:
        """Clean up subscriptions on close."""
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:
                pass  # Ignore cleanup errors
        super().closeEvent(event)
```

**Commit:** `feat(widgets): Add EventBusWidget base class`

---

### 5.2 Update NavigationWidget

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/stage/navigation.py`

- [ ] Add EventBus to constructor
- [ ] Replace direct stage calls with events
- [ ] Subscribe to position updates

**Current (problematic) pattern:**
```python
class NavigationWidget(QWidget):
    def __init__(self, stage, ...):
        self.stage = stage  # Direct hardware!

    def move_x_positive(self):
        self.stage.move_x(self.step_size)  # Direct call!
```

**Target pattern:**
```python
from squid.events import (
    MoveStageCommand,
    MoveStageRelativeCommand,
    StagePositionChanged,
    HomeStageCommand,
)
from control.widgets.base import EventBusWidget


class NavigationWidget(EventBusWidget):
    """Stage navigation controls using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to stage events."""
        self._subscribe(StagePositionChanged, self._on_position_changed)

    def _on_position_changed(self, event: StagePositionChanged) -> None:
        """Update position display."""
        self.label_x.setText(f"X: {event.x_mm:.3f}")
        self.label_y.setText(f"Y: {event.y_mm:.3f}")
        self.label_z.setText(f"Z: {event.z_mm:.3f}")

    # ========================================================================
    # Button handlers - publish Command events
    # ========================================================================

    def _on_move_x_positive(self) -> None:
        """Move stage +X."""
        step = self.spinbox_step_xy.value()
        self._publish(MoveStageRelativeCommand(x_mm=step))

    def _on_move_x_negative(self) -> None:
        """Move stage -X."""
        step = self.spinbox_step_xy.value()
        self._publish(MoveStageRelativeCommand(x_mm=-step))

    def _on_move_y_positive(self) -> None:
        """Move stage +Y."""
        step = self.spinbox_step_xy.value()
        self._publish(MoveStageRelativeCommand(y_mm=step))

    def _on_move_y_negative(self) -> None:
        """Move stage -Y."""
        step = self.spinbox_step_xy.value()
        self._publish(MoveStageRelativeCommand(y_mm=-step))

    def _on_move_z_positive(self) -> None:
        """Move stage +Z."""
        step = self.spinbox_step_z.value()
        self._publish(MoveStageRelativeCommand(z_mm=step))

    def _on_move_z_negative(self) -> None:
        """Move stage -Z."""
        step = self.spinbox_step_z.value()
        self._publish(MoveStageRelativeCommand(z_mm=-step))

    def _on_go_to_position(self) -> None:
        """Move to entered position."""
        x = self.spinbox_goto_x.value()
        y = self.spinbox_goto_y.value()
        z = self.spinbox_goto_z.value()
        self._publish(MoveStageCommand(x_mm=x, y_mm=y, z_mm=z))

    def _on_home(self) -> None:
        """Home all axes."""
        self._publish(HomeStageCommand())
```

**Verification:**
```bash
# Should return NO matches
grep -n "self\.stage\." control/widgets/stage/navigation.py
```

**Commit:** `refactor(widgets): Update NavigationWidget to use EventBus`

---

### 5.3 Update LiveControlWidget

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/camera/live_control.py`

- [ ] Add EventBus to constructor
- [ ] Replace direct LiveController calls with events
- [ ] Subscribe to live state changes

**Current (problematic) pattern:**
```python
class LiveControlWidget(QWidget):
    def __init__(self, liveController, ...):
        self.liveController = liveController  # Direct controller!

    def toggle_live(self):
        if self.is_live:
            self.liveController.stop_live()  # Direct call!
        else:
            self.liveController.start_live()  # Direct call!
```

**Target pattern:**
```python
from squid.events import (
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    TriggerModeChanged,
)
from control.widgets.base import EventBusWidget


class LiveControlWidget(EventBusWidget):
    """Live view controls using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._is_live = False
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to live view events."""
        self._subscribe(LiveStateChanged, self._on_live_state_changed)
        self._subscribe(TriggerModeChanged, self._on_trigger_mode_changed)

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Update UI when live state changes."""
        self._is_live = event.is_live
        self.btn_live.setText("Stop" if event.is_live else "Live")
        self.btn_live.setChecked(event.is_live)

    def _on_trigger_mode_changed(self, event: TriggerModeChanged) -> None:
        """Update UI when trigger mode changes."""
        self.combo_trigger_mode.setCurrentText(event.mode.name)

    # ========================================================================
    # Button handlers - publish Command events
    # ========================================================================

    def _on_live_clicked(self) -> None:
        """Toggle live view."""
        if self._is_live:
            self._publish(StopLiveCommand())
        else:
            self._publish(StartLiveCommand())

    def _on_trigger_mode_changed(self) -> None:
        """Trigger mode selection changed."""
        mode_name = self.combo_trigger_mode.currentText()
        self._publish(SetTriggerModeCommand(mode=mode_name))

    def _on_fps_changed(self) -> None:
        """FPS spinbox value changed."""
        fps = self.spinbox_fps.value()
        self._publish(SetTriggerFPSCommand(fps=fps))
```

**Verification:**
```bash
# Should return NO matches
grep -n "self\.liveController\." control/widgets/camera/live_control.py
grep -n "liveController\." control/widgets/camera/live_control.py
```

**Commit:** `refactor(widgets): Update LiveControlWidget to use EventBus`

---

### 5.4 Update CameraSettingsWidget

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/camera/settings.py`

- [ ] Replace direct camera/service calls with events
- [ ] Subscribe to camera state events

**Target pattern:**
```python
from squid.events import (
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    CameraSettingsChanged,
)
from control.widgets.base import EventBusWidget


class CameraSettingsWidget(EventBusWidget):
    """Camera settings controls using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to camera events."""
        self._subscribe(CameraSettingsChanged, self._on_settings_changed)

    def _on_settings_changed(self, event: CameraSettingsChanged) -> None:
        """Update UI when camera settings change."""
        if event.exposure_time_ms is not None:
            self.spinbox_exposure.blockSignals(True)
            self.spinbox_exposure.setValue(event.exposure_time_ms)
            self.spinbox_exposure.blockSignals(False)
        if event.analog_gain is not None:
            self.spinbox_gain.blockSignals(True)
            self.spinbox_gain.setValue(event.analog_gain)
            self.spinbox_gain.blockSignals(False)

    def _on_exposure_changed(self) -> None:
        """Exposure spinbox value changed."""
        exposure_ms = self.spinbox_exposure.value()
        self._publish(SetExposureTimeCommand(exposure_time_ms=exposure_ms))

    def _on_gain_changed(self) -> None:
        """Gain spinbox value changed."""
        gain = self.spinbox_gain.value()
        self._publish(SetAnalogGainCommand(gain=gain))
```

**Commit:** `refactor(widgets): Update CameraSettingsWidget to use EventBus`

---

### 5.5 Update AutoFocusWidget

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/stage/autofocus.py`

- [ ] Replace direct autofocus controller calls with events
- [ ] Subscribe to autofocus state events

**Target pattern:**
```python
from squid.events import (
    StartAutofocusCommand,
    StopAutofocusCommand,
    SetAutofocusParamsCommand,
    AutofocusProgress,
    AutofocusCompleted,
)
from control.widgets.base import EventBusWidget


class AutoFocusWidget(EventBusWidget):
    """Autofocus controls using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to autofocus events."""
        self._subscribe(AutofocusProgress, self._on_progress)
        self._subscribe(AutofocusCompleted, self._on_completed)

    def _on_progress(self, event: AutofocusProgress) -> None:
        """Update progress during autofocus."""
        progress = event.current_step / event.total_steps * 100
        self.progress_bar.setValue(int(progress))
        self.label_status.setText(f"Step {event.current_step}/{event.total_steps}")

    def _on_completed(self, event: AutofocusCompleted) -> None:
        """Update UI when autofocus completes."""
        self.progress_bar.setValue(100 if event.success else 0)
        if event.success:
            self.label_status.setText(f"Focus found at Z={event.z_position:.3f}")
        else:
            self.label_status.setText(f"Failed: {event.error}")
        self.btn_autofocus.setEnabled(True)

    def _on_autofocus_clicked(self) -> None:
        """Start autofocus."""
        self.btn_autofocus.setEnabled(False)
        self.progress_bar.setValue(0)

        # Get parameters from UI
        n_planes = self.spinbox_n_planes.value()
        delta_z = self.spinbox_delta_z.value()

        # Publish parameter update if needed
        self._publish(SetAutofocusParamsCommand(
            n_planes=n_planes,
            delta_z_um=delta_z,
        ))

        # Start autofocus
        self._publish(StartAutofocusCommand())

    def _on_stop_clicked(self) -> None:
        """Stop autofocus."""
        self._publish(StopAutofocusCommand())
```

**Commit:** `refactor(widgets): Update AutoFocusWidget to use EventBus`

---

### 5.6 Update TriggerControlWidget

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/hardware/trigger.py`

- [ ] Replace direct trigger calls with events
- [ ] Subscribe to trigger state events

**Target pattern:**
```python
from squid.events import (
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
)
from control.widgets.base import EventBusWidget


class TriggerControlWidget(EventBusWidget):
    """Hardware trigger controls using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to trigger events."""
        self._subscribe(TriggerModeChanged, self._on_mode_changed)
        self._subscribe(TriggerFPSChanged, self._on_fps_changed)

    def _on_mode_changed(self, event: TriggerModeChanged) -> None:
        """Update UI when trigger mode changes."""
        self.combo_mode.blockSignals(True)
        self.combo_mode.setCurrentText(event.mode.name)
        self.combo_mode.blockSignals(False)

    def _on_fps_changed(self, event: TriggerFPSChanged) -> None:
        """Update UI when trigger FPS changes."""
        self.spinbox_fps.blockSignals(True)
        self.spinbox_fps.setValue(event.fps)
        self.spinbox_fps.blockSignals(False)

    def _on_mode_selection_changed(self) -> None:
        """Mode combo changed."""
        mode = self.combo_mode.currentText()
        self._publish(SetTriggerModeCommand(mode=mode))

    def _on_fps_value_changed(self) -> None:
        """FPS spinbox changed."""
        fps = self.spinbox_fps.value()
        self._publish(SetTriggerFPSCommand(fps=fps))
```

**Commit:** `refactor(widgets): Update TriggerControlWidget to use EventBus`

---

### 5.7 Update WellplateCalibrationWidget

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/wellplate/calibration.py`

- [ ] Replace direct stage calls with events
- [ ] Subscribe to stage position events

**Target pattern:**
```python
from squid.events import (
    MoveStageCommand,
    StagePositionChanged,
    SaveWellplateCalibrationCommand,
)
from control.widgets.base import EventBusWidget


class WellplateCalibrationWidget(EventBusWidget):
    """Wellplate calibration using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._current_position = None
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to stage events."""
        self._subscribe(StagePositionChanged, self._on_position_changed)

    def _on_position_changed(self, event: StagePositionChanged) -> None:
        """Track current position for calibration."""
        self._current_position = (event.x_mm, event.y_mm, event.z_mm)
        self.label_current.setText(
            f"Current: ({event.x_mm:.3f}, {event.y_mm:.3f}, {event.z_mm:.3f})"
        )

    def _on_go_to_well(self, well_id: str) -> None:
        """Navigate to calibrated well position."""
        position = self._calibration.get_well_position(well_id)
        if position:
            self._publish(MoveStageCommand(
                x_mm=position.x,
                y_mm=position.y,
                z_mm=position.z,
            ))

    def _on_set_calibration_point(self, point_name: str) -> None:
        """Set calibration point to current position."""
        if self._current_position:
            self._calibration.set_point(point_name, self._current_position)
            self._update_calibration_display()

    def _on_save_calibration(self) -> None:
        """Save calibration."""
        self._publish(SaveWellplateCalibrationCommand(
            calibration=self._calibration,
        ))
```

**Commit:** `refactor(widgets): Update WellplateCalibrationWidget to use EventBus`

---

### 5.8 Update DACWidget (Lower Priority)

**File:** `/Users/wea/src/allenlab/Squid/software/control/widgets/hardware/dac.py`

- [ ] Replace direct peripheral calls with events
- [ ] Subscribe to DAC state events

**Target pattern:**
```python
from squid.events import (
    SetDACCommand,
    DACValueChanged,
)
from control.widgets.base import EventBusWidget


class DACWidget(EventBusWidget):
    """DAC control using EventBus."""

    def __init__(self, event_bus: "EventBus", parent=None):
        super().__init__(event_bus, parent)
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Subscribe to DAC events."""
        self._subscribe(DACValueChanged, self._on_value_changed)

    def _on_value_changed(self, event: DACValueChanged) -> None:
        """Update UI when DAC value changes."""
        slider = self._get_slider_for_channel(event.channel)
        if slider:
            slider.blockSignals(True)
            slider.setValue(int(event.value * 100))  # 0-1 to 0-100
            slider.blockSignals(False)

    def _on_slider_changed(self, channel: int, value: int) -> None:
        """DAC slider moved."""
        self._publish(SetDACCommand(
            channel=channel,
            value=value / 100.0,  # 0-100 to 0-1
        ))
```

**Commit:** `refactor(widgets): Update DACWidget to use EventBus`

---

### 5.9 Add Missing Events for Widgets

Some widgets may need new events. Add these to `squid/events.py`:

```python
# ============================================================================
# Stage Commands (if not already present)
# ============================================================================

@dataclass(frozen=True)
class MoveStageCommand(Event):
    """Move stage to absolute position."""
    x_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None


@dataclass(frozen=True)
class MoveStageRelativeCommand(Event):
    """Move stage relative to current position."""
    x_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None


@dataclass(frozen=True)
class HomeStageCommand(Event):
    """Home all stage axes."""
    pass


# ============================================================================
# DAC Commands
# ============================================================================

@dataclass(frozen=True)
class SetDACCommand(Event):
    """Set DAC channel value."""
    channel: int
    value: float  # 0.0 to 1.0


@dataclass(frozen=True)
class DACValueChanged(Event):
    """DAC value changed."""
    channel: int
    value: float


# ============================================================================
# Wellplate Commands
# ============================================================================

@dataclass(frozen=True)
class SaveWellplateCalibrationCommand(Event):
    """Save wellplate calibration."""
    calibration: object  # WellplateCalibration
```

**Commit:** `feat(events): Add widget-related events`

---

### 5.10 Write Tests for Widget Event Usage

**File:** `/Users/wea/src/allenlab/Squid/software/tests/unit/control/widgets/test_widget_events.py`

```python
"""Tests for widget event-based communication."""

import pytest
from unittest.mock import Mock, MagicMock, call
from qtpy.QtWidgets import QApplication

from squid.events import (
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    MoveStageCommand,
    MoveStageRelativeCommand,
    StagePositionChanged,
)


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication for widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def mock_event_bus():
    """Create mock event bus."""
    bus = Mock()
    bus.subscribe = Mock()
    bus.unsubscribe = Mock()
    bus.publish = Mock()
    return bus


class TestLiveControlWidget:
    """Test LiveControlWidget event usage."""

    def test_publishes_start_live_command(self, qapp, mock_event_bus):
        """Widget publishes StartLiveCommand when live button clicked."""
        from control.widgets.camera.live_control import LiveControlWidget

        widget = LiveControlWidget(event_bus=mock_event_bus)
        widget._is_live = False

        # Simulate button click
        widget._on_live_clicked()

        # Verify event published
        mock_event_bus.publish.assert_called_once()
        event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(event, StartLiveCommand)

    def test_publishes_stop_live_command(self, qapp, mock_event_bus):
        """Widget publishes StopLiveCommand when stopping live."""
        from control.widgets.camera.live_control import LiveControlWidget

        widget = LiveControlWidget(event_bus=mock_event_bus)
        widget._is_live = True

        # Simulate button click
        widget._on_live_clicked()

        # Verify event published
        mock_event_bus.publish.assert_called_once()
        event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(event, StopLiveCommand)

    def test_subscribes_to_live_state(self, qapp, mock_event_bus):
        """Widget subscribes to LiveStateChanged."""
        from control.widgets.camera.live_control import LiveControlWidget

        widget = LiveControlWidget(event_bus=mock_event_bus)

        # Verify subscription
        subscribe_calls = mock_event_bus.subscribe.call_args_list
        event_types = [call[0][0] for call in subscribe_calls]
        assert LiveStateChanged in event_types


class TestNavigationWidget:
    """Test NavigationWidget event usage."""

    def test_publishes_relative_move_command(self, qapp, mock_event_bus):
        """Widget publishes MoveStageRelativeCommand for jog buttons."""
        from control.widgets.stage.navigation import NavigationWidget

        widget = NavigationWidget(event_bus=mock_event_bus)
        widget.spinbox_step_xy = Mock(value=Mock(return_value=1.0))

        # Simulate +X button click
        widget._on_move_x_positive()

        # Verify event published
        mock_event_bus.publish.assert_called()
        event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(event, MoveStageRelativeCommand)
        assert event.x_mm == 1.0

    def test_publishes_absolute_move_command(self, qapp, mock_event_bus):
        """Widget publishes MoveStageCommand for go-to button."""
        from control.widgets.stage.navigation import NavigationWidget

        widget = NavigationWidget(event_bus=mock_event_bus)
        widget.spinbox_goto_x = Mock(value=Mock(return_value=10.0))
        widget.spinbox_goto_y = Mock(value=Mock(return_value=20.0))
        widget.spinbox_goto_z = Mock(value=Mock(return_value=5.0))

        # Simulate go-to button click
        widget._on_go_to_position()

        # Verify event published
        mock_event_bus.publish.assert_called()
        event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(event, MoveStageCommand)
        assert event.x_mm == 10.0
        assert event.y_mm == 20.0
        assert event.z_mm == 5.0

    def test_subscribes_to_position_changes(self, qapp, mock_event_bus):
        """Widget subscribes to StagePositionChanged."""
        from control.widgets.stage.navigation import NavigationWidget

        widget = NavigationWidget(event_bus=mock_event_bus)

        # Verify subscription
        subscribe_calls = mock_event_bus.subscribe.call_args_list
        event_types = [call[0][0] for call in subscribe_calls]
        assert StagePositionChanged in event_types


class TestWidgetNoDirectAccess:
    """Verify widgets don't have direct hardware access."""

    def test_live_widget_no_controller_reference(self, qapp, mock_event_bus):
        """LiveControlWidget should not have liveController attribute."""
        from control.widgets.camera.live_control import LiveControlWidget

        widget = LiveControlWidget(event_bus=mock_event_bus)

        assert not hasattr(widget, 'liveController')
        assert not hasattr(widget, 'camera')

    def test_navigation_widget_no_stage_reference(self, qapp, mock_event_bus):
        """NavigationWidget should not have stage attribute."""
        from control.widgets.stage.navigation import NavigationWidget

        widget = NavigationWidget(event_bus=mock_event_bus)

        assert not hasattr(widget, 'stage')
```

**Run tests:**
```bash
cd /Users/wea/src/allenlab/Squid/software
NUMBA_DISABLE_JIT=1 pytest tests/unit/control/widgets/test_widget_events.py -v
```

**Commit:** `test(widgets): Add tests for widget event communication`

---

## Verification Checklist

Before proceeding to Phase 6, verify:

- [ ] No direct stage access in widgets: `grep -rn "self\.stage\." control/widgets/` returns no matches
- [ ] No direct camera access in widgets: `grep -rn "self\.camera\." control/widgets/` returns no matches
- [ ] No direct controller access: `grep -rn "\.liveController\." control/widgets/` returns no matches
- [ ] All widgets have EventBus in constructor
- [ ] Tests pass: `NUMBA_DISABLE_JIT=1 pytest tests/unit/control/widgets/ -v`
- [ ] Application starts: `python main_hcs.py --simulation`
- [ ] UI interaction works (manual testing)

**Full verification command:**
```bash
cd /Users/wea/src/allenlab/Squid/software

echo "=== Checking for direct hardware access in widgets ==="
echo "Stage:" && grep -c "self\.stage\." control/widgets/**/*.py || echo "0"
echo "Camera:" && grep -c "self\.camera\." control/widgets/**/*.py || echo "0"
echo "LiveController:" && grep -c "\.liveController\." control/widgets/**/*.py || echo "0"
echo "Microcontroller:" && grep -c "microcontroller\." control/widgets/**/*.py || echo "0"

echo "=== Running widget tests ==="
NUMBA_DISABLE_JIT=1 pytest tests/unit/control/widgets/ -v
```

---

## Commit Summary

| Order | Commit Message | Files |
|-------|----------------|-------|
| 1 | `feat(widgets): Add EventBusWidget base class` | `control/widgets/base.py` |
| 2 | `refactor(widgets): Update NavigationWidget to use EventBus` | `stage/navigation.py` |
| 3 | `refactor(widgets): Update LiveControlWidget to use EventBus` | `camera/live_control.py` |
| 4 | `refactor(widgets): Update CameraSettingsWidget to use EventBus` | `camera/settings.py` |
| 5 | `refactor(widgets): Update AutoFocusWidget to use EventBus` | `stage/autofocus.py` |
| 6 | `refactor(widgets): Update TriggerControlWidget to use EventBus` | `hardware/trigger.py` |
| 7 | `refactor(widgets): Update WellplateCalibrationWidget to use EventBus` | `wellplate/calibration.py` |
| 8 | `refactor(widgets): Update DACWidget to use EventBus` | `hardware/dac.py` |
| 9 | `feat(events): Add widget-related events` | `squid/events.py` |
| 10 | `test(widgets): Add tests for widget event communication` | `tests/...` |

---

## Widget Pattern Reference

Use this pattern for all widgets:

```python
from control.widgets.base import EventBusWidget
from squid.events import SomeCommand, SomeStateEvent


class MyWidget(EventBusWidget):
    """Widget description."""

    def __init__(self, event_bus, parent=None):
        super().__init__(event_bus, parent)
        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _setup_ui(self):
        """Create UI elements."""
        pass

    def _connect_signals(self):
        """Connect Qt signals to handlers."""
        self.btn_action.clicked.connect(self._on_action)

    def _subscribe_events(self):
        """Subscribe to state events."""
        self._subscribe(SomeStateEvent, self._on_state_changed)

    def _on_state_changed(self, event: SomeStateEvent):
        """Handle state event - update UI."""
        self.label.setText(str(event.value))

    def _on_action(self):
        """Handle user action - publish command."""
        self._publish(SomeCommand(value=self.spinbox.value()))
```

---

## Next Steps

Once all checkmarks are complete, proceed to:
→ [PHASE_6_CLEANUP.md](./PHASE_6_CLEANUP.md)
