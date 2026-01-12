# Cross-Cutting Infrastructure Patterns

## Status: Planning

## Problem

The Squid codebase has accumulated several anti-patterns that appear across multiple files, creating maintenance burden and making refactoring difficult. These patterns were identified during analysis of "god objects" in the codebase.

### Anti-Pattern 1: Scattered Event Subscriptions

**Scope:** 45 files use `event_bus.subscribe()` with inline calls in `__init__`

**Worst offenders:**
- `WellplateMultiPointWidget`: 16 subscriptions (lines 188-203)
- `ScanCoordinates`: 15 subscriptions (lines 149-169)
- `FlexibleMultiPointWidget`: 9 subscriptions (lines 110-118)

**Current pattern:**
```python
def __init__(self, event_bus, ...):
    self._event_bus = event_bus
    self._event_bus.subscribe(EventA, self._on_event_a)
    self._event_bus.subscribe(EventB, self._on_event_b)
    # ... 10+ more lines of subscriptions
```

**Issues:**
- Subscriptions are distant from handler implementations
- Verbose boilerplate in every subscribing class
- Easy to forget cleanup in `shutdown()`
- No way to see what events a class handles at a glance

### Anti-Pattern 2: Feature Flag Sprawl

**Scope:** 47 flags scattered through `_def.py` with two access patterns

**Access patterns:**
1. Direct imports (UI layer): `from _def import SUPPORT_LASER_AUTOFOCUS`
2. Safe getattr (backend): `getattr(_config, "ENABLE_TRACKING", False)`

**Locations:**
- `application.py`: 22+ inline checks
- `main_window.py`: 20+ conditional blocks
- `widget_factory.py`: 10+ conditionals
- `microscope.py`: 12+ conditionals

**Issues:**
- No single source of truth for flag definitions
- No validation that flag names are correct
- Inconsistent access patterns between layers
- Hard to discover all available flags

### Anti-Pattern 3: Axis Method Repetition

**Scope:** `Microcontroller` class (969 lines, 76 methods)

**Pattern:** 5 nearly-identical methods per axis (X, Y, Z, Theta, W):
```python
def move_x_usteps(self, usteps): ...
def move_y_usteps(self, usteps): ...
def move_z_usteps(self, usteps): ...
# ... repeated for move_to, home, zero, configure_pid
```

**Issues:**
- ~30 methods that could be 6 parameterized methods
- Copy-paste errors possible
- Adding new axis requires 5+ new methods

### Anti-Pattern 4: Mixed Widget Concerns

**Scope:** 5 widgets with 1,000+ lines each

| Widget | Lines | Methods | Responsibilities |
|--------|-------|---------|------------------|
| WellplateMultiPointWidget | 2,506 | 92 | Grid UI + selection + params + file I/O + progress |
| FlexibleMultiPointWidget | 1,579 | 61 | Location mgmt + Z-stack + acquisition + events |
| ConfigEditor | 1,293 | 31 | Dynamic UI + config I/O + profiles + validation |

**Issues:**
- Business logic mixed with UI code
- Widgets are untestable without mocking Qt
- Event handlers contain domain logic
- File I/O embedded in UI classes

### Anti-Pattern 5: God Object Managers

**Scope:** Backend managers with multiple responsibilities

| Manager | Lines | Methods | Responsibilities |
|---------|-------|---------|------------------|
| ScanCoordinates | 1,414 | 59 | Region storage + grid generation + wellplate + 15 event handlers |
| LaserAutofocusController | 1,221 | 47 | Image processing + 20 hardware wrappers + calibration + events |

**Issues:**
- Multiple reasons to change
- Hard to unit test individual responsibilities
- Complex initialization

## Solution

Implement systematic infrastructure patterns that can be adopted across the codebase:

### 1. Event Subscription Decorator (`@handles`)

Co-locate subscription with handler, reduce boilerplate:
```python
class ScanCoordinates(EventSubscriberMixin):
    @handles(ClearScanCoordinatesCommand)
    def _on_clear(self, cmd): ...
```

### 2. Feature Flags Registry

Centralized, typed flag access with validation:
```python
flags = get_feature_flags()
if flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
    ...
```

### 3. Axis Parameterization

Generic methods with axis enum:
```python
mc.move_axis_usteps(StageAxis.X, 1000)
mc.home_axis(StageAxis.Z)
```

### 4. Presenter Pattern (MVP)

Separate UI from business logic:
```python
class WellplatePresenter(Presenter[WellplateView]):
    @handles(AcquisitionProgress)
    def _on_progress(self, event): ...
```

### 5. Manager Decomposition

Extract focused components:
- `ScanCoordinates` → `RegionStore`, `GridGenerator`, `WellplateGenerator`
- `LaserAutofocusController` → `LaserSpotDetector` + slim controller

## Dependencies

- **Requires:** Multipoint refactor complete (`conductor/tracks/multipoint-refactor-20251230/`)
  - Provides `AcquisitionService` pattern to follow
  - Establishes service layer conventions

## Scope

### In Scope
- `@handles` decorator and `EventSubscriberMixin`
- `FeatureFlags` registry with all 47 flags
- `Microcontroller` axis parameterization
- `ScanCoordinates` decomposition
- `LaserSpotDetector` extraction
- `ControllerFactory` for `application.py`
- `Presenter` base class
- One presenter extraction as proof of concept

### Out of Scope
- Full MVP conversion of all widgets
- UI-specific testing infrastructure
- Changes to `_def.py` format (flags still defined there)
- New feature flag values

## Success Criteria

| Metric | Target |
|--------|--------|
| Event subscription boilerplate | Reduced by 50% in migrated classes |
| Feature flag access | Single pattern across codebase |
| Microcontroller methods | 30 → 6 core + thin wrappers |
| ScanCoordinates lines | 1,414 → ~400 (facade only) |
| LaserAF controller lines | 1,221 → ~600 (after extraction) |
| Unit test coverage | New components at 80%+ |

## Documents

- [plan.md](plan.md) - Detailed implementation plan
