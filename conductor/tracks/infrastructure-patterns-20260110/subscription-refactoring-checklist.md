# Subscription Pattern Refactoring - Implementation Checklist

## Phase 1: Create BaseController

- [ ] **Create `squid/backend/controllers/base.py`**
  - [ ] Add imports (TYPE_CHECKING, squid.core.logging, auto_subscribe, auto_unsubscribe)
  - [ ] Define `BaseController` class with:
    - [ ] `_event_bus: EventBus` type hint
    - [ ] `_log: Logger` type hint
    - [ ] `_subscriptions: List[Tuple[type, Callable]]` type hint
    - [ ] `__init__(self, event_bus)` that sets `_event_bus`, `_log`, calls `auto_subscribe`
    - [ ] `shutdown(self)` that calls `auto_unsubscribe` and clears list
  - [ ] Add docstring with usage example
- [ ] **Update `squid/backend/controllers/__init__.py`**
  - [ ] Export `BaseController`
- [ ] **Run tests:** `pytest tests/unit/squid/backend/controllers/ -v --simulation`

## Phase 2: Add Subscription Tracking to StateMachine

- [ ] **Modify `squid/core/state_machine.py`**
  - [ ] Add import: `from squid.core.events import auto_subscribe, auto_unsubscribe`
  - [ ] Add `_subscriptions` list initialization in `__init__`
  - [ ] Add `auto_subscribe` call if `event_bus` is not None
  - [ ] Add `shutdown()` method that calls `auto_unsubscribe`
- [ ] **Run tests:** `pytest tests/unit/squid/core/ -v --simulation`

## Phase 3: Create BaseManager

- [ ] **Create `squid/backend/managers/base.py`**
  - [ ] Same pattern as BaseController but with optional event_bus
  - [ ] Guard `auto_subscribe` and `auto_unsubscribe` with `if event_bus`
- [ ] **Update `squid/backend/managers/__init__.py`**
  - [ ] Export `BaseManager`
- [ ] **Run tests:** `pytest tests/unit/squid/backend/managers/ -v --simulation`

## Phase 4: Migrate Controllers to BaseController

### 4.1 Controllers with simple migration (no extra shutdown logic)

- [ ] **`microscope_mode_controller.py`**
  - [ ] Add import: `from squid.backend.controllers.base import BaseController`
  - [ ] Change class to: `class MicroscopeModeController(BaseController):`
  - [ ] Add `super().__init__(event_bus)` as first line of `__init__`
  - [ ] Remove `self._bus = event_bus` (now `self._event_bus`)
  - [ ] Find-replace: `self._bus` → `self._event_bus`
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Remove `shutdown()` method entirely (or call `super().shutdown()`)
  - [ ] Run tests

- [ ] **`tracking_controller.py`**
  - [ ] Add import: `from squid.backend.controllers.base import BaseController`
  - [ ] Change class to: `class TrackingControllerCore(BaseController):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._bus = event_bus`
  - [ ] Find-replace: `self._bus` → `self._event_bus`
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Simplify `shutdown()` to call `super().shutdown()`
  - [ ] Run tests

- [ ] **`image_click_controller.py`**
  - [ ] Add import: `from squid.backend.controllers.base import BaseController`
  - [ ] Change class to: `class ImageClickController(BaseController):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._bus = event_bus`
  - [ ] Find-replace: `self._bus` → `self._event_bus`
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Remove `shutdown()` method entirely
  - [ ] Run tests

- [ ] **`peripherals_controller.py`**
  - [ ] Add import: `from squid.backend.controllers.base import BaseController`
  - [ ] Change class to: `class PeripheralsController(BaseController):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._bus = event_bus`
  - [ ] Find-replace: `self._bus` → `self._event_bus`
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Simplify `shutdown()` to call `super().shutdown()`
  - [ ] Run tests

### 4.2 Controllers with extra shutdown logic

