# Service Layer Completion Plan

## Context for Engineers

### What This Project Is
Squid is a microscopy control software. It has a hardware abstraction layer (HAL) with abstract base classes (`squid/abc.py`) and implementations (`control/peripherals/`). A service layer was started but left incomplete.

### Current State (The Problem)
The service layer is **partially wired**:
- ✓ Services exist: `CameraService`, `StageService`, `PeripheralService`
- ✓ Services are instantiated in `ApplicationContext`
- ✓ Some widgets use services for basic operations
- ✗ Many widget operations bypass services (direct hardware calls)
- ✗ Services don't expose all hardware functionality
- ✗ Command events are scaffolded but never published

### Goal
Complete the service layer so **all widget→hardware communication goes through services**. This enables:
- Centralized validation and logging
- Event-based state synchronization
- Easier testing (mock services, not hardware)
- Consistent API for GUI developers

### Architecture Decision: Direct Calls vs Events
We will use **direct service method calls** (not command events). Rationale:
- Simpler to understand and debug
- Already working pattern in codebase
- Command events add indirection without clear benefit
- State events (service→GUI) will continue working

```
# What we're doing:
Widget → service.set_exposure_time(100) → Hardware → publish(ExposureTimeChanged)

# NOT this (too complex):
Widget → publish(SetExposureTimeCommand) → Service → Hardware → publish(ExposureTimeChanged)
```

---

## File Reference

### Core Service Files
| File | Purpose |
|------|---------|
| `squid/services/base.py` | BaseService ABC with subscribe/publish |
| `squid/services/camera_service.py` | Camera operations |
| `squid/services/stage_service.py` | Stage operations |
| `squid/services/peripheral_service.py` | DAC/pin operations |
| `squid/services/__init__.py` | ServiceRegistry |
| `squid/events.py` | Event definitions and EventBus |
| `squid/application.py` | ApplicationContext (creates services) |

### Widget Files to Refactor
| File | Status | Notes |
|------|--------|-------|
| `control/widgets/camera.py` | Partial | Has service, but many direct calls |
| `control/widgets/stage.py` | Done | Uses StageService |
| `control/widgets/hardware.py` | Done | Uses PeripheralService |
| `control/widgets/display.py` | TODO | Direct camera access |
| `control/widgets/acquisition.py` | TODO | Direct stage access |
| `control/widgets/wellplate.py` | TODO | Direct stage access |
| `control/widgets/tracking.py` | TODO | Direct stage/camera access |

### Test Files
| File | Purpose |
|------|---------|
| `tests/unit/squid/services/test_camera_service.py` | Existing camera tests |
| `tests/unit/squid/services/test_stage_service.py` | Existing stage tests |
| `tests/unit/squid/services/test_peripheral_service.py` | Existing peripheral tests |

---

## Implementation Tasks

### Phase 1: Extend CameraService (TDD)

**Goal:** Add all camera operations to service layer.

#### Task 1.1: Add ROI methods to CameraService
**File:** `squid/services/camera_service.py`

**Test first** (`tests/unit/squid/services/test_camera_service.py`):
```python
def test_set_region_of_interest(self):
    mock_camera = Mock()
    mock_camera.get_resolution.return_value = (2048, 2048)
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_region_of_interest(100, 100, 800, 600)

    mock_camera.set_region_of_interest.assert_called_once_with(100, 100, 800, 600)

def test_get_region_of_interest(self):
    mock_camera = Mock()
    mock_camera.get_region_of_interest.return_value = (0, 0, 1024, 768)
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    result = service.get_region_of_interest()

    assert result == (0, 0, 1024, 768)
```

**Then implement:**
```python
def set_region_of_interest(self, x_offset: int, y_offset: int, width: int, height: int):
    """Set camera region of interest."""
    self._log.debug(f"Setting ROI: offset=({x_offset}, {y_offset}), size=({width}, {height})")
    self._camera.set_region_of_interest(x_offset, y_offset, width, height)
    self.publish(ROIChanged(x_offset=x_offset, y_offset=y_offset, width=width, height=height))

def get_region_of_interest(self) -> tuple:
    """Get current ROI as (x_offset, y_offset, width, height)."""
    return self._camera.get_region_of_interest()

def get_resolution(self) -> tuple:
    """Get camera resolution as (width, height)."""
    return self._camera.get_resolution()
```

