# Cephla laser engine v2: configuration, cabling, the Laser Engine tab and the DF 560 nm line

Quick guide to running a Cephla laser engine v2 from Squid. Laser engine v2 is a five-line engine on a Teensy 4.1
carrier board (the firmware identifies itself as `Cephla,LaserEngineCarrier-rev1`, the board revision). It comes in
two variants, which the firmware reports and Squid reads at connect:

| Variant | Fiber | Lines |
|---|---|---|
| 400R, 400HP | 400 µm, standard | diode lines on engine lines 1-5 (which lines are fitted depends on the build) |
| DF | 50 µm, for the Dragonfly | diode lines plus a 560 nm fiber laser with an AOM on line 3 |

The earlier engine (laser engine v1, `control/squid_laser_engine.py`) is still supported; both are selected with the
same key.

| Setting | Where it is set | Default |
|---|---|---|
| Which engine, its USB serial number | machine ini, `laser_engine`, `laser_engine_sn` | no engine |
| DF: 560 nm laser power | Laser Engine tab, kept in `cache/laser_engine_v2.yaml` | the source's minimum |
| DF: 560 nm idle-off | Laser Engine tab, kept in `cache/laser_engine_v2.yaml` | 30 min |
| DF: 560 nm shutter mode | Laser Engine tab, kept in `cache/laser_engine_v2.yaml` | On use |

## 1. Machine ini

In the machine ini (`configuration_<machine>.ini`, section `[GENERAL]`):

```ini
laser_engine = v2
laser_engine_sn = 12345670
```

- `laser_engine`: `v1` or `v2` (any case). Blank or `None` means no laser engine. Any other value stops the software
  at start with an error naming the key and the allowed values.
- `laser_engine_sn`: the engine's USB serial number, for every version (an all-digit number is fine).
  `python -m serial.tools.list_ports -v` lists it.

Legacy v1 keys are still read, so a v1 machine ini keeps working unchanged:

- `use_squid_laser_engine = True` with no `laser_engine` selects v1, and the log says once to use
  `laser_engine = v1` (and `laser_engine_sn`) instead.
- `squid_laser_engine_sn` is the v1 serial number when `laser_engine_sn` is not set. It is never used for v2.
- `use_squid_laser_engine = True` together with `laser_engine = v2` is a contradiction and stops the software at
  start. Together with `laser_engine = v1` it is accepted.

There are no other engine keys in the ini: the DF 560 nm source is found by its own USB IDs, and its power,
idle-off and shutter mode are operator settings kept in the cache (section 4).

## 2. Cabling and how Squid drives the engine

- USB from the computer to the engine's Teensy: intensity set-points, status, arm / disarm, faults.
- Squid controller TTL outputs **D1-D5 to engine TTL1-TTL5**, port n to line n. Exposure timing is the TTL line,
  in hardware; there is no software exposure path. The engine's light source accepts only
  `ShutterControlMode.TTL` and `IntensityControlMode.Software` and refuses a software shutter, which would leave a
  line emitting between exposures.
- The engine line for a wavelength is the port that wavelength's TTL uses in Squid's illumination port map, so
  intensity and exposure always reach the same line. Squid's default map puts 405 on D1, 470/488 on D2, 545-561 on
  D3, 638/640 on D4 and 730-750 on D5; a port-map edit moves both together. A wavelength on a port beyond D5 is left
  to the controller and gets no engine set-point (logged once).
- The port map is the machine's record of which wavelength is on which engine line: the engine reports its variant
  (its drive electronics), not the wavelengths fitted, so an engine with other wavelengths needs only a port-map edit.
  Wavelengths on one port share that line and its set-point, as on other multi-line light sources.
- Intensity is linear in the line's drive: % of its current ceiling (on DF line 3, of the AOM's 0-5 V input).
  Old calibration files (`machine_configs/intensity_calibrations/<wavelength>.csv`, `560_aom.csv`) are not used:
  they record nothing about the engine they were measured on.

