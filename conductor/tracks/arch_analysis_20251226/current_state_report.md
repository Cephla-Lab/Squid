# Current State Analysis Report - Squid Architecture

## 1. Architectural Mapping
- **Layers:** Clear separation into UI, Application, Backend (Controllers/Managers/Services), HAL (Microscope/Drivers), and Core (EventBus).
- **Communication:** Heavily reliant on a central `EventBus` for decoupled communication.
- **Bootstrapping:** `ApplicationContext` owns the lifecycle of all components.

## 2. Dependency Analysis
- **Circular Dependency:** A significant back-reference exists where `Microscope` (HAL) depends on `LiveController` (High-level Controller). This prevents a clean separation of concerns and makes testing difficult.
- **Service Layer:** The `ServiceRegistry` provides good abstraction, but it's not yet fully utilized across all controllers.
- **Core Dependencies:** The `core` layer is clean and does not depend on higher layers.

## 3. Complexity Audit (Hotspots)
- **`MultiPointController` (~1100 LOC):** Acts as a "God Object" for acquisition, handling everything from state management to hardware orchestration and worker lifecycle.
- **`ApplicationContext` (~1000 LOC):** Contains extensive hardware initialization logic that could be moved to services or specialized factories.
- **`Microscope` (~600 LOC):** Aggregates too much logic for low-level device coordination, including high-level controller references.

## 4. Data Flow Analysis
- **Image Pipeline:** `StreamHandler` performs image manipulation (crop/squeeze) directly in the camera driver's callback thread. While UI updates are decoupled via Qt Signals, the backend processing is still synchronous and could impact high-speed acquisition.
- **Event-Driven UI:** UI widgets are well-decoupled from the backend via the `EventBus` and `QtStreamHandler`.

## 5. Identified Technical Debt
- **Large Objects:** Monolithic controllers that are hard to test and maintain.
- **Coupling:** Hardware abstraction layer "knows" about the control plane.
- **Threading:** Heavy processing in driver-owned threads.
- **Manual Wiring:** `ApplicationContext` performs manual wiring of many components that could be handled via structured dependency injection.
