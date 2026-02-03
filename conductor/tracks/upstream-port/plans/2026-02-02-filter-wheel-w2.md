# Second Filter Wheel (W2) Support

**Status:** PENDING
**Started:** 2026-02-02

## Upstream Commits

- [ ] `9a74aea7` - feat: Add second filter wheel (W2) support (#478)

## Context

arch_v2's filter wheel architecture is **already designed for multi-wheel support**:
- `AbstractFilterWheelController` ABC uses dict-based position APIs (`set_filter_wheel_position({1: 3, 2: 5})`)
- `FilterWheelService` accepts `wheel_index` parameter
- EventBus events include `wheel_index` field
- `FilterControllerWidget` takes `wheel_index` constructor param
- `FilterWheelConfig.indices` supports `[1, 2]`

**However**, the Cephla/Squid filter wheel driver (`backend/drivers/filter_wheels/cephla.py`) only supports a single wheel (index=1) with a TODO comment. The microcontroller has no W2 protocol commands.

### Key arch_v2 Files
- `core/abc.py` — `AbstractFilterWheelController` (multi-wheel ABC)
- `backend/drivers/filter_wheels/cephla.py` — `SquidFilterWheel` (single-wheel only, has TODO)
- `backend/microcontroller.py` — W axis: `move_w_usteps()`, `home_w()`, `configure_squidfilter()`
- `backend/services/filter_wheel_service.py` — Thread-safe wrapper with `wheel_index`
- `ui/widgets/hardware/filter_controller.py` — EventBus-based widget
- `core/config/__init__.py` — `SquidFilterWheelConfig` (single `motor_slot_index`)

### Key Protocol Constants
- `CMD_SET.MOVE_W = 4`, `CMD_SET.MOVETO_W = 18`
- `AXIS.W = 5`
- No W2 equivalents exist

## Implementation Checklist

### Phase 1: Protocol Layer
- [ ] Add `AXIS.W2 = 6` constant to `_def.py`
- [ ] Add `CMD_SET.MOVE_W2` command constant (value 19 per upstream)
- [ ] Add `CMD_SET.INITFILTERWHEEL_W2` command constant (value 252 per upstream)
- [ ] Add `move_w2_usteps()` method to `Microcontroller`
- [ ] Add `home_w2()` method to `Microcontroller`
- [ ] Add `zero_w2()` method to `Microcontroller`
- [ ] Add `self.w2_pos` state tracking in microcontroller state parsing
- [ ] Add `configure_squidfilter2()` or extend existing config for W2 axis
- [ ] Add `protocol_axis_to_internal()` mapping function for safe axis conversion
- [ ] Add `INITFILTERWHEEL_W2` to `_CMD_NAMES` for logging
- [ ] Update `SimSerialMicrocontroller` to handle W2 commands

### Phase 2: Driver Layer
- [ ] Extend `SquidFilterWheel` in `cephla.py` to support W2:
  - Remove single-wheel restriction
  - Accept `{1: motor_slot_3, 2: motor_slot_4}` configuration
  - Track positions as dict: `{1: w_pos, 2: w2_pos}`
  - Route wheel index 1 to W axis, wheel index 2 to W2 axis
  - Update `set_filter_wheel_position()` for multi-wheel
  - Update `get_filter_wheel_position()` for multi-wheel
  - Update `home()` to handle both wheels
  - Update `available_filter_wheels` to return configured wheels
- [ ] Add `_move_to_position()` with automatic re-home on movement failure

### Phase 3: Configuration
- [ ] Extend `SquidFilterWheelConfig` to support per-wheel motor config:
  - Option A: `motor_slot_indices: Dict[int, int]` mapping wheel_index → motor_slot
  - Option B: Keep `motor_slot_index` for W, add `motor_slot_index_w2` for W2
- [ ] Update `_load_filter_wheel_config()` to support `indices = [1, 2]`
- [ ] Add W2-specific config constants to `_def.py` (or per-wheel config dict)

### Phase 4: UI
- [ ] Create second `FilterControllerWidget` instance for wheel_index=2 when configured
- [ ] Add tabbed layout or side-by-side display for dual wheels
- [ ] Update main_window.py to create W2 widget when `filter_wheel_config.indices` includes 2

### Tests
- [ ] Unit tests for W2 microcontroller methods
- [ ] Unit tests for multi-wheel SquidFilterWheel driver
- [ ] Unit tests for W2 configuration loading
- [ ] Integration test for dual-wheel home and move operations

## Notes

- Firmware changes for W2 (CS pin 16, clock pin 28 at 16MHz, motor slot 4) are in the shared firmware directory
- Camera triggers reduced from 6 to 4 (pins 29-32) to free up pins for W2
- The ABC and service layers are already W2-ready; this is primarily a driver + protocol port
- PID setup must use protocol axis constants (AXIS.W/AXIS.W2), not motor slot indices (3/4)
