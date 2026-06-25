# Acquisition Watchdog

Detect when an acquisition ends prematurely — process **crash / hang / kill**, **fatal
error**, or **user abort** — and send a single Slack alert. Works for acquisitions
launched from the GUI *and* from the MCP control server, on Ubuntu and Windows.

## Motivation

A crashing process cannot report its own death. The existing in-process
`SlackNotifier` (`control/slack_notifier.py`) runs on a daemon thread *inside* the GUI
process, so it can report live errors and a clean finish, but it can never report a
segfault in a camera SDK, an OOM-kill, `os._exit()`, a power-loss of the process, or a
frozen UI — the thing that would send the alert is the thing that died.

The fix is an **independent process** that watches on-disk breadcrumbs the app leaves
behind. Because both the GUI and the MCP control server run acquisitions through the
same engine (`MultiPointController.run_acquisition()` → `MultiPointWorker.run()`, both
in `control/core/`), instrumenting the **engine** — not the GUI widgets — covers both
launch paths with no extra code. Server-driven runs are unattended, which is exactly
when an alert matters most.

## Architecture

Three parts, with a clean dependency DAG (`acquisition_watchdog` → `squid`; `control` →
`squid`; the watchdog never imports `control`):

```
  GUI process (main_hcs.py, incl. in-process MCP control server)
  ┌───────────────────────────────────────────────┐
  │ MultiPointController.run_acquisition()          │   writes
  │   MultiPointWorker.run():                        │ ─────────►  <state_dir>/run.json
  │     start  → write run.json (status=running)     │             (atomic os.replace)
  │     loop   → beat() heartbeat + progress (~5s)   │
  │     finally→ write end (status=ended, reason)    │
  │ in-process SlackNotifier (unchanged role):       │
  │   live errors, progress, finish-with-mosaic      │
  └───────────────────────────────────────────────┘
                                                          reads
  acquisition_watchdog  (independent always-on process) ◄────────  <state_dir>/run.json
    poll every ~5s → classify → Slack alert (once per run_id)      reads bot_token/channel_id
                                                                    from cache/slack_settings.yaml
```

### Part 1 — Breadcrumb protocol (in the acquisition engine)

A new leaf module `squid/acquisition_state.py` owns the run-state schema and atomic
read/write. The acquisition engine writes; the watchdog reads. It must stay
import-light (stdlib only) and must not import `control`.

