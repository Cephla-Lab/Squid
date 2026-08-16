# Squid laser engine firmware

Firmware for the Cephla laser engine controller (Teensy 4.1). The host-side
driver that talks to this board is `software/control/squid_laser_engine.py`;
the two must stay in step, which is why the sketch is vendored here rather than
tracked only in its own repository.

Upstream: https://github.com/veerwang/laser_engine (base `V1.27`).

## Wire protocol

Host -> board, each frame `<body><crc32 LE><0x0A 0x0D>`:

| Command | Body | Meaning |
|---|---|---|
| `Q` | `'Q'` | query status |
| `W` | `'W' + <uint32 LE channel>` | wake channel |
| `S` | `'S' + <uint32 LE channel>` | sleep channel |

The board replies to `Q` with a 72-byte `'S'` status payload: identifier(1) +
laser TTL(5) + 6x TCM block(7: state, temp, TEC voltage, TEC current) + 6x dT +
6x hi-temp setpoint. Temperatures are signed big-endian centidegrees.

`ChannelState` values are sent verbatim, so the enum here and
`LaserChannelState` in the Python driver must match exactly. A build whose enum
is offset by one reports every channel one state out — `ACTIVE` arrives as
`WAKE_UP`, and a normal `PREPARE_SLEEP` arrives as `CHECK_ERROR`, which the
driver treats as a fault. Check this first if states look wrong.

## Local changes vs upstream V1.27

- **Sleep timeout is 4 hours on all six channels.** Upstream used 3 hours for
  405/470/638/735 and 30 minutes for the two 55x modules, so 55x dropped out of
  `ACTIVE` during long acquisitions with idle stretches.

- **`CHANNEL_TEC_EXTERNAL[]` marks channels whose TEC controller is not wired to
  this board.** On this engine that is 638 and 735: they have TEC, but the
  controllers run standalone and are left powered on continuously, so the board
  has no TCM link to them. Those channels are pinned `ACTIVE` — reported ready
  to the host and enabled by `doEnableLasersAction()`, which gates
  `enableLaser()` on `ACTIVE` — and are skipped by the thermal state machine,
  the idle-sleep rule, and host wake/sleep commands.

- **Startup no longer blocks on absent TCM modules.** `getAdjustTemperature()`
  and `enableAllTCMs()` only advanced to the next channel when a reply arrived,
  so a channel with no controller on the bus was re-queried forever and
  `setup()` never returned: the board enumerated over USB but was completely
  unresponsive. They now skip `CHANNEL_TEC_EXTERNAL` channels, and each
  handshake is capped by `TCM_STARTUP_TIMEOUT` (10 s) so a missing module can
  never prevent the board from reaching `loop()` and staying reflashable.

`CHANNEL_TEC_EXTERNAL` is per-engine. On an engine with every controller wired,
set all six entries to `false`.

## Building and flashing

Board `teensy:avr:teensy41`. Libraries: FastLED, CRC32 (`bakercp/CRC32`) and
elapsedMillis (bundled with Teensyduino).

```sh
arduino-cli lib install CRC32 FastLED
arduino-cli compile -b teensy:avr:teensy41 firmware/laser_engine
```

Flash by USB port location, not by serial device — a Squid has more than one
Teensy attached and `arduino-cli` targets whichever board enters the bootloader.
Resolve the laser engine's location from its USB serial number first:

```sh
# find the laser engine's USB path (its serial number is SQUID_LASER_ENGINE_SN)
readlink -f /sys/class/tty/ttyACM0/device     # -> .../usb1/1-13/1-13.2/...
arduino-cli board list                        # confirm it is listed as a Teensy port

arduino-cli upload -b teensy:avr:teensy41 -p usb1/1-13/1-13.2 firmware/laser_engine
```

Flashing the wrong board overwrites the main microscope controller, so confirm
the mapping before uploading.
