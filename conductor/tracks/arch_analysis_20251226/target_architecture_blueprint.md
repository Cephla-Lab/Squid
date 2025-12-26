# Target Architecture Blueprint - Squid

## 1. Core Principles
- **Unidirectional Dependency Flow:** Higher layers depend on lower layers. HAL (Hardware Abstraction Layer) must NEVER depend on the Control Plane or UI.
- **Inversion of Control (IoC):** Use Dependency Injection (DI) to provide components with their dependencies, rather than having them create or look up their own.
- **Event-Driven Communication:** Leverage the `EventBus` for cross-component communication to keep modules decoupled.
- **Asynchronous Data Planes:** Decouple hardware data acquisition from UI rendering and storage to prevent blocking.

## 2. Proposed Layered Architecture

### Layer 1: Core (`squid.core`)
- **EventBus:** The central nervous system.
- **Logging/Utility:** Pure utilities with zero internal dependencies.
- **Common Types:** Dataclasses and Interfaces (ABCs).

### Layer 2: Hardware Abstraction Layer (HAL) (`squid.backend.hal`)
- **Microscope:** A thin container for physical devices.
- **Drivers:** Device-specific logic (e.g., `CameraDriver`, `StageDriver`).
- **State:** Simple hardware state tracking (e.g., current position, exposure).
- **CRITICAL:** Removed all references to controllers or high-level business logic.

### Layer 3: Service Layer (`squid.backend.services`)
- **Domain Services:** Orchestrate HAL components for specific domains (e.g., `ImagingService`, `MotionService`).
- **State Management:** Maintain consistent, thread-safe state for the domain.
- **Command Handling:** Subscribe to `Command` events and translate them to HAL calls.

### Layer 4: Control Plane (`squid.backend.control`)
- **Task Controllers:** Manage complex, multi-step workflows (e.g., `ScanController`, `AutofocusController`).
- **Workers:** Specific implementations for long-running tasks that run in dedicated threads.
- **Coordination:** Coordinate multiple services to achieve a goal.

### Layer 5: Orchestration Layer (`squid.application`)
- **ApplicationContext:** Bootstraps the system, performs Dependency Injection, and manages the global application lifecycle.

### Layer 6: UI Layer (`squid.ui`)
- **Decoupled Widgets:** Widgets only communicate via the `EventBus` (Commands/Notifications).
- **UI Coordinators:** (Optional) If multiple widgets need to coordinate complex UI state, use a dedicated UI coordinator.

## 3. Key Refactoring Strategies

### Eliminating Circular Dependencies
- **Pattern:** Use the `Observer` pattern via the `EventBus`. Instead of the `Microscope` calling `LiveController.turn_on_illumination()`, the `Microscope` publishes a `FrameAcquisitionStarted` event, and the `IlluminationService` (or controller) reacts to it if configured for software triggering.

### Decomposing God Objects
- **`MultiPointController`:** Break into:
    - `ScanPlanner`: Logic for calculating coordinates.
    - `AcquisitionOrchestrator`: Manages the high-level flow and state machine.
    - `DataWriter`: Specialized service for IO/Storage.
- **`ApplicationContext`:** Move hardware-specific setup into specialized `Factory` classes (e.g., `MicroscopeFactory`, `ServiceFactory`).

### Performance Optimization (Data Plane)
- **Shared Buffers:** Use pre-allocated, shared memory buffers for image data between the Driver and `StreamHandler`.
- **Worker Pools:** Use a thread pool for heavy image processing (compression, stitching) to keep the data acquisition thread lean.
- **Reactive UI:** Ensure UI updates are rate-limited and perform zero heavy lifting on the main thread.

## 4. Proposed High-Level Component Interaction
1. **User Action:** UI Widget publishes `StartScanCommand`.
2. **Control Plane:** `ScanController` receives the command, transitions state to `RUNNING`, and starts a `ScanWorker`.
3. **Service Layer:** `ScanWorker` calls `MotionService` to move the stage and `ImagingService` to capture frames.
4. **Data Plane:** `CameraDriver` captures a frame and puts it into a shared queue.
5. **Processing Layer:** `ProcessingService` picks up the frame, performs minimal processing, and notifies `DataWriter` and `UIEventBus`.
6. **UI Layer:** Widgets receive the notification and update their display.