**Add event** (`squid/events.py`):
```python
@dataclass
class ROIChanged(Event):
    """Notification that ROI changed."""
    x_offset: int
    y_offset: int
    width: int
    height: int
```

**Commit:** `Add ROI methods to CameraService with tests`

---

#### Task 1.2: Add binning methods to CameraService
**File:** `squid/services/camera_service.py`

**Test first:**
```python
def test_set_binning(self):
    mock_camera = Mock()
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_binning(2, 2)

    mock_camera.set_binning.assert_called_once_with(2, 2)

def test_get_binning_options(self):
    mock_camera = Mock()
    mock_camera.get_binning_options.return_value = [(1, 1), (2, 2), (4, 4)]
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    result = service.get_binning_options()

    assert result == [(1, 1), (2, 2), (4, 4)]
```

**Then implement:**
```python
def set_binning(self, binning_x: int, binning_y: int):
    """Set camera binning."""
    self._log.debug(f"Setting binning: {binning_x}x{binning_y}")
    self._camera.set_binning(binning_x, binning_y)
    self.publish(BinningChanged(binning_x=binning_x, binning_y=binning_y))

def get_binning(self) -> tuple:
    """Get current binning as (x, y)."""
    return self._camera.get_binning()

def get_binning_options(self) -> list:
    """Get available binning options."""
    return self._camera.get_binning_options()
```

**Add event:**
```python
@dataclass
class BinningChanged(Event):
    binning_x: int
    binning_y: int
```

**Commit:** `Add binning methods to CameraService with tests`

---

#### Task 1.3: Add pixel format methods to CameraService
**File:** `squid/services/camera_service.py`

**Test first:**
```python
def test_set_pixel_format(self):
    from squid.config import CameraPixelFormat
    mock_camera = Mock()
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_pixel_format(CameraPixelFormat.MONO16)

    mock_camera.set_pixel_format.assert_called_once_with(CameraPixelFormat.MONO16)
```

**Then implement:**
```python
def set_pixel_format(self, pixel_format: CameraPixelFormat):
    """Set camera pixel format."""
    self._log.debug(f"Setting pixel format: {pixel_format}")
    self._camera.set_pixel_format(pixel_format)
    self.publish(PixelFormatChanged(pixel_format=pixel_format))

def get_pixel_format(self) -> Optional[CameraPixelFormat]:
    """Get current pixel format."""
    return self._camera.get_pixel_format()

def get_available_pixel_formats(self) -> list:
    """Get available pixel formats."""
    return self._camera.get_available_pixel_formats()
```

**Add event:**
```python
@dataclass
class PixelFormatChanged(Event):
    pixel_format: CameraPixelFormat
```

**Commit:** `Add pixel format methods to CameraService with tests`

---

#### Task 1.4: Add temperature methods to CameraService
**File:** `squid/services/camera_service.py`

**Test first:**
```python
def test_set_temperature(self):
    mock_camera = Mock()
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_temperature(-20.0)

    mock_camera.set_temperature.assert_called_once_with(-20.0)
```

**Then implement:**
```python
def set_temperature(self, temperature: float):
    """Set camera target temperature."""
    self._log.debug(f"Setting temperature: {temperature}°C")
    self._camera.set_temperature(temperature)

def set_temperature_reading_callback(self, callback):
    """Set callback for temperature readings."""
    self._camera.set_temperature_reading_callback(callback)
```

**Commit:** `Add temperature methods to CameraService with tests`

---

#### Task 1.5: Add white balance methods to CameraService
**File:** `squid/services/camera_service.py`

