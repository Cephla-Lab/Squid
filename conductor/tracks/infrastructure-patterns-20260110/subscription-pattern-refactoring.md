# Subscription Pattern Refactoring

## Overview

Reduce code duplication in event bus subscription/unsubscription patterns across controllers, managers, and widgets.

**Priority order:**
1. Create `BaseController` for 10 controllers
2. Add subscription tracking to `StateMachine`
3. Create `BaseManager` for ~5 managers
4. Migrate controllers to `BaseController` (includes `_bus` → `_event_bus` rename)
5. Migrate managers to `BaseManager`
6. Widget base class consolidation (mixin + remove redundant calls)
7. Migrate remaining widgets to use base classes

---

## Current State Analysis

### Usage Statistics

| Pattern | Count |
|---------|-------|
| `auto_subscribe` usage in widgets | 33 files |
| Widgets using EventBus* base class | 12 files |
| Widgets NOT using base class | 21 files |
| Controllers with subscription pattern | 10 files |
| Managers with subscription pattern | 3 files |

---

## Phase 1: Create `BaseController`

**File:** `squid/backend/controllers/base.py` (new)

```python
"""Base class for controllers with event bus subscription support."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple, Callable

import squid.core.logging
from squid.core.events import auto_subscribe, auto_unsubscribe

if TYPE_CHECKING:
    from squid.core.events import EventBus


class BaseController:
    """Base class for controllers with event bus subscription support.

    Subclasses use @handles decorator on methods and get automatic
    subscription via auto_subscribe.

    Usage:
        class MyController(BaseController):
            def __init__(self, event_bus, other_deps):
                super().__init__(event_bus)
                self._other = other_deps

            @handles(SomeCommand)
            def _on_command(self, cmd: SomeCommand) -> None:
                ...
    """

    _event_bus: "EventBus"
    _log: "logging.Logger"
    _subscriptions: List[Tuple[type, Callable]]

    def __init__(self, event_bus: "EventBus") -> None:
        self._event_bus = event_bus
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._subscriptions = auto_subscribe(self, self._event_bus)

    def shutdown(self) -> None:
        """Unsubscribe from all events."""
        auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []
```

---

## Phase 2: Add Subscription Tracking to StateMachine

**File:** `squid/core/state_machine.py`

```python
# Add import at top
from squid.core.events import auto_subscribe, auto_unsubscribe

class StateMachine(ABC, Generic[S]):
    def __init__(
        self,
        initial_state: S,
        transitions: Dict[S, Set[S]],
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        # ... existing code ...

        # ADD: Subscription tracking with auto_subscribe
        self._subscriptions: List[Tuple[Type[Event], Callable]] = []
        if event_bus:
            self._subscriptions = auto_subscribe(self, event_bus)

    # ADD: New shutdown method
    def shutdown(self) -> None:
        """Unsubscribe from all events."""
        if self._event_bus:
            auto_unsubscribe(self._subscriptions, self._event_bus)
            self._subscriptions = []
```

---

## Phase 3: Create `BaseManager`

**File:** `squid/backend/managers/base.py` (new)

```python
"""Base class for managers with event bus subscription support."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple, Callable, Optional

import squid.core.logging
from squid.core.events import auto_subscribe, auto_unsubscribe

if TYPE_CHECKING:
    from squid.core.events import EventBus


class BaseManager:
    """Base class for managers with event bus subscription support.

    Subclasses use @handles decorator on methods and get automatic
    subscription via auto_subscribe.

    Supports optional event_bus (some managers may not need events).

    Usage:
        class MyManager(BaseManager):
            def __init__(self, event_bus=None, other_deps):
                super().__init__(event_bus)
                self._other = other_deps

            @handles(SomeCommand)
            def _on_command(self, cmd: SomeCommand) -> None:
                ...
    """

    _event_bus: Optional["EventBus"]
    _log: "logging.Logger"
    _subscriptions: List[Tuple[type, Callable]]

    def __init__(self, event_bus: Optional["EventBus"] = None) -> None:
        self._event_bus = event_bus
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._subscriptions = []
        if event_bus:
            self._subscriptions = auto_subscribe(self, event_bus)

    def shutdown(self) -> None:
        """Unsubscribe from all events."""
        if self._event_bus:
            auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []
```

