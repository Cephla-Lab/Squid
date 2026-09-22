# Squid filter wheel: shortest path, completion window, and per-instrument tuning

Quick guide to the filter wheel features added in September 2026 (PRs #657, #662, #664). All of them apply to the
Cephla (Squid) filter wheel driven by the controller's W axis. Third-party wheels (Optospin, Zaber) are not affected.

| Feature | Needs firmware | Where it is set | Default |
|---|---|---|---|
| Shortest-path slot changes | 1.6 (`auto`), or 1.4 if switched on explicitly | `squid_filterwheel_wrap`, Preferences | `auto` |
| Wheel position kept across a GUI restart | any | automatic | on |
| Completion window | 1.6 | `squid_filterwheel_completion_window_deg`, Preferences | `0` (off) |
| Verify / Tune (command line and GUI) | 1.6 | Utils menu, `tools/filter_wheel_tuner.py` | - |

Firmware 1.6 is flashed on TMC2240 controllers only for now. A machine on firmware 1.5 gets the position record and
the Preferences rows; the other features stay off until it is flashed.

## 1. Prerequisites

In the machine ini (`configuration_<machine>.ini`, section `[GENERAL]`), the wheel has to be enabled as before:

```ini
USE_EMISSION_FILTER_WHEEL = True
EMISSION_FILTER_WHEEL_TYPE = "SQUID"
```

To check the firmware version, look at the log line `SquidFilterWheel.__init__: firmware v1.x` at start, or at
`Filter Wheel Tuning...` (see section 5), which shows it.

To flash firmware 1.6 on a TMC2240 controller: from `firmware/controller`, `pio run -t upload`, with only that
Teensy connected and nothing else holding its port (details in `firmware/README.md`). The wheel homes on the
next normal start.

## 2. Shortest-path slot changes

The wheel may cross its index flag, so slot 8 to slot 1 is one slot instead of seven. On the bench that took a
1-to-8 change from 364 ms to 144 ms; the worst case is now a 4-slot move. Adjacent moves are unchanged.

`squid_filterwheel_wrap` takes three values:

| Value | Meaning |
|---|---|
| `auto` (default) | On when the controller runs firmware 1.6 or later, off below. This is where crossing the flag was verified. |
| `True` | On from firmware 1.4, for a machine you have checked yourself. Below 1.4 a warning is logged and moves take the long way. |
| `False` | Off. Slot changes stay inside one turn, as before. |

`1` and `0` are accepted for `True` and `False`. Anything else stops the software at start with a clear error.

In the GUI: **Preferences > Advanced > Hardware Configuration > Filter Wheel Shortest Path** (Auto / On / Off). A
change takes effect after the restart the dialog offers.

With shortest path on, the filter panel's **Next** at the last slot goes to the first, and **Previous** at the
first goes to the last.

## 3. Wheel position across a GUI restart

The controller cannot report where the wheel is. After every successful move or home the host writes the wheel's
slot to `cache/filter_wheel_position.json`, and a GUI restart (which does not reset the controller) reads it back,
so the system stays on the same channel. Nothing to configure.

When the wheel homes at a restart anyway, one of these happened:

- there was no usable record (first start after the update, or the file was deleted or corrupt);
- the previous run ended in the middle of a move, a home, or a tuning run;
- the wheel's motor settings in the ini changed since the record was written (microstepping, current, velocity,
  acceleration, screw pitch, full steps per revolution, movement sign): the host reconfigures the driver, homes,
  and goes back to the recorded slot;
- the controller was power-cycled between the two runs: the first move is refused, the host re-initialises the
  wheel, homes, and continues.

To force a home at the next start, delete `cache/filter_wheel_position.json`.

## 4. Completion window

With a window set, the controller reports a slot change complete while the last degrees are still travelled, so
the exposure can start earlier. The filter's clear aperture already covers the field at that point.

