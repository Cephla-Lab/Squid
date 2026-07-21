# Firmware

## Directory Structure

```
firmware/
├── controller/          # Main motion controller (Teensy 4.1)
├── joystick/            # Joystick/control panel (Teensy LC)
└── legacy/              # Archived firmware versions
```

## Building with PlatformIO (Recommended)

[PlatformIO](https://platformio.org/) is the recommended build system for firmware development. It provides consistent builds, dependency management, and command-line tooling.

### Installation

```bash
# Using pip
pip install platformio

# Or using Homebrew (macOS)
brew install platformio
```

`pio run -t upload` flashes the board with the [Teensy Loader](https://www.pjrc.com/teensy/loader.html) application, which PlatformIO bundles in its Teensy platform tools (`tool-teensy`) — there is no separate uploader to install. During upload the Teensy Loader window opens, the board is automatically rebooted into the bootloader, and the firmware is flashed.

> **Note:** The Teensy Loader is a graphical application, so uploading normally requires a desktop (display) environment. On platforms where the GUI app is unavailable (e.g. Linux ARM), PlatformIO automatically falls back to the command-line loader (`teensy_loader_cli`).

On Linux, install the [PJRC udev rules](https://www.pjrc.com/teensy/00-teensy.rules) into `/etc/udev/rules.d/` so non-root users can flash. A copy ships with PlatformIO at `~/.platformio/packages/tool-teensy/00-teensy.rules`.

### Quick Start

```bash
# Build and upload controller firmware
cd firmware/controller
pio run -t upload

# Build and upload joystick firmware
cd firmware/joystick
pio run -t upload
```

**Important:** Before uploading, verify only one Teensy is connected:
```bash
pio device list
```
If multiple devices appear, disconnect the extras before uploading. The upload tool may not warn you and could flash the wrong board.

### Common Commands

| Command | Description |
|---------|-------------|
| `pio run` | Compile firmware |
| `pio run -t upload` | Compile and upload to device |
| `pio run -t clean` | Clean build artifacts |
| `pio device monitor` | Open serial monitor |
| `pio run -t upload && pio device monitor` | Upload and monitor |
| `pio test -e native` | Run unit tests (no hardware required) |

### Running Tests

Unit tests run on your host machine without needing hardware:

```bash
cd firmware/controller
pio test -e native
```

**Example output:**
```
test_crc8:      test_crc8_empty_data           [PASSED]
test_crc8:      test_crc8_single_byte_zero     [PASSED]
test_protocol:  test_command_ids_are_unique    [PASSED]
...
================= 11 test cases: 11 succeeded =================
```

Tests are located in `controller/test/` and use the [Unity](https://github.com/ThrowTheSwitch/Unity) test framework.

### Build Output

After successful compilation, the firmware binary is located at:
- `.pio/build/teensy41/firmware.hex` (controller)
- `.pio/build/teensyLC/firmware.hex` (joystick)

### Troubleshooting

**Device not found during upload:**
- Ensure Teensy is connected via USB
- Check that no other application is using the serial port
- If firmware is unresponsive, press the button on Teensy to enter bootloader mode

**Permission denied (Linux):**
```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

**First build is slow:**
- PlatformIO downloads toolchains and libraries on first run
- Subsequent builds are much faster (incremental compilation)

## Building with Arduino IDE (Alternative)

If you prefer Arduino IDE:

### Controller (Teensy 4.1)

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `controller/main_controller_teensy41.ino` in Arduino IDE
3. Select Board: "Teensy 4.1"
4. Click Upload

### Joystick (Teensy LC)

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `joystick/control_panel_teensyLC.ino` in Arduino IDE
3. Select Board: "Teensy LC"
4. Click Upload

## Controller

The main motion controller firmware for Teensy 4.1. Handles:
- XYZ stage motion control (TMC4361A + TMC2660 drivers)
- Illumination control (lasers and LED matrix)
- Camera triggering
- Serial communication with host software

### Configuration

Hardware-specific settings are in `src/def/def_v1.h`. This includes:
- Motor parameters (steps per rev, microstepping, current)
- Stage limits and velocities
- Joystick sensitivity
- Limit switch polarity

### Build Options

**Disable Laser Safety Interlock:**

By default, the firmware includes laser safety interlock detection. To disable it:

```bash
PLATFORMIO_BUILD_FLAGS="-DDISABLE_LASER_INTERLOCK" pio run -e teensy41 -t upload
```

> **WARNING:** Only use this flag for systems without lasers installed. Disabling the interlock removes laser safety protection.

### Source Structure

```
controller/
├── main_controller_teensy41.ino    # Entry point (v1 protocol — still the live path)
├── platformio.ini                   # PlatformIO config (teensy41, teensy41_boardv2, native)
├── fuzz/
│   └── fuzz_framer.cpp             # libFuzzer/ASAN harness for the protocol-v2 path
├── test/                            # Unit tests (run with pio test -e native)
│   ├── test_crc8/                  # CRC8 checksum tests (v1)
│   ├── test_protocol/              # Protocol/command ID tests (v1)
│   ├── test_crc16/ test_cobs/      # protocol-v2 framing codec
│   ├── test_frames/                # protocol-v2 wire-struct layout
│   ├── test_framer/                # protocol-v2 COBS framer
│   ├── test_claims/ test_slots/    # protocol-v2 claims + slot manager
│   ├── test_dispatch/              # protocol-v2 dispatcher + system commands
│   ├── test_boot/                  # boot/fault module core
│   ├── test_board/ test_board_v2/  # board descriptors (v1/v2)
│   └── test_golden/                # C<->Python golden vectors (generated)
└── src/
    ├── commands/                    # Command handlers (v1)
    ├── def/                         # Hardware configuration (v1)
    ├── tmc/                         # TMC stepper driver library
    ├── utils/                       # crc8 and other pure utilities
    ├── protocol/                    # protocol-v2 core (NOT yet wired to serial — Phase C)
    │   ├── crc16, cobs, frames      #   CRC-16/CCITT-FALSE, COBS codec, wire contract
    │   ├── framer                   #   COBS framer (resync + non-blocking TX)
    │   ├── claims, claims_table     #   resource-claims table + conflict checker
    │   ├── slots                    #   5-slot manager + completion ring (RETRY dedup)
    │   └── dispatch_v2              #   claims-gated dispatcher + HELLO/GET_INFO/GET_STATE/DIAG
    ├── boot/                        # boot/fault module (NOT yet wired — Phase C)
    │   ├── boot.cpp/h              #   watchdog/safe-state/reset-cause/nonce/fault-ring (native-tested)
    │   └── boot_bind_teensy41.cpp  #   RT1062 binding (WDOG1/SRC_SRSR/EEPROM/DWT; teensy41 build only)
    ├── hal/                         # board profiles (compile-time selected)
    │   ├── board.h                 #   GET_INFO descriptor + board-scoped pin constants
    │   └── boards/                 #   board_squid_v1.cpp, board_squid_v2.cpp
    ├── init.cpp/h                   # Initialization routines (v1)
    ├── operations.cpp/h             # Main loop operations (v1)
    ├── serial_communication.cpp/h   # Serial protocol handling (v1 — the live path)
    ├── functions.cpp/h              # Utility functions
    ├── globals.cpp/h                # Global state variables
    └── constants.h                  # Constants and pin definitions
```

### Protocol v2 (Phase B — native-tested, not yet live)

`src/protocol/`, `src/boot/`, and `src/hal/` implement the protocol-v2 core
(COBS + CRC-16 framing, claims-gated 5-slot command dispatch with a completion
ring, system commands, and per-board GET_INFO descriptors). These modules
compile into the firmware binary but are **not wired to `SerialUSB`** — the v1
protocol in `serial_communication.cpp` remains the live path. Phase C performs
the single-PR switchover. The mirrored host codec lives in
`software/control/protocol_v2/`, and C↔Python agreement is enforced by the
golden vectors in `test/test_golden/` (regenerate with
`software/tools/gen_protocol_golden.py`).

## Joystick

Control panel firmware for Teensy LC. Handles:
- Joystick X/Y axis input
- Rotary encoder for focus control
- Button states
- Serial communication with main controller

## Legacy

Archived firmware versions kept for reference. Not actively maintained.