---

## Phase 4: Migrate Controllers to BaseController

### Migration Pattern

```python
# Before:
class SomeController:
    def __init__(self, event_bus, other_deps):
        self._bus = event_bus  # or self._event_bus
        self._log = get_logger(...)
        self._subscriptions = auto_subscribe(self, self._bus)
        # ... other init ...

    def shutdown(self) -> None:
        auto_unsubscribe(self._subscriptions, self._bus)
        self._subscriptions = []

# After:
class SomeController(BaseController):
    def __init__(self, event_bus, other_deps):
        super().__init__(event_bus)  # Handles _event_bus, _log, _subscriptions
        # ... other init (no _bus, no _log, no _subscriptions) ...

    # shutdown() inherited from BaseController
    # If extra cleanup needed:
    # def shutdown(self) -> None:
    #     self.stop()  # extra logic
    #     super().shutdown()
```

### Controllers to Migrate (9 files)

| File | Uses `_bus` | Extra shutdown logic |
|------|-------------|---------------------|
| `multipoint/multi_point_controller.py` | `_event_bus` | Yes - complex |
| `microscope_mode_controller.py` | `_bus` | No |
| `tracking_controller.py` | `_bus` | No |
| `autofocus/auto_focus_controller.py` | `_event_bus` | Yes - `stop()` |
| `autofocus/laser_auto_focus_controller.py` | `_event_bus` | Yes - `stop()` |
| `autofocus/continuous_focus_lock.py` | `_event_bus` | Yes - `stop()` |
| `autofocus/focus_lock_simulator.py` | `_event_bus` | Yes - `stop()` |
| `image_click_controller.py` | `_bus` | No |
| `peripherals_controller.py` | `_bus` | No |

### LiveController (special case)

`live_controller.py` extends `StateMachine`, so it gets subscription tracking from Phase 2. Just remove the manual auto_subscribe call.

---

## Phase 5: Migrate Managers to BaseManager

### Managers to Migrate (3 files)

| File | Uses `_bus` | Extra shutdown logic |
|------|-------------|---------------------|
| `navigation_state_service.py` | `_bus` | No |
| `channel_configuration_manager.py` | `_event_bus` | No |
| `scan_coordinates/scan_coordinates.py` | `_event_bus` | No |

---

## Phase 6: Widget Base Class Consolidation

### 6a: Create EventBusSubscriptionMixin

**File:** `squid/ui/widgets/base.py`

Replace the duplicated code in `EventBusWidget`, `EventBusFrame`, `EventBusDialog` with a mixin:

