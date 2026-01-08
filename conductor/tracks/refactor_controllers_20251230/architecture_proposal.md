# Architecture Proposal: Controller Refactoring

## Vision
To transform the current monolithic controllers into a set of focused, single-responsibility components that coordinate via well-defined interfaces and services.

## Proposed Architecture

### 1. New Core Services
These services will sit in `software/src/squid/backend/services/` and abstract hardware complexity.

*   **`AcquisitionService`**
    *   **Responsibilities:**
        *   `apply_configuration(channel_mode, trigger_mode)`: Sets exposure, gain, illumination, filters, etc.
        *   `trigger_acquisition(trigger_mode)`: Handles the specific sequence of turning on illumination (if software trigger), sending the trigger command, and handling exposure waits.
        *   `wait_for_ready()`: Encapsulates checking `camera.get_ready_for_trigger()`.
    *   **Benefits:** Removes duplicated hardware orchestration from `LiveController` and `MultiPointWorker`.

*   **`ExperimentManager`** (New Component)
    *   **Responsibilities:**
        *   Creating the experiment directory structure.
        *   Writing standard metadata files (`configurations.xml`, `acquisition parameters.json`).
        *   Managing per-acquisition log handlers.
    *   **Benefits:** Simplifies `MultiPointController` initialization and testing.

*   **`AcquisitionPlanner`** (New Component)
    *   **Responsibilities:**
        *   `estimate_disk_usage(params)`
        *   `estimate_ram_usage(params)`
        *   `calculate_image_count(params)`
    *   **Benefits:** Pure logic component that is easy to unit test without mocking hardware services.

### 2. Refactored `LiveController`
*   **Role:** Orchestrator of the "Live" user experience.
*   **Changes:**
    *   Delegates hardware interaction to `AcquisitionService`.
    *   Delegates timing logic to a new internal helper `TriggerGenerator` (or similar utility class).
    *   Focuses purely on state transitions (STARTING -> LIVE -> STOPPING) and reacting to UI commands.

### 3. Refactored `MultiPointController`
*   **Role:** Orchestrator of the "Multi-Point" acquisition workflow.
*   **Changes:**
    *   Uses `ExperimentManager` to prepare the environment.
    *   Uses `AcquisitionPlanner` for UI feedback (estimates).
    *   Instantiates and monitors `MultiPointWorker`.
    *   Remains the entry point for EventBus commands related to acquisition parameters.

### 4. Refactored `MultiPointWorker`
*   **Role:** Executor of the acquisition plan.
*   **Changes:**
    *   **Task-Based Decomposition:** The nested loop will be broken down.
        *   `AcquisitionSequence`: High-level manager of the time loop.
        *   `TimepointTask`: Manages a single timepoint.
        *   `RegionTask`: Manages a single region (or list of regions).
    *   **Hardware Delegation:** Uses `AcquisitionService` for the actual capture steps.
    *   **Output Delegation:** Events for "Image Captured" are emitted, and a separate `PlateViewListener` (or similar) handles the UI updates, decoupling the worker from the view logic.

## Migration Strategy

1.  **Phase 1 (Foundation):** Implement `AcquisitionService` and migrate `LiveController` to use it. This is a lower-risk first step.
2.  **Phase 2 (Preparation):** Extract `ExperimentManager` and `AcquisitionPlanner` from `MultiPointController`.
3.  **Phase 3 (Core Logic):** Refactor `MultiPointWorker` to use `AcquisitionService`.
4.  **Phase 4 (Cleanup):** Decompose the `MultiPointWorker` loops.

## Impact Analysis
*   **Testing:** Unit tests for `AcquisitionService` and `AcquisitionPlanner` will be trivial to write and fast to run.
*   **Maintenance:** Changing how a camera is triggered will only require editing `AcquisitionService`, not multiple controllers.
*   **Extensibility:** Adding new hardware (e.g., a new type of illumination) involves updating `AcquisitionService` and `IlluminationService`, without touching the acquisition loops.
