# Actor Refactor Simplification — Step 00: Hard Audit (Cruft + Wiring)

This document is an audit output, not an execution plan. It is meant to prevent “paper-complete” refactors where the app compiles/tests but runtime wiring still relies on legacy signal/callback paths.

Two audit fronts:
1) **Deletions + exact replacements**
2) **End-to-end GUI signal/command chains** (no cheating via legacy bridges)

---

## 1) Deletions + Exact Replacements

### A. Second control-plane thread (must remove)

**Remove**
- `src/squid/core/actor/` (`BackendActor`, `BackendCommandRouter`, thread assertions)
- `ApplicationContext._build_backend_actor` + all `detach_event_bus_commands()` usage
- Any `subscribe_to_bus=False` constructor paths that exist only to support BackendActor routing

**Why**
- Creates two backend control threads (EventBus dispatch thread + BackendActor thread).
- Leads to split ownership (controllers on actor thread, services on EventBus thread).

**Replace with**
- Single queued `EventBus` dispatch thread as the only control-plane “actor”.
- If priority is required: add priority to EventBus queue (single thread), not a second thread.

---

### B. Lease-based coordinator (must remove)

**Remove**
- `src/squid/core/coordinator.py` (`ResourceCoordinator`, leases, watchdog)
- `ResourceLease` usage and controller `_acquire_resources/_release_resources` paths
- Lease events in `src/squid/core/events.py` if unused after removal

**Why**
- High complexity, low enforcement value: services don’t use it to block unsafe commands.

**Replace with**
- Minimal backend-owned **mode gate** (`IDLE|LIVE|ACQUIRING|ABORTING|ERROR`) enforced in service command handlers.

---

### C. Multipoint callback bridges (must remove)

**Remove**
- `on_new_image_fn`, `on_acquisition_start_fn`, `on_acquisition_finish_fn`, `on_current_configuration_fn`, `on_current_fov_fn`
  - `src/squid/ops/acquisition/multi_point_controller.py`
  - `src/squid/ops/acquisition/multi_point_worker.py`
- `src/squid/ui/gui/qt_controllers.py` (`MultiPointSignalBridge`)
- All `_multipoint_signal_bridge` wiring in `src/squid/ui/main_window.py` and `src/squid/ui/gui/signal_connector.py`

**Why**
- These are control-plane/data-plane mixed callbacks, and they break frontend/backend separation.
- They also fail at runtime when controllers are constructed in `ApplicationContext` without those callbacks (current observed failure mode: “Snap”/acquisition logs happen but GUI doesn’t update).

**Replace with**
- Data plane: route acquisition images into `StreamHandler` (or a dedicated acquisition stream channel) and let UI subscribe via `QtStreamHandler`.
- Control plane: use EventBus state/progress events (`AcquisitionStateChanged`, `AcquisitionProgress`, `ScanCoordinatesUpdated`, etc.).

---

### D. UI publishes UI-state commands (should remove)

**Currently present**
- `AcquisitionUIToggleCommand` published by acquisition widgets.

**Why this is “cheating”**
- UI is mutating UI state based on intent, not on backend truth. If backend rejects/aborts early, UI can desync.

**Replace with**
- UI should derive UI state from backend `AcquisitionStateChanged` (and/or a dedicated `AcquisitionUIStateChanged` produced by backend if needed).

---

### E. Phase 8 unused/dead artifacts (delete or wire fully; prefer delete)

**Likely unused**
- `WellplateConfigurationCommand` (declared; no publisher/subscriber found)
- `LiveScanGridCommand` (declared; no publisher/subscriber found)
- `WellSelectorVisibilityCommand` (declared; no publisher/subscriber found)
- `src/squid/ui/acquisition_ui_coordinator.py` (not constructed/used)

**Replace with**
- Either (A) delete them, or (B) fully wire them and remove duplicate code paths. Prefer (A) for simplicity.

---

### F. Command callbacks (must remove)

**Remove**
- `MoveStageToLoadingPositionCommand.callback`
- `MoveStageToScanningPositionCommand.callback`
- `StageService.move_to_loading_position(..., callback=...)` and scanning equivalent