**Test first:**
```python
def test_set_white_balance_gains(self):
    mock_camera = Mock()
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_white_balance_gains(1.2, 1.0, 1.5)

    mock_camera.set_white_balance_gains.assert_called_once_with(1.2, 1.0, 1.5)

def test_set_auto_white_balance(self):
    mock_camera = Mock()
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_auto_white_balance(True)

    mock_camera.set_auto_white_balance_gains.assert_called_once_with(on=True)
```

**Then implement:**
```python
def set_white_balance_gains(self, r: float, g: float, b: float):
    """Set white balance gains."""
    self._camera.set_white_balance_gains(r, g, b)

def get_white_balance_gains(self) -> tuple:
    """Get white balance gains as (r, g, b)."""
    return self._camera.get_white_balance_gains()

def set_auto_white_balance(self, enabled: bool):
    """Enable/disable auto white balance."""
    self._camera.set_auto_white_balance_gains(on=enabled)
```

**Commit:** `Add white balance methods to CameraService with tests`

---

#### Task 1.6: Add black level method to CameraService
**File:** `squid/services/camera_service.py`

```python
def set_black_level(self, level: float):
    """Set camera black level."""
    self._log.debug(f"Setting black level: {level}")
    self._camera.set_black_level(level)
```

**Commit:** `Add black level method to CameraService`

---

### Phase 2: Extend StageService

#### Task 2.1: Add theta axis methods to StageService
**File:** `squid/services/stage_service.py`

Currently missing theta axis support.

**Test first** (`tests/unit/squid/services/test_stage_service.py`):
```python
def test_move_theta(self):
    mock_stage = Mock()
    bus = EventBus()
    service = StageService(mock_stage, bus)

    service.move_theta(0.5)

    mock_stage.move_theta.assert_called_once_with(0.5, True)
```

**Then implement:**
```python
def move_theta(self, distance_rad: float, blocking: bool = True):
    """Move theta axis by relative distance."""
    self._stage.move_theta(distance_rad, blocking)
    self._publish_position()

def move_theta_to(self, abs_rad: float, blocking: bool = True):
    """Move theta to absolute position."""
    self._stage.move_theta_to(abs_rad, blocking)
    self._publish_position()
```

**Commit:** `Add theta axis methods to StageService with tests`

---

#### Task 2.2: Add get_config method to StageService
**File:** `squid/services/stage_service.py`

Some widgets need stage config for calculations.

```python
def get_config(self):
    """Get stage configuration."""
    return self._stage.get_config()
```

**Commit:** `Add get_config method to StageService`

---

### Phase 3: Refactor CameraSettingsWidget

**Goal:** Remove all direct `self.camera.*` calls, use `self._service.*` instead.

**File:** `control/widgets/camera.py`

#### Task 3.1: Fix initialization to use service
**Lines 93-103** currently call camera directly:
```python
# BEFORE:
self.entry_exposureTime.setValue(20)
self.camera.set_exposure_time(20)
...
self.camera.set_analog_gain(gain_range.min_gain)
```

**Change to:**
```python
# AFTER:
default_exposure = 20.0
self.entry_exposureTime.setValue(default_exposure)
self._service.set_exposure_time(default_exposure)
...
self._service.set_analog_gain(gain_range.min_gain)
```

**Test:** Run existing widget tests, verify initialization works.

**Commit:** `Refactor CameraSettingsWidget initialization to use service`

---

#### Task 3.2: Fix ROI methods to use service
**Lines 309-340** call camera directly:
```python
# BEFORE (line 309):
self.camera.set_region_of_interest(...)

# AFTER:
self._service.set_region_of_interest(...)
```

**Apply to:**
- `set_Width()` (line 309)
- `set_Height()` (line 326)
- `set_ROI_offset()` (line 334)

**Commit:** `Refactor CameraSettingsWidget ROI methods to use service`

---

#### Task 3.3: Fix binning method to use service
**Line 355:**
```python
# BEFORE:
self.camera.set_binning(binning_x, binning_y)

# AFTER:
self._service.set_binning(binning_x, binning_y)
```

**Commit:** `Refactor CameraSettingsWidget binning to use service`

---