At start, Squid connects (one fault reset when anything is latched, since the engine latches at every power-up;
the tab says what it cleared), switches the TECs on, arms and brings every fitted line up, one step per status poll, so the GUI never
waits for the TECs. **Sleep all** or **Disarm** while it runs stops it, and a line put to sleep is not switched on
again by it. A line that was switched off, or an engine that disarmed (key cycle, host timeout), comes back
on the next use. Everything that images declares which channels it uses, through Squid's lighting layer
(`IlluminationController.use`), and that wakes their lines:

| Use | Channels | Held | Waits for READY |
|---|---|---|---|
| live view | the live channel (follows a channel switch) | start → stop | no: warns "image may be dim" |
| snap | the live channel | the frame | no: warns |
| acquisition | its channels, plus the autofocus channel with contrast autofocus | each time point (released in the wait between time points) | yes, before each time point (up to 5 min; a lost link aborts) |
| autofocus (contrast) | the live channel | the autofocus run | yes (skipped if it does not come up) |
| tracking | the tracking channels | the tracking run | yes (not started if it does not come up) |
| `Microscope.acquire_image` (scripts, TCP `acquire_image`) | the current channel | the frame | yes (raises if it does not come up) |
| TCP `turn_on_illumination` | the current channel (follows `set_channel`) | on → `turn_off_illumination` | no |

A new intensity also wakes its line. Squid polls the engine once a second; that poll is also the heartbeat the engine
needs to stay armed, so the engine disarms itself about 5 s after Squid stops.

## 3. The Laser Engine tab

- The engine state as a coloured pill (Armed, Disarmed, Paused (cover open), Fault, Connection lost) and the
  buttons **Arm**, **Disarm**, **Reset faults**, **Wake all**, **Sleep all**.
- The startup bring-up while it runs or when it did not finish, the last engine event, and the last two notices
  (all recent ones in the tooltip).
- One row per fitted line: line, wavelength(s) from the port map, state, set-point, maximum, and the reason when it
  is not READY. States: READY; STARTING; WARMING UP (TEC not in window yet); OFF; NOT ARMED; PAUSED (cover open, the
  engine resumes by itself); SOURCE OFF, NEEDS KEY, NOT CONFIGURED (DF 560); BLOCKED (a TEC left its window: Reset
  faults); FAULT.
- On DF, the **560 nm laser** box (section 4): laser power, idle-off and the shutter mode.

The tab scrolls when Squid's side panel is shorter than it.

## 4. DF: the 560 nm line

The 560 nm fiber laser sits behind an AOM and a shutter. Each has one job:

- **Laser power** (mW) is set by the operator in the tab: type it and press **Set**. It is clamped to the laser's
  own limits, shown next to it. Unset, the laser runs at its minimum. On every start the laser comes up at its
  minimum and goes to the set power once it reads ready. After a power change line 3 reads STARTING until the laser
  reads the new power, so an acquisition does not start at the old one. Squid's intensity never changes the laser
  power.