**Replace with**
- Completion events:
  - `LoadingPositionReached`, `ScanningPositionReached` already exist and should be the only mechanism.
  - Add explicit failure events if needed.

---

### G. Tracking controller is a major architecture violation (refactor)

**Current**
- `src/squid/ops/tracking/tracking.py` uses Qt (`QObject`, `QThread`, Signals), raw hardware refs, and UI objects.

**Replace with**
- Backend `TrackingControllerCore` (no Qt, services only) + worker threads
- UI adapter only if needed

---

### H. Skipped tests / “papering over”

**Problem**
- Tests are skipped instead of being rewritten:
  - `tests/unit/control/gui/test_qt_signal_bridges.py` (module skip)
  - `tests/unit/control/core/test_multi_point_utils.py` (module skip)

**Replace with**
- New tests for:
  - EventBus-driven progress/state updates
  - StreamHandler-driven image display channel
  - No Qt signal bridges required for control plane

---

## 2) GUI Signal/Command Chain Audit (End-to-End)

The easiest way to enumerate “cheat chains” is `src/squid/ui/gui/signal_connector.py`, because it contains most cross-widget wiring. Today it explicitly states:
> “Qt signal connections are kept as primary mechanism for reliability.”

This contradicts the target architecture (widgets communicate via EventBus only).

### A. Acquisition: “Start Acquisition” (WellplateMultiPointWidget)

**Current chain (mixed, not clean)**
- UI button → `WellplateMultiPointWidget.toggle_acquisition()`
  - publishes backend commands (`SetAcquisition*`, `StartNewExperimentCommand`, `StartAcquisitionCommand`)
  - also emits Qt signals (`signal_acquisition_started`, `signal_acquisition_shape`)
  - also publishes `AcquisitionUIToggleCommand`
- `signal_connector.connect_acquisition_signals` connects `signal_acquisition_started` → `main_window.toggleAcquisitionStart`
- UI progress updates:
  - widget subscribes to `AcquisitionStateChanged` / `AcquisitionProgress` via injected bus (currently `UIEventBus` in widget_factory — good)
  - **BUT** global GUI display updates (mosaic tabs, napari layers, plots) still rely on `_multipoint_signal_bridge` callbacks.

**Observed failure mode**
- When controllers are injected from `ApplicationContext`, multipoint display callbacks are not wired → StartAcquisition logs happen but GUI display/plots don’t update.

**Correct chain (target)**
- UI button publishes only backend commands.
- Backend controller publishes:
  - `AcquisitionStateChanged` (authoritative)
  - `AcquisitionProgress` / `AcquisitionWorkerProgress`
- UI derives UI state exclusively from those events (no `AcquisitionUIToggleCommand`, no Qt signal for “started”).
- Images flow via StreamHandler only.

### B. Acquisition: “Snap Images”

**Current**
- `on_snap_images()` publishes `StartNewExperimentCommand` + `StartAcquisitionCommand(acquire_current_fov=True)`
- Visual output currently relies on multipoint display callbacks/bridges, which are not consistently wired.

**Correct**
- Same as acquisition: backend emits state/progress; images go to StreamHandler.

### C. Display wiring (Napari + non-Napari)

**Current**
- `signal_connector.connect_display_signals` wires both:
  - `streamHandler.image_to_display` (good)
  - `_multipoint_signal_bridge.image_to_display` (legacy callback bridge)

**Correct**
- Only StreamHandler produces images to display. Multipoint should feed StreamHandler directly.

### D. Wellplate format & navigation wiring

**Current**
- `signal_connector.connect_wellplate_signals` uses Qt signals to call methods on:
  - `navigationViewer.update_wellplate_settings`
  - `scanCoordinates.update_wellplate_settings`
  - `wellSelectionWidget.onWellplateChanged`
  - `main_window.onWellplateChanged`

**Correct**
- UI publishes a single wellplate-format command.
- Backend services/models update, then publish state events consumed by widgets.
- Avoid “fan-out” in signal_connector.

---

## 2.1 Full signal_connector audit (by section)

This is a direct reading of `src/squid/ui/gui/signal_connector.py` as of Phase 8.