#### Task 3.4: Fix pixel format to use service
**Line 180:**
```python
# BEFORE:
lambda s: self.camera.set_pixel_format(CameraPixelFormat.from_string(s))

# AFTER:
lambda s: self._service.set_pixel_format(CameraPixelFormat.from_string(s))
```

**Commit:** `Refactor CameraSettingsWidget pixel format to use service`

---

#### Task 3.5: Fix temperature/white balance to use service
**Lines 251, 255-261:**
```python
# BEFORE:
self.camera.set_temperature(float(self.entry_temperature.value()))
self.camera.set_auto_white_balance_gains(on=True)
self.camera.set_white_balance_gains(r, g, b)

# AFTER:
self._service.set_temperature(float(self.entry_temperature.value()))
self._service.set_auto_white_balance(True)
self._service.set_white_balance_gains(r, g, b)
```

**Commit:** `Refactor CameraSettingsWidget temperature/WB to use service`

---

#### Task 3.6: Fix black level to use service
**Line 292:**
```python
# BEFORE:
self.camera.set_black_level(blacklevel)

# AFTER:
self._service.set_black_level(blacklevel)
```

**Commit:** `Refactor CameraSettingsWidget black level to use service`

---

#### Task 3.7: Add getters through service where possible
Some getters are used for UI initialization. Add convenience methods to service:
```python
# In camera_service.py:
def get_pixel_size_binned_um(self) -> float:
    return self._camera.get_pixel_size_binned_um()
```

**Commit:** `Add getter methods to CameraService for UI initialization`

---

### Phase 4: Refactor acquisition.py (Stage Access)

**File:** `control/widgets/acquisition.py`

This widget has ~30 direct stage calls.

#### Task 4.1: Accept StageService in constructor
```python
def __init__(
    self,
    stage: AbstractStage = None,  # Legacy
    stage_service: Optional["StageService"] = None,
    ...
):
    if stage_service is not None:
        self._stage_service = stage_service
        self.stage = stage  # Keep for read-only access if needed
    elif stage is not None:
        from squid.services import StageService
        self._stage_service = StageService(stage, event_bus)
        self.stage = stage
```

**Commit:** `Add StageService parameter to acquisition widgets`

---

#### Task 4.2: Refactor movement calls
Replace direct stage calls with service calls:
```python
# BEFORE:
self.stage.move_x_to(x)
self.stage.move_y_to(y)
self.stage.move_z_to(z)

# AFTER:
self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)
```

**Note:** `get_pos()` can remain direct access (read-only).

**Commit:** `Refactor acquisition.py stage movement to use service`

---

### Phase 5: Refactor display.py

**File:** `control/widgets/display.py`

#### Task 5.1: Accept CameraService in constructor
Similar pattern to above.

#### Task 5.2: Refactor camera access
```python
# Line ~450:
# BEFORE:
self.entry_exposureTime.setRange(*self.camera.get_exposure_limits())

# AFTER:
self.entry_exposureTime.setRange(*self._camera_service.get_exposure_limits())
```

**Commit:** `Refactor display.py to use CameraService`

---

### Phase 6: Refactor wellplate.py

**File:** `control/widgets/wellplate.py`

Has direct stage access for well navigation.

#### Task 6.1: Accept StageService
Add constructor parameter.

#### Task 6.2: Refactor movement calls
Use `stage_service.move_to()` instead of direct calls.

**Commit:** `Refactor wellplate.py to use StageService`

---

### Phase 7: Refactor tracking.py

**File:** `control/widgets/tracking.py`

Has both camera and stage direct access.

#### Task 7.1: Accept services
```python
def __init__(
    self,
    camera_service: Optional["CameraService"] = None,
    stage_service: Optional["StageService"] = None,
    ...
):
```

#### Task 7.2: Refactor calls
Use services for all hardware access.

**Commit:** `Refactor tracking.py to use services`

---

### Phase 8: Update gui_hcs.py

**File:** `control/gui_hcs.py`

Pass services to all widgets that need them.

