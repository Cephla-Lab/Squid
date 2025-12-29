# Specification - High-Speed Compiled Acquisition Implementation

## Overview
This track implements "compiled" hardware-controlled acquisition for maximum speed. The core logic of the per-FOV inner loop (Z-stepping, illumination switching, and camera triggering) will be offloaded from Python to the Teensy 4.1 microcontroller.

## Goals
- Extend Teensy 4.1 firmware to support a Sequence Table (up to 128 steps) and an autonomous execution engine.
- Implement a `SequenceCompiler` in Python to translate acquisition parameters into hardware-optimized binary commands.
- Integrate a new `CompiledAcquisitionEngine` into the existing `MultiPointController` to enable hardware-orchestrated acquisition.

## Functional Requirements
### Firmware (C++/Teensy)
- **Sequence Table Storage:** Support up to 128 steps, where each step defines Z-position, Illumination Mask, Settle Time, and Exposure Time.
- **Autonomous Execution Loop:** A hardware state machine that executes the table sequence precisely upon a single trigger/command.
- **Command Protocol:** New binary protocol for uploading and verifying the sequence table.

### Backend (Python)
- **Sequence Compiler:** Logic to transform high-level `AcquisitionParameters` into the binary format for the Teensy.
- **Driver Updates:** `Microcontroller` class updates to support sequence upload and execution commands.
- **Acquisition Engine Integration:** A new high-speed acquisition mode in `MultiPointController` that uses the compiled hardware sequence for the per-FOV loop.

## Non-Functional Requirements
- **Performance:** Minimize the latency between the completion of one frame and the start of the next move.
- **Reliability:** Ensure software remains in sync with hardware state throughout the autonomous execution.

## Acceptance Criteria
- Firmware successfully executes a 10-step sequence (Z-moves + Triggers) autonomously.
- Python backend can upload a sequence and trigger its execution.
- `MultiPointController` can successfully complete an acquisition using the new hardware-orchestrated mode.

## Out of Scope
- Full refactoring of `MultiPointController` (this track focuses on the new execution mode).
- Major XY stage driver modifications (XY moves remain orchestrated by Python).