```python
from squid.core.events import auto_subscribe, auto_unsubscribe


class EventBusSubscriptionMixin:
    """Mixin providing event bus subscription management with @handles support."""

    _bus: "UIEventBus"
    _subscriptions: List[Tuple[type, Callable]]
    _state_cache: Dict[str, Any]

    def _init_subscriptions(self, event_bus: "UIEventBus") -> None:
        """Initialize event bus subscriptions. Call from __init__."""
        self._bus = event_bus
        self._state_cache = {}
        # Auto-subscribe all @handles decorated methods
        self._subscriptions = auto_subscribe(self, self._bus)

    def _publish(self, event: "Event") -> None:
        """Publish an event."""
        self._bus.publish(event)

    def _cache_state(self, key: str, value: Any) -> None:
        """Cache a state value for later retrieval."""
        self._state_cache[key] = value

    def _get_cached_state(self, key: str, default: Any = None) -> Any:
        """Get a cached state value."""
        return self._state_cache.get(key, default)

    def _cleanup_subscriptions(self) -> None:
        """Unsubscribe from all events. Call from closeEvent."""
        auto_unsubscribe(self._subscriptions, self._bus)
        self._subscriptions = []
        self._state_cache.clear()


class EventBusWidget(EventBusSubscriptionMixin, QWidget):
    """Base widget with event bus support and @handles decorator."""

    def __init__(self, event_bus: "UIEventBus", parent: Optional[QWidget] = None) -> None:
        QWidget.__init__(self, parent)
        self._init_subscriptions(event_bus)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cleanup_subscriptions()
        super().closeEvent(event)


class EventBusFrame(EventBusSubscriptionMixin, QFrame):
    """Base QFrame with event bus support and @handles decorator."""

    def __init__(self, event_bus: "UIEventBus", parent: Optional[QWidget] = None) -> None:
        QFrame.__init__(self, parent)
        self._init_subscriptions(event_bus)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cleanup_subscriptions()
        super().closeEvent(event)


class EventBusDialog(EventBusSubscriptionMixin, QDialog):
    """Base QDialog with event bus support and @handles decorator."""

    def __init__(self, event_bus: "UIEventBus", parent: Optional[QWidget] = None) -> None:
        QDialog.__init__(self, parent)
        self._init_subscriptions(event_bus)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cleanup_subscriptions()
        super().closeEvent(event)
```

### 6b: Remove Redundant auto_subscribe Calls

Widgets that already extend `EventBusWidget/Frame/Dialog` but ALSO call `auto_subscribe` - remove the redundant call.

**Files (12):**
1. `camera/live_control.py` - LiveControlWidget(EventBusFrame)
2. `camera/settings.py` - CameraSettingsWidget(EventBusFrame)
3. `hardware/trigger.py` - TriggerControlWidget(EventBusFrame)
4. `hardware/dac.py` - DACControWidget(EventBusFrame)
5. `hardware/focus_lock_status.py` - FocusLockStatusWidget(EventBusFrame)
6. `stage/navigation.py` - NavigationWidget(EventBusFrame)
7. `stage/autofocus.py` - AutoFocusWidget(EventBusFrame)
8. `stage/utils.py` - StageUtils(EventBusDialog)
9. `wellplate/calibration.py` - WellplateCalibration(EventBusDialog)
10. `wellplate/format.py` - WellplateFormatWidget(EventBusWidget)
11. `wellplate/sample_settings.py` - SampleSettingsWidget(EventBusWidget)

**Pattern:**
```python
# Before:
class TriggerControlWidget(EventBusFrame):
    def __init__(self, event_bus):
        super().__init__(event_bus)
        ...
        self._subscriptions = auto_subscribe(self, self._bus)  # DELETE THIS

# After:
class TriggerControlWidget(EventBusFrame):
    def __init__(self, event_bus):
        super().__init__(event_bus)  # auto_subscribe now happens here
        ...
```

---

## Phase 7: Migrate Remaining Widgets to Base Classes

Widgets that use `auto_subscribe` directly but don't extend a base class.

### Migration Pattern

```python
# Before:
class SomeWidget(QWidget):
    def __init__(self, event_bus):
        super().__init__()
        self._event_bus = event_bus
        self._subscriptions = auto_subscribe(self, self._event_bus)

    def closeEvent(self, event):
        auto_unsubscribe(self._subscriptions, self._event_bus)
        super().closeEvent(event)

# After:
class SomeWidget(EventBusWidget):
    def __init__(self, event_bus):
        super().__init__(event_bus)
        # No more boilerplate!
```

### Widgets to Migrate (21 files)

#### Display Widgets (6 files)
| File | Current Base | Target Base |
|------|--------------|-------------|
| `display/image_display.py` | QMainWindow | EventBusWidget (or keep QMainWindow + mixin) |
| `display/napari_live.py` | QWidget | EventBusWidget |
| `display/napari_mosaic.py` | QWidget | EventBusWidget |
| `display/napari_multichannel.py` | QWidget | EventBusWidget |
| `display/navigation_viewer.py` | QWidget | EventBusWidget |
| `display/focus_map.py` | QWidget | EventBusWidget |

