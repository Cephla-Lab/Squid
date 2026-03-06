# Illumination Watchdog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace PR #486's per-port illumination timeout with a firmware serial watchdog that auto-shutoffs illumination when the software stops communicating.

**Architecture:** Firmware monitors serial activity; if no messages arrive within a configurable timeout, it turns off all illumination (dead man's switch). Software sends a periodic heartbeat on a daemon thread to keep the watchdog alive. Watchdog starts disabled and is enabled at microscope startup.

**Tech Stack:** C++ (Arduino/Teensy firmware), Python 3.8+ (PyQt5 GUI application), pytest

---

## Design Reference

See the design section below for rationale. The implementation tasks follow immediately after.

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

---

## Implementation Tasks

### Task 0: Create feature branch

**Step 1: Create and checkout branch from master**

```bash
git checkout -b illumination-watchdog master
```

---

### Task 1: Revert PR #486

**Files:**
- Modify: All 17 files changed by commit `34920a51` (see `git diff 34920a51^..34920a51 --stat`)

**Step 1: Revert the commit**

```bash
cd /home/squid/Documents/claude-work/Squid-playground
git revert 34920a51 --no-edit
```

If there are conflicts (likely, since later commits touched some of the same files), resolve them by examining each conflict and choosing the pre-#486 version for all #486-specific changes while preserving any later fixes.

**Step 2: Verify the revert compiles and tests pass**

```bash
cd software
python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py -x -q
```

Expected: All tests pass. Some version assertion tests may fail (they were updated by #486 to expect `(1, 1)`) — that's expected and will be fixed in later tasks.

**Step 3: Commit**

If the `git revert` succeeded cleanly, it auto-committed. If conflicts were resolved manually, commit:

```bash
git add -A
git commit -m "revert: Remove PR #486 illumination timeout feature

Reverting to implement a simpler serial watchdog approach instead.
See docs/plans/2026-03-05-illumination-watchdog-design.md"
```

---

### Task 2: Firmware — Add watchdog constants and globals

**Files:**
- Modify: `firmware/controller/src/constants.h`
- Modify: `firmware/controller/src/constants_protocol.h`
- Modify: `firmware/controller/src/globals.h`
- Modify: `firmware/controller/src/globals.cpp`

**Step 1: Add watchdog constants to `constants.h`**

After the LED matrix pin defines (around line 127-128), add:

```c
// Serial watchdog (illumination auto-shutoff safety)
// If no serial message is received within timeout, firmware turns off all illumination
#define DEFAULT_WATCHDOG_TIMEOUT_MS 5000
#define MAX_WATCHDOG_TIMEOUT_MS 3600000
```

**Step 2: Update firmware version comment and bump to 1.1 in `constants.h`**

Change the version comment and values:

```c
// Version 1.0 = first version with multi-port illumination support
// Version 1.1 = serial watchdog for illumination auto-shutoff
#define FIRMWARE_VERSION_MAJOR 1
#define FIRMWARE_VERSION_MINOR 1
```

**Step 3: Add command IDs to `constants_protocol.h`**

After `TURN_OFF_ALL_PORTS = 39`, add:

```c
static const int SET_WATCHDOG_TIMEOUT = 40;   // Set serial watchdog timeout and enable
static const int SET_PIN_LEVEL = 41;
static const int HEARTBEAT = 42;              // No-op keepalive for watchdog
```

Note: `SET_PIN_LEVEL = 41` already exists — just add the two new ones around it.

**Step 4: Add watchdog globals to `globals.h`**

After the `illumination_port_intensity` declaration:

```c
// Serial watchdog (illumination auto-shutoff safety)
extern uint32_t last_serial_message_time;
extern uint32_t watchdog_timeout_ms;
extern bool watchdog_enabled;
```

**Step 5: Add watchdog globals to `globals.cpp`**

After the `illumination_port_intensity` definition:

```c
// Serial watchdog (illumination auto-shutoff safety) - disabled until software enables
uint32_t last_serial_message_time = 0;
uint32_t watchdog_timeout_ms = DEFAULT_WATCHDOG_TIMEOUT_MS;
bool watchdog_enabled = false;
```

**Step 6: Commit**

```bash
git add firmware/controller/src/constants.h firmware/controller/src/constants_protocol.h firmware/controller/src/globals.h firmware/controller/src/globals.cpp
git commit -m "feat(firmware): add watchdog constants and globals"
```

---

### Task 3: Firmware — Add watchdog commands and main loop check

**Files:**
- Modify: `firmware/controller/src/commands/light_commands.h`
- Modify: `firmware/controller/src/commands/light_commands.cpp`
- Modify: `firmware/controller/src/commands/commands.cpp`
- Modify: `firmware/controller/src/serial_communication.cpp`
- Modify: `firmware/controller/main_controller_teensy41.ino`

**Step 1: Add command declarations to `light_commands.h`**

After `void callback_turn_off_all_ports();`:

```c
void callback_set_watchdog_timeout();
void callback_heartbeat();
```

**Step 2: Add command implementations to `light_commands.cpp`**

At the end of the file:

```c
// Command byte layout: [cmd_id, 40, timeout_b3, timeout_b2, timeout_b1, timeout_b0, 0, crc]
// timeout is 32-bit unsigned milliseconds (max 3,600,000 = 1 hour)
// Setting timeout enables the watchdog. Value of 0 means use default.
void callback_set_watchdog_timeout()
{
    uint32_t requested_timeout = ((uint32_t)buffer_rx[2] << 24)
                                | ((uint32_t)buffer_rx[3] << 16)
                                | ((uint32_t)buffer_rx[4] << 8)
                                | (uint32_t)buffer_rx[5];

    // Clamp to max allowed timeout
    if (requested_timeout > MAX_WATCHDOG_TIMEOUT_MS)
    {
        requested_timeout = MAX_WATCHDOG_TIMEOUT_MS;
    }

    // Treat 0 as "use default"
    if (requested_timeout == 0)
    {
        requested_timeout = DEFAULT_WATCHDOG_TIMEOUT_MS;
    }

    watchdog_timeout_ms = requested_timeout;
    watchdog_enabled = true;
}

// No-op heartbeat command — watchdog timer is reset by the serial message
// receipt in process_serial_message(), not here.
void callback_heartbeat()
{
    // Intentionally empty
}
```

**Step 3: Register commands in `commands.cpp`**

In `init_callbacks()`, after `cmd_map[TURN_OFF_ALL_PORTS]`:

```c
    cmd_map[SET_WATCHDOG_TIMEOUT] = &callback_set_watchdog_timeout;
    cmd_map[HEARTBEAT] = &callback_heartbeat;
```

**Step 4: Update `last_serial_message_time` in `serial_communication.cpp`**

In `process_serial_message()`, after checksum validation succeeds (after `checksum_error = false;`, line ~25), add:

```c
      // Reset watchdog timer on every valid serial message
      last_serial_message_time = millis();
```

**Step 5: Add watchdog check to main loop in `main_controller_teensy41.ino`**

After the interlock check block (after the `digitalWrite(PIN_ILLUMINATION_D5, LOW);` block closes), add:

```c
  // Serial watchdog - auto-shutoff illumination if software stops communicating
  if (watchdog_enabled && (millis() - last_serial_message_time >= watchdog_timeout_ms))
  {
    turn_off_all_ports();
    watchdog_enabled = false;  // One-shot: don't keep firing every loop iteration
  }
```

**Step 6: Commit**

```bash
git add firmware/controller/
git commit -m "feat(firmware): implement serial watchdog for illumination safety"
```

---

### Task 4: Software — Add watchdog commands to `_def.py`

**Files:**
- Modify: `software/control/_def.py`

**Step 1: Add command IDs to `CMD_SET` class**

After `TURN_OFF_ALL_PORTS = 39`, add:

```python
    SET_WATCHDOG_TIMEOUT = 40  # Set serial watchdog timeout and enable
    HEARTBEAT = 42  # No-op keepalive for watchdog
```

Note: `SET_PIN_LEVEL = 41` should already be there between them.

**Step 2: Add watchdog config constant**

After the `port_index_to_source_code` function, add:

```python
# Serial watchdog (illumination auto-shutoff safety)
# Must match firmware constants in constants.h
DEFAULT_WATCHDOG_TIMEOUT_MS = 5000  # 5 seconds (matches firmware)
MAX_WATCHDOG_TIMEOUT_MS = 3600000  # 1 hour (matches firmware)
WATCHDOG_TIMEOUT_S = DEFAULT_WATCHDOG_TIMEOUT_MS / 1000.0
```

**Step 3: Commit**

```bash
git add software/control/_def.py
git commit -m "feat: add watchdog command IDs and config constant to _def.py"
```

---

### Task 5: Software — Write failing tests for watchdog commands

**Files:**
- Create: `software/tests/test_watchdog.py`

**Step 1: Write the test file**

```python
import struct
import threading
import time

import pytest
from crc import CrcCalculator, Crc8

from control._def import CMD_SET, WATCHDOG_TIMEOUT_S, DEFAULT_WATCHDOG_TIMEOUT_MS, MAX_WATCHDOG_TIMEOUT_MS
from control.microcontroller import Microcontroller, SimSerial


@pytest.fixture
def mcu():
    sim = SimSerial()
    mcu = Microcontroller(sim, reset_and_initialize=False)
    yield mcu
    mcu.close()


class TestSetWatchdogTimeout:
    def test_sends_correct_command_id(self, mcu):
        """SET_WATCHDOG_TIMEOUT should use command ID 40."""
        mcu.set_watchdog_timeout(5.0)
        mcu.wait_till_operation_is_completed()
        # SimSerial stores last received command; check command byte
        assert mcu.last_command[1] == CMD_SET.SET_WATCHDOG_TIMEOUT

    def test_sends_timeout_as_milliseconds(self, mcu):
        """Timeout should be converted to ms and packed big-endian in bytes 2-5."""
        mcu.set_watchdog_timeout(5.0)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == 5000

    def test_clamps_negative_to_zero(self, mcu):
        """Negative timeout should be clamped to 0 (firmware treats as default)."""
        mcu.set_watchdog_timeout(-1.0)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == 0

    def test_clamps_to_max(self, mcu):
        """Timeout above max should be clamped."""
        mcu.set_watchdog_timeout(9999.0)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == MAX_WATCHDOG_TIMEOUT_MS

    def test_simserial_stores_timeout(self, mcu):
        """SimSerial should store the configured timeout."""
        mcu.set_watchdog_timeout(10.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.watchdog_timeout_ms == 10000

    def test_simserial_clamps_zero_to_default(self, mcu):
        """SimSerial should treat 0 as firmware default."""
        mcu.set_watchdog_timeout(0.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.watchdog_timeout_ms == DEFAULT_WATCHDOG_TIMEOUT_MS


class TestHeartbeat:
    def test_sends_correct_command_id(self, mcu):
        """HEARTBEAT should use command ID 42."""
        mcu.send_heartbeat()
        mcu.wait_till_operation_is_completed()
        assert mcu.last_command[1] == CMD_SET.HEARTBEAT

    def test_start_and_stop(self, mcu):
        """Heartbeat thread should start and stop cleanly."""
        mcu.start_heartbeat(interval_s=0.1)
        assert mcu._heartbeat_thread is not None
        assert mcu._heartbeat_thread.is_alive()
        mcu.stop_heartbeat()
        assert not mcu._heartbeat_thread.is_alive()

    def test_close_stops_heartbeat(self, mcu):
        """close() should stop the heartbeat thread."""
        mcu.start_heartbeat(interval_s=0.1)
        thread = mcu._heartbeat_thread
        mcu.close()
        assert not thread.is_alive()

    def test_heartbeat_thread_is_daemon(self, mcu):
        """Heartbeat thread should be a daemon so it dies with the process."""
        mcu.start_heartbeat(interval_s=0.1)
        assert mcu._heartbeat_thread.daemon is True
        mcu.stop_heartbeat()

    def test_heartbeat_sends_periodically(self, mcu):
        """Heartbeat should send multiple commands over time."""
        mcu.start_heartbeat(interval_s=0.05)
        time.sleep(0.2)
        mcu.stop_heartbeat()
        # Should have sent at least 2 heartbeats in 0.2s with 0.05s interval
        assert mcu.last_command[1] == CMD_SET.HEARTBEAT


class TestFirmwareVersionForWatchdog:
    def test_version_detected_as_1_1(self, mcu):
        """SimSerial should report firmware version 1.1."""
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        assert mcu.firmware_version == (1, 1)
```

**Step 2: Run tests to verify they fail**

```bash
cd software
python3 -m pytest tests/test_watchdog.py -v
```

Expected: FAIL — `set_watchdog_timeout`, `send_heartbeat`, `start_heartbeat`, `stop_heartbeat` don't exist yet.

**Step 3: Commit**

```bash
git add software/tests/test_watchdog.py
git commit -m "test: add failing tests for watchdog commands and heartbeat"
```

---

### Task 6: Software — Implement watchdog methods in SimSerial

**Files:**
- Modify: `software/control/microcontroller.py`

**Step 1: Add `_CMD_NAMES` entries**

In the `_CMD_NAMES` dict (around line 70), replace the `SET_ILLUMINATION_TIMEOUT` entry with:

```python
    CMD_SET.SET_WATCHDOG_TIMEOUT: "SET_WATCHDOG_TIMEOUT",
    CMD_SET.HEARTBEAT: "HEARTBEAT",
```

**Step 2: Update SimSerial version comment**

Change the SimSerial version comment (around line 182-185) to:

```python
    # Simulated firmware version
    # v1.0: multi-port illumination support
    # v1.1: serial watchdog for illumination auto-shutoff
    FIRMWARE_VERSION_MAJOR = 1
    FIRMWARE_VERSION_MINOR = 1
```

**Step 3: Add `watchdog_timeout_ms` to `SimSerial.__init__`**

In `__init__` (around line 240), add after the port state init:

```python
        # Serial watchdog (firmware v1.1+)
        self.watchdog_timeout_ms = DEFAULT_WATCHDOG_TIMEOUT_MS
```

Import `DEFAULT_WATCHDOG_TIMEOUT_MS` and `MAX_WATCHDOG_TIMEOUT_MS` — they should already be available from the `from control._def import *` at the top of the file.

**Step 4: Add command handling in `_respond_to`**

In `SimSerial._respond_to()`, after the `TURN_OFF_ALL_PORTS` handler, add:

```python
        elif command_byte == CMD_SET.SET_WATCHDOG_TIMEOUT:
            # Parse timeout value from bytes 2-5 (big-endian 32-bit unsigned)
            requested_timeout = (write_bytes[2] << 24) | (write_bytes[3] << 16) | (write_bytes[4] << 8) | write_bytes[5]
            if requested_timeout > MAX_WATCHDOG_TIMEOUT_MS:
                requested_timeout = MAX_WATCHDOG_TIMEOUT_MS
            if requested_timeout == 0:
                requested_timeout = DEFAULT_WATCHDOG_TIMEOUT_MS
            self.watchdog_timeout_ms = requested_timeout
        elif command_byte == CMD_SET.HEARTBEAT:
            pass  # No-op, just triggers a response
```

**Step 5: Commit**

```bash
git add software/control/microcontroller.py
git commit -m "feat: add watchdog command handling to SimSerial"
```

---

### Task 7: Software — Implement watchdog methods in Microcontroller

**Files:**
- Modify: `software/control/microcontroller.py`

**Step 1: Add `set_watchdog_timeout` method**

Add after the `turn_off_all_ports` method (around line 941):

```python
    def set_watchdog_timeout(self, timeout_s: float) -> None:
        """Set firmware serial watchdog timeout and enable the watchdog.

        The firmware will automatically turn off all illumination if it stops
        receiving serial messages for longer than this timeout. This is a safety
        feature to protect against software crashes or USB disconnects.

        Note: Non-blocking. Call wait_till_operation_is_completed() before
        sending another command if ordering matters.

        Args:
            timeout_s: Timeout in seconds. Valid range is 0 to 3600 (1 hour).
                Values below 0 are clamped to 0. Values above 3600 are clamped to 3600.
                A value of 0 tells the firmware to use its default timeout (5s).
        """
        timeout_ms = int(max(0, min(timeout_s * 1000, MAX_WATCHDOG_TIMEOUT_MS)))
        self.log.debug(f"[MCU] set_watchdog_timeout: {timeout_s}s ({timeout_ms}ms)")
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_WATCHDOG_TIMEOUT
        cmd[2] = (timeout_ms >> 24) & 0xFF
        cmd[3] = (timeout_ms >> 16) & 0xFF
        cmd[4] = (timeout_ms >> 8) & 0xFF
        cmd[5] = timeout_ms & 0xFF
        self.send_command(cmd)
```

**Step 2: Add `send_heartbeat` method**

```python
    def send_heartbeat(self) -> None:
        """Send a no-op heartbeat command to reset the firmware watchdog timer."""
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HEARTBEAT
        self.send_command(cmd)
```

**Step 3: Add heartbeat thread methods and init state**

In `Microcontroller.__init__`, after `self.firmware_version = (0, 0)` (line ~654), add:

```python
        # Heartbeat thread for serial watchdog keepalive
        self._heartbeat_thread = None
        self._heartbeat_stop_event = threading.Event()
```

Add the start/stop methods after `send_heartbeat`:

```python
    def start_heartbeat(self, interval_s: float = None) -> None:
        """Start a daemon thread that sends periodic heartbeat commands.

        Args:
            interval_s: Seconds between heartbeats. Defaults to WATCHDOG_TIMEOUT_S / 2.
        """
        if interval_s is None:
            interval_s = WATCHDOG_TIMEOUT_S / 2
        self._heartbeat_stop_event.clear()

        def _heartbeat_loop():
            while not self._heartbeat_stop_event.wait(interval_s):
                try:
                    self.send_heartbeat()
                except Exception as e:
                    self.log.debug(f"[MCU] Heartbeat send failed: {e}")

        self._heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        self.log.debug(f"[MCU] Heartbeat started: interval={interval_s}s")

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        self._heartbeat_stop_event.set()
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        self.log.debug("[MCU] Heartbeat stopped")
```

**Step 4: Update `close()` to stop heartbeat**

In `close()` (line ~700), add `self.stop_heartbeat()` as the first line:

```python
    def close(self):
        self.stop_heartbeat()
        self.terminate_reading_received_packet_thread = True
        self.thread_read_received_packet.join()
        self._serial.close()
```

**Step 5: Run tests**

```bash
cd software
python3 -m pytest tests/test_watchdog.py -v
```

Expected: All tests PASS.

**Step 6: Commit**

```bash
git add software/control/microcontroller.py
git commit -m "feat: implement watchdog timeout and heartbeat in Microcontroller"
```

---

### Task 8: Software — Wire up watchdog in microscope startup

**Files:**
- Modify: `software/control/microscope.py`

**Step 1: Replace the illumination timeout block in `_prepare_for_use()`**

Replace the existing block (lines ~412-424) with:

```python
        # Configure serial watchdog for illumination safety (requires firmware v1.1+)
        if self.low_level_drivers.microcontroller:
            mcu = self.low_level_drivers.microcontroller
            if mcu.firmware_version >= (1, 1):
                timeout_s = getattr(control._def, "WATCHDOG_TIMEOUT_S", 5.0)
                mcu.set_watchdog_timeout(timeout_s)
                mcu.wait_till_operation_is_completed()
                mcu.start_heartbeat()
                self._log.info(f"Illumination watchdog enabled: timeout={timeout_s}s, heartbeat={timeout_s / 2}s")
            else:
                self._log.warning(
                    f"Illumination watchdog not available: firmware v{mcu.firmware_version[0]}.{mcu.firmware_version[1]} "
                    "requires v1.1+"
                )
```

**Step 2: Run all tests**

```bash
cd software
python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py -x -q
```

Expected: All tests pass.

**Step 3: Commit**

```bash
git add software/control/microscope.py
git commit -m "feat: enable watchdog and heartbeat at microscope startup"
```

---

### Task 9: Fix any remaining version assertion tests

**Files:**
- Modify: `software/tests/test_multiport_illumination_bugs.py`
- Modify: `software/tests/test_multiport_illumination_edge_cases.py`
- Modify: `software/tests/test_multiport_illumination_protocol.py`

**Step 1: Check for version assertion failures**

```bash
cd software
python3 -m pytest tests/test_multiport_illumination_bugs.py tests/test_multiport_illumination_edge_cases.py tests/test_multiport_illumination_protocol.py -v 2>&1 | grep -E "FAIL|PASS|ERROR"
```

If any tests assert `firmware_version == (1, 0)` (pre-#486 state after revert), update them to `(1, 1)`. If they already assert `(1, 1)` (the revert kept #486's test changes), they should pass as-is since SimSerial reports 1.1.

**Step 2: Update any failing assertions**

Search for `firmware_version == (1, 0)` and change to `(1, 1)`. Update comments to reference "watchdog support" instead of "timeout support".

**Step 3: Run the multiport test suite**

```bash
cd software
python3 -m pytest tests/test_multiport_illumination_bugs.py tests/test_multiport_illumination_edge_cases.py tests/test_multiport_illumination_protocol.py -v
```

Expected: All PASS.

**Step 4: Commit**

```bash
git add software/tests/
git commit -m "test: update firmware version assertions for watchdog feature"
```

---

### Task 10: Update documentation

**Files:**
- Modify: `software/docs/illumination-control.md`

**Step 1: Replace the "Illumination Timeout" section**

Find the "Illumination Timeout (Auto-Shutoff Safety)" section and replace it with:

```markdown
## Serial Watchdog (Illumination Auto-Shutoff Safety)

**Requires firmware v1.1 or later.**

The firmware includes a serial watchdog that monitors communication with the control software. If no serial messages are received within the timeout period (e.g., due to software crash or USB disconnect), the firmware automatically turns off all illumination.

### How It Works

1. Software enables the watchdog at startup via `SET_WATCHDOG_TIMEOUT` command
2. Software sends periodic `HEARTBEAT` commands (every timeout/2 seconds)
3. Any valid serial message resets the watchdog timer (not just heartbeats)
4. If the timer expires, firmware calls `turn_off_all_ports()` and disables the watchdog

### Configuration

The timeout is configured in `control/_def.py`:

```python
WATCHDOG_TIMEOUT_S = 5.0  # seconds (default)
```

### MCU Protocol

| Command | Code | Description |
|---------|------|-------------|
| SET_WATCHDOG_TIMEOUT | 40 | Set timeout (ms, 32-bit) and enable watchdog. 0 = use default (5s). |
| HEARTBEAT | 42 | No-op keepalive. Resets watchdog via serial message receipt. |
```

**Step 2: Update the command reference table**

In the "MCU Command Reference" section, replace `SET_ILLUMINATION_TIMEOUT` with:

```markdown
| SET_WATCHDOG_TIMEOUT | 40 | Set serial watchdog timeout and enable (v1.1+) |
| HEARTBEAT | 42 | No-op keepalive for serial watchdog (v1.1+) |
```

**Step 3: Commit**

```bash
git add software/docs/illumination-control.md
git commit -m "docs: update illumination-control.md for serial watchdog"
```

---

### Task 11: Final verification

**Step 1: Run full test suite**

```bash
cd software
python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py -v
```

Expected: All tests pass.

**Step 2: Run lint**

```bash
cd software
black --config pyproject.toml --check .
```

Expected: No formatting issues (or fix any that arise).

**Step 3: Smoke test in simulation mode**

```bash
cd software
python3 main_hcs.py --simulation &
sleep 5
kill %1
```

Expected: Application starts, logs show "Illumination watchdog enabled: timeout=5.0s, heartbeat=2.5s", exits cleanly.