- **Intensity** in Squid (the 560 channel's intensity) is the AOM amplitude: line 3's set-point, 0-5 V, full scale =
  full transmission. The AOM's on/off input is the controller's D3 TTL, so exposure timing works as for the other
  lines. The intensity is linear in AOM volts (50 % = 2.5 V).
- **Shutter**: a mechanical shutter, never moved per exposure and not part of line 3's readiness. Its mode is set in
  the tab:
  - **On use** (default): open while line 3 is READY **and** something uses the 560 (section 2: live view on the 560
    channel, a 560 snap, a time point of an acquisition with a 560 channel, autofocus, tracking, `acquire_image`, TCP
    illumination on). It opens and closes at once when a use starts or ends; for a use waiting on line 3 it opens as
    soon as the laser reads ready.
  - **Always open**: open whenever line 3 is READY.

  It opens in 1 ms, and nothing images before it has been open that long: a use that starts while line 3 is READY
  returns only then, and the acquisition's readiness check (and every wait for READY) includes it. It stays open while
  line 3 ramps to a new AOM set-point (every 560 intensity change) as long as the source reads ready. It is closed in
  every other state (On use with nothing using the 560, key not cycled, source off or starting, line 3 off or paused,
  a fault, disarm) and before any source restart; a restart is abandoned if the engine refuses the close or the engine
  link is lost on the way.
- **Idle-off**: the 560 laser switches off after this many minutes without use, so it is never left on
  indefinitely. It never switches off while something uses the 560, and counts from the end of the last use. An
  acquisition releases the 560 between time points, so a long time-lapse interval can switch it off; the next time
  point waits for it. 0 means 24 h; there is no "never". Line 3 reads SOURCE OFF from the moment it is switched off,
  and the next use starts it again behind the closed shutter.

The power, the idle-off and the shutter mode are kept across sessions in `cache/laser_engine_v2.yaml` (the power
when **Set** is pressed, the others whenever they are changed):

```yaml
idle_off_560_min: 30.0
power_560_mw: 600.0
shutter_mode_560: on_use   # or always_open
```

A missing or unreadable file, or a bad value, gives the default for that value (logged). If the file cannot be
written the setting still applies for the session and the tab says it was not saved. Deleting the file resets all
three.

A 560 laser found on at connect (a previous session that ended without switching it off) is switched off first.
When the source asks for its key to be turned off and on (as after a power-up), line 3 reads NEEDS KEY and the
bring-up waits for the operator.

### The 560 nm source driver is supplied separately

The driver for the 560 nm source is not in this repository. It is supplied separately as
`control/laser_engine_v2_560_driver.py`, providing `open_560_source()` (no arguments; it finds the source by its USB
IDs) that returns an object with `min_power_mw`, `max_power_mw`, `poll()` (never raises), `set_power_mw()`,
`enable()`, `disable()` and `close()`, as in `SourceDriver` in `control/laser_engine_v2.py`. Without the file, line
3 reads NOT CONFIGURED, the 560 box is not shown, and every other line works normally.

## 5. Simulation

`python3 main_hcs.py --simulation` with `laser_engine = v2` runs a simulated DF engine with a simulated 560 laser
(200-1000 mW), through the same driver code. In simulation the tab saves nothing to the cache: the simulated
laser's limits are not the machine's, and the tab says so when a setting is changed.

## 6. Bench app

From `software/`, with Squid closed (the bench opens the engine itself):

```
python3 tools/laser_engine_v2_bench.py
```

It drives the engine through the same Squid driver without a microscope: a connection bar (Teensy port, Simulate,
Bring up on connect, Connect / Disconnect), the Laser Engine tab, a service panel and the log. The service panel
has per-line intensity, Wake and Sleep, the 560 source state, and a raw command line (a trailing `?` is a query).
The bench has no Squid controller and so no TTL: the per-line **Gate (bench, no TTL)** box holds the line's gate on
in the firmware instead; emission still needs the hardware permits, and on line 3 it drives only the AOM's analog
path. The 560 shutter follows the shutter mode: closed in On use (nothing on the bench declares a use), open at READY
in Always open. Every line starts at a set-point of 0 at connect, and Disconnect releases the gates before it disarms.
The 560 power, idle-off and shutter mode come from, and are saved to, the same `cache/laser_engine_v2.yaml` as
Squid's.

## 7. Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| Start fails with `laser_engine = ... use v1, v2, or leave it blank` | Typo in the ini. Fix the value or remove the key. |
| Start fails with `use_squid_laser_engine = True ... contradicts laser_engine = v2` | Remove `use_squid_laser_engine` (and `squid_laser_engine_sn`). |
| Log: `use laser_engine = v1 (and laser_engine_sn) instead of use_squid_laser_engine ...` | A v1 machine on the legacy keys. It works; replace the keys when convenient. |
| `laser engine: no USB device with serial number ...` | Wrong `laser_engine_sn`, or the engine is not powered or not connected. |
| A line stays WARMING UP | Its TEC is not in its temperature window yet. It becomes READY by itself. |
| A line reads BLOCKED | Its TEC left its window while the line was on. Fix the cause, then **Reset faults**. |
| Every line reads PAUSED | The cover interlock is open. The engine resumes the lines when it closes. |
| Line 3 (DF) reads NEEDS KEY | Turn the 560 key off, then on. |
| Line 3 (DF) reads NOT CONFIGURED | The 560 source driver is not installed (section 4), or the source was not found at connect. |
| 560 power not kept across restarts | The tab says "not saved": check that `cache/` exists and is writable. In simulation nothing is saved. |
