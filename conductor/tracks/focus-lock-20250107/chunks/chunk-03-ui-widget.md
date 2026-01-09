# Chunk 3: Focus Lock Status Widget

## Goal

Create a dockable status widget that displays focus lock state and controls. Testable with the simulator from Chunk 2.

## Dependencies

- Chunk 1 (Events and Configuration)
- Chunk 2 (Simulator) - for testing

## Files to Create

| File | Purpose |
|------|---------|
| `software/src/squid/ui/widgets/hardware/focus_lock_status.py` | Widget implementation |

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/ui/widgets/hardware/__init__.py` | Export widget |
| `software/src/squid/ui/widgets/__init__.py` | Lazy import |
| `software/src/squid/ui/gui/widget_factory.py` | Instantiate widget |
| `software/src/squid/ui/gui/layout_builder.py` | Dock widget |

## Deliverables

### Widget Layout

```
┌─────────────────────────────────┐
│ Focus Lock          [▼] [Off ▼] │  <- Collapse button, Mode selector
├─────────────────────────────────┤
│ ● LOCKED            Z: 150.0 μm │  <- Status LED, Z position
│ Error: +0.05 μm     SNR: 12.3   │  <- Z error, Signal quality
├─────────────────────────────────┤
│ Lock: ▓▓▓▓▓ 5/5                 │  <- Lock buffer bar
│ Piezo: ▓▓▓▓▓░░░░░ 150/300 μm    │  <- Piezo range bar (custom paint)
├─────────────────────────────────┤
│ [ Start Lock ]                  │  <- Action button (for Always On)
│ Adjust: 0.5 [Down] [Up]         │  <- Fine adjust lock target (relative)
└─────────────────────────────────┘
```

### Collapsed View

```
┌─────────────────────────────────┐
│ Focus Lock [▶]  ● LOCKED  150μm │
└─────────────────────────────────┘
```

### Widget Class

**IMPORTANT**: Widget only takes `UIEventBus`, not both event buses.

```python
from squid.ui.ui_event_bus import UIEventBus

class FocusLockStatusWidget(QWidget):
    """Dockable focus lock status and control widget.

    Uses UIEventBus for both subscriptions and publishing commands.
    Follow pattern from main_window.py for UIEventBus usage.
    """

    def __init__(
        self,
        ui_event_bus: UIEventBus,
        parent: QWidget = None,
    ):
        super().__init__(parent)
        self._ui_event_bus = ui_event_bus
        self._setup_ui()
        self._connect_events()

    def _connect_events(self) -> None:
        """Subscribe to focus lock events via UIEventBus."""
        self._ui_event_bus.subscribe(FocusLockStatusChanged, self._on_status_changed)
        self._ui_event_bus.subscribe(FocusLockMetricsUpdated, self._on_metrics_updated)
        self._ui_event_bus.subscribe(FocusLockWarning, self._on_warning)
        self._ui_event_bus.subscribe(FocusLockModeChanged, self._on_mode_changed)

    def _on_mode_dropdown_changed(self, mode: str) -> None:
        """User changed mode dropdown - publish command."""
        self._ui_event_bus.publish(SetFocusLockModeCommand(mode=mode))
```

### Custom Piezo Range Bar

QProgressBar cannot show warning zones. Use custom paint widget:

```python
class PiezoRangeBar(QWidget):
    """Custom painted bar showing piezo position with warning zones."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._position_um = 150.0
        self._range_um = (0.0, 300.0)
        self._warning_margin_um = 20.0
        self.setFixedHeight(20)

    def set_position(self, position_um: float) -> None:
        self._position_um = position_um
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        # Draw warning zones (red/yellow)
        # Draw safe zone (green)
        # Draw position indicator
        ...
```

### Layout Integration Notes

Handle different layout modes:

```python
# In layout_builder.py - handle live_only_mode
def _add_focus_lock_widget(self, focus_lock_widget):
    if self._live_only_mode:
        # imageDisplayTabs is a widget, not QTabWidget
        # Use QSplitter or dock differently
        pass
    else:
        # Normal tab mode - dock alongside tabs
        pass

    # Also handle multi-window mode
    if self._multi_window_mode:
        # May need different docking strategy
        pass
```

## Testing

```bash
cd software
python main_hcs.py --simulation

# Or standalone widget test (follow main_window.py pattern for UIEventBus)
```

## Completion Checklist

### Widget Structure
- [ ] Create `FocusLockStatusWidget` class
- [ ] **Only take `UIEventBus`** (not both event_bus and ui_event_bus)
- [ ] Follow `main_window.py` pattern for UIEventBus usage
- [ ] Implement expanded layout with all components
- [ ] Implement collapsed layout
- [ ] Add collapse/expand toggle

### Visual Components
- [ ] Status LED (green/yellow/red/gray)
- [ ] Z position label (μm)
- [ ] Z error label (μm, signed, color-coded)
- [ ] SNR label
- [ ] Lock buffer progress bar (N/N from config)
- [ ] **Custom paint** `PiezoRangeBar` with warning zones (NOT QProgressBar)

### Invalid Reading Display
When `is_good_reading=False` in `FocusLockMetricsUpdated`:
- [ ] Display Z error as "--" or "N/A" (not a stale/misleading value)
- [ ] Display SNR as "--" if below threshold
- [ ] Optionally dim or gray out the invalid metrics
- [ ] Status LED should reflect status from `FocusLockStatusChanged` (not metrics)

### Controls
- [ ] Mode dropdown (Off / Always On / Auto Lock)
- [ ] Start/Stop lock button
- [ ] Button visibility tied to mode
- [ ] Fine adjust controls (step + up/down) publish relative adjustments

### Event Handling
- [ ] Subscribe to `FocusLockStatusChanged` via UIEventBus
- [ ] Subscribe to `FocusLockMetricsUpdated` via UIEventBus
- [ ] Subscribe to `FocusLockWarning` via UIEventBus
- [ ] Subscribe to `FocusLockModeChanged` via UIEventBus
- [ ] Update UI on main thread (UIEventBus handles this)

### Command Publishing
- [ ] Publish `SetFocusLockModeCommand` on dropdown change
- [ ] Publish `StartFocusLockCommand` on start button
- [ ] Publish `StopFocusLockCommand` on stop button
- [ ] Publish `AdjustFocusLockTargetCommand` on fine adjust up/down

### Conflict Prevention (UI-level)
- [ ] When lock is running (`is_locked=True` or `status="searching"`):
  - Consider disabling Laser AF control widget's "Move to Target" button
  - Or display a warning when user attempts conflicting action
- [ ] Subscribe to `FocusLockWarning(warning_type="action_blocked")` to show user feedback
- [ ] **Note**: Backend handles the actual blocking (Chunk 10), UI just provides feedback

### Integration
- [ ] Export from `widgets/hardware/__init__.py`
- [ ] Add lazy import to `widgets/__init__.py`
- [ ] Instantiate in `widget_factory.py`
- [ ] Dock in `layout_builder.py`
- [ ] Handle `live_only_mode` (imageDisplayTabs is widget, not QTabWidget)
- [ ] Handle multi-window mode

### Testing
- [ ] Widget instantiates without crash
- [ ] Widget displays correctly with simulator
- [ ] Status LED changes color on status events
- [ ] Metrics update at ~10 Hz
- [ ] Mode switching works
- [ ] Collapse/expand works
- [ ] Warnings display (tooltip or indicator)

### Verification
- [ ] Run `cd software && python main_hcs.py --simulation`
- [ ] Widget visible alongside Live View
- [ ] Widget persists across tab switches
- [ ] Simulator + widget work together end-to-end
