# Final Architectural Strategy - Squid Microscope Software

## 1. Executive Summary
The architectural analysis of the Squid software identified a robust but maturing system. Key challenges include tight coupling (circular dependencies), monolithic "God Objects," and synchronous performance bottlenecks in the data plane. This strategy proposes a migration to a modular, tiered, and event-driven architecture that prioritizes unidirectional dependencies and asynchronous processing.

## 2. Current State Findings
- **Hotspots:** `MultiPointController`, `ApplicationContext`, and `Microscope` are overly large and hold multiple responsibilities.
- **Dependency Issues:** A critical back-reference from the low-level `Microscope` to high-level `LiveController` violates abstraction boundaries.
- **Performance Bottlenecks:** The image processing pipeline is synchronous within the camera driver thread.

## 3. Target Architecture: The "Ideal State"
- **Layered Design:** Strict separation into Core, HAL, Services, Control, Orchestration, and UI layers.
- **Unidirectional Flow:** Higher layers depend on lower layers; never the reverse.
- **Reactive State:** A centralized, observable `StateStore` using `pydantic` models for consistency and thread-safety.
- **Asynchronous Data Plane:** Decoupled image acquisition, processing, and persistence using shared buffer pools and specialized worker threads.

## 4. Refactoring Strategy: The Roadmap
The refactoring will proceed in five prioritized phases:
1.  **Foundation & Decoupling:** Eliminate circular dependencies and formalize the Service layer.
2.  **Data Plane Optimization:** Implement asynchronous, zero-copy image handling.
3.  **Bootstrap Refactoring:** Extract `Factory` classes for hardware and service initialization.
4.  **Monolith Decomposition:** Break down `MultiPointController` into specialized orchestrators, planners, and engines.
5.  **Reactive State Migration:** Centralize application state and move to a unidirectional update flow.

## 5. Key Design Blueprints
- **[Target Architecture Blueprint](./target_architecture_blueprint.md):** Detailed layer and component organization.
- **[Performance Strategy](./performance_strategy.md):** Tiered processing and buffer management.
- **[State Management Design](./state_management_design.md):** Centralized reactive state architecture.
- **[Monolith Decomposition Plan](./refactoring_plan.md):** Specific steps for breaking down God Objects.
- **[Phased Roadmap](./refactoring_roadmap.md):** Execution order and risk management.

## 6. Conclusion
Implementing this strategy will significantly improve the maintainability, testability, and performance of the Squid software, providing a solid foundation for future scientific imaging modalities and hardware integrations.
