# Experiment Orchestrator Implementation Checklist

## Overview

This checklist tracks the implementation of the experiment orchestrator system for automating multi-round experiments (sequential FISH, cyclic IF, etc.).

**Estimated total effort:** ~13 days

---

## Phase 0: Missing Dependencies (2-3 days)

Extract components from MultiPointController that will be reused by the orchestrator.

### 0.1 ExperimentManager

- [ ] Create `software/src/squid/backend/controllers/multipoint/experiment_manager.py`
- [ ] Extract folder creation logic from `MultiPointController.start_new_experiment()`
- [ ] Extract metadata writing (JSON experiment info)
- [ ] Extract logging setup for experiment directories
- [ ] Add `ExperimentContext` dataclass to hold experiment state
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/multipoint/test_experiment_manager.py`

### 0.2 AcquisitionPlanner

- [ ] Create `software/src/squid/backend/controllers/multipoint/acquisition_planner.py`
- [ ] Extract disk space estimation logic
- [ ] Extract RAM estimation logic
- [ ] Extract image count calculation
- [ ] Add validation methods (check channels exist, positions valid, hardware available)
- [ ] Make methods pure functions where possible (testable without hardware)
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/multipoint/test_acquisition_planner.py`

### 0.3 ImageCaptureExecutor Enhancement

- [ ] Extend `software/src/squid/backend/controllers/multipoint/image_capture.py`
- [ ] Add `ImageCaptureExecutor` class wrapping `CaptureContext` + camera triggering
- [ ] Add single-image capture method (for orchestrator use)
- [ ] Add z-stack capture method (optional, may defer to ZStackExecutor)
- [ ] Write unit tests

---

## Phase 1: Protocol Definition System (2 days)

### 1.1 Directory Structure

- [ ] Create `software/src/squid/core/protocol/` directory
- [ ] Create `software/src/squid/core/protocol/__init__.py`

### 1.2 Protocol Schema

- [ ] Create `software/src/squid/core/protocol/schema.py`
- [ ] Define `FluidicsAction` enum
- [ ] Define `FluidicsStep` dataclass
- [ ] Define `FluidicsSequence` dataclass
- [ ] Define `ZStackConfig` dataclass
- [ ] Define `AutofocusConfig` dataclass
- [ ] Define `ImagingConfig` dataclass
- [ ] Define `Round` dataclass
- [ ] Define `PositionSource` dataclass
- [ ] Define `MicroscopeConfig` dataclass
- [ ] Define `Protocol` dataclass
- [ ] Write unit tests: `tests/unit/squid/core/protocol/test_schema.py`

### 1.3 Protocol Loader

- [ ] Create `software/src/squid/core/protocol/loader.py`
- [ ] Define `ProtocolValidationError` exception
- [ ] Implement `ProtocolLoader.load()` - parse YAML to Protocol
- [ ] Implement `ProtocolLoader.save()` - serialize Protocol to YAML
- [ ] Implement schema validation (required fields, types)
- [ ] Implement `_parse_protocol()` recursive dataclass construction
- [ ] Implement `_serialize_protocol()` for round-trip support
- [ ] Write unit tests: `tests/unit/squid/core/protocol/test_loader.py`
- [ ] Test error cases: malformed YAML, missing fields, invalid types

### 1.4 Example Protocol

- [ ] Create `software/configurations/protocols/` directory
- [ ] Create `software/configurations/protocols/example_sequential_fish.yaml`
- [ ] Include multi-round example with fluidics and imaging
- [ ] Document protocol format in comments

---

## Phase 2: CancelToken + State Management (1.5 days)

### 2.1 CancelToken

- [ ] Create `software/src/squid/core/utils/cancel_token.py`
- [ ] Define `AbortRequested` exception
- [ ] Define `AbortMode` enum (SOFT, HARD)
- [ ] Define `CheckpointContext` dataclass
- [ ] Implement `CancelToken` class
  - [ ] `request_pause()` - set pause flag
  - [ ] `request_resume()` - clear pause flag
  - [ ] `request_abort()` - set abort flag with mode
  - [ ] `checkpoint()` - check flags, block on pause, raise on abort
  - [ ] `on_paused()` callback registration
  - [ ] `on_resumed()` callback registration
  - [ ] `on_checkpoint()` callback registration
  - [ ] `atomic_operation()` context manager
