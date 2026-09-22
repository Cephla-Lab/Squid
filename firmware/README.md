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

### Choosing the controller at upload time

Camera trigger wiring differs between controller generations, so the pin map is selected when
you flash:

| Controller | Command | Camera trigger | Trigger-ready input |
|---|---|---|---|
| Previous controllers (default) | `pio run -e teensy41 -t upload` | pins 29–32 through an inverting stage — the firmware drives the pin LOW to assert | none |
| New controller | `pio run -e teensy41_newctrl -t upload` | pin 19, wired directly to the GPIO — the firmware drives the pin HIGH to assert | pin 18 (3.3 V max) |

Both present an **active-high** trigger at the camera connector. Flashing the wrong profile is
not subtle: the camera receives no trigger (wrong pins) and acquisitions time out.
Arduino IDE: for the new controller add `#define SQUID_CONTROLLER_NEWCTRL` at the top of
`src/controller_profile.h`.

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

An axis left at `DRIVER_UNKNOWN` has its **driver chip** left alone — the driver
dispatcher has no `DRIVER_UNKNOWN` arm, and configuring registers on a chip that
may not be there would be writing into the dark — so its power stage stays at its
reset/strapped state and unenergised. (Its TMC4361A is still configured: ramp,
limit switches and homing settings are written as usual. Those are motion-
controller registers and command nothing on their own.)

Because its current scaling, and therefore its torque, is unknown, **every path
that can put the motor in motion rejects it**. There are four such paths, and
they are enumerated rather than asserted:

1. **Host move commands** — `MOVE_X/Y/Z/W/W2`, `MOVETO_X/Y/Z/W/W2`, and the
   homing branch of `HOME_OR_ZERO`. Return `CMD_EXECUTION_ERROR`. Zeroing is not
   gated: it sets the current position and commands no motion.
2. **`ENABLE_STAGE_PID` (command 26)** — writing
   `ENC_IN_CONF.REGULATION_MODUS = PID_BPG0` hands the axis to the TMC4361A's
   closed loop, which then drives continuously to null the encoder error with no
   further command. Returns `CMD_EXECUTION_ERROR`. `DISABLE_STAGE_PID` and
   `CONFIGURE_STAGE_PID` are not gated — the first stops regulation, the second
   writes coefficients and encoder configuration but never `REGULATION_MODUS`.
3. **Joystick X and Y**, and **4. the focus-wheel Z path** — these reject
   **silently**: they are not host commands, so there is no command to report an
   error against.

The `PID_BPG0` re-enables inside `finalize_homing_*` are covered transitively:
they run only while `is_homing_*` is set, which only a guarded `HOME_OR_ZERO`
can set, and only when `stage_PID_enabled[axis]` is set, which only the guarded
`ENABLE_STAGE_PID` writes.

That is 14 guarded sites in total (10 in `src/commands/stage_commands.cpp`,
1 in `src/commands/commands.cpp`, 3 in `src/operations.cpp`), pinned by a
source-scan test in `test/test_command_layout/` so that deleting a guard fails
the suite.

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

**Hold current means the same thing on both drivers.** `*_MOTOR_I_HOLD` (e.g.
`Z_MOTOR_I_HOLD = 0.5`) is applied in exactly one place — the TMC4361A's
`SCALE_VALUES.HOLD_SCALE_VAL` — for a TMC2660 axis and for a TMC2240 axis alike.
The TMC2240 has a second attenuator available in its own `IHOLD` field and this
firmware deliberately does **not** use it: `IHOLD` is written equal to `IRUN`.
Using both would make hold current `hold_ratio²` (25% where a TMC2660 gives 50%),
and under the family's direct-mode rule that the coil current is scaled by
`IHOLD`, it would cut *run* current too. The cross-driver invariant is pinned by
`test_hold_ratio_attenuates_exactly_once_on_both_drivers` in
`test/test_driver_sequence/`. **Bench:** confirm on a TMC2240 axis that the
standstill/running coil-current ratio is `hold_ratio`, not 1.0 and not
`hold_ratio²`.

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

**Log the driver probe (bench builds only):**

The stepper-driver probe report — each probed slot's verdict and raw probe word
as ASCII on the USB link, at boot and when a filter wheel is initialised via
`INITFILTERWHEEL` — is compiled out by default and enabled with:

```bash
PLATFORMIO_BUILD_FLAGS="-DTMC_PROBE_REPORT" pio run -e teensy41 -t upload
```

> **WARNING: never ship an image built with this flag.** The report is ASCII on
> the same USB link that carries the 24-byte status packets. The host accepts
> any 24-byte window whose last byte is zero; the packets contain zero bytes and
> this text contains none, so a misaligned window is reliably accepted and the
> host reports a **garbage stage position as if it were real** — a wild position
> jump in the GUI and the logs, plus an ack for a command nobody sent. That
> holds mid-session and at boot alike: a host reconnecting while the controller
> starts reads the boot lines ahead of the first status packet.
>
> Build it only to capture the probe words for the bench gate above; the warm
> filter-wheel path is only reachable at runtime.

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
│   ├── test_driver_sequence/       # Pinned SPI register sequences + probe
│   ├── test_seq_types/             # Sequencer program structs + validation
│   └── test_seq_engine/            # Sequencer engine timing tests (virtual clock)
└── src/
    ├── commands/                    # Command handlers
    │   ├── commands.cpp/h          # General commands
    │   ├── light_commands.cpp/h    # Illumination control
    │   └── stage_commands.cpp/h    # Motion control
    ├── def/
    │   └── def_v1.h                # Hardware configuration
    ├── sequencer/                   # Hardware-sequenced acquisition engine (pure C++,
    │   │                           #   natively tested; NOT yet wired to hardware)
    │   ├── seq_types.cpp/h         # Acquisition program structs + validation
    │   ├── seq_hal.h               # Hardware interface the engine drives
    │   └── seq_engine.cpp/h        # Timing state machine (readout overlap, trigger-
    │                               #   ready gating, cancel/abort semantics)
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

