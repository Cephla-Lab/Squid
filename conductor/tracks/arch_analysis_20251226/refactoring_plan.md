# Refactoring Plan: Decomposing Monolithic Components - Squid

## 1. Overview
This document outlines the strategy for decomposing the "God Objects" identified in the current state analysis: `MultiPointController`, `ApplicationContext`, and `Microscope`.

## 2. Decomposing `MultiPointController`
The `MultiPointController` handles too many responsibilities. It will be broken down into specialized components:

### A. `AcquisitionCoordinator` (The Brain)
- **Responsibility:** Manages the high-level state machine (IDLE, RUNNING, PAUSED, etc.).
- **Interactions:** Receives high-level commands and delegates to specialized services.

### B. `ScanPlanner` (The Map Maker)
- **Responsibility:** Translates user requests (wells, regions, z-stacks) into a deterministic list of acquisition points.
- **Interactions:** Inputs: User Config; Outputs: `List[AcquisitionPoint]`.

### C. `AcquisitionEngine` (The Heart)
- **Responsibility:** Orchestrates the precise timing and hardware synchronization for a single acquisition point (FOV).
- **Hardware-Orchestrated FOV:** Specifically designed to "compile" and upload the Z-stack and channel sequence for a single FOV to the microcontroller, allowing the hardware to handle the tight Z-Move -> Settle -> Illumination -> Trigger loop without Python intervention.
- **Interactions:** Coordinates `MotionService`, `ImagingService`, and `TriggerService`.

### D. `DataArchiveService` (The Librarian)
- **Responsibility:** Handles all persistence concerns (file naming, folder structure, metadata writing).
- **Interactions:** Consumes frames from the data plane and writes to disk (async).

## 3. Decomposing `ApplicationContext`
The `ApplicationContext` currently contains excessive hardware initialization logic.

### A. `MicroscopeFactory`
- **Responsibility:** Encapsulates the complex logic of building the `Microscope` object from the global configuration.

### B. `ServiceFactory`
- **Responsibility:** Handles the instantiation and initial wiring of all domain services.

### C. `ControllerFactory`
- **Responsibility:** Handles the instantiation of task controllers using explicit dependency injection.

## 4. Decomposing `Microscope`
The `Microscope` class will be stripped of high-level logic and circular dependencies.

### A. Pure HAL `Microscope`
- **Responsibility:** A thin registry of hardware devices (Camera, Stage, etc.).
- **Change:** Remove all references to `LiveController` or acquisition logic.

### B. `MotionService` & `ImagingService`
- **Responsibility:** Move coordinate-aware logic (e.g., `home_xyz`, `acquire_image`) from `Microscope` into these domain services.

## 5. Migration Strategy: "Strangler Fig" Pattern
We will not refactor everything at once. Instead, we will:
1.  **Define Interfaces:** Create new, clean interfaces for the specialized services.
2.  **Wrappers:** Wrap existing monolithic logic in these new interfaces.
3.  **Incremental Replacement:** Piece by piece, move implementation logic from the monoliths into the new specialized classes.
4.  **Decouple:** Use the `EventBus` to replace direct calls between the new components.