- [ ] Thread-safety: use `threading.Event` for flags
- [ ] Write unit tests: `tests/unit/squid/core/utils/test_cancel_token.py`
- [ ] Test pause/resume flow
- [ ] Test abort during pause
- [ ] Test callback invocation

### 2.2 State Definitions

- [ ] Create `software/src/squid/backend/controllers/orchestrator/` directory
- [ ] Create `software/src/squid/backend/controllers/orchestrator/__init__.py`
- [ ] Create `software/src/squid/backend/controllers/orchestrator/state.py`
- [ ] Define `OrchestratorState` enum (IDLE, LOADING, VALIDATING, READY, RUNNING_FLUIDICS, RUNNING_IMAGING, PAUSED, WAITING_FOR_USER, COMPLETING_ROUND, ABORTING, COMPLETED, FAILED)
- [ ] Define `RoundProgress` dataclass
- [ ] Define `ExperimentProgress` dataclass with `progress_percent` property
- [ ] Define `ExperimentCheckpoint` dataclass
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/orchestrator/test_state.py`

### 2.3 Checkpoint Manager

- [ ] Create `software/src/squid/backend/controllers/orchestrator/checkpoint.py`
- [ ] Implement `CheckpointManager` class
  - [ ] `save()` - atomic JSON write (write to temp, rename)
  - [ ] `load()` - load checkpoint from disk
  - [ ] `clear()` - remove checkpoint file
  - [ ] `_serialize()` - convert to JSON-safe dict
  - [ ] `_deserialize()` - reconstruct from JSON
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/orchestrator/test_checkpoint_manager.py`
- [ ] Test atomic write (no partial files)
- [ ] Test round-trip serialization

---

## Phase 3: Orchestrator Controller (3 days)

### 3.1 Orchestrator Events

- [ ] Add events to `software/src/squid/core/events.py`
- [ ] Commands:
  - [ ] `LoadProtocolCommand`
  - [ ] `StartExperimentCommand`
  - [ ] `PauseExperimentCommand`
  - [ ] `ResumeExperimentCommand`
  - [ ] `SkipToRoundCommand`
  - [ ] `SkipCurrentFOVCommand`
  - [ ] `AbortExperimentCommand`
  - [ ] `RetryCurrentOperationCommand`
- [ ] State events:
  - [ ] `ProtocolLoaded`
  - [ ] `ProtocolLoadFailed`
  - [ ] `ExperimentStateChanged`
  - [ ] `ExperimentProgressUpdate`
  - [ ] `RoundStarted`
  - [ ] `RoundCompleted`
  - [ ] `FluidicsStepStarted`
  - [ ] `FluidicsStepCompleted`
  - [ ] `UserInterventionRequired`
  - [ ] `ExperimentCompleted`

### 3.2 Fluidics Executor

- [ ] Create `software/src/squid/backend/controllers/orchestrator/fluidics_executor.py`
- [ ] Implement `FluidicsExecutor` class
  - [ ] Constructor with FluidicsService dependency
  - [ ] `execute()` - run sequence with checkpoints between steps
  - [ ] `_execute_step()` - atomic step execution (ADD_PROBE, WASH, INCUBATE, CLEAVE, CUSTOM)
  - [ ] `_describe_step()` - human-readable descriptions
- [ ] Handle missing FluidicsService gracefully (skip)
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/orchestrator/test_fluidics_executor.py`
- [ ] Test checkpoint behavior
- [ ] Test abort handling

### 3.3 Imaging Executor

- [ ] Create `software/src/squid/backend/controllers/orchestrator/imaging_executor.py`
- [ ] Implement `ImagingExecutor` class
  - [ ] Constructor with dependencies (experiment_manager, acquisition_service, position_controller, etc.)
  - [ ] `execute()` - run imaging for all positions with checkpoints between FOVs
  - [ ] `_acquire_fov()` - atomic FOV acquisition (move, AF, z-stack, channels)
  - [ ] `_resolve_channels()` - convert channel names to ChannelMode
  - [ ] `_should_autofocus()` - check AF interval
  - [ ] `_perform_autofocus()` - delegate to AF controller
  - [ ] `_acquire_z_stack()` - z-stack capture
  - [ ] `_acquire_single_plane()` - single plane capture
  - [ ] `_calculate_z_levels()` - compute z offsets
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/orchestrator/test_imaging_executor.py`
- [ ] Test FOV checkpoint behavior
- [ ] Test resume from specific FOV

