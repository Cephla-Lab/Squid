# Fluidics protocol (engine)

Status: phases 0–1 implemented (engine only, PR pending review); the GUI (Fluidics Protocol record tab,
Fluidics display tab) is phase 2. Design: AI-docs `Squid/in-progress/2026-08-30-fluidics-protocol-design.md`.

## What a protocol is

A Squid-Fluidics **sequence file** (`sequences:` list of `flow_reagent` / `priming` / `clean_up` /
`set_temperature` rows) with two additions: an optional `round:` label on every row and a row type
`imaging` (`folder`, `settings`, `coordinates`). A Squid-only `imaging:` header holds the settings and
coordinate blocks the imaging rows point at; `settings` / `coordinates` may also name a saved acquisition
folder or `acquisition.yaml`, or (coordinates only) a `coordinates.csv`, relative to the protocol file. The
standalone fluidics GUI opens the same file — its loader reads only `sequences`. Until the library accepts
`round` and `imaging` itself, Squid strips both before every library call (`strip_for_library`).

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
  (phase 2) the imaging port; rebuilds Load-Coordinates regions from FOV lists
  (`ScanCoordinates.add_region_from_fovs`); `export_acquisition_settings` produces the two blocks.
- Engine seams used by the runner: `MultiPointController.start_new_experiment(folder, add_timestamp=False)`,
  `last_end_reason` / `last_image_count` (every `run_acquisition()` reports `acquisition_finished` exactly
  once), `protocol_info` → `acquisition.yaml`'s `protocol:` section, `regions[].fovs` in `acquisition.yaml`.

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
`tests/control/core/test_acquisition_settings.py`. Install the library with
`git submodule update --init software/fluidics_v2 && pip3 install --no-deps -e fluidics_v2/software`.
