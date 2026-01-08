# Specification: Refactor Controllers

## Context
The `multi_point_controller`, `multi_point_worker`, and `live_controller` have become "God objects", handling too many responsibilities. This makes the code hard to test, maintain, and extend.

## Goals
1.  **Decomposition**: Break down these large classes into smaller, single-responsibility components.
2.  **Redundancy Reduction**: Identify and consolidate duplicated logic between the controllers and workers.
3.  **Architecture Simplification**: Streamline the communication between these components.

## Scope
- `software/src/squid/backend/controllers/multipoint/multi_point_controller.py`
- `software/src/squid/backend/controllers/multipoint/multi_point_worker.py`
- `software/src/squid/backend/controllers/live_controller.py`

## Success Criteria
- Reduced Lines of Code (LOC) in the target files (by moving logic to helper classes/modules).
- Improved testability (measured by ease of writing new unit tests).
- Clearer separation of concerns (e.g., hardware control vs. acquisition logic vs. UI updates).