### 3.4 Orchestrator Controller

- [ ] Create `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py`
- [ ] Implement `OrchestratorController(StateMachine[OrchestratorState])`
  - [ ] Define `VALID_TRANSITIONS` dict
  - [ ] Constructor with all dependencies
  - [ ] Use `@handles` decorators for command handlers (BaseController pattern)
- [ ] Command handlers:
  - [ ] `@handles(LoadProtocolCommand)` - `_on_load_protocol()`
  - [ ] `@handles(StartExperimentCommand)` - `_on_start_experiment()`
  - [ ] `@handles(PauseExperimentCommand)` - `_on_pause()`
  - [ ] `@handles(ResumeExperimentCommand)` - `_on_resume()`
  - [ ] `@handles(SkipToRoundCommand)` - `_on_skip_to_round()`
  - [ ] `@handles(SkipCurrentFOVCommand)` - `_on_skip_fov()`
  - [ ] `@handles(AbortExperimentCommand)` - `_on_abort()`
- [ ] CancelToken callbacks:
  - [ ] `_on_token_paused()` - transition to PAUSED, save checkpoint
  - [ ] `_on_token_resumed()` - calculate pause duration
- [ ] Main experiment loop:
  - [ ] `_run_experiment()` - worker thread entry point
  - [ ] Round iteration with checkpoints
  - [ ] Fluidics phase delegation
  - [ ] Imaging phase delegation
  - [ ] Progress tracking and events
- [ ] Helper methods:
  - [ ] `_initialize_experiment()` - create folder, init progress
  - [ ] `_finalize_experiment()` - cleanup, publish completion
  - [ ] `_save_checkpoint()` - persist state
  - [ ] `_calculate_total_fovs()` - count all imaging FOVs
  - [ ] `_estimate_duration_hours()` - rough time estimate
  - [ ] `_validate_channels()` - check all referenced channels exist
- [ ] Pre-flight validation before RUNNING:
  - [ ] All channels exist
  - [ ] Positions file valid
  - [ ] Fluidics service available (if needed)
  - [ ] Sufficient disk space
  - [ ] Hardware connected
- [ ] Write unit tests: `tests/unit/squid/backend/controllers/orchestrator/test_orchestrator_controller.py`
- [ ] Test state transitions
- [ ] Test pause/resume
- [ ] Test checkpoint save/restore

---

## Phase 4: Performance Mode UI (2-3 days)

**Integration:** Performance Mode is a **tab in `imageDisplayTabs`** (alongside "Live View", "Mosaic View", etc.) - not a separate window.

### 4.1 UI Directory Structure

- [ ] Create `software/src/squid/ui/widgets/orchestrator/` directory
- [ ] Create `software/src/squid/ui/widgets/orchestrator/__init__.py`

### 4.2 Performance Mode Widget

- [ ] Create `software/src/squid/ui/widgets/orchestrator/performance_mode.py`
- [ ] Implement `PerformanceModeWidget(QWidget)`
- [ ] Header section:
  - [ ] Experiment name label
  - [ ] Status indicator with color-coded states
- [ ] Progress panel:
  - [ ] Overall progress bar + percentage
  - [ ] Current round progress bar + label
  - [ ] FOV progress bar + label
  - [ ] Elapsed time display
  - [ ] ETA display
  - [ ] Current activity label
- [ ] Timeline panel:
  - [ ] Scrollable list of rounds
  - [ ] Current round highlight
  - [ ] Completed/pending indicators
- [ ] Controls panel:
  - [ ] Pause/Resume button (toggle)
  - [ ] Skip FOV button
  - [ ] Skip to Round button
  - [ ] Abort button (styled red)
- [ ] Event subscriptions:
  - [ ] `ExperimentStateChanged` - update status
  - [ ] `ExperimentProgressUpdate` - update progress
  - [ ] `ProtocolLoaded` - populate timeline
  - [ ] `RoundStarted` - highlight current round
  - [ ] `RoundCompleted` - mark round complete
  - [ ] `FluidicsStepStarted` - update activity
