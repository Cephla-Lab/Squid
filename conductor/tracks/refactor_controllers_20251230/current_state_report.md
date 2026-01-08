# Current State Report: Controller Architecture

## Executive Summary
The `MultiPointController`, `MultiPointWorker`, and `LiveController` classes have accumulated excessive responsibilities, leading to high coupling, code duplication, and "God object" anti-patterns. This report details the current architectural issues and identifies specific areas for refactoring.

## Component Analysis

### 1. `MultiPointController` (God Object)
**Current Responsibilities:**
-   **State Management:** Manages the high-level acquisition state (IDLE, PREPARING, RUNNING, etc.).
-   **Experiment Management:** Creates experiment folders, writes metadata files (`configurations.xml`, `acquisition parameters.json`), and manages per-acquisition logging.
-   **Resource Estimation:** Calculates disk space usage and RAM requirements for mosaic views.
-   **Worker Management:** Instantiates and manages the lifecycle of `MultiPointWorker`.
-   **Configuration Validation:** Checks if laser AF is ready, etc.
-   **Event Handling:** Subscribes to numerous EventBus commands to update its settings.

**Issues:**
-   **Violates Single Responsibility Principle (SRP):** It knows too much about file I/O, hardware details, and business logic.
-   **Hard to Test:** Testing estimation logic requires mocking a full controller setup.
-   **Coupling:** Tightly coupled to `LiveController` for restoring state after acquisition.

### 2. `MultiPointWorker` (God Object / Worker)
**Current Responsibilities:**
-   **Acquisition Loop:** Implements the core nested loops (Time, Region, FOV, Z-stack, Channel).
-   **Hardware Control:** Directly interacts with `CameraService`, `StageService`, `PeripheralService`, `PiezoService`, etc.
-   **Job Dispatching:** Manages `JobRunner` instances for saving images and generating downsampled views.
-   **Plate View Management:** Orchestrates the accumulation of tiles for the plate view UI.
-   **Autofocus Logic:** Implements the orchestration for both contrast-based and laser-based autofocus.

**Issues:**
-   **Complex `run` Method:** The `run()` method is a massive procedural block that is difficult to read and modify.
-   **Duplicated Hardware Logic:** Re-implements logic for applying channel modes and triggering that also exists in `LiveController`.
-   **Mixed Abstraction Levels:** Mixes high-level flow control with low-level hardware waits and sleeps.

### 3. `LiveController`
**Current Responsibilities:**
-   **Live Loop:** Manages the software trigger timer for live view.
-   **Hardware Control:** Directly manages illumination and camera triggers.
-   **Mode Switching:** Handles applying channel configurations (exposure, gain, filters).
-   **State Machine:** Manages transitions between STOPPED, STARTING, LIVE, STOPPING.

**Issues:**
-   **Duplication:** The logic for "apply configuration" and "fire trigger" is duplicated from `MultiPointWorker` (or vice-versa).
-   **Timer Logic:** Custom threading timer logic for software triggering is embedded directly in the controller.

## Shared Pain Points
1.  **Hardware Synchronization:** Both `LiveController` and `MultiPointWorker` have to manually manage the sequence of `Turn On Illumination -> Wait -> Trigger -> Wait -> Turn Off`. This logic is brittle and duplicated.
2.  **Configuration Application:** Both controllers need to know how to apply a `ChannelMode` to the hardware (setting exposure, filter wheel position, etc.).
3.  **Dependencies:** All controllers take a long list of services as arguments, making instantiation complex.

## Recommendations
1.  **Extract `AcquisitionService`:** A new service to encapsulate the primitives of acquisition: `apply_configuration(config)` and `trigger_acquisition()`. This removes hardware details from the controllers.
2.  **Extract `ExperimentManager`:** Move folder creation and metadata writing out of `MultiPointController`.
3.  **Decompose `MultiPointWorker`:** Break the `run` loop into smaller tasks or use a state pattern. Extract `PlateViewManager` logic completely.
4.  **Extract `TriggerGenerator`:** Move the FPS timer logic out of `LiveController`.