#### Task 8.1: Pass services to acquisition widgets
```python
# Line ~500-600 area where widgets are created:
self.multiPointWidget = MultiPointWidget(
    stage_service=self._services.get('stage') if self._services else None,
    camera_service=self._services.get('camera') if self._services else None,
    ...
)
```

**Commit:** `Pass services to acquisition and display widgets`

---

### Phase 9: Clean Up Unused Code

#### Task 9.1: Remove command event handlers (optional)
Since we're using direct method calls, the command event subscriptions in services are unused. Two options:
1. **Keep them** (YAGNI says remove, but they don't hurt)
2. **Remove them** for cleaner code

**Decision:** Keep for now, document as "for future scripting API".

#### Task 9.2: Update docstrings
Update service docstrings to match actual usage pattern.

**Commit:** `Update service documentation`

---

### Phase 10: Update Documentation

#### Task 10.1: Update ARCHITECTURE_V2.md
Document the service layer pattern:
- Services as method wrappers (not event-driven)
- State events for GUI synchronization
- How to add new service methods

#### Task 10.2: Update TESTING_STRATEGY.md
Add section on testing widgets with mocked services.

**Commit:** `Update documentation for service layer`

---

## Testing Strategy

### Unit Tests
Each service method should have:
1. **Happy path test** - calls hardware correctly
2. **Clamping test** - validates input ranges
3. **Event test** - publishes correct event

Example pattern:
```python
def test_set_exposure_time_calls_camera(self):
    mock_camera = Mock()
    mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_exposure_time(100.0)

    mock_camera.set_exposure_time.assert_called_once_with(100.0)
```

### Integration Tests
Test that widgets work with simulated services:
```python
@pytest.fixture
def camera_service(simulated_camera):
    return CameraService(simulated_camera, EventBus())

def test_camera_widget_uses_service(qtbot, camera_service):
    widget = CameraSettingsWidget(camera_service=camera_service)
    qtbot.addWidget(widget)

    widget.entry_exposureTime.setValue(50.0)

    assert camera_service.get_exposure_time() == 50.0
```

### Running Tests
```bash
# Run service unit tests
pytest tests/unit/squid/services/ -v

# Run with coverage
pytest tests/unit/squid/services/ --cov=squid/services

# Run all tests
pytest tests/ -v
```

---

## Commit Checklist

Each commit should:
- [ ] Have passing tests
- [ ] Follow pattern: `{verb} {what} to/from {where}`
- [ ] Be atomic (one logical change)

Good commit messages:
- `Add ROI methods to CameraService with tests`
- `Refactor CameraSettingsWidget to use service for binning`
- `Pass CameraService to display widgets`

Bad commit messages:
- `WIP`
- `fix stuff`
- `refactoring`

---

## Order of Operations

1. **Phase 1** (1.1-1.6): Extend CameraService - ~2 hours
2. **Phase 2** (2.1-2.2): Extend StageService - ~30 min
3. **Phase 3** (3.1-3.7): Refactor camera widget - ~1 hour
4. **Phase 4** (4.1-4.2): Refactor acquisition widget - ~1 hour
5. **Phase 5** (5.1-5.2): Refactor display widget - ~30 min
6. **Phase 6** (6.1-6.2): Refactor wellplate widget - ~30 min
7. **Phase 7** (7.1-7.2): Refactor tracking widget - ~30 min
8. **Phase 8** (8.1): Update gui_hcs.py - ~30 min
9. **Phase 9** (9.1-9.2): Clean up - ~15 min
10. **Phase 10** (10.1-10.2): Documentation - ~30 min

**Total estimate:** ~7 hours of focused work

---

## Verification

After completing all phases:

```bash
# 1. All tests pass
pytest tests/ -v

# 2. Simulation mode works
python main_hcs.py --simulation

# 3. No direct hardware calls in widgets (except read-only getters)
grep -r "self\.camera\." control/widgets/*.py | grep -v "get_" | grep -v "#"
grep -r "self\.stage\." control/widgets/*.py | grep -v "get_" | grep -v "#"
# Should return minimal results (only read-only access)
```
