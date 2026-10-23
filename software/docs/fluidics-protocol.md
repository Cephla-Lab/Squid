# Fluidics protocol

Status: engine and GUI implemented (phases 0–2), the Flow tab (phase 3), and plate-sipper rigs (well rows,
volume tracking, the sipper's run-time moves; teaching stays in the standalone fluidics GUI). Surfacing
`warn` flow faults beyond the log (run card, manifest, Slack) is not done.
Design: AI-docs `Squid/in-progress/2026-08-30-fluidics-protocol-design.md`.

## What a protocol is

A Squid-Fluidics **sequence file** (`sequences:` list of `flow_reagent` / `priming` / `clean_up` /
`set_temperature` rows) with two additions: an optional `round:` label on every row and a row type
`imaging` (`folder`, `settings`, `coordinates`). A Squid-only `imaging:` header holds the settings and
coordinate blocks the imaging rows point at; `settings` / `coordinates` may also name a saved acquisition
folder or `acquisition.yaml`, or (coordinates only) a `coordinates.csv`, relative to the protocol file. The
library accepts `round` natively; `imaging` rows are Squid-only and are stripped before every library
call (`strip_for_library`) — running a protocol's imaging is Squid's job, never the standalone tool's,
so a combined file is not loadable there (pure-fluidics files remain interchangeable).

```yaml
version: 1
name: merfish_24r_liver
imaging:
  settings:
    current_1358: {channels: [Fluorescence 405 nm Ex, Fluorescence 488 nm Ex], z_stack: {nz: 7, delta_z_um: 0.5}}
  coordinates:
    current_1358: {regions: [{name: A1, fovs: [[12.345, 34.567, 3.210], [12.845, 34.567, 3.211]]}]}
sequences:
  - {type: priming,      round: setup, name: prime, fluidic_port: 25, flow_rate: 5000, volume: 800}
  - {type: flow_reagent, round: R01,   name: probe, fluidic_port: 1,  flow_rate: 2000, volume: 500, incubation_time: 15}
  - {type: imaging,      round: R01,   name: image, folder: R01_image, settings: current_1358, coordinates: current_1358}
  - {type: flow_reagent, round: R01,   name: rinse, fluidic_port: 25, flow_rate: 10000, volume: 2000, repeat: 2}
```

Models: `control/models/fluidics_protocol.py` (`ProtocolFile`, `SettingsBlock`, `CoordinatesBlock`,
`split_into_steps`, `expand_rounds` for "Add rounds…") and `control/models/fluidics_run.py` (`RunManifest`).

### Plate-sipper rigs

A config with a `reagent_selection.nimotion_gantry_system` block has a sipper: an XYZ stage that dips a tip
into well plates, on one selector-valve port. Rows draw from a well instead of a bottle, with the library's
own fields, which reach it unchanged: `well` on `flow_reagent` (with the sipper's `fluidic_port`), `priming`
and `clean_up`; `sipper_well` (where the tip sits while its port is primed; blank: the config's priming or
clean-up well); `dip_cycles`; and the sipper-only `push` row (a well and a flow rate; it draws the config's
`push_volume_ul`, tip to flow cell). A well is `A1` on the config's default plate or `ssct/A1`. Squid-Fluidics'
`sample_sequences/seqfish-plate-sipper.yaml` is a worked example:

```yaml
  - {type: flow_reagent, round: R01, name: hyb,  fluidic_port: 1, well: A1,      flow_rate: 2000, volume: 100}
  - {type: push,         round: R01, name: push, well: ssct/A1, flow_rate: 2000, incubation_time: 20}
  - {type: flow_reagent, round: R01, name: ssct, fluidic_port: 1, well: ssct/A1, flow_rate: 5000, volume: 1000, dip_cycles: 3}
```

Every rig tracks volumes (the library's `VolumeTracker`, `devices.reservoirs`): what each bottle and each well
has given since its last reset, and, where the config declares a loaded volume (every well; a bottle under
`loaded_volume_ul`), what is left. A draw that would not fit is refused before the pump moves, and a run that
would need one is refused before it starts.

## Engine

- `control/fluidics_system.py` — `FluidicsService`: the one import site of the library. Created uninitialized
  in `MicroscopeAddons.fluidics` when `RUN_FLUIDICS = True`; `initialize(config_path)` builds the
  `FluidicsSystem` (blocking; call off the GUI thread). `SIMULATE_FLUIDICS` under `[SIMULATION]` picks the
  simulated system. Library log records (`fluidics.*`, `XCaliburD`) are forwarded into the `squid` logger.
- `control/core/fluidics_protocol/` — `resolve.py` (bind imaging rows to blocks, collect every problem,
  and gate the whole protocol's draws once with the library's `check_reservoirs`: the library itself gates
  each run, i.e. each step, only as it starts),
  `runner.py` (`ProtocolRunner`: IDLE → RUNNING ⇄ PAUSED → HELD → … → ENDED; one library run per contiguous
  block of fluidics rows with the same round; one acquisition per imaging row; HELD actions resume-from-
  sequence / restart / skip / accept / end), `ports.py` (the two engines as `typing.Protocol`s),
  `library_port.py` (`LibraryFluidicsPort` over `FluidicsSystem`), `manifest.py` (run folder, atomic
  `run_manifest.json`, `find_unfinished_runs` for crash recovery). Each fluidics attempt records
  `reagent_used_ul` per port from the volume tracker (`used_rows()`; a well's draws count under the sipper's
  port).
- `control/core/acquisition_settings.py` — Qt-free `apply_acquisition_settings` shared by the TCP command and
  the imaging port; rebuilds Load-Coordinates regions from FOV lists
  (`ScanCoordinates.add_region_from_fovs`); `export_acquisition_settings` produces the two blocks.
- Engine seams used by the runner: `MultiPointController.start_new_experiment(folder, add_timestamp=False)`,
  `last_end_reason` / `last_image_count` (every `run_acquisition()` reports `acquisition_finished` exactly
  once), `protocol_info` → `acquisition.yaml`'s `protocol:` section, `regions[].fovs` in `acquisition.yaml`.

## GUI

Both tabs exist only when `RUN_FLUIDICS = True` (`control/widgets_fluidics/`).

**Fluidics display tab** (next to Live View): the instrument side and the Protocol editor.

- The instrument side is a column with a draggable divider (`InstrumentColumn`): the instrument block
  (Initialize, manual control, device status) over the Log / sensor tabs. In a window too short for both,
  the block scrolls and the tabs keep the height their plots need (on a short screen the divider moves up
  the first time a plot tab is opened); with room for both (≈1300 px) nothing scrolls. Once the divider is
  dragged the split is the operator's.

- **Manual control** also has an inline **Prime / Clean** row (no pop-ups), the old widget's fields:
  the ports to prime (`1-4, 25`), the wash port the final volume is drawn from, that volume, a flow
  rate, and a repeat (Clean). It calls the library operation directly (`system.run_manual` →
  `operations.priming_or_clean_up(wash_port, flow, volume, use_ports=..., clean_up=...)`): each named port's
  tubing is filled with its **config** `tubing_fluid_amount_ul`, then `volume` is drawn from the wash port.
  On a sipper rig the list must include the sipper's port (the library refuses to skip it), whose line is
  filled from the config's priming well (Prime) or clean-up well (Clean). **Stop** aborts it. It shares the
  library session with protocol runs, so the two mutually exclude.
- **Sipper** (sipper rigs only): where the tip is (`in ssct/A1`, `retracted`, or not known to be retracted),
  **Go to well** (plate + well), **Retract**, **Home**, and **Stop** for a move started there; each is a
  library manual job. **Do not lower tip** (on when Squid starts) makes Go to well stop retracted over the
  well, to check a taught position without dipping. It applies to that button only: the library's switch is
  set for the one move and put back, so runs and Prime/Clean always lower the tip. Teaching (A1, dip depth,
  travel height, the dedicated wells) is done in the standalone
  fluidics GUI's Robot tab, which cannot hold the rig while Squid does. After a run the tip rests in its last
  well, and that plate cannot be reset until it is retracted.
- A protocol start is refused while any job holds the fluidics session (Prime/Clean, a manual-control or
  sipper move, a reservoir reset).
- **Initialize** builds the `FluidicsSystem` from `machine_configs/fluidics_config.yaml` (path editable,
  remembered in `cache/fluidics_protocol.json`) off the GUI thread; on success the upstream manual-control
  widget (`fluidics.qt.manual_control`), device status, and — when the config lists a temperature
  controller / flow sensors that came up — the Temperature / Flow tabs appear. Log | Temperature | Flow |
  Reservoirs live under the status panel; Temperature and Flow embed the fluidics module's own plot widgets
  (each with the standalone software's Start Recording CSV).
- **Reservoirs** embeds the library's Reservoirs window: per bottle and per well, Accumulated (given since
  the last reset), Current (left) and Loaded, with **Reset selected / Reset <plate> / Reset all** and
  **Set selected… / Set <plate>…** (or a double click on a Loaded cell), which write the config's volume
  fields. A tracked reservoir whose record is missing or whose loaded volume changed comes up undeclared,
  and runs are refused until it is reset. These edits, and the Sipper moves, are live when no protocol
  runs and while one is HELD: a step refused for an empty well goes on by Retract, reloading the plate,
  Reset, then **Restart step**.
- **Flow** has one panel per sensor: reading, plot (invalid samples are gaps), and the **Draw protection**
  combo (`off` / `warn` / `stop`), live during a run — a change applies from the next draw. `stop` halts the
  draw and fails the sequence, so the run goes HELD with the flow fault as its message (and Slack hears it);
  `warn` only logs (Log tab, `run.log`, the library's run report). Draw protection exists only for the
  `Flow Cell` application: on any other, the library's bring-up switches a configured `warn`/`stop` off
  and reports it as a bring-up issue (status line, log); the combo is disabled.
- **Protocol editor**: rounds-grouped step list with include checkboxes and live validation (on a sipper
  rig against its plates; the Port column shows a row's well), a field editor with "apply to all rows with
  this name", **Add rounds…** (template round × N with a port list for one row, and, when the round draws
  from wells, a well list: round k moves every well row to the k-th well on its own plate, prefilled with
  the wells after the round's own along the row, A1 → A2, A3, …),
  **+ Imaging** (folder auto-named, e.g. `R01_image`, editable per row), and per-imaging-row settings/coordinates
  sources: **Apply current** captures the Wellplate Multipoint panel into a header block; **From file…**
  points at a saved acquisition folder / `acquisition.yaml` / `coordinates.csv`.

**Fluidics Protocol record tab** (with the acquisition panels): name the run, pick where run folders go,
**Start run…** (pre-flight dialog lists every problem — a reservoir that cannot supply the whole protocol
among them — or the run summary, with the tracked reservoirs that will have least left), then a status card (state, step,
progress, elapsed, open-folder) with **Pause** (finishes the current step first; during imaging it reads
"Pause after imaging"), **Abort step**, and **Abort run…**. A failed step raises the orange HELD panel —
resume from the failed sequence / restart the step / skip / accept (imaging `completed_with_errors`) /
end run, with an optional TEC-restore checkbox. A run in progress locks the protocol structure, keeps the
display on the Fluidics tab, and asks before exit; after Initialize (and when the Save to folder changes) unfinished
run folders offer crash recovery — reopening the run HELD at the interrupted step. Run notifications go to
Slack when the notifier is configured.

## Run folder

```
{run_name}_{YYYY-MM-DD_HH-MM-SS}/
├── protocol.yaml         exactly what ran (file-based blocks inlined)
├── run_manifest.json     status, cursor, per-step attempts, TEC state, pid + heartbeat
├── run.log               protocol + fluidics log for the whole run
├── R01_image/            a standard Squid experiment folder (+ protocol_step.json)
├── R02_image/            attempt 1 — aborted, kept, never renamed (aborted.json inside)
└── R02_image_attempt2/   attempt 2 — completed
```

## Testing

`tests/control/core/fluidics_protocol/` (scripted fakes for both engines; the library tests skip when the
`fluidics` package is not installed), `tests/control/test_fluidics_system.py`,
`tests/control/core/test_acquisition_settings.py`; GUI: `tests/control/widgets_fluidics/`
(`test_end_to_end_gui_run.py` drives a 3-round protocol through the real Qt imaging path, including
abort-during-imaging → Restart → `_attempt2`). Plate-sipper: `test_plate_sipper.py` (a two-round well protocol
through the runner on the simulated sipper rig, and the whole-protocol volume gate) and
`widgets_fluidics/test_sipper_and_reservoirs.py`, over `SIPPER_CONFIG_YAML` (a copy of the library's sample
rig). Install the library with
`git submodule update --init software/fluidics_v2 && pip3 install --no-deps -e fluidics_v2/software`.