#### Acquisition Widgets (3 files)
| File | Current Base | Target Base |
|------|--------------|-------------|
| `acquisition/wellplate_multipoint.py` | QWidget | EventBusWidget |
| `acquisition/flexible_multipoint.py` | QWidget | EventBusWidget |
| `acquisition/fluidics_multipoint.py` | QWidget | EventBusWidget |

#### Wellplate Widgets (2 files)
| File | Current Base | Target Base |
|------|--------------|-------------|
| `wellplate/well_selection.py` | QWidget | EventBusWidget |
| `wellplate/well_1536.py` | QWidget | EventBusWidget |

#### Tracking Widgets (3 files)
| File | Current Base | Target Base |
|------|--------------|-------------|
| `tracking/controller.py` | QWidget | EventBusWidget |
| `tracking/plate_reader.py` | QWidget | EventBusWidget |
| `tracking/displacement.py` | QWidget | EventBusWidget |

#### Hardware Widgets (3 files)
| File | Current Base | Target Base |
|------|--------------|-------------|
| `hardware/laser_autofocus.py` | QWidget | EventBusWidget |
| `hardware/confocal.py` | QWidget | EventBusWidget |
| `hardware/filter_controller.py` | QWidget | EventBusWidget |

#### Stage Widgets (1 file)
| File | Current Base | Target Base |
|------|--------------|-------------|
| `stage/piezo.py` | QWidget | EventBusWidget |

### Special Cases

**QMainWindow widgets:** `display/image_display.py` uses QMainWindow. Options:
1. Create `EventBusMainWindow` base class
2. Use mixin directly: `class ImageDisplayWindow(EventBusSubscriptionMixin, QMainWindow)`

**Multiple classes per file:** Some files have multiple widget classes (e.g., `laser_autofocus.py` has 2+). Each class needs migration.

---

## Phase 8: Standardize `_bus` → `_event_bus` in Controllers

Controllers that use `_bus` should be renamed to `_event_bus` for consistency with `BaseController`.

**Files (5):**
- `controllers/live_controller.py`
- `controllers/microscope_mode_controller.py`
- `controllers/tracking_controller.py`
- `controllers/image_click_controller.py`
- `controllers/peripherals_controller.py`

**Pattern:**
```bash
# In each file, find-replace:
self._bus → self._event_bus
```

**Note:** Widget base classes intentionally use `_bus` to distinguish UI event bus from backend event bus. Keep `_bus` in widgets.

---

## Files Summary

### New Files (2)
- `squid/backend/controllers/base.py`
- `squid/backend/managers/base.py`

### Modified Files

| Phase | Files | Count |
|-------|-------|-------|
| Phase 2 | `squid/core/state_machine.py` | 1 |
| Phase 4 | Controllers | 10 |
| Phase 5 | Managers | 3 |
| Phase 6a | `squid/ui/widgets/base.py` | 1 |
| Phase 6b | Widgets (redundant calls) | 11 |
| Phase 7 | Widgets (migration) | 21 |
| Phase 8 | Controllers (rename) | 5 |
| **Total** | | **52** |

---

## Impact Summary

| Before | After |
|--------|-------|
| 10 controllers with repeated pattern | 10 controllers inheriting BaseController/StateMachine |
| 3 managers with repeated pattern | 3 managers inheriting BaseManager |
| 3 widget base classes with ~50 lines each | 1 mixin + 3 thin base classes |
| 21 widgets with manual subscription | 21 widgets using base classes |
| ~400 lines of boilerplate | ~100 lines in base classes |
| Inconsistent `_bus` vs `_event_bus` | Standardized naming |

---

## Verification

```bash
cd software

# Run full test suite
pytest tests/ -v --simulation

# Type check
pyright src/squid/

# Start application in simulation mode
python main_hcs.py --simulation

# Test:
# - Live view start/stop (uses StateMachine)
# - Multipoint acquisition (uses BaseController)
# - Stage navigation (uses EventBusWidget)
# - Shutdown application (verify clean unsubscribe)
```
