# Quickstart: Run a Plate Scan via the API

Run an automated well-plate scan without touching the GUI. This is the 5-minute
version; for the full endpoint reference see [core-service-api.md](core-service-api.md).

## 1. Start the software

```bash
cd software
python3 main_hcs.py --simulation --start-server   # drop --simulation on a real instrument
```

Wait ~30 s, then confirm the API is up:

```bash
curl http://127.0.0.1:8060/v1/healthz          # -> {"alive": true}
```

Open **http://127.0.0.1:8060/docs** in a browser to explore and try every endpoint interactively.

> **macOS note:** if requests hang after the window is hidden, launch with
> `caffeinate -i python3 main_hcs.py ...` (macOS "App Nap" pauses background processes).

## 2. See what's available

```bash
curl http://127.0.0.1:8060/v1/imaging/channels      # channel names for the current objective
curl http://127.0.0.1:8060/v1/sample_formats        # supported plate formats
curl http://127.0.0.1:8060/v1/system/capabilities   # objectives, stage limits, camera, versions
```

Channel names must match exactly, e.g. `BF LED matrix full`, `Fluorescence 488 nm Ex`.

## 3. Run a scan — pick one of three ways

### A. Inline grid (fastest — no setup)

Name wells, channels, and an FOV grid per well. Single z-plane, single timepoint.

```bash
curl -X POST http://127.0.0.1:8060/v1/acquisitions \
  -H 'Content-Type: application/json' \
  -d '{
    "experiment_id": "my_scan",
    "grid": {
      "wells": "A1:B3",
      "channels": ["BF LED matrix full", "Fluorescence 488 nm Ex"],
      "nx": 2, "ny": 2,
      "overlap_percent": 10,
      "wellplate_format": "96 well plate"
    },
    "overrides": {"output_path": "/tmp/scans"}
  }'
```

`wells` accepts a range (`A1:B3`) or a list (`A1,A2,B1`). Add autofocus with
`"autofocus": {"reflection": true}`.

### B. Named method (reusable — best for schedulers)

Store a full acquisition definition once (z-stack, timelapse, AF, regions), then run it by name.
No filesystem access needed — author it entirely over the API.

```bash
# Create the method (one time)
curl -X POST http://127.0.0.1:8060/v1/methods \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "spheroid_4ch_20x",
    "config": {
      "acquisition": {"widget_type": "wellplate"},
      "sample": {"wellplate_format": "96 well plate"},
      "z_stack": {"nz": 5, "delta_z_mm": 0.002},
      "time_series": {"nt": 1, "delta_t_s": 0},
      "channels": [{"name": "BF LED matrix full"}, {"name": "Fluorescence 488 nm Ex"}],
      "autofocus": {"contrast_af": false, "laser_af": true},
      "wellplate_scan": {
        "scan_size_mm": 1.5, "overlap_percent": 10,
        "regions": [{"name": "A1", "center_mm": [14.3, 11.36, 2.1], "shape": "Square"}]
      }
    }
  }'

# Run it, overriding wells and output path per run
curl -X POST http://127.0.0.1:8060/v1/acquisitions \
  -d '{"method": "spheroid_4ch_20x",
       "overrides": {"wells": "A1:D6", "output_path": "/data/exp42"}}'
```

`curl http://127.0.0.1:8060/v1/methods` lists methods; `GET /v1/methods/{name}` shows one.
A `wells` override replaces the stored regions. You can also author a method by saving an
acquisition in the GUI and copying its YAML into `machine_configs/acquisition_methods/`.

### C. Existing GUI-saved YAML

Point at any `acquisition.yaml` a previous GUI session saved:

```bash
curl -X POST http://127.0.0.1:8060/v1/acquisitions \
  -d '{"yaml_path": "/path/to/acquisition.yaml", "overrides": {"wells": "A1:B2"}}'
```

## 4. What you get back

Every start returns **202 Accepted** with a job handle:

```json
{
  "job_id": "5903a3e01d4f",
  "expected_fov_count": 1,
  "expected_image_count": 1,
  "output_dir": "/tmp/scans/my_scan_2026-07-03_17-23-56"
}
```

## 5. Track it

```bash
curl http://127.0.0.1:8060/v1/jobs/5903a3e01d4f
```

```json
{
  "state": "COMPLETED",
  "outcome": "SUCCESS",
  "progress": {"images_acquired": 1, "total_images": 1,
               "af_failures": 0, "save_failures": 0, "elapsed_s": 1.6},
  "result": {"output_dir": "...", "image_count_written": 1,
             "end_reason": "completed", "skipped_fovs": []}
}
```

- `state`: `ACCEPTED` → `RUNNING` → `COMPLETED`. `outcome`: `SUCCESS` / `FAILURE` / `ABORTED` / `PARTIAL`.
- A `.done` file appears in `output_dir` when data is fully written to disk.
- `curl http://127.0.0.1:8060/v1/jobs/last` returns the most recent job (survives restarts).

Live event stream instead of polling:

```bash
curl -N http://127.0.0.1:8060/v1/events    # state_changed, progress, job_completed, fault
```

Abort a running scan:

```bash
curl -X POST http://127.0.0.1:8060/v1/jobs/5903a3e01d4f/abort   # graceful; finishes current FOV
```

## 6. Validate before running (optional)

`preflight` runs all checks (channels, format, wells, disk space) with **no hardware motion**:

```bash
curl -X POST http://127.0.0.1:8060/v1/acquisitions/preflight -d '<same body as the run>'
# -> {"ok": true, "checks": [{"name": "channels", "ok": true}, ...]}
```

## Prefer a command line or an AI assistant?

Same three modes, wrapped:

```bash
# CLI script
python scripts/run_acquisition.py --method spheroid_4ch_20x --wells "A1:B3" --wait
python scripts/run_acquisition.py --yaml acquisition.yaml --wait

# Claude Code (MCP): just ask in plain language, e.g.
#   "scan wells A1 to B3 in brightfield and 488, then tell me when it's done"
```

## If something fails

Every error is a structured fault you can branch on — never a bare string:

```json
{"error": {"category": "CONFIG", "code": 3001, "recoverable": false,
           "scheduler_action": "REJECT_PLATE", "terminal": true,
           "message": "Channel 'GFP' not found for objective '20x'"}}
```

Common ones: `CONFIG` (unknown channel/objective/format), `INVALID_PARAM` (out of range),
`IO` (output path not writable / disk full), `HARDWARE_TRANSIENT` (retryable). Full catalogue
in [core-service-api.md](core-service-api.md#faults).
