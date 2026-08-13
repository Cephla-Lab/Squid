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
test_crc8:          test_crc8_empty_data           [PASSED]
test_crc8:          test_crc8_single_byte_zero     [PASSED]
test_protocol:      test_command_ids_are_unique    [PASSED]
test_driver_math:   test_tmc2660_shipped_xy        [PASSED]
...
================ 134 test cases: 134 succeeded ================
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
- XYZ stage motion control (TMC4361A motion controller + TMC2660 or TMC2240 power stage)
- Illumination control (lasers and LED matrix)
- Camera triggering
- Serial communication with host software

### Stepper driver auto-detection

Firmware 1.5 and later runs on boards populated with either a TMC2660 or a
TMC2240 behind the TMC4361A. One image serves both: the TMC4361A is the motion
controller in either case, and only five operations differ between the power
stages (init, run/hold current, microstepping, enable, StallGuard). They are
dispatched through a seam in `src/tmc/drivers/`. Ramp generation, limit
switches, the encoder, PID and homing are unchanged and driver-agnostic.

There is **no protocol change** — command codes, packet layout and the host
contract are identical to 1.4, so this firmware runs against existing host
software with no changes.

#### How an axis is identified

Each axis is probed at initialisation by reading the TMC2240's `IOIN.VERSION`
through the TMC4361A's cover-datagram passthrough:

| Read | Verdict |
|------|---------|
| `0x40` in bits [31:24] | TMC2240 |
| A live response that is not `0x40` | TMC2660 |
| All-zeros or all-ones (nothing answering) | `DRIVER_UNKNOWN` |

Cover reads are unreliable by construction, so the probe reads three times and
takes the verdict a strict majority agrees on. Three reads that disagree mean a
flaky bus, and the result is `DRIVER_UNKNOWN`.

A TMC2660 has no ID register, so it cannot be positively identified — "something
answered, and it was not a 2240" is the only evidence available. This detects an
unpopulated axis, a dead chip or a stuck SPI bus. It does **not** detect a chip
that answers like a TMC2660 but is a different part.

The result is printed once at boot, per axis, on the USB serial link:

```
[TMC] X: TMC2240 probe_raw=0x40000000
[TMC] Y: TMC2660 probe_raw=0x00004000
[TMC] Z: UNKNOWN probe_raw=0x00000000
```

`probe_raw` is the raw word of the **last** of the three reads, not a summary,
and it is emitted only for axes that were actually probed.

#### What happens to an unidentified axis

An axis left at `DRIVER_UNKNOWN` is never written to — configuring registers on
a chip that may not be there would be writing into the dark — so its power stage
stays at its reset/strapped state and unenergised. Because its current scaling,
and therefore its torque, is unknown, **every motion path rejects it**:

- Host move commands (`MOVE_X/Y/Z/W/W2`, `MOVETO_X/Y/Z/W/W2`, and the homing
  branch of `HOME_OR_ZERO`) return `CMD_EXECUTION_ERROR`. Zeroing is not gated —
  it sets the current position and commands no motion.
- The joystick X and Y paths and the focus-wheel Z path reject **silently** —
  they are not host commands, so there is no command to report an error against.

That is 13 guarded sites in total (10 in `src/commands/stage_commands.cpp`,
3 in `src/operations.cpp`), pinned by a source-scan test in
`test/test_command_layout/` so that deleting a guard fails the suite.

Guarding the joystick and focus wheel is not incidental. Rejecting a host move
leaves `*_commanded_movement_in_progress` false, which is exactly the condition
that opens the joystick gate — without these guards the operator could drive the
very axis the firmware had just locked out, at unknown current.

#### Recovering from a failed probe

- **X / Y / Z** — send `INITIALIZE`. It re-probes only the axes currently at
  `DRIVER_UNKNOWN`, then re-initialises and re-applies current, ramp, limits and
  homing configuration. Axes that were identified successfully keep their verdict
  and see no extra SPI traffic. No power cycle is needed.
- **W / W2** — `INITFILTERWHEEL` always re-probes, because it re-runs
  `tmc4361A_init()` which resets the cached verdict.

#### Motor current and StallGuard

Motor current is requested in mA by the host (`CONFIGURE_STEPPER_DRIVER`, command
21) and converted per driver by the pure, Arduino-free math in
`src/tmc/drivers/driver_math.h`, which is unit-tested on the host.

The TMC2660 conversion is deliberately unchanged: it reproduces pre-1.5 firmware
bit-identically for every shipped sense-resistor value, so **no fielded machine's
motor current moves as a side effect of this feature**. That formula is known to
run 4–7% low (it assumes `V_FS = 0.325 V` where `DRVCONF` selects `0.310 V`, and
divides by 31 rather than `(CS+1)/32`), but correcting it would change current on
every existing machine, so it is left for a separate change with its own bench
thermal check. Do not "fix" it here.

The one behavioural change on that path: a current too large for the 5-bit `CS`
field is now refused, leaving the axis at its previous current, instead of
wrapping. Pre-1.5, 1100 mA on X wrapped to `CS = 0`, i.e. *minimum* current.
`CONFIGURE_STEPPER_DRIVER` reports no status either way, so an out-of-range
request is still silent from the host's point of view — check the axis actually
moves as expected after changing current in the INI.

