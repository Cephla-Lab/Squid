# State Management Design - Squid

## 1. Problem Statement
Current state is fragmented across multiple managers (`ObjectiveStore`, `ScanCoordinates`, `ConfigurationManager`) and controllers. This makes it difficult to ensure consistency, especially in a multi-threaded, asynchronous environment. Direct member access and manual callback wiring lead to brittle code.

## 2. Design Goals
- **Single Source of Truth:** Centralize state where appropriate.
- **Immutability & Predictability:** Treat state as immutable; updates occur via explicit actions.
- **Reactivity:** Components automatically react to state changes via events.
- **Thread Safety:** Ensure state access and updates are safe across GUI and background threads.
- **Persistence:** Simplify saving and restoring the application's state.

## 3. Proposed Architecture: Centralized Reactive State

### A. State Models (Schemas)
Use `pydantic` to define clear, typed schemas for different domains of the application state.
- `HardwareState`: Current positions, device statuses, temperatures.
- `AcquisitionState`: Current experiment parameters, progress, regions.
- `UIState`: View modes, visibility toggles, selected items.

### B. State Store (The "Single Source of Truth")
Implement a `StateStore` that holds the current version of all state models.
- **Read Access:** Components can read the current state snapshot.
- **Write Access:** State is updated only through `Command` handlers in the Service layer.

### C. Update Flow (Unidirectional Data Flow)
1. **Action:** A UI widget or controller publishes a `Command` (e.g., `UpdateExposureCommand`).
2. **Handle:** The relevant Service receives the command, interacts with hardware, and then requests a state update.
3. **Update:** The `StateStore` updates the model and publishes a `StateChanged` event (e.g., `ExposureChanged`).
4. **React:** All interested components (UI widgets, other services) react to the `StateChanged` event.

## 4. Specific Domain Strategies

### Hardware State
- **Polling vs. Events:** For high-speed changes (e.g., stage movement), use a combination of debounced events (for UI) and high-frequency updates (for critical control loops).
- **Shadow State:** Maintain a "shadow" of the hardware state in software to allow for immediate UI feedback and validation.

### Configuration & Settings
- **Pydantic-XML:** Continue using XML/JSON for persistence, but wrap the data in `pydantic` models for runtime usage and validation.
- **Hot-Reloading:** Implement the ability to reload configurations and have the UI/Backend automatically synchronize via the `EventBus`.

### Scan & Coordinate State
- **Decoupled Registry:** Move `ScanCoordinates` from a monolithic manager to a set of observable collections. This allows multiple widgets (e.g., Mosaic View, Wellplate View) to stay in sync without direct references.

## 5. Implementation Benefits
- **Improved Testability:** State can be easily mocked or snapshotted for unit and integration tests.
- **Simplified Debugging:** A centralized event stream of state changes makes it much easier to trace the cause of bugs.
- **Easier Undo/Redo:** (Optional future feature) Centralized state snapshots enable implementing undo/redo for complex configuration changes.
- **Modular GUI:** Widgets no longer need to "know" about each other; they simply observe and influence a shared state.
