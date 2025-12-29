# Plan - Continuous Laser-Based Auto-Focus (Focus Lock)

This plan outlines the implementation of a continuous, closed-loop focus lock system using the AF camera and Z-piezo, including a dedicated monitoring GUI and integration with the acquisition engine.

## Phase 1: Focus Lock Core Service
Implement the background service responsible for the PID control loop and displacement calculation.

- [ ] Task: Define Focus Lock Events and State. Create necessary dataclasses for lock status, quality, and displacement.
- [ ] Task: Implement PID Controller. Write a robust PID class with anti-windup and gain scheduling support.
- [ ] Task: Focus Lock Service - Unit Tests. Write tests for displacement calculation and PID response.
- [ ] Task: Focus Lock Service - Implementation. Create the background worker that captures AF frames and applies Z-piezo corrections.
- [ ] Task: Conductor - User Manual Verification 'Phase 1: Focus Lock Core Service' (Protocol in workflow.md)

## Phase 2: Focus Lock GUI (Widget)
Develop the dedicated "Focus Lock" widget for real-time monitoring and parameter tuning.

- [ ] Task: Widget UI Structure. Create the base Qt widget with placeholders for plots and gauges.
- [ ] Task: Real-time Displacement & Quality Gauges. Implement instantaneous Z-displacement and signal quality indicators.
- [ ] Task: Live AF Camera Feed. Integrate the AF camera stream into the widget using `QtStreamHandler`.
- [ ] Task: PID Tuning & Control UI. Add inputs for PID gains and controls to enable/disable the lock.
- [ ] Task: Conductor - User Manual Verification 'Phase 2: Focus Lock GUI (Widget)' (Protocol in workflow.md)

## Phase 3: Integration and Safety
Integrate the focus lock into the acquisition workflow and implement safety safeguards.

- [ ] Task: Acquisition Engine Integration. Add a polling mechanism to `AcquisitionEngine` to wait for lock stability after XY moves.
- [ ] Task: Safety Limits & Auto-Unlock. Implement logic to automatically unlock the system if Z-piezo hits limits or signal quality is lost.
- [ ] Task: Integration Tests. Verify the full workflow: Move -> Lock Stabilization -> Z-Stack (Pause Lock) -> Resume Lock.
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Integration and Safety' (Protocol in workflow.md)
