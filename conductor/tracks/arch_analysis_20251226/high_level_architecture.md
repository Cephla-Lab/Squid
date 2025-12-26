# High-Level Architecture Mapping - Squid

## Overview
Squid uses a decoupled, event-driven architecture organized into several distinct layers. Communication between these layers primarily happens through a central asynchronous `EventBus`, allowing for a responsive UI and flexible backend.

## Layers

### 1. UI Layer (`squid.ui`)
- **Main Window:** `HighContentScreeningGui` (in `main_window.py`) is the primary UI container.
- **Widgets:** specialized components (e.g., `CameraWidget`, `StageWidget`, `MultiPointWidget`) located in `squid/ui/widgets/`.
- **UI Event Bus:** `UIEventBus` and `QtEventDispatcher` provide thread-safe mechanisms for updating UI components in response to backend events.

### 2. Application Layer (`squid.application`)
- **Application Context:** `ApplicationContext` (in `application.py`) is the "root" object that initializes and owns all major components (Microscope, Controllers, Services).
- **Global Mode Gate:** `GlobalModeGate` (in `squid/core/mode_gate.py`) manages the top-level state of the microscope (e.g., preventing acquisition while in manual live mode).

### 3. Backend Control Plane (`squid.backend`)
- **Controllers:** High-level logic for complex operations (e.g., `MultiPointController`, `LiveController`, `AutoFocusController`).
- **Managers:** Handle persistent state and configurations (e.g., `ChannelConfigurationManager`, `ObjectiveStore`, `ScanCoordinates`).
- **Service Registry:** `ServiceRegistry` (in `squid/backend/services/`) provides a centralized lookup for hardware services (e.g., `CameraService`, `StageService`).

### 4. Hardware Abstraction Layer (HAL)
- **Microscope:** `Microscope` (in `squid/backend/microscope.py`) aggregates all physical hardware components.
- **Drivers:** Low-level implementations for specific hardware devices (cameras, stages, filter wheels, etc.) in `squid/backend/drivers/`.
- **Microcontroller:** `Microcontroller` (in `squid/backend/microcontroller.py`) abstracts communication with the Teensy-based controllers.

### 5. Core Layer (`squid.core`)
- **Event Bus:** Central asynchronous message bus (`EventBus`) for decoupled communication using dataclass-based events.
- **Logging:** Centralized logging configuration.
- **Abstractions:** Base classes and interfaces for hardware and core components.

## Key Interaction Patterns

### Command Pattern (UI -> Backend)
UI Widgets publish "Command" events (e.g., `MoveStageToCommand`) to the global `EventBus`. The relevant Service or Controller subscribes to these commands and executes the hardware operation.

### State Notification (Backend -> UI)
Services and Controllers publish "State" events (e.g., `StagePositionChanged`) when hardware or internal state changes. The UI Layer dispatches these events to the main thread to update widgets.

### Service Orchestration
Controllers interact with hardware by retrieving services from the `ServiceRegistry` and calling their methods. This avoids controllers having direct references to low-level hardware drivers.

### Event-Driven Acquisition
Large tasks like multi-point acquisition are handled by Workers (e.g., `MultiPointWorker`) that run in separate threads, communicating their progress and completion via the `EventBus`.
