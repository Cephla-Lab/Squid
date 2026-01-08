# Plan: Continuous Closed-Loop Laser Autofocus

## Phase 1: Backend Core Logic
**Goal:** Implement the continuous feedback loop mechanism in the `LaserAutoFocusController`.

- [ ] Task: Implement Focus Quality Calculation
    - [ ] Sub-task: Write tests for `calculate_focus_quality` (correlation/intensity metric).
    - [ ] Sub-task: Implement `calculate_focus_quality` in `LaserAutoFocusController`.
- [ ] Task: Implement Continuous Loop Worker
    - [ ] Sub-task: Write tests for a background worker/thread that continuously reads laser data.
    - [ ] Sub-task: Implement the background worker structure and start/stop mechanisms.
- [ ] Task: Implement Active Compensation Logic
    - [ ] Sub-task: Write tests for the PID or threshold-based Z-correction logic.
    - [ ] Sub-task: Implement the correction logic to adjust Z-piezo based on error.
- [ ] Task: Implement Lock Status & Acquisition Guards
    - [ ] Sub-task: Write tests for `is_locked` status and pre-acquisition checks (retry/abort logic).
    - [ ] Sub-task: Implement `is_locked`, `wait_for_lock`, and acquisition integration hooks.
- [ ] Task: Conductor - User Manual Verification 'Backend Core Logic' (Protocol in workflow.md)

## Phase 2: New UI Widget Creation
**Goal:** Create a new `ContinuousLaserAutofocus` widget.

- [ ] Task: Create Widget Skeleton
    - [ ] Sub-task: Write tests for the basic widget class structure and layout.
    - [ ] Sub-task: Implement the `ContinuousLaserAutofocus` widget class.
- [ ] Task: Add Continuous Mode Controls
    - [ ] Sub-task: Write tests for controls (Toggle, Threshold SpinBox, Timeout SpinBox).
    - [ ] Sub-task: Add these controls to the widget layout.
- [ ] Task: Implement Metric Bars & Status Indicators
    - [ ] Sub-task: Write tests for updating ProgressBars (Piezo Pos, Quality) and Status Label.
    - [ ] Sub-task: Implement the visual indicators and their update slots.
- [ ] Task: Implement Real-time Graph
    - [ ] Sub-task: Write tests for the scrolling plot widget (Z-Pos & Error vs Time).
    - [ ] Sub-task: Embed and configure the `pyqtgraph` plot in the widget.
- [ ] Task: Implement Live Camera Feed with Overlay
    - [ ] Sub-task: Write tests for the video display widget and overlay drawing.
    - [ ] Sub-task: Implement the video feed widget and integrate the profile fit overlay.
- [ ] Task: Conductor - User Manual Verification 'New UI Widget Creation' (Protocol in workflow.md)

## Phase 3: Integration
**Goal:** Connect the new Widget to the Backend and verify system-wide behavior.

- [ ] Task: Register New Widget in Main Window
    - [ ] Sub-task: Modify `main_window.py` to instantiate `ContinuousLaserAutofocus` and add it to `laserfocus_dockArea`.
- [ ] Task: Connect Signals and Slots
    - [ ] Sub-task: Write tests for signal propagation between Controller and the new Widget.
    - [ ] Sub-task: Wire up all signals (metrics updates, status changes, control toggles).
- [ ] Task: Verify Acquisition Interception
    - [ ] Sub-task: Write integration tests ensuring acquisition waits for lock or aborts.
    - [ ] Sub-task: Finalize integration with the main Acquisition Manager.
- [ ] Task: Conductor - User Manual Verification 'Integration' (Protocol in workflow.md)
