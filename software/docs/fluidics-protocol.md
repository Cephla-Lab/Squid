# Fluidics protocol

Status: engine and GUI implemented (phases 0–2); the flow-sensor tab is phase 3.
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
  folder_pattern: "{round}_{step}"
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

## Engine

- `control/fluidics_system.py` — `FluidicsService`: the one import site of the library. Created uninitialized
  in `MicroscopeAddons.fluidics` when `RUN_FLUIDICS = True`; `initialize(config_path)` builds the
  `FluidicsSystem` (blocking; call off the GUI thread). `SIMULATE_FLUIDICS` under `[SIMULATION]` picks the
  simulated system. Library log records (`fluidics.*`, `XCaliburD`) are forwarded into the `squid` logger.
- `control/core/fluidics_protocol/` — `resolve.py` (bind imaging rows to blocks, collect every problem),
  `runner.py` (`ProtocolRunner`: IDLE → RUNNING ⇄ PAUSED → HELD → … → ENDED; one library run per contiguous
  block of fluidics rows with the same round; one acquisition per imaging row; HELD actions resume-from-
  sequence / restart / skip / accept / end), `ports.py` (the two engines as `typing.Protocol`s),
  `library_port.py` (`LibraryFluidicsPort` over `FluidicsSystem`), `manifest.py` (run folder, atomic
  `run_manifest.json`, `find_unfinished_runs` for crash recovery).
- `control/core/acquisition_settings.py` — Qt-free `apply_acquisition_settings` shared by the TCP command and
  the imaging port; rebuilds Load-Coordinates regions from FOV lists
  (`ScanCoordinates.add_region_from_fovs`); `export_acquisition_settings` produces the two blocks.
- Engine seams used by the runner: `MultiPointController.start_new_experiment(folder, add_timestamp=False)`,
  `last_end_reason` / `last_image_count` (every `run_acquisition()` reports `acquisition_finished` exactly
  once), `protocol_info` → `acquisition.yaml`'s `protocol:` section, `regions[].fovs` in `acquisition.yaml`.

## GUI

Both tabs exist only when `RUN_FLUIDICS = True` (`control/widgets_fluidics/`).

**Fluidics display tab** (next to Live View): the instrument side and the Protocol editor.

- **Manual control** also has an inline **Prime / Clean** row (no pop-ups), the old widget's fields:
  the ports to prime (`1-4, 25`), the wash port the final volume is drawn from, that volume, a flow
  rate, and a repeat (Clean). It calls the library operation directly (`system.run_manual` →
  `operations.priming_or_clean_up(wash_port, flow, volume, use_ports=...)`): each named port's tubing
  is filled with its **config** `tubing_fluid_amount_ul`, then `volume` is drawn from the wash port.
  **Stop** aborts it. It shares the library session with protocol runs, so the two mutually exclude.
- **Initialize** builds the `FluidicsSystem` from `machine_configs/fluidics_config.yaml` (path editable,
  remembered in `cache/fluidics_protocol.json`) off the GUI thread; on success the upstream manual-control
  widget (`fluidics.qt.manual_control`), device status, and — when the config lists a temperature
  controller — the Temperature tab appear. Log | Temperature | Reagents live under the status panel;
  Temperature embeds the fluidics module's own per-channel plot widgets (each with the standalone
  software's Start Recording CSV), Reagents accumulates estimated µL per port.
- **Protocol editor**: rounds-grouped step list with include checkboxes and live validation, a field editor
  with "apply to all rows with this name", **Add rounds…** (template round × N with a port list),
  **+ Imaging** (`folder_pattern`, default `{round}_{step}`), and per-imaging-row settings/coordinates
  sources: **Apply current** captures the Wellplate Multipoint panel into a header block; **From file…**
  points at a saved acquisition folder / `acquisition.yaml` / `coordinates.csv`.

**Fluidics Protocol record tab** (with the acquisition panels): name the run, pick where run folders go,
**Start run…** (pre-flight dialog lists every problem, or the run summary), then a status card (state, step,
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
abort-during-imaging → Restart → `_attempt2`). Install the library with
`git submodule update --init software/fluidics_v2 && pip3 install --no-deps -e fluidics_v2/software`.