**`run.json`** — a single file in the shared state dir, replaced atomically
(`os.replace`, atomic and torn-read-free on both POSIX and Windows):

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1` |
| `run_id` | str | `uuid4().hex`; the watchdog's dedup key |
| `experiment_id` | str | from `MultiPointController` |
| `machine` | str | config machine-name if present, else `socket.gethostname()` |
| `pid` | int | `os.getpid()` of the GUI process |
| `config_path` | str | absolute path of the active `.ini` (so the watchdog finds Slack settings with no args) |
| `output_path` | str | experiment output dir |
| `started_at` | float | epoch seconds, UTC |
| `heartbeat_at` | float | epoch seconds; bumped ~every `HEARTBEAT_INTERVAL_S` |
| `progress` | obj | `{timepoint, expected_timepoints, fov, region_fovs, images}` |
| `expected` | obj | `{timepoints, regions, fovs, channels, z}` — `fovs` is total planned across all regions |
| `status` | str | `running` \| `ended` |
| `reason` | str\|null | set when `ended` — see taxonomy below |
| `ended_at` | float\|null | epoch seconds |
| `stats` | obj\|null | `{total_images, errors_encountered, total_duration_seconds}` at end |

**Write points in the engine:**

1. **Start** — in `MultiPointController.run_acquisition()`, which owns the experiment id
   and acquisition parameters: write the full record with `status=running`, a fresh
   `run_id`, and `expected` totals. The `run_id` is handed to the worker so its heartbeat
   and end writes update the same record.
2. **Heartbeat** — a `HeartbeatWriter.beat(progress)` helper, called at the worker
   loop's existing abort-check points (the per-timepoint, per-FOV, and the long-wait
   poll loops in `MultiPointWorker.run()`). `beat()` is cheap: it updates an in-memory
   timestamp and **flushes to disk at most every `HEARTBEAT_INTERVAL_S` (default 5 s)**.
   Because the long-wait loops (timelapse `dt`, fluidics) already poll the abort flag,
   the heartbeat stays fresh whenever the worker thread is alive and freezes only on a
   true hang or process death.
3. **End** — in the `finally` of `MultiPointWorker.run()` (which already runs for normal
   finish, abort, and caught exceptions): write `status=ended`, `reason`, `ended_at`,
   `stats` (reuse the existing `AcquisitionStats` / `_acquisition_error_count`).
4. **App close while acquiring** — `main_hcs.py` shuts down via `os._exit()` (skips
   destructors). In the shutdown path, if `acquisition_in_progress()`, request the
   normal abort and **join the worker** (bounded timeout) before `os._exit()`, so the
   worker writes its normal `user_abort` end record and a deliberate quit is not
   misreported as a crash. (A distinct `app_closed` reason is future work.)

**Reason taxonomy** (computed at the end write):

| `reason` | When | Watchdog alerts? |
|---|---|---|
| `completed` | loop finished all timepoints, `errors_encountered == 0` | no (silent) |
| `completed_with_errors` | loop finished but `errors_encountered > 0` | yes |
| `error` | uncaught exception, or auto-abort from `TimeoutError` / failed-job abort | yes |
| `user_abort` | abort flag set externally (human / server) **or** GUI closed mid-run (shutdown aborts + joins) | yes |
| *(no end record)* | process crashed/killed/hung before writing end | yes (crash/hang) |

To distinguish `error` from `user_abort`, the engine records an **abort cause**: the
auto-abort paths (`TimeoutError`, failed-job abort) tag the cause as error-type; a bare
`request_abort_aquisition()` is `user`. The end write maps cause → reason. (`errors_encountered`
already exists and drives `completed` vs `completed_with_errors`.)

### Part 2 — The watchdog process (`software/acquisition_watchdog/`)

Independent, lightweight (stdlib + `pyyaml`), **does not import `control`**.

- **Poll loop** (every `POLL_INTERVAL_S`, default 5 s): read `run.json`; if absent, idle.
- **Classification:**
  - `status=running` and (`pid` not alive **or** `now − heartbeat_at > HEARTBEAT_TIMEOUT_S`) → **crash/hang** → alert.
  - `status=ended` and `reason ∈ {completed_with_errors, error, user_abort}` → alert.
  - `status=ended` and `reason=completed` → silent.
- **PID check** is a best-effort accelerator catching hard death within one poll:
  `psutil.pid_exists(pid)` if `psutil` is importable, else POSIX `os.kill(pid, 0)`, else
  skip (heartbeat-only). The **heartbeat is the primary, OS-agnostic signal**; PID just
  makes a true crash detectable in ~5 s instead of waiting out the heartbeat timeout.
- **Alert once per `run_id`.** Alerted ids are persisted to `<state_dir>/alerted.json` so
  a watchdog restart never re-alerts, and so a crash that happened while the watchdog was
  down is alerted exactly once on its next start.
- **Defaults** (overridable via CLI flags / config): `POLL_INTERVAL_S=5`,
  `HEARTBEAT_INTERVAL_S=5`, `HEARTBEAT_TIMEOUT_S=120` (comfortably above the longest
  legitimate single blocking op — long exposures, stage moves, fluidics — while still
  catching a hang within ~2 min).

**Alert payload:** machine name, experiment id, classification (crash / hang / error /
aborted / completed-with-errors), progress vs expected ("stopped at timepoint 3/10,
360 images"), start + last-heartbeat / end timestamps, output path.

### Part 3 — Notifier trim (minimal)

`control/slack_notifier.py` keeps live in-run error warnings, per-timepoint progress, and
the finish-with-mosaic summary. It **stops flagging bad *endings* itself** (the
end-of-run failure messaging moves to the watchdog), so a failed run produces exactly one
alert. The ~20-line Slack `chat.postMessage` send is extracted into a shared,
dependency-free `squid/slack.py` (stdlib `urllib`/`json`, no Qt/`control` imports) used by
both the notifier and the watchdog. Image upload (`files.getUploadURLExternal`) stays in
`SlackNotifier` — the watchdog never needs it.

## Cross-platform (Ubuntu + Windows)

- **State dir** via `platformdirs` (already used for logs in `squid/logging.py`):
  `default_state_dir()` in `squid/acquisition_state.py` returns
  `platformdirs.user_state_path("squid", "cephla") / "watchdog"` — `~/.local/state` (or
  `~/.cache`) on Linux, `%LOCALAPPDATA%\cephla\squid\…` on Windows. Writer and reader call
  the same helper so they always agree. Overridable via `SQUID_WATCHDOG_STATE_DIR` (both)
  and `--state-dir` (watchdog).
- **Atomic writes** use `os.replace` (atomic on both OSes). **PID check** is guarded per
  above. No POSIX-only calls on the hot path.

## Config sharing

The watchdog reads the **same `cache/slack_settings.yaml`** the GUI writes and loads
(`bot_token` / `channel_id` / `enabled`), resolved cwd-relative or overridden via the
`--slack-settings` flag or `$SQUID_SLACK_SETTINGS` env. It is parsed with `yaml`; the
watchdog never imports `control._def`. A `watchdog_enabled: false` key (default `true`)
disables watchdog alerts on a machine without disabling the in-process GUI notifier.

## Deployment — always-on user service

Core process is just `python -m acquisition_watchdog [--slack-settings <yaml>]`, identical on
both OSes (run from `software/` so the default `cache/slack_settings.yaml` resolves). Shipped
recipes:

- **Linux:** a systemd **`--user`** unit (`Restart=always`, `WantedBy=default.target`),
  `systemctl --user enable --now squid-acquisition-watchdog`. Runs as the same user as the
  GUI, sharing the `platformdirs` state dir.
- **Windows:** a **Task Scheduler** task triggered "at log on" of the user (sample `.xml`
  + a small `install.ps1`). Same user, same state dir.

Both ship in `acquisition_watchdog/` with a README. The identical code can later run as a
**remote monitor** (the option-3 variant) by pointing `--state-dir` at a shared/synced
mount — see Future work.

## Proposed file layout

| Path | Role |
|---|---|
| `squid/acquisition_state.py` | run-state schema, `default_state_dir()`, atomic read/write, `HeartbeatWriter` (engine writes, watchdog reads) |
| `squid/slack.py` | shared dependency-free `chat.postMessage` sender |
| `software/acquisition_watchdog/__main__.py` | CLI entry (`python -m acquisition_watchdog`) |
| `software/acquisition_watchdog/monitor.py` | poll loop + classification + dedup |
| `software/acquisition_watchdog/config.py` | resolve & load `cache/slack_settings.yaml` (the GUI's Slack creds) |
| `software/acquisition_watchdog/alerts.py` | format the Slack alert payload |
| `software/acquisition_watchdog/systemd/`, `windows/`, `README.md` | install recipes + docs |
| `control/core/multi_point_controller.py`, `control/core/multi_point_worker.py` | write breadcrumbs (start / beat / end) + abort-cause tagging |
| `control/slack_notifier.py` | stop end-of-run failure messaging; call `squid/slack.py` |
| `main_hcs.py` | write `app_closed` end record on shutdown-while-acquiring |

Named `acquisition_watchdog` (not `watchdog`) to avoid colliding with the PyPI `watchdog`
filesystem-events package.

## Tests

- `squid/acquisition_state.py`: round-trip (start → beats → end), atomic-replace, schema
  versioning; `beat()` throttling (many calls, ≤1 flush per interval).
- `acquisition_watchdog/monitor.py`: classification table — synthetic `run.json` for each
  state (`running`+stale heartbeat, `running`+dead PID, each `ended` reason, `completed`)
  → expected alert/no-alert; dedup (no double alert per `run_id`, persists across a
  monitor restart via `alerted.json`).
- `acquisition_watchdog/config.py`: resolution precedence (`--slack-settings` > env >
  default `cache/slack_settings.yaml`); missing/disabled Slack → logs, no crash.
- `squid/slack.py`: monkeypatch `urllib`, assert request shape; no network.
- PID check: alive (current pid) vs an impossible/known-dead pid, on the available
  platform; graceful degrade when `psutil` absent.
- Engine integration (simulation mode): run a short simulated acquisition and assert
  `run.json` transitions `running → ended/completed` and `heartbeat_at` advances. Mirror
  the abort path → `reason=user_abort`, and a forced job error → `completed_with_errors`.
- Black (120) over the new package; it is not in the formatter excludes.

## Out of scope (v1)

- **Progress-stall detection** (process alive but worker wedged) — needs per-step timing
  from `acquisition.yaml`; fragile, deferred. v1 catches death + full hang + abort + error.
- **Machine power-loss coverage** — needs the remote-monitor variant.
- **MCP control-server thread health** — server-thread death does not abort an in-flight
  acquisition, so it is not an "acquisition ended" event.
- **Multi-microscope aggregation**, a GUI panel for the watchdog, and secrets management
  for the bot token (stays in the `.ini` as today).

## Future work

- **Remote monitor:** point `--state-dir` at a shared mount; key the state file per
  machine (`run-<machine>.json`) to avoid collisions, and add a clock-skew tolerance to
  the heartbeat comparison (writer/reader no longer share a clock).
- Progress-stall detection; packaging the per-OS install into one script.