- [ ] Button state management based on current state

### 4.3 Protocol Loader Dialog

- [ ] Create `software/src/squid/ui/widgets/orchestrator/protocol_loader.py`
- [ ] Implement `ProtocolLoaderDialog(QDialog)`
- [ ] File browser section
- [ ] Protocol preview tree:
  - [ ] Protocol info (name, version, author)
  - [ ] Microscope settings
  - [ ] Rounds with fluidics/imaging details
- [ ] Pre-flight validation panel:
  - [ ] Channels validation status
  - [ ] Positions file status
  - [ ] Hardware availability status
  - [ ] Disk space status
  - [ ] Overall status indicator
- [ ] Validation message area (errors in red, success in green)
- [ ] Load button (disabled until validation passes)
- [ ] Cancel button

### 4.4 Intervention Dialog

- [ ] Create `software/src/squid/ui/widgets/orchestrator/intervention_dialog.py`
- [ ] Implement `InterventionDialog(QDialog)`
- [ ] Display reason for intervention
- [ ] Show options as buttons (Retry, Skip, Abort, etc.)
- [ ] Subscribe to `UserInterventionRequired` event
- [ ] Publish corresponding command on button click
- [ ] Modal behavior (blocks orchestrator)

---

## Phase 5: Integration + Tests (2 days)

### 5.1 Application Integration

- [ ] Update `software/src/squid/application.py`
  - [ ] Create ExperimentManager instance
  - [ ] Create AcquisitionPlanner instance
  - [ ] Create OrchestratorController with all dependencies
  - [ ] Wire up event subscriptions

### 5.2 Main Window Integration

- [ ] Update `software/src/squid/ui/main_window.py`
- [ ] Add "Performance Mode" tab to `imageDisplayTabs` in `setupImageDisplayTabs()`
- [ ] Add "Experiment" menu to menubar:
  - [ ] "Load Protocol..." action -> ProtocolLoaderDialog
  - [ ] "Go to Performance Mode" action -> switch to Performance Mode tab

### 5.3 Integration Tests

- [ ] Create `tests/integration/squid/controllers/test_orchestrator_integration.py`
- [ ] Test protocol load → validate → ready flow
- [ ] Test start → pause → resume flow
- [ ] Test start → abort flow
- [ ] Test checkpoint save/restore (simulate crash)
- [ ] Test skip to round functionality
- [ ] Test with simulated fluidics service
- [ ] Test with simulated camera (no real hardware)

### 5.4 Manual Testing Checklist

- [ ] Load valid protocol - verify preview shows correctly
- [ ] Load invalid protocol - verify error messages
- [ ] Run pre-flight validation - verify all checks appear
- [ ] Start experiment in simulation mode
- [ ] Test pause at FOV boundary
- [ ] Test resume from pause
- [ ] Test abort
- [ ] Kill app mid-acquisition, restart, verify resume works
- [ ] Test skip to round (while paused)
- [ ] Verify progress bars update correctly
- [ ] Verify ETA calculation

---

## Post-Implementation

### Documentation

- [ ] Add docstrings to all public classes and methods
- [ ] Update CLAUDE.md if architecture changes
- [ ] Create user guide for protocol format
- [ ] Document pre-flight validation requirements

### Code Review

- [ ] Review all new files for code style consistency
- [ ] Check for proper error handling
- [ ] Verify thread safety
- [ ] Check for memory leaks (proper cleanup)

### Performance Testing

- [ ] Test with 100+ FOV acquisition
- [ ] Verify checkpoint saves don't impact performance
- [ ] Verify UI remains responsive during acquisition

---

## Notes

- **BaseController Pattern**: Use `@handles` decorators for event handlers. StateMachine calls `auto_subscribe()` automatically.
- **Atomic Operations**: FOV acquisition and fluidics steps are atomic - no pause/abort mid-operation.
- **Pre-flight Validation**: Two stages - schema validation in loader, hardware validation before RUNNING state.
- **Checkpoint Frequency**: Save after each FOV completion for fine-grained resume.