### connect_acquisition_signals (must delete)

**Current**
- `*.signal_acquisition_started → main_window.toggleAcquisitionStart`
- `wellplateMultiPointWidget.signal_toggle_live_scan_grid → main_window.toggle_live_scan_grid`
- `fluidics_initialized_signal → multipointWithFluidicsWidget.init_fluidics`

**Why this is cheating**
- Uses Qt signals as the primary control plane.
- Duplicates the new EventBus `AcquisitionStateChanged` / `AcquisitionUIToggleCommand` mechanisms.

**Replace with**
- Remove all acquisition-widget Qt signals.
- Drive UI state from backend `AcquisitionStateChanged` (authoritative).
- If fluidics init needs coordination, publish a `FluidicsInitialized` event and subscribe in backend (or a UI-only handler that publishes backend commands).

### connect_wellplate_signals (must delete)

**Current**
- `WellplateFormatWidget.signalWellplateSettings` fans out directly into:
  - `navigationViewer.update_wellplate_settings`
  - `scanCoordinates.update_wellplate_settings`
  - `wellSelectionWidget.onWellplateChanged`
  - `main_window.onWellplateChanged`

**Why**
- Fan-out cross-widget wiring; violates “widgets via EventBus only”.

**Replace with**
- One EventBus command (e.g., `SetWellplateFormatCommand(format_name, ...)`) published by `WellplateFormatWidget`.
- Backend model/service updates scan coordinates + publishes `ScanCoordinatesUpdated`.
- UI widgets subscribe to those state events.

### connect_display_signals (must delete multipoint bridge parts)

**Current**
- Both `streamHandler.image_to_display` and `_multipoint_signal_bridge.image_to_display` feed the display.
- Napari click coordinates connect directly to `main_window.move_from_click_image`.

**Why**
- Multipoint display depends on a callback bridge.

**Replace with**
- Display images from StreamHandler only.
- Click-to-move is already event-driven via `ImageCoordinateClickedCommand` (keep that), but do not allow it to bypass backend gating.

### connect_navigation_signals (must delete bridge-dependent parts)

**Current**
- Uses `_multipoint_signal_bridge.signal_register_current_fov`, `.signal_current_configuration`, `.signal_set_display_tabs`, etc.

**Replace with**
- Replace these with EventBus state events and/or UI logic driven from StreamHandler metadata.

### connect_plot_signals (must delete)

**Current**
- Z plot is driven from `_multipoint_signal_bridge.signal_coordinates` and `signal_acquisition_progress`.

**Replace with**
- Subscribe to EventBus acquisition events (`AcquisitionWorkerProgress`, `AcquisitionProgress`, and/or a dedicated plot event published by backend).

### connect_live_control_signals (should delete)

**Current**
- LiveControlWidget Qt signals call CameraSettingsWidget methods directly.

**Why**
- Widget→widget direct calls; violates “EventBus only”.

**Replace with**
- LiveControlWidget publishes commands; CameraSettingsWidget renders state via EventBus updates.

### connect_laser_autofocus_signals (refactor required)

**Current**
- Uses Qt signals on `laserAutofocusController` (e.g., `signal_cross_correlation`, `image_to_display`).

**Why**
- Backend controller should not be Qt-dependent.

**Replace with**
- Laser AF publishes EventBus state events + StreamHandler images; UI subscribes via UIEventBus/QtStreamHandler.

### connect_confocal_signals (refactor required)

**Current**
- Confocal widget signals call backend objects directly (`channelConfigurationManager.toggle_confocal_widefield`, `liveControlWidget.select_new_microscope_mode_by_name`).

**Replace with**
- Publish commands via EventBus; backend controllers/services perform changes and publish state.

---

## Immediate conclusions

- There is still significant legacy Qt signal wiring acting as the “real” mechanism.
- Multipoint acquisition display is not consistently wired across “controllers constructed in UI” vs “controllers injected from ApplicationContext”.
- Several Phase 8 events/files are currently unused and should be deleted for simplicity.

This audit feeds directly into the simplification plan docs (Step 02/04/05/07/08).
