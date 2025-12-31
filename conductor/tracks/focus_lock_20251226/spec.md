# Specification - Continuous Laser-Based Auto-Focus (Focus Lock)

## Overview
This track implements a continuous, closed-loop laser-based auto-focus (Focus Lock) system. Unlike the current "Move-then-AF" approach, this system runs continuously in Python, monitoring the AF camera and adjusting the Z piezo position to maintain a target lock displacement. The system will pause only during the actual image acquisition of a Z-stack/FOV.

## Goals
- Implement a software-based PID controller for continuous Z-displacement correction.
- Create a dedicated "Focus Lock" GUI for monitoring lock status, quality, and Z-displacement.
- Integrate a polling-based "Safe to Acquire" check for the `MultiPointController`.

## Functional Requirements
- **Continuous Closed-Loop Control:** A background service that captures AF camera frames, calculates displacement, and applies PID-based corrections to the Z piezo.
- **Dynamic Lock/Unlock:** Ability to enable focus lock during XY movements and disable/pause it during acquisition sequences.
- **Focus Lock Widget:** A new GUI component providing:
    - Real-time Z-displacement and piezo position gauges.
    - Signal quality/Lock quality monitoring.
    - Live AF camera feed.
    - PID error and output readouts.
- **Status Reporting:** The service must maintain a clear "Locked/Unlocked/Searching" status that can be polled by acquisition controllers.
- **Safety Limits:** Automated unlock if displacement exceeds safe hardware limits or signal quality drops below a critical threshold.

## Non-Functional Requirements
- **Latency:** The PID loop should run at the highest frequency allowed by the AF camera's frame rate to ensure tight tracking.
- **Stability:** The PID implementation must include safeguards against integral windup and excessive oscillations.

## Acceptance Criteria
- Focus lock successfully maintains a target Z position while the stage is moving between FOVs.
- The `MultiPointController` can successfully wait for the lock to stabilize before starting an acquisition.
- The Focus Lock Widget provides clear, real-time feedback on the system's performance and Z-axis safety margins.

## Out of Scope
- Firmware-level PID implementation (all control logic remains in Python for this track).
- Hardware modifications.