StallGuard on the TMC2240 is **StallGuard2** — `COOLCONF.SGT` for the threshold
and `COOLCONF.SFILT` (bit 24) for the filter — not StallGuard4/`SG4_THRS`, which
only operates under StealthChop. This firmware selects SpreadCycle, and
StallGuard2's flag is also the only one the TMC4361A's `STOP_ON_STALL` can
consume. The two parts share the threshold *field name* but not its scale, so a
TMC2240 board needs its own `SGT` value; the TMC2660's is not transferable.

#### Bench gate before deploying to a TMC2660 board

> **STOP — this firmware must not go onto a TMC2660 board for general use until
> the bench gate below has passed.**
>
> The probe's liveness rule rests on an assumption that **could not be verified
> in software**: that a live TMC2660 never returns all-zeros. The reasoning is
> that the 2660 is a 20-bit shift register, so past bit 20 of a 40-bit frame it
> shifts our own transmitted address byte back out, making a zero word
> impossible. The *alignment* half of that is confirmed from the reference
> implementation. The *pass-through* half — that SDO keeps shifting rather than
> tri-stating or holding after bit 20 — is confirmed nowhere: no datasheet text,
> and the reference implementation is no evidence either way because its probe
> has no liveness test at all.
>
> **If the assumption is wrong, every TMC2660 axis on every existing board reads
> `DRIVER_UNKNOWN` and refuses to move on first boot.** Whole installed base.
>
> The gate is step 0 of section 10 of the design doc
> (`AI-docs Squid/.../2026-08-12-tmc2240-driver-support-design.md`). Capture
> `probe_raw` from a known-TMC2660 axis on **both** probe paths — cold boot
> (`RDSEL = 0`) and a warm filter-wheel re-init (`SDOFF = 1`, `RDSEL = 2`, where
> SG and SE are both zero at standstill, the case most likely to read
> all-zeros). Read the *verdict* alongside the word: `probe_raw` is the last of
> three reads, so a zero word beside a `TMC2660` verdict is one flaky read, not
> the failure. The assumption has failed only when the verdict itself is
> `DRIVER_UNKNOWN`. If it has, the remedy is to drop the all-zeros half of the
> liveness test and keep only all-ones — **not** to fall back on
> `COVER_DRV_HIGH_RD`, whose bits are the ones most likely to be zero anyway.

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

**Log the runtime driver probe (bench builds only):**

The stepper-driver probe result is reported at boot in every build. The *runtime*
report — the one emitted when a filter wheel is initialised via `INITFILTERWHEEL`
while the instrument is running — is compiled out by default and enabled with:

```bash
PLATFORMIO_BUILD_FLAGS="-DTMC_PROBE_REPORT_RUNTIME" pio run -e teensy41 -t upload
```

> **WARNING: never ship an image built with this flag.** The report is ASCII on
> the same USB link that carries the 24-byte status packets. Mid-session the host
> accepts any 24-byte window whose last byte is zero; the packets contain zero
> bytes and this text contains none, so a misaligned window is reliably accepted
> and the host reports a **garbage stage position as if it were real** — a wild
> position jump in the GUI and the logs, plus an ack for a command nobody sent.
> At boot none of that applies, because no status packet has been sent yet, which
> is why the boot report needs no flag.
>
> Build it only to capture the warm-path probe word for the bench gate above,
> which cold boot cannot exercise.

### Source Structure

```
controller/
├── main_controller_teensy41.ino    # Entry point
├── platformio.ini                   # PlatformIO config
├── test/                            # Unit tests (run with pio test -e native)
│   ├── test_crc8/                  # CRC8 checksum tests
│   ├── test_protocol/              # Protocol/command ID tests
│   ├── test_command_layout/        # Command dispatch + driver fail-safe guards
│   ├── test_driver_math/           # Current/microstep math, both drivers
│   ├── test_driver_regs/           # Register datagram builders
│   └── test_driver_sequence/       # Pinned SPI register sequences + probe
└── src/
    ├── commands/                    # Command handlers
    │   ├── commands.cpp/h          # General commands
    │   ├── light_commands.cpp/h    # Illumination control
    │   └── stage_commands.cpp/h    # Motion control
    ├── def/
    │   └── def_v1.h                # Hardware configuration
    ├── tmc/                         # TMC4361A motion controller library
    │   └── drivers/                # Power-stage seam (TMC2660 / TMC2240)
    │       ├── stepper_driver.h    # Dispatch contract + driver_type
    │       ├── driver_probe.cpp/h  # Runtime per-axis driver identification
    │       ├── driver_math.h       # Pure current/microstep math (host-tested)
    │       ├── tmc2660.cpp/h       # TMC2660 implementation
    │       └── tmc2240.cpp/h       # TMC2240 implementation
    ├── utils/
    │   └── crc8.cpp/h              # CRC calculation
    ├── init.cpp/h                   # Initialization routines
    ├── operations.cpp/h             # Main loop operations
    ├── serial_communication.cpp/h   # Serial protocol handling
    ├── functions.cpp/h              # Utility functions
    ├── globals.cpp/h                # Global state variables
    └── constants.h                  # Constants and pin definitions
```

## Joystick

Control panel firmware for Teensy LC. Handles:
- Joystick X/Y axis input
- Rotary encoder for focus control
- Button states
- Serial communication with main controller

## Legacy

Archived firmware versions kept for reference. Not actively maintained.