### Sequencer engine (`src/sequencer/`)

Runs a whole multichannel z-stack from one program: per step it moves the stack axis (and
filter wheel) during the previous frame's readout, waits for settle + camera ready, then
schedules the trigger and illumination edges. Pure C++11 with no Arduino dependencies — it
drives hardware only through `SeqHal`, so it is tested natively against a virtual clock
(`test/test_seq_engine/`).

- `load(loop, channels, cams, n_cameras)` once per acquisition; `start(now_us,
  wait_timeout_us, stack_axis_start)` once per FOV. `start()` works from `Idle`, `Done` or
  `Failed`, and refuses the whole run (`StackOutOfRange`) before the first move if any stack
  target leaves the axis range (piezo: DAC codes 0–65535).
- States: `WaitHw` → `Exposing` → … → `Returning` → `Done`, or `Failed`. With
  `return_to_start`, `Done` is reported only after the stack axis is back and settled.
- `cancel()` never truncates an exposure; while waiting it winds down at once. `abort(err)` is
  for the laser interlock, the serial watchdog and `TURN_OFF_ALL_PORTS`: terminal immediately.
  Every failure calls `SeqHal::all_off()` **and** `SeqHal::stop_motion()`.
- Time is the 32-bit `micros()` counter, which wraps every 71.6 min. Timestamps are compared
  only through `reached()` (signed difference), never with `<` / `>`; `validate()` bounds
  every duration to `kMaxDurationUs` so that comparison is always valid.
- `SeqError` and `SeqState` values are wire format — append only.

**Hardware binding** (`src/sequencer/seq_bind.*`, `src/timing/event_timer.*`): `seq_tick()` runs
every `loop()` pass. Exposure edges (trigger assert/release, TTL illumination on/off) are
executed by a one-shot timer whose ISR only pops due edges from a time-sorted queue and writes
GPIO — no SPI, no FastLED, no waits; the laser interlock is checked on the illumination-ON
edge. It is separate from the v1 strobe ISR on purpose. An open interlock, the serial watchdog
and `TURN_OFF_ALL_PORTS` abort a running sequence *through the engine*, so the run fails
visibly instead of completing with dark frames. `seq_load()` rejects anything the flashed
controller profile cannot do (camera beyond the trigger count, a ready line the controller
lacks, a TTL port that does not exist).

**Serial transport** (firmware 1.7; `src/commands/sequence_commands.*`, `src/sequencer/seq_staging.*`,
wire contract in `src/sequencer/seq_wire.h`). A v1 command carries 5 payload bytes and the
protocol tracks ONE pending command, which shapes everything:

| Opcode | Payload | |
|---|---|---|
| `SEQ_WRITE` 60 | `[2]` word index, `[3..6]` 4 bytes | absolute write into the staging buffer — a blind v1 resend is idempotent |
| `SEQ_COMMIT` 61 | `[2..3]` length, `[4..5]` CRC-16/CCITT-FALSE | catches a lost chunk, then parses + validates against the flashed controller profile |
| `SEQ_RUN` 62 | `[2..5]` int32 stack start | stays `IN_PROGRESS` until the sequence is terminal, so the host's normal wait works; failure = `CMD_EXECUTION_ERROR` |
| `SEQ_CANCEL` 63 | — | completes when the run is terminal; never truncates an exposure |

The host never polls during a run. Status rides bytes 14–17 of the 10 ms status packet:
`[14]` = state (3 b) · `SeqError` (5 b), `[15]` = detail, `[16..17]` = frames fired. While a
sequence runs the dispatcher refuses every opcode except `HEARTBEAT`, `SEQ_CANCEL`,
`TURN_OFF_ALL_PORTS` and `RESET` (one allow-table, natively tested), and the joystick / focus
wheel are ignored. `TURN_OFF_ALL_PORTS` and `RESET` abort the run through the engine; the
shutdown command itself still reports success. Opcodes 44–50 and status bytes 19–21 are left
to the Z encoder interface (firmware 1.6). **Firmware older than 1.7 answers these opcodes
with success and does nothing — the host must gate on the version.**

**Bench self-test** (`src/sequencer/seq_selftest.*`, never shipped): runs a canned 3-layer ×
2-channel program every 2 s with no host, to put trigger / illumination / stack-axis timing
on a scope. You must say which DAC channel is stepped as the stack axis — the build refuses
to guess, and **7 is the real objective piezo, which will move** (~1 µm per layer around
mid-range). No light by default.

```bash
PLATFORMIO_BUILD_FLAGS="-D SEQ_SELFTEST -D SEQ_SELFTEST_STACK_DAC=7" \
    pio run -e teensy41_newctrl -t upload
# optional: -D SEQ_SELFTEST_TTL_MASK=0x01   strobe TTL port D1 (lasers disconnected or safe!)
#           -D SEQ_SELFTEST_READY_LINE      gate on the camera trigger-ready input (pin 18)
```
Expect on the scope: trigger HIGH for 20.3 ms then 50.3 ms (strobe delay + exposure), repeating
per layer; the stack DAC stepping right after the 50 ms exposure ends, i.e. inside that
frame's readout window; the next trigger no sooner than 20 ms (settle) after the step.

## Joystick

Control panel firmware for Teensy LC. Handles:
- Joystick X/Y axis input
- Rotary encoder for focus control
- Button states
- Serial communication with main controller

## Legacy

Archived firmware versions kept for reference. Not actively maintained.
