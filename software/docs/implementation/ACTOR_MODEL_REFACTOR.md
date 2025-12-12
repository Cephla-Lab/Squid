# Actor Model Architecture Refactor - Implementation Checklist

## Overview

Transform Squid from synchronous EventBus with mixed threading to a proper actor model:

- **UI Actor** (Qt main thread): Owns only widgets, publishes commands, subscribes to state events via UIEventBus
- **Backend Actor** (single control thread): Owns controllers and services, processes commands from queue
- **Data Plane**: Camera callbacks + workers for compute/I/O, communicate via messages only

**Core Invariants:**
1. UI thread never runs controller logic
2. No controller method invoked directly from worker via callbacks
3. All control-plane events are queued (ordering deterministic)
4. Data-plane (frames) never touches EventBus
5. Every acquisition event carries `experiment_id`

---

## Phase 1: Queued EventBus

### 1.1 Convert EventBus to Queued Dispatcher ✅

**File:** `src/squid/core/events.py`

- [x] Add `queue.Queue[Event]` to EventBus
- [x] Add `_dispatch_thread: threading.Thread`
- [x] Modify `publish()` to enqueue instead of direct dispatch
- [x] Add `_dispatch_loop()` method that drains queue
- [x] Add `start()` / `stop()` lifecycle methods
- [x] Ensure exceptions in handlers are caught/logged (don't stall queue)
- [x] Add unit tests for queued dispatch

**Key code change:**
```python
def publish(self, event: Event) -> None:
    """O(1) thread-safe enqueue. Never blocks."""
    self._queue.put(event)

def _dispatch_loop(self) -> None:
    while self._running:
        try:
            event = self._queue.get(timeout=0.1)
            self._dispatch(event)
        except queue.Empty:
            continue
```

### 1.2 Add experiment_id to Acquisition Events ✅

**File:** `src/squid/core/events.py`

- [x] Add `experiment_id: str` field to `AcquisitionStateChanged`
- [x] Add `experiment_id: str` field to `AcquisitionProgress`
- [x] Add `experiment_id: str` field to `AcquisitionRegionProgress`
- [x] Add `experiment_id: str` field to `AcquisitionStarted` (already had it)
- [x] Add `experiment_id: str` field to `AcquisitionFinished`
- [x] Update all places that publish these events

### 1.3 Thread Assertion Utilities ✅

**New File:** `src/squid/core/actor/thread_assertions.py`

- [x] Create file with module-level `_backend_thread` variable
- [x] Implement `set_backend_thread(thread)` function
- [x] Implement `assert_backend_thread(operation)` function
- [x] Implement `assert_not_backend_thread(operation)` function
- [x] Added `get_backend_thread()` and `clear_backend_thread()` helper functions
- [x] Created `src/squid/core/actor/__init__.py` with exports
- [x] Added comprehensive unit tests

---

## Phase 2: Backend Actor Infrastructure ✅

### 2.1 Create BackendActor Class ✅

**New File:** `src/squid/core/actor/backend_actor.py`

- [x] Create `CommandEnvelope` dataclass (command, priority, timestamp)
- [x] Create `PriorityCommandQueue` class
- [x] Create `BackendActor` class with:
  - [x] Constructor with worker_pool_size parameter
  - [x] `_command_queue: PriorityCommandQueue`
  - [x] `_worker_pool: ThreadPoolExecutor` for compute/IO
  - [x] `start()` method - spawns backend thread
  - [x] `stop(timeout_s)` method - graceful shutdown
  - [x] `enqueue(command, priority)` method
  - [x] `_run_loop()` method - main processing loop
  - [x] `_dispatch_command(command)` method - routes to registered handlers
  - [x] `register_handler()` / `unregister_handler()` methods
  - [x] `submit_work()` for offloading compute/IO to thread pool
  - [x] `drain()` for synchronous test support
- [x] Add unit tests (19 tests)

### 2.2 Create BackendCommandRouter ✅

**New File:** `src/squid/core/actor/command_router.py`

- [x] Create `BackendCommandRouter` class
- [x] Implement `register_commands(command_types)` - subscribes to all
- [x] Implement `_route(command)` - enqueues to BackendActor
- [x] Implement `_get_priority(command)` - detects Stop/Abort/Cancel keywords
- [x] Add unit tests (11 tests)

### 2.3 Create Package Init ✅

**New File:** `src/squid/core/actor/__init__.py`

- [x] Export `BackendActor`, `BackendCommandRouter`
- [x] Export `CommandEnvelope`, `Priority`, `PriorityCommandQueue`
- [x] Export thread assertion functions

### 2.4 Wire into ApplicationContext ✅

**File:** `src/squid/application.py`

- [x] Add `_backend_actor: BackendActor` attribute
- [x] Add `_command_router: BackendCommandRouter` attribute
- [x] Create `_build_backend_actor()` method
- [x] Call after controllers/services are built
- [x] Start BackendActor in `__init__`
- [x] Stop BackendActor in `shutdown()`
- [x] Register common command types with router (Live, Mode, Peripheral, Autofocus, Acquisition)

---

## Phase 3: ResourceCoordinator ✅

### 3.1 Create ResourceCoordinator ✅

**New File:** `src/squid/core/coordinator.py`

- [x] Create `Resource` enum:
  - [x] CAMERA_CONTROL
  - [x] STAGE_CONTROL
  - [x] ILLUMINATION_CONTROL
  - [x] FLUIDICS_CONTROL
  - [x] FOCUS_AUTHORITY
- [x] Create `GlobalMode` enum:
  - [x] IDLE, LIVE, ACQUIRING, ABORTING, ERROR
- [x] Create `ResourceLease` dataclass:
  - [x] lease_id, owner, resources, acquired_at, expires_at
- [x] Create `ResourceCoordinator` class:
  - [x] `_leases: Dict[str, ResourceLease]`
  - [x] `_resource_owners: Dict[Resource, str]`
  - [x] `acquire(resources, owner, timeout_s)` - returns lease or None
  - [x] `release(lease)` - release held lease
  - [x] `can_acquire(resources)` - check without acquiring
  - [x] `get_mode()` - derive from active leases
  - [x] Watchdog thread for expired leases
- [x] Add unit tests (28 tests)

### 3.2 Add Coordinator Events ✅

**File:** `src/squid/core/events.py`

- [x] Add `GlobalModeChanged(old_mode, new_mode)` event
- [x] Add `LeaseAcquired(lease_id, owner, resources)` event
- [x] Add `LeaseReleased(lease_id, owner)` event
- [x] Add `LeaseRevoked(lease_id, owner, reason)` event

### 3.3 Wire Coordinator into ApplicationContext ✅

**File:** `src/squid/application.py`

- [x] Add `_coordinator: ResourceCoordinator` attribute
- [x] Create `_build_coordinator()` method
- [x] Wire coordinator callbacks to publish EventBus events
- [x] Start/stop coordinator in lifecycle
- [x] Add `coordinator` property for access

---

## Phase 4: Controller State Machines ✅

### 4.1 Create StateMachine Base Class ✅

**New File:** `src/squid/core/state_machine.py`

- [x] Create generic `StateMachine[S]` base class:
  - [x] Constructor: `initial_state`, `transitions`, `event_bus`
  - [x] `state` property (thread-safe read)
  - [x] `transition_to(new_state)` - validate and transition
  - [x] `_require_state(*allowed_states)` - guard method
  - [x] `register_valid_commands(state, commands)` - command validation
  - [x] `is_command_valid(command_type)` - check current state
  - [x] Abstract `_publish_state_changed(old, new)` method
- [x] Add unit tests (24 tests in `tests/unit/squid/core/test_state_machine.py`)

### 4.2 Refactor LiveController ✅

**File:** `src/squid/mcs/controllers/live_controller.py`

- [x] Create `LiveControllerState` enum: STOPPED, STARTING, LIVE, STOPPING
- [x] Make `LiveController` extend `StateMachine[LiveControllerState]`
- [x] Define transitions in constructor
- [x] Add `_coordinator: ResourceCoordinator` dependency
- [x] Define `LIVE_REQUIRED_RESOURCES = {CAMERA_CONTROL, ILLUMINATION_CONTROL}`
- [x] Refactor `_on_start_live_command()`:
  - [x] Check state == STOPPED
  - [x] Transition to STARTING
  - [x] Acquire resources via coordinator
  - [x] If failed, transition back to STOPPED
  - [x] Start streaming
  - [x] Transition to LIVE
- [x] Refactor `_on_stop_live_command()`:
  - [x] Check state == LIVE
  - [x] Transition to STOPPING
  - [x] Stop streaming
  - [x] Release resources
  - [x] Transition to STOPPED
- [x] Added `observable_state` property for UI state
- [x] Update tests

### 4.3 Refactor AutoFocusController ✅

**File:** `src/squid/mcs/controllers/autofocus/auto_focus_controller.py`

- [x] Create `AutofocusControllerState` enum: IDLE, RUNNING, COMPLETED, FAILED
- [x] Make `AutoFocusController` extend `StateMachine[AutofocusControllerState]`
- [x] Define transitions
- [x] Add `_coordinator` dependency
- [x] Define `AUTOFOCUS_REQUIRED_RESOURCES = {CAMERA_CONTROL, STAGE_CONTROL, FOCUS_AUTHORITY}`
- [x] Added `AutofocusStateChanged` event to `events.py`
- [x] Refactor `autofocus()`:
  - [x] Check state == IDLE
  - [x] Acquire resources
  - [x] Transition to RUNNING
  - [x] Spawn worker
- [x] Handle completion:
  - [x] Transition to COMPLETED or FAILED
  - [x] Release resources
  - [x] Transition to IDLE
- [x] Added `autofocus_in_progress` property (backwards compatibility)
- [x] Update tests

### 4.4 Refactor MultiPointController ✅

**File:** `src/squid/ops/acquisition/multi_point_controller.py`

- [x] Create `AcquisitionControllerState` enum: IDLE, PREPARING, RUNNING, ABORTING, COMPLETED, FAILED
- [x] Make `MultiPointController` extend `StateMachine[AcquisitionControllerState]`
- [x] Define transitions
- [x] Add `_coordinator` dependency
- [x] Define `ACQUISITION_REQUIRED_RESOURCES = {CAMERA_CONTROL, STAGE_CONTROL, ILLUMINATION_CONTROL, FOCUS_AUTHORITY}`
- [x] Refactor `run_acquisition()`:
  - [x] Check state == IDLE
  - [x] Transition to PREPARING
  - [x] Acquire resources
  - [x] Validate settings
  - [x] Transition to RUNNING
  - [x] Spawn worker
- [x] Refactor `request_abort_acquisition()`:
  - [x] Check state == RUNNING
  - [x] Transition to ABORTING
- [x] Refactor `_on_acquisition_completed()`:
  - [x] Transition to COMPLETED
  - [x] Cleanup
  - [x] Release resources
  - [x] Transition to IDLE
- [x] Updated `acquisition_in_progress()` to use state machine
- [x] Update tests

---

## Phase 5: Worker Communication via Events

### 5.1 Define Worker Events

**File:** `src/squid/core/events.py`

- [x] Add `AcquisitionWorkerFinished` event:
  - [x] experiment_id, success, error, final_fov_count
- [x] Add `AcquisitionWorkerProgress` event:
  - [x] experiment_id, current_region, total_regions, current_fov, total_fovs, current_timepoint, total_timepoints

### 5.2 Refactor MultiPointWorker

**File:** `src/squid/ops/acquisition/multi_point_worker.py`

- [x] Add `experiment_id: str` to constructor (already exists as part of AcquisitionParameters)
- [x] Add `event_bus: EventBus` to constructor (already exists)
- [x] Keep callbacks for backward compatibility; added event publishing alongside
- [x] In `run()`:
  - [x] Wrap in try/except (already done)
  - [x] On success: publish `AcquisitionWorkerFinished(success=True)`
  - [x] On error: publish `AcquisitionWorkerFinished(success=False, error=str(e))`
- [x] Added `AcquisitionWorkerProgress` events alongside existing progress callbacks
- [x] Ensure streaming stopped in ALL exit paths (try/finally) (already done)
- [x] Tests pass

### 5.3 Subscribe Controller to Worker Events

**File:** `src/squid/ops/acquisition/multi_point_controller.py`

- [x] Subscribe to `AcquisitionWorkerFinished` in constructor
- [x] In handler:
  - [x] Check `event.experiment_id == self.experiment_ID`
  - [x] If stale, ignore
  - [x] Call cleanup logic (`_on_acquisition_completed()`)
- [x] Subscribe to `AcquisitionWorkerProgress` for internal tracking
- [x] Tests pass (370 unit tests, 58 events/acquisition tests)

---

## Phase 6: Service Renames ✅

### 6.1 Rename Misnamed Services ✅

- [x] Review `LiveService` - does not exist (already `LiveController`)
- [x] Review `MicroscopeModeService` - does not exist (already `MicroscopeModeController`)
- [x] No imports to update - naming is already correct
- [x] No ApplicationContext changes needed
- [x] No test changes needed

### 6.2 Document Service vs Controller Distinction ✅

**File:** `docs/architecture/SERVICE_VS_CONTROLLER.md` (created)

- [x] Define Service: Thread-safe hardware wrapper
- [x] Define Controller: State machine, orchestrates services
- [x] List all Services (11 services documented)
- [x] List all Controllers (10 controllers documented)
- [x] Explain when to use which (with table and examples)

---

## Phase 7: Widget Decoupling

### 7.1 Add UI-Friendly State Events ✅

**File:** `src/squid/core/events.py`

- [x] Add `AcquisitionUIStateChanged`:
  - [x] experiment_id, is_running, is_aborting, current_region, total_regions, progress_percent
- [x] Add `LiveUIStateChanged`:
  - [x] is_live, current_configuration, exposure_time_ms, trigger_mode
- [x] Add `NavigationViewerStateChanged`:
  - [x] x_mm, y_mm, fov_width_mm, fov_height_mm, wellplate_format
- [x] Add `ScanCoordinatesUpdated`:
  - [x] total_regions, total_fovs, region_ids

### 7.2 Enhance Widget Base Classes ✅

**File:** `src/squid/ui/widgets/base.py`

- [x] Add `_state_cache: Dict[str, Any]` to `EventBusWidget`
- [x] Add `_cache_state(key, value)` method
- [x] Add `_get_cached_state(key, default)` method
- [x] Add docstring enforcing rules:
  - Only accept UIEventBus (not raw EventBus)
  - MAY accept read-only initial state
  - Never accept services/controllers
  - Publish commands, subscribe to state events only

### 7.3 UIStateAggregator - SKIPPED ✅

Decided not to create UIStateAggregator - widgets can subscribe directly to
fine-grained events via UIEventBus. The coarse-grained UI events (7.1) are
available as optional convenience events but no aggregator is needed.

### 7.4 Migrate Simple Widgets ✅

Updated all simple widgets to use `UIEventBus` type hints instead of `EventBus`:

- [x] `CameraSettingsWidget` - already event-driven, updated type hint
- [x] `LiveControlWidget` - already event-driven, updated type hint
- [x] `DACControlWidget` - already event-driven, updated type hint
- [x] `TriggerControlWidget` - already event-driven, updated type hint
- [x] `AutoFocusWidget` - already event-driven, updated type hint
- [x] `NavigationWidget` - already event-driven, updated type hint
- [x] `WellplateCalibration` - already event-driven, updated type hint
- [x] `WellplateFormatWidget` - already event-driven, updated type hint
- [x] `StageUtils` - already event-driven, updated type hint
- [x] Updated `_common.py` files for camera, stage, wellplate widgets

### 7.5 Migrate Complex Widgets (In Progress)

**Completed:**
- [x] `WellplateMultiPointWidget` - Subscribe to `ScanCoordinatesUpdated`
- [x] `FlexibleMultiPointWidget` - Subscribe to `ScanCoordinatesUpdated`
- [x] `FocusMapWidget` - Subscribe to `ScanCoordinatesUpdated`

**Remaining (future work):**
- [ ] Remove `navigationViewer` reference from widgets
- [ ] Remove `scanCoordinates` reference from widgets (replace with events)
- [ ] Publish commands instead of direct method calls
- [ ] Add experiment_id filtering in progress handlers
- [ ] Refactor `WellplateFormatWidget` to publish `UpdateWellplateSettingsCommand`
- [ ] Ensure `NapariLiveWidget` only uses UIEventBus

### 7.6 Transform Shared Objects to Services (In Progress)

**Completed:**
- [x] `ScanCoordinates` has `event_bus` parameter and `_publish_coordinates_updated()` method
- [x] Widgets have subscriptions to `ScanCoordinatesUpdated`

**Thread Safety Fix (completed):**
- [x] Event publishing from ScanCoordinates now uses UIEventBus
- Root cause was: EventBus dispatch thread calls handlers, but widget handlers need Qt main thread
- Solution: Pass UIEventBus to ScanCoordinates instead of EventBus
- UIEventBus wraps EventBus and dispatches to Qt main thread via QtEventDispatcher

**Remaining (future work):**
- [ ] Create `ScanCoordinatesService` wrapper (optional - direct use works)
- [ ] Transform `NavigationViewer` to event-driven:
  - [ ] Subscribe to commands (RegisterFOVCommand, etc.)
  - [ ] Publish state (NavigationViewerStateChanged)

---

## Phase 8: Main Window Transformation ✅

### 8.1 New Events and Controllers ✅

**New Events in `src/squid/core/events.py`:**
- [x] `ImageCoordinateClickedCommand` - UI publishes when user clicks on image
- [x] `AcquisitionUIToggleCommand` - UI state toggle for acquisition
- [x] `WellplateConfigurationCommand` - Wellplate format changes
- [x] `LiveScanGridCommand` - Toggle live scan grid
- [x] `ClickToMoveEnabledChanged` - Click-to-move state
- [x] `WellSelectorVisibilityCommand` - Well selector visibility

**New Controller `src/squid/mcs/controllers/image_click_controller.py`:**
- [x] `ImageClickController` - Converts image clicks to stage movement commands
- [x] Subscribes to `ImageCoordinateClickedCommand` and `ClickToMoveEnabledChanged`
- [x] Publishes `MoveStageCommand` for X and Y axes
- [x] Uses `ObjectiveStore` and `CameraService` for pixel size calculation
- [x] Registered with BackendActor for command routing

**New UI Coordinator `src/squid/ui/acquisition_ui_coordinator.py`:**
- [x] `AcquisitionUICoordinator` - Manages UI state during acquisition lifecycle
- [x] Handles live scan grid toggle, tab enabling, autolevel, well selector visibility
- [x] Subscribes to `AcquisitionUIToggleCommand` and `AcquisitionStateChanged`

### 8.2 Main Window Updates ✅

**File:** `src/squid/ui/main_window.py`

- [x] `move_from_click_image()` now publishes `ImageCoordinateClickedCommand` event
- [x] `toggleAcquisitionStart()` now publishes `ClickToMoveEnabledChanged` event
- [x] Business logic moved to ImageClickController (backend) via EventBus
- [x] Subscribes to `AcquisitionUIToggleCommand` via UIEventBus → calls `toggleAcquisitionStart()`
- [x] Subscribes to `WellplateFormatChanged` via UIEventBus → calls `onWellplateChanged()`

### 8.3 ApplicationContext Integration ✅

**File:** `src/squid/application.py`

- [x] `Controllers` dataclass extended with `image_click` field
- [x] `_build_image_click_controller()` method creates ImageClickController
- [x] ImageClickController registered with BackendActor for command routing
- [x] `ImageCoordinateClickedCommand` and `ClickToMoveEnabledChanged` routed through BackendActor

### 8.4 Widget Updates ✅

**Acquisition Widgets** (`src/squid/ui/widgets/acquisition/`):
- [x] `FlexibleMultiPointWidget` - publishes `AcquisitionUIToggleCommand` on start/stop
- [x] `WellplateMultiPointWidget` - publishes `AcquisitionUIToggleCommand` on start/stop
- [x] `MultiPointWithFluidicsWidget` - publishes `AcquisitionUIToggleCommand` on start/stop

**Wellplate Widget** (`src/squid/ui/widgets/wellplate/format.py`):
- [x] Already publishes `WellplateFormatChanged` event (no changes needed)

### 8.5 Signal Connector - Dual Path ✅

**File:** `src/squid/ui/gui/signal_connector.py`

- [x] Qt signal connections kept as primary mechanism (reliable, synchronous)
- [x] EventBus events published in parallel (for future migration)
- [x] Both paths coexist: Qt signals handle immediate UI updates, EventBus enables future decoupling
- [x] When EventBus-only path is stable, Qt signals can be removed

### 8.6 Remaining Work (Future)

**Phase 8 audit notes (post-implementation):**

- `WellplateConfigurationCommand` is defined but currently appears unused (no publisher/subscriber). Either wire it fully (controller + UI publisher) or delete it to avoid “half-API” drift.
- `LiveScanGridCommand` and `WellSelectorVisibilityCommand` are currently only referenced by `AcquisitionUICoordinator`. If `AcquisitionUICoordinator` is not constructed/used, these events are dead weight.
- `AcquisitionUICoordinator` exists but should be either:
  - (A) deleted in favor of direct widget subscriptions and simple main-window container logic, or
  - (B) constructed and used, with `toggleAcquisitionStart` logic removed from `main_window.py` to eliminate duplication.
- `ImageClickController` is currently “actor-routed”. If the final architecture is a single queued EventBus control plane, it should instead subscribe directly on the core EventBus and the BackendActor/router machinery should be removed.

**Remaining Qt signal connections in signal_connector.py:**
- [ ] `signal_toggle_live_scan_grid` - Consider converting to `LiveScanGridCommand`
- [ ] `fluidics_initialized_signal` - Consider event-based initialization
- [ ] `signal_objective_changed` connections - Consider `ObjectiveChanged` subscriptions
- [ ] `signal_coordinates_clicked` - Already handled via `ImageCoordinateClickedCommand`
- [ ] Profile/camera/display signals - Low priority, working as-is

**Main window simplification:**
- [ ] Remove direct hardware references after init (self.microscope, self.stage, self.camera)
- [ ] These are kept for now due to widespread usage in legacy code

---

## Phase 9: Simplification and Legacy Purge (Planned)

Phase 1–8 delivered major infrastructure and some UI decoupling, but the codebase still contains duplicate control-plane threading, unused events, and explicit “backwards compatibility” paths. This phase is intentionally focused on deletion and convergence.

Authoritative step-by-step execution plan:

- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_01_FREEZE_ARCHITECTURE.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_02_SINGLE_CONTROL_THREAD_REMOVE_BACKENDACTOR.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_03_REPLACE_COORDINATOR_WITH_MODE_GATE.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_04_PURGE_ALL_CALLBACKS.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_05_SERVICES_ONLY_CONTROLLERS.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_06_MULTIPOINT_CORRECTNESS_FIXES.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_07_UI_ONLY_WIDGETS_AND_MAIN_WINDOW.md`
- [ ] `docs/implementation/ACTOR_SIMPLIFICATION_STEP_08_VALIDATION_AND_DOCS.md`

---

## Phase 10: Testing

### 10.1 Unit Tests

- [ ] EventBus queuing and dispatch
- [ ] Command/state event contracts (no callbacks in commands)
- [ ] StateMachine transitions and guards
- [ ] Mode/resource gating (single mechanism)

### 10.2 Integration Tests

- [ ] Commands from UI reach EventBus thread
- [ ] Worker completion events trigger cleanup
- [ ] Unsafe commands blocked during acquisition
- [ ] experiment_id filtering in widgets
- [ ] Rapid Start/Stop sequences don't deadlock

### 10.3 Race Condition Tests

- [ ] Concurrent command submission
- [ ] Worker + GUI command interleaving
- [ ] Second acquisition after first completes
- [ ] Stop during acquisition startup

---

## Critical Files Summary

### Implemented (high-impact)
- `src/squid/core/events.py` - Queued EventBus + expanded command/state model
- `src/squid/core/state_machine.py` - Controller state machine base
- `src/squid/ui/ui_event_bus.py` - Qt-safe subscriptions for widgets
- `src/squid/mcs/controllers/image_click_controller.py` - Event-driven click-to-move (Phase 8)
- `docs/architecture/SERVICE_VS_CONTROLLER.md` - Layering guidance

### Known complexity to remove (audit-driven)
- `src/squid/core/actor/` - BackendActor/Router introduces a second control-plane thread
- `src/squid/core/coordinator.py` - Lease-based coordinator is heavier than required for gating
- Callback/compat layers (controllers, services, widgets) - remove once event-driven paths are verified
- `src/squid/ui/gui/signal_connector.py` - delete after remaining Qt signal migrations
- Unused Phase 8 artifacts:
  - `WellplateConfigurationCommand`, `LiveScanGridCommand`, `WellSelectorVisibilityCommand` (if left unused)
  - `src/squid/ui/acquisition_ui_coordinator.py` (if not constructed/used)

### Simplification plan (authoritative)
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_01_FREEZE_ARCHITECTURE.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_02_SINGLE_CONTROL_THREAD_REMOVE_BACKENDACTOR.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_03_REPLACE_COORDINATOR_WITH_MODE_GATE.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_04_PURGE_ALL_CALLBACKS.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_05_SERVICES_ONLY_CONTROLLERS.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_06_MULTIPOINT_CORRECTNESS_FIXES.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_07_UI_ONLY_WIDGETS_AND_MAIN_WINDOW.md`
- `docs/implementation/ACTOR_SIMPLIFICATION_STEP_08_VALIDATION_AND_DOCS.md`
