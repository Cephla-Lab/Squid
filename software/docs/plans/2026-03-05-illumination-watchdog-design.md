# Illumination Watchdog (Auto-Shutoff) Design

## Problem

If the control software crashes, is killed, or the USB connection drops while laser illumination is active, the lasers remain on indefinitely. This is a safety hazard. PR #486 attempted to solve this with per-port firmware timers, but the approach had issues (complex firmware state, no protection against USB disconnect, software couldn't keep lasers on indefinitely without toggling).

## Approach

A firmware-level watchdog timer that monitors serial communication activity. If the firmware stops receiving serial messages for a configurable duration, it assumes the software is dead and turns off all illumination. The software sends a periodic heartbeat to keep the watchdog happy.

This is a standard "dead man's switch" pattern used in industrial controllers, robotics, and CNC systems.

## Prerequisites

Revert PR #486 (`34920a51`) in its entirety before implementing. This removes per-port timers, `SET_ILLUMINATION_TIMEOUT` command, port status byte 19, and the `turn_off_all_ports()` behavior change.

## Firmware Changes

### New State (globals.cpp / globals.h)

- `uint32_t last_serial_message_time = 0` — updated to `millis()` on every received serial message
- `uint32_t watchdog_timeout_ms = DEFAULT_WATCHDOG_TIMEOUT_MS` — configurable via serial command
- `bool watchdog_enabled = false` — disabled by default, software enables at startup

### New Constants (constants.h)

- `DEFAULT_WATCHDOG_TIMEOUT_MS = 5000` (5 seconds)
- `MAX_WATCHDOG_TIMEOUT_MS = 3600000` (1 hour)

### New Commands (constants_protocol.h)

| ID | Name | Description |
|----|------|-------------|
| 40 | `SET_WATCHDOG_TIMEOUT` | Set timeout (ms, 32-bit big-endian in bytes 2-5) and enable watchdog. 0 = use default. Clamped to max. |
| 42 | `HEARTBEAT` | No-op. Firmware acknowledges, does nothing. Watchdog resets because any serial message resets `last_serial_message_time`. |

### Main Loop (main_controller_teensy41.ino)

Add after the interlock check:

```c
if (watchdog_enabled && (millis() - last_serial_message_time >= watchdog_timeout_ms)) {
    turn_off_all_ports();
    watchdog_enabled = false;  // One-shot: don't keep firing every loop
}
```

### Serial Message Handling (serial_communication.cpp)

Update `last_serial_message_time = millis()` at the top of `process_serial_message()`, before dispatching to command callbacks.

### Command Callbacks (light_commands.cpp)

`callback_set_watchdog_timeout()`:
- Parse 32-bit timeout from bytes 2-5
- Clamp to `MAX_WATCHDOG_TIMEOUT_MS`
- If 0, use `DEFAULT_WATCHDOG_TIMEOUT_MS`
- Set `watchdog_timeout_ms` and `watchdog_enabled = true`

`callback_heartbeat()`:
- No-op. The serial message receipt already resets the watchdog timer.

### Restored Behavior

`turn_off_all_ports()` restores its pre-PR #486 behavior:
- Turns off all discrete illumination ports (D1-D16)
- Calls `clear_matrix()` (LED matrix off)
- Sets `illumination_is_on = false`

### Version

Firmware version: 1.1 (reusing the version number since we're replacing the 1.1 feature).
Comment: "Version 1.1 = serial watchdog for illumination auto-shutoff"

## Software Changes

### control/_def.py

- `CMD_SET.SET_WATCHDOG_TIMEOUT = 40`
- `CMD_SET.HEARTBEAT = 42`
- `WATCHDOG_TIMEOUT_S = 5.0` — configurable constant
- Remove all PR #486 additions (timeout constants, `NUM_TIMEOUT_PORTS`, `RESPONSE_BYTE_PORT_STATUS`, etc.)

### control/microcontroller.py — Microcontroller class

New methods:

`set_watchdog_timeout(timeout_s: float)`:
- Converts to ms, clamps to valid range, sends `SET_WATCHDOG_TIMEOUT` command
- Byte layout: `[cmd_id, 40, timeout_b3, timeout_b2, timeout_b1, timeout_b0, 0, crc]`

`send_heartbeat()`:
- Sends `HEARTBEAT` command (minimal bytes)

`start_heartbeat(interval_s: float = None)`:
- Default interval: `WATCHDOG_TIMEOUT_S / 2`
- Starts a daemon thread that calls `send_heartbeat()` every `interval_s` seconds
- Thread exits cleanly when `_heartbeat_stop_event` is set

`stop_heartbeat()`:
- Sets stop event, joins thread
- Called from `close()`

Remove all PR #486 additions (`illumination_port_is_on`, port status parsing from byte 19, eager local state updates in `turn_off_port`/`set_port_illumination`/`set_multi_port_mask`/`turn_off_all_ports`).

### control/microcontroller.py — SimSerial class

- Handle `SET_WATCHDOG_TIMEOUT`: store value, no actual timer simulation
- Handle `HEARTBEAT`: no-op, standard acknowledgement
- Version back to `(1, 1)` with updated comment
- Remove `port_status` parameter from `response_bytes_for()`
- Remove port status calculation from `_respond_to()`

### control/microscope.py — _prepare_for_use()

Replace PR #486's timeout configuration with:

```python
if mcu.firmware_version >= (1, 1):
    timeout_s = getattr(control._def, "WATCHDOG_TIMEOUT_S", 5.0)
    mcu.set_watchdog_timeout(timeout_s)
    mcu.wait_till_operation_is_completed()
    mcu.start_heartbeat()
    self._log.info(f"Illumination watchdog enabled: timeout={timeout_s}s, heartbeat={timeout_s/2}s")
else:
    self._log.warning(
        f"Illumination watchdog not available: firmware v{mcu.firmware_version[0]}.{mcu.firmware_version[1]} "
        "requires v1.1+"
    )
```

### Tests

- Revert PR #486 test changes (version assertions back to `(1, 1)` with new semantics)
- Test `set_watchdog_timeout()` sends correct byte layout
- Test `send_heartbeat()` sends correct command
- Test heartbeat thread starts and stops cleanly
- Test `SimSerial` handles both new commands
- Test `close()` stops heartbeat thread

### Documentation

Update `software/docs/illumination-control.md`:
- Replace the "Illumination Timeout" section with watchdog documentation
- Add `SET_WATCHDOG_TIMEOUT` and `HEARTBEAT` to command reference table

## Future Work (not in scope)

- **USB reconnect re-enablement**: After firmware reboots (USB re-enumeration), the heartbeat thread could detect reconnection and re-send `SET_WATCHDOG_TIMEOUT` to re-enable the watchdog. The current design does not preclude this — the heartbeat thread and serial reconnect infrastructure make it straightforward to add.

## Key Design Decisions

1. **Watchdog resets on any serial message**, not just heartbeats. This means normal command traffic during acquisition also keeps the watchdog alive.
2. **Watchdog fires once then disables itself.** Prevents `turn_off_all_ports()` from being called every loop iteration after software dies.
3. **Watchdog starts disabled.** Bare firmware without software is unaffected. Software explicitly enables it at startup.
4. **Dedicated HEARTBEAT command** instead of reusing SET_WATCHDOG_TIMEOUT for keepalive. Separates configuration from keepalive, avoids overwriting runtime timeout changes, and is the lightest possible command.
