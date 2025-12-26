# Refactoring Roadmap - Squid

## Phase 1: Foundation & Decoupling (Low Risk, High Impact)
*Goal: Eliminate circular dependencies and formalize communication.*

1.  **Strict HAL Isolation:** Remove the `LiveController` back-reference from `Microscope`. Refactor `acquire_image` and `home_xyz` to use purely low-level primitives.
2.  **Event-Driven Hardware Sync:** Implement `FrameAcquisitionStarted/Finished` events to handle software-triggered illumination via the `EventBus`.
3.  **Formalize Service Registry:** Ensure all existing controllers use the `ServiceRegistry` for hardware access instead of direct object references.

## Phase 2: Data Plane Optimization
*Goal: Unblock the camera driver thread.*

1.  **Buffer Pool Implementation:** Create a centralized `BufferManager`.
2.  **Asynchronous `StreamHandler`:** Refactor `StreamHandler` to use a background queue for image processing (cropping/scaling).
3.  **Dedicated Display & Save Workers:** Create specialized consumers for the image queue.

## Phase 3: Monolith Decomposition - Part 1
*Goal: Clean up the application lifecycle.*

1.  **Implement `MicroscopeFactory`:** Move complex initialization from `ApplicationContext` and `Microscope.build_from_global_config`.
2.  **Service & Controller Factories:** Decouple component instantiation from the main application context.

## Phase 4: Monolith Decomposition - Part 2 (High Risk)
*Goal: Refactor the core acquisition logic.*

1.  **Decompose `MultiPointController`:**
    - Extract `ScanPlanner` (pure logic).
    - Extract `AcquisitionEngine` (single-point orchestration).
    - Implement the new `AcquisitionCoordinator` state machine.
2.  **Data Archive Service:** Migrate all disk IO and folder management logic from `MultiPointWorker` to a dedicated service.

## Phase 5: Reactive State Migration
*Goal: Centralize and formalize application state.*

1.  **Define Pydantic State Models:** Create schemas for Hardware, Acquisition, and UI state.
2.  **Implement `StateStore`:** A centralized, observable repository for the application state.
3.  **Widget Migration:** Gradually move widgets to observe the `StateStore` instead of multiple independent managers.
