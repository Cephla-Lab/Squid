# Plan - High-Speed Compiled Acquisition Implementation

This plan outlines the steps to implement hardware-orchestrated acquisition, offloading the per-FOV inner loop (Z, Illumination, Trigger) to the Teensy 4.1 microcontroller.

## Phase 1: Firmware Implementation (Teensy 4.1)
Extend the Teensy firmware to support sequence table storage and autonomous execution.

- [ ] Task: Define Sequence Table Structure. Implement the data structure for holding up to 128 acquisition steps.
- [ ] Task: Implement Binary Command Protocol. Add support for uploading and verifying the sequence table via serial.
- [ ] Task: Implement Autonomous Execution State Machine. Create the hardware loop for Z-Move -> Settle -> Trigger -> Exposure.
- [ ] Task: Conductor - User Manual Verification 'Phase 1: Firmware Implementation' (Protocol in workflow.md)

## Phase 2: Python Driver and Sequence Compiler
Implement the backend logic to prepare and upload hardware-optimized sequences.

- [ ] Task: Write Tests for SequenceCompiler. Define expected binary output for various acquisition parameters.
- [ ] Task: Implement SequenceCompiler. Translate high-level parameters into the hardware binary format.
- [ ] Task: Update Microcontroller Driver. Add Python methods to the `Microcontroller` class for uploading and starting sequences.
- [ ] Task: Verify Driver and Compiler. Run unit tests with mocked hardware communication.
- [ ] Task: Conductor - User Manual Verification 'Phase 2: Python Driver and Sequence Compiler' (Protocol in workflow.md)

## Phase 3: Integration and Acquisition Mode
Integrate the high-speed engine into the existing MultiPointController.

- [ ] Task: Implement CompiledAcquisitionEngine. Create the logic for orchestrating XY moves with compiled per-FOV hardware sequences.
- [ ] Task: Integrate into MultiPointController. Add the high-speed mode toggle and wiring in the acquisition logic.
- [ ] Task: Integration Testing. Verify the full loop (XY Move -> Compiled FOV Acquisition) using simulated hardware.
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Integration and Acquisition Mode' (Protocol in workflow.md)
