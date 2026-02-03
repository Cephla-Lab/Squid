# Illumination Suite: Port-Based Naming + Multi-Port Control + Timeout Safety

**Status:** PENDING
**Started:** 2026-02-02

## Upstream Commits

- [ ] `064ee07d` - refactor: Rename laser constants from wavelength-based to port-based names (#479)
- [ ] `ef375f28` - feat: Multi-port illumination control + laser port naming refactor (#481)
- [ ] `34920a51` - feat: Add illumination timeout (auto-shutoff) safety feature (#486)

## Context

arch_v2 currently uses wavelength-based illumination constants (`ILLUMINATION_SOURCE_405NM = 11`, etc.) in `src/_def.py` `ILLUMINATION_CODE` class. The microcontroller module has basic `turn_on_illumination()`, `turn_off_illumination()`, `set_illumination()` commands (CMD 10-12) but no multi-port simultaneous control. The `IlluminationService` already supports `Dict[int, LightSource]` for multiple sources and the `IlluminationController` driver wraps the microcontroller.

### Key Differences from Upstream

- arch_v2 has `IlluminationService` (thread-safe, EventBus) wrapping `IlluminationController` driver
- upstream puts everything in `microcontroller.py` and `lighting.py`
- arch_v2 uses `ILLUMINATION_CODE` class; upstream uses `_def.py` flat constants
- D3/D4 source code mapping bug exists in arch_v2 (`ILLUMINATION_SOURCE_638NM = 13`, `ILLUMINATION_SOURCE_561NM = 14` — need to verify if D3/D4 mapping is correct)

## Implementation Checklist

### Phase 1: Port-Based Naming (064ee07d)
- [ ] Add `ILLUMINATION_D1` through `ILLUMINATION_D5` aliases to `ILLUMINATION_CODE` class in `src/_def.py`
- [ ] Verify D3/D4 source code mapping is correct (D3=14/561nm, D4=13/638nm)
- [ ] Fix mapping if wrong in `src/squid/core/config/models/illumination_config.py` defaults
- [ ] Keep old wavelength-based names as deprecated aliases for backward compat
- [ ] Add port mapping utility functions (`source_code_to_port_index()`, `port_index_to_source_code()`)

### Phase 2: Multi-Port Protocol Commands (ef375f28)
- [ ] Add new protocol command constants to `CMD_SET` in `src/_def.py`:
  - `SET_PORT_INTENSITY = 34`
  - `TURN_ON_PORT = 35`
  - `TURN_OFF_PORT = 36`
  - `SET_PORT_ILLUMINATION = 37`
  - `SET_MULTI_PORT_MASK = 38`
  - `TURN_OFF_ALL_PORTS = 39`
- [ ] Add microcontroller methods in `backend/microcontroller.py`:
  - `set_port_intensity(port_index, intensity)`
  - `turn_on_port(port_index)`
  - `turn_off_port(port_index)`
  - `set_port_illumination(port_index, intensity)`
  - `set_multi_port_mask(mask)`
  - `turn_off_all_ports()`
  - Port validation for all new methods
- [ ] Add firmware version detection in microcontroller init (read byte 22 of response)
- [ ] Update `SimSerial` / `SimSerialMicrocontroller` to handle new commands
- [ ] Add `supports_multi_port()` check based on firmware version
- [ ] Update `IlluminationController` driver (`backend/drivers/lighting/led.py`) with multi-port state tracking
- [ ] Update `IlluminationService` to use multi-port API when available

### Phase 3: Illumination Timeout Safety (34920a51)
- [ ] Add `SET_ILLUMINATION_TIMEOUT = 40` command constant to `CMD_SET`
- [ ] Add `set_illumination_timeout(timeout_seconds)` method to microcontroller
- [ ] Add `ILLUMINATION_TIMEOUT_S = 3.0` configuration constant
- [ ] Configure timeout at microscope startup in `microscope.py` or `microscope_factory.py`
- [ ] Add simulation support for timeout command

### Tests
- [ ] Unit tests for port mapping utilities
- [ ] Unit tests for new microcontroller protocol methods
- [ ] Unit tests for firmware version detection
- [ ] Unit tests for multi-port state tracking in IlluminationController
- [ ] Unit test for timeout configuration

## Notes

- Firmware changes are shared between branches (firmware directory is not branch-specific)
- The multi-port commands require firmware v1.0+; timeout requires v1.1+
- The `IlluminationService` already has thread-safe multi-channel support via `set_channel_power()`, `turn_on_channel()`, etc. — the multi-port protocol enables simultaneous hardware control
- Phase 3 is a safety-critical feature that protects against laser damage from software crashes