`squid_filterwheel_completion_window_deg`, in degrees; `0` is off. `5` is the value measured for 32 mm filters:
adjacent moves went from 145 ms to 109 ms at the shipping profile, and to 58 ms with the tuned profile of
section 5, with the wheel about 4.2 degrees (at most 4.5) from the slot when "complete" is reported and ending
exactly on the slot. Do not use it with smaller filters without checking the aperture.

In the GUI: **Preferences > Advanced > Hardware Configuration > Filter Wheel Completion Window**, 0 to 10 degrees.
Needs firmware 1.6; on older firmware a warning is logged once and moves complete at the exact slot as before.

## 5. Verify and Tune

Each wheel carries a different load, so the acceleration it can take without losing steps is a property of the
instrument, not a setting. The tuner measures it with the wheel's encoder. It needs firmware 1.6.

### In the GUI: Utils > Filter Wheel Tuning...

- **Verify** runs 96 slot changes at the machine's current settings and reports PASS or FAIL with the time per
  slot. About 40 s. Use it after mounting filters or when a channel looks wrong.
- **Tune** finds the fastest profile this wheel runs cleanly and confirms it over 96 moves. About one minute
  when the wheel has torque to spare, up to four minutes when it finds a limit and also tries lower top speeds.
  The result is a proposal shown next to the current profile; nothing is saved.
- **Apply and save** writes the proposal to the ini (a backup is kept next to it), reconfigures the wheel, homes
  it and returns to the slot you were on. The new profile is in force at once.
- **Cancel** stops a run after the move it is on. However a run ends, the wheel is put back on your slot.

The dialog refuses to start during live view or an acquisition, in simulation, on firmware below 1.6, and for a
wheel that is not on the W axis (one wheel is supported for now). It cannot be closed while a run is active.

On the bench wheel the tuned profile was 8 microsteps per full step, 6 rev/s, 250 rev/s²: 78 ms per adjacent slot
against 145 ms at the shipping profile. Your machine's numbers will differ; that is the point of running it.

### From the command line

From `software/`, with the GUI closed (the tool opens the controller itself):

```
python tools/filter_wheel_tuner.py verify               # PASS / FAIL at this machine's settings, exit code 1 on FAIL
python tools/filter_wheel_tuner.py tune                 # find and confirm this wheel's profile, print the ini keys
python tools/filter_wheel_tuner.py tune --write-ini     # ... and write them to the machine ini (backup first)
```

The keys written are `microstepping_default_w`, `max_velocity_w_mm` and `max_acceleration_w_mm` in `[GENERAL]`.
`--current-ma <mA>` runs at a reduced motor current to exercise the tool on a lightly loaded wheel; a profile found
that way is never written, it is not the machine's.

## 6. What firmware 1.6 changes for the rest of the instrument

Besides the wheel commands, firmware 1.6 checks for move completion every millisecond instead of every ten and
sends a status packet the moment a command completes. The host therefore learns that any X, Y or Z move is done up
to about 20 ms sooner. Motion itself is unchanged; configured settle times are unchanged.

## 7. Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| Start fails with `squid_filterwheel_wrap must be auto, True or False` | Typo in the ini. Fix the value; the dialog's row writes valid values. |
| Log: `completion window ... needs firmware >= 1.6; ignored` | The controller runs older firmware. Flash 1.6 or set the window to 0. |
| Wheel homes at every restart | The record cannot be written: check that `cache/` exists and is writable. |
| `Filter wheel 1 is being driven directly (filter wheel tuning is running)` | A script or the console tried to move the wheel during a tuning run. Wait for the run to finish or cancel it. |
| Tune reports `TUNE FAIL: no clean profile` | The wheel loses steps even at the gentlest settings: check its load, the motor current in the ini, and that the encoder is connected. |
| After Apply, slot changes land off the filters | Should not happen: Apply reconfigures and homes. If it does, delete `cache/filter_wheel_position.json` and restart, and report it with the log. |

Bench data and decisions: AI-docs `Squid/to-do/2026-09-20-filter-wheel-split-and-per-instrument-tuning.md`.
