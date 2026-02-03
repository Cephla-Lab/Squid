# Second Filter Wheel (W2) Support

**Status:** COMPLETED
**Started:** 2026-02-02

## Upstream Commits

- [x] `9a74aea7` - feat: Add second filter wheel (W2) support (#478)

## Implementation Checklist

### Phase 1: Protocol Layer
- [x] Add `AXIS.W2 = 6` constant to `_def.py`
- [x] Add `CMD_SET.MOVE_W2 = 19` command constant
- [x] Add `CMD_SET.INITFILTERWHEEL_W2 = 252` command constant
- [x] Add `STAGE_MOVEMENT_SIGN_W2 = 1` constant
- [x] Add `SQUID_FILTERWHEEL_CONFIGS` dict for per-wheel motor config
- [x] Add `move_w2_usteps()` method to `Microcontroller`
- [x] Add `home_w2()` method to `Microcontroller`
- [x] Add `zero_w2()` method to `Microcontroller`
- [x] Add `w2_pos` state tracking in Microcontroller
- [x] Update `init_filter_wheel(axis)` to accept axis parameter (W or W2)
- [x] Update `configure_squidfilter(axis)` to accept axis parameter
- [x] Add `MOVE_W2` and `INITFILTERWHEEL_W2` to `_CMD_NAMES`
- [x] Add `_default_w2_homing_direction`
- [x] Update `SimSerial` to handle MOVE_W, MOVE_W2, HOME_OR_ZERO for W/W2

### Phase 2: Driver Layer
- [x] Rewrite `SquidFilterWheel` in `cephla.py` for multi-wheel:
  - [x] `_WHEEL_AXIS` mapping: {1: AXIS.W, 2: AXIS.W2}
  - [x] `_configure_wheel()` per-wheel init
  - [x] `_move_wheel()` routes to move_w_usteps or move_w2_usteps
  - [x] `home()` supports per-wheel or all-wheel homing
  - [x] `set_filter_wheel_position()` supports multi-wheel dict
  - [x] `get_filter_wheel_position()` returns all wheels
  - [x] `initialize()` accepts multiple wheel indices

### Files Modified
- `software/src/_def.py` — AXIS.W2, MOVE_W2, INITFILTERWHEEL_W2, SQUID_FILTERWHEEL_CONFIGS, STAGE_MOVEMENT_SIGN_W2
- `software/src/squid/backend/microcontroller.py` — move_w2_usteps, home_w2, zero_w2, w2_pos, axis params
- `software/src/squid/backend/drivers/stages/serial.py` — SimSerial W/W2 movement and homing
- `software/src/squid/backend/drivers/filter_wheels/cephla.py` — Multi-wheel SquidFilterWheel driver

## Architecture Notes

- arch_v2's ABC, service, and UI layers already support multi-wheel (wheel_index param throughout)
- This port only needed protocol layer + driver layer changes
- The `_WHEEL_AXIS` routing pattern keeps the driver clean while supporting both wheels
- PID setup uses protocol axis constants (AXIS.W/W2), not motor slot indices (3/4)