- [ ] **`autofocus/auto_focus_controller.py`**
  - [ ] Add import: `from squid.backend.controllers.base import BaseController`
  - [ ] Change class to: `class AutoFocusController(BaseController):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Update `shutdown()` to:
    ```python
    def shutdown(self) -> None:
        self.stop()  # Keep existing logic
        super().shutdown()
    ```
  - [ ] Run tests

- [ ] **`autofocus/laser_auto_focus_controller.py`**
  - [ ] Same pattern as auto_focus_controller.py
  - [ ] Run tests

- [ ] **`autofocus/continuous_focus_lock.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`autofocus/focus_lock_simulator.py`**
  - [ ] Same pattern
  - [ ] Run tests

### 4.3 MultiPointController (complex)

- [ ] **`multipoint/multi_point_controller.py`**
  - [ ] Add import: `from squid.backend.controllers.base import BaseController`
  - [ ] Change class to: `class MultiPointController(BaseController):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Update `shutdown()` to call `super().shutdown()` at end
  - [ ] Run tests: `pytest tests/unit/squid/backend/controllers/multipoint/ -v --simulation`

### 4.4 LiveController (uses StateMachine)

- [ ] **`live_controller.py`**
  - [ ] Remove `self._subscriptions = auto_subscribe(...)` (inherited from StateMachine)
  - [ ] Find-replace: `self._bus` → `self._event_bus` (uses `_bus` currently)
  - [ ] Update `shutdown()` to call `super().shutdown()` (or remove if only unsubscribing)
  - [ ] Run tests

### 4.5 Verification

- [ ] **Run all controller tests:** `pytest tests/unit/squid/backend/controllers/ -v --simulation`
- [ ] **Run integration tests:** `pytest tests/integration/ -v --simulation`

## Phase 5: Migrate Managers to BaseManager

- [ ] **`navigation_state_service.py`**
  - [ ] Add import: `from squid.backend.managers.base import BaseManager`
  - [ ] Change class to: `class NavigationViewerStateService(BaseManager):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._bus = event_bus`
  - [ ] Find-replace: `self._bus` → `self._event_bus`
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Remove `shutdown()` method
  - [ ] Run tests

- [ ] **`channel_configuration_manager.py`**
  - [ ] Add import: `from squid.backend.managers.base import BaseManager`
  - [ ] Change class to: `class ChannelConfigurationManager(BaseManager):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Remove `shutdown()` method
  - [ ] Run tests

- [ ] **`scan_coordinates/scan_coordinates.py`**
  - [ ] Add import: `from squid.backend.managers.base import BaseManager`
  - [ ] Change class to: `class ScanCoordinates(BaseManager):`
  - [ ] Add `super().__init__(event_bus)` as first line
  - [ ] Handle `set_event_bus()` method - may need special handling
  - [ ] Run tests

## Phase 6: Widget Base Class Consolidation

### 6a: Create EventBusSubscriptionMixin

- [ ] **Modify `squid/ui/widgets/base.py`**
  - [ ] Add import: `from squid.core.events import auto_subscribe, auto_unsubscribe`
  - [ ] Create `EventBusSubscriptionMixin` class with:
    - [ ] `_init_subscriptions(self, event_bus)` method
    - [ ] `_publish(self, event)` method
    - [ ] `_cache_state(self, key, value)` method
    - [ ] `_get_cached_state(self, key, default)` method
    - [ ] `_cleanup_subscriptions(self)` method
  - [ ] Update `EventBusWidget` to use mixin:
    - [ ] Change to: `class EventBusWidget(EventBusSubscriptionMixin, QWidget):`
    - [ ] Call `self._init_subscriptions(event_bus)` in `__init__`
  - [ ] Update `EventBusFrame` same pattern
  - [ ] Update `EventBusDialog` same pattern
  - [ ] Remove duplicate code from each class
- [ ] **Run tests:** `pytest tests/unit/squid/ui/ -v --simulation`

### 6b: Remove Redundant auto_subscribe Calls (11 files)

- [ ] **`camera/live_control.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Remove import of `auto_subscribe` if no longer used
  - [ ] Run tests

- [ ] **`camera/settings.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`hardware/trigger.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`hardware/dac.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`hardware/focus_lock_status.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`stage/navigation.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`stage/autofocus.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`stage/utils.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`wellplate/calibration.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`wellplate/format.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

