# Illumination Suite: Port-Based Naming + Multi-Port Control + Timeout Safety

**Status:** COMPLETED
**Started:** 2026-02-02

## Upstream Commits

- [x] `064ee07d` - refactor: Rename laser constants from wavelength-based to port-based names (#479)
- [x] `ef375f28` - feat: Multi-port illumination control + laser port naming refactor (#481)
- [x] `34920a51` - feat: Add illumination timeout (auto-shutoff) safety feature (#486)

## Context

arch_v2 currently uses wavelength-based illumination constants (`ILLUMINATION_SOURCE_405NM = 11`, etc.) in `src/_def.py` `ILLUMINATION_CODE` class. The microcontroller module has basic `turn_on_illumination()`, `turn_off_illumination()`, `set_illumination()` commands (CMD 10-12) but no multi-port simultaneous control. The `IlluminationService` already supports `Dict[int, LightSource]` for multiple sources and the `IlluminationController` driver wraps the microcontroller.

### Key Differences from Upstream

- arch_v2 has `IlluminationService` (thread-safe, EventBus) wrapping `IlluminationController` driver
- upstream puts everything in `microcontroller.py` and `lighting.py`
- arch_v2 uses `ILLUMINATION_CODE` class; upstream uses `_def.py` flat constants
- D3/D4 source code mapping confirmed correct (D3=14/561nm, D4=13/638nm)

## Implementation Checklist

### Phase 1: Port-Based Naming (064ee07d)
- [x] Add `ILLUMINATION_D1` through `ILLUMINATION_D5` aliases to `ILLUMINATION_CODE` class in `src/_def.py`
- [x] Verify D3/D4 source code mapping is correct (D3=14/561nm, D4=13/638nm)
- [x] Keep old wavelength-based names as deprecated aliases for backward compat
- [x] Add `ILLUMINATION_PORT` class with port indices D1=0 through D5=4
- [x] Add port mapping utility functions (`source_code_to_port_index()`, `port_index_to_source_code()`)

### Phase 2: Multi-Port Protocol Commands (ef375f28)
- [x] Add new protocol command constants to `CMD_SET` in `src/_def.py`:
  - `SET_PORT_INTENSITY = 34`
  - `TURN_ON_PORT = 35`
  - `TURN_OFF_PORT = 36`
  - `SET_PORT_ILLUMINATION = 37`
  - `SET_MULTI_PORT_MASK = 38`
  - `TURN_OFF_ALL_PORTS = 39`
- [x] Add microcontroller methods in `backend/microcontroller.py`:
  - `set_port_intensity(port_index, intensity)`
  - `turn_on_port(port_index)`
  - `turn_off_port(port_index)`
  - `set_port_illumination(port_index, intensity, turn_on)`
  - `set_multi_port_mask(port_mask, on_mask)`
  - `turn_off_all_ports()`
  - `_validate_port_index()` for all new methods
- [x] Add firmware version detection in microcontroller init (read byte 22 of response)
- [x] Update `SimSerial` to handle new commands + firmware version + port status
- [x] Add `supports_multi_port()` check based on firmware version
- [x] Update `IlluminationController` driver (`backend/drivers/lighting/led.py`) with multi-port state tracking and API
- [x] Add `_CMD_NAMES` entries for all new commands

### Phase 3: Illumination Timeout Safety (34920a51)
- [x] Add `SET_ILLUMINATION_TIMEOUT = 40` command constant to `CMD_SET`
- [x] Add `set_illumination_timeout(timeout_seconds)` method to microcontroller
- [x] Add `ILLUMINATION_TIMEOUT_S = 3.0` configuration constant
- [x] Configure timeout at startup in `Microcontroller.__init__` (after firmware version detection)
- [x] Add simulation support for timeout command in `SimSerial`

### Tests
- [x] Unit tests for port mapping utilities (6 tests)
- [x] Unit tests for SimSerial multi-port command handling (12 tests)
- [x] Unit tests for CMD_SET constants (1 test)

### Files Modified
- `software/src/_def.py` — ILLUMINATION_D1-D5, ILLUMINATION_PORT, CMD_SET 34-40, mapping functions, timeout constants
- `software/src/squid/backend/drivers/stages/serial.py` — SimSerial firmware version, multi-port state, command handlers
- `software/src/squid/backend/microcontroller.py` — firmware version detection, 7 MCU methods, timeout config, port status parsing
- `software/src/squid/backend/drivers/lighting/led.py` — multi-port state tracking, 7 API methods

### Files Created
- `software/tests/unit/squid/backend/test_illumination_multiport.py` — 19 tests

## Notes

- Firmware changes are shared between branches (firmware directory is not branch-specific)
- The multi-port commands require firmware v1.0+; timeout requires v1.1+
- The `IlluminationService` already has thread-safe multi-channel support via `set_channel_power()`, `turn_on_channel()`, etc. — the multi-port protocol enables simultaneous hardware control
- Phase 3 is a safety-critical feature that protects against laser damage from software crashes
- Legacy illumination commands (SET_ILLUMINATION, TURN_ON/OFF_ILLUMINATION) are synced with multi-port state in SimSerial
- Port status byte (byte 19 in response) allows real-time hardware state monitoring