- [ ] **`wellplate/sample_settings.py`**
  - [ ] Remove line: `self._subscriptions = auto_subscribe(self, self._bus)`
  - [ ] Run tests

## Phase 7: Migrate Remaining Widgets to Base Classes

### Display Widgets (6 files)

- [ ] **`display/image_display.py`**
  - [ ] Check what base classes are used (QMainWindow?)
  - [ ] If QMainWindow, create EventBusMainWindow or use mixin directly
  - [ ] Remove manual `auto_subscribe`/`auto_unsubscribe`
  - [ ] Remove manual `closeEvent` if only doing unsubscribe
  - [ ] Run tests

- [ ] **`display/napari_live.py`**
  - [ ] Change base class from QWidget to EventBusWidget
  - [ ] Update `super().__init__()` to `super().__init__(event_bus)`
  - [ ] Remove `self._event_bus = event_bus`
  - [ ] Change `self._event_bus` to `self._bus` (widget convention)
  - [ ] Remove `self._subscriptions = auto_subscribe(...)`
  - [ ] Remove/simplify `closeEvent` if only unsubscribing
  - [ ] Run tests

- [ ] **`display/napari_mosaic.py`**
  - [ ] Same pattern as napari_live.py
  - [ ] Run tests

- [ ] **`display/napari_multichannel.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`display/navigation_viewer.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`display/focus_map.py`**
  - [ ] Same pattern
  - [ ] Run tests

### Acquisition Widgets (3 files)

- [ ] **`acquisition/wellplate_multipoint.py`**
  - [ ] Change base class from QWidget to EventBusWidget
  - [ ] Update super().__init__ to pass event_bus
  - [ ] Remove subscription boilerplate
  - [ ] Run tests

- [ ] **`acquisition/flexible_multipoint.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`acquisition/fluidics_multipoint.py`**
  - [ ] Same pattern
  - [ ] Run tests

### Wellplate Widgets (2 files)

- [ ] **`wellplate/well_selection.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`wellplate/well_1536.py`**
  - [ ] Same pattern
  - [ ] Run tests

### Tracking Widgets (3 files)

- [ ] **`tracking/controller.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`tracking/plate_reader.py`**
  - [ ] Same pattern
  - [ ] Run tests

- [ ] **`tracking/displacement.py`**
  - [ ] Same pattern
  - [ ] Run tests

### Hardware Widgets (3 files)

- [ ] **`hardware/laser_autofocus.py`**
  - [ ] Note: May have multiple classes
  - [ ] Migrate each widget class
  - [ ] Run tests

- [ ] **`hardware/confocal.py`**
  - [ ] Note: May have multiple classes
  - [ ] Migrate each widget class
  - [ ] Run tests

- [ ] **`hardware/filter_controller.py`**
  - [ ] Same pattern
  - [ ] Run tests

### Stage Widgets (1 file)

- [ ] **`stage/piezo.py`**
  - [ ] Same pattern
  - [ ] Run tests

## Final Verification

- [ ] **Run full test suite:** `pytest tests/ -v --simulation`
- [ ] **Type check:** `pyright src/squid/`
- [ ] **Start application:** `python main_hcs.py --simulation`
- [ ] **Test live view start/stop**
- [ ] **Test stage navigation**
- [ ] **Test multipoint acquisition**
- [ ] **Test application shutdown (clean unsubscribe)**

## Summary

| Phase | Files | Status |
|-------|-------|--------|
| Phase 1: Create BaseController | 2 | ☐ |
| Phase 2: StateMachine | 1 | ☐ |
| Phase 3: Create BaseManager | 2 | ☐ |
| Phase 4: Migrate Controllers | 10 | ☐ |
| Phase 5: Migrate Managers | 3 | ☐ |
| Phase 6a: Widget Mixin | 1 | ☐ |
| Phase 6b: Remove Redundant | 11 | ☐ |
| Phase 7: Migrate Widgets | 21 | ☐ |
| **Total** | **51** | ☐ |
