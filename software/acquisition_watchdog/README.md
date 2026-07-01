# Acquisition Watchdog

An independent process that watches Squid acquisitions and posts a **single Slack alert**
when one ends prematurely — a process **crash / hang / kill**, a **fatal error**, or a
**user abort**. Clean completions stay silent ("no news is good news"). It covers
acquisitions started from the **GUI** and from the **MCP control server**, on **Ubuntu and
Windows**.

Because it runs as a *separate* process, it can report failures the in-app Slack notifier
can't — a segfault in a camera SDK, an OOM-kill, a frozen UI, or the whole process dying.

## How it works

The Squid GUI writes a small `run.json` breadcrumb into a shared state dir: `status=running`
at acquisition start, a throttled heartbeat (+ progress) during the run, and `status=ended`
with a reason at the end. This watchdog polls that file and alerts when a run's process has
died / gone silent, or ended with a non-clean reason. One alert per run (de-duplicated).

## Prerequisites

1. **Slack configured in the GUI.** Open *Settings → Slack Notifications*, enter your **Bot
   Token** (`xoxb-…`) and **Channel ID** (`C…`), and click *Save*. That writes
   `cache/slack_settings.yaml`, which the watchdog reads — there is no separate config.
   (Need to create the token? See [`../docs/slack_notifications.md`](../docs/slack_notifications.md).)
2. **"Enable watchdog alerts" checked** (the default) in that same dialog — or the
   `watchdog_enabled` key in `cache/slack_settings.yaml`.

## Run it

From the `software/` directory:

```bash
cd software
python3 -m acquisition_watchdog
```

Leave it running. Start an acquisition; if it crashes / hangs / aborts / errors, you get a
Slack alert. A clean finish produces nothing.

### Options

| Flag | Default | Purpose |
|---|---|---|
| `--heartbeat-timeout` | `120` | Seconds of heartbeat silence (with a live process) before declaring a hang. Raise it if you have very long single exposures / fluidics steps. |
| `--poll-interval` | `5` | Seconds between checks. |
| `--once` | — | Run a single check and exit (handy for testing or a cron probe). |
| `--slack-settings <path>` | `cache/slack_settings.yaml` | Only needed if you don't run from `software/`. |
| `--state-dir <path>` | platformdirs user-state dir | Must match the GUI's; override here or via `$SQUID_WATCHDOG_STATE_DIR`. |

## Run it always-on (recommended for a lab microscope)

A manual run stops when you close the terminal or reboot. To keep it up independently of the
GUI:

- **Linux (systemd user service):** copy `systemd/squid-acquisition-watchdog.service` to
  `~/.config/systemd/user/`, edit `WorkingDirectory` to your `software/` path, then:
  ```bash
  systemctl --user daemon-reload
  systemctl --user enable --now squid-acquisition-watchdog
  ```
  It starts at login, restarts on failure, and keeps running across GUI restarts.
- **Windows (Task Scheduler):** run `windows/install.ps1` in PowerShell from `software\`. It
  registers a logon-triggered task (via `pythonw.exe`; make sure it's on `PATH`, or edit the
  path in the script).

## Verify it works

```bash
python3 -m acquisition_watchdog --once   # one check, then exits — no error means it's healthy
```

End-to-end: start a `--simulation` acquisition, `kill -9` the GUI process, and you should get
a crash alert — within one poll (~5 s) when `psutil` is installed (it is, by default),
otherwise within `--heartbeat-timeout` seconds.

## What triggers an alert

| Situation | Alert |
|---|---|
| Process died — `running` breadcrumb + PID gone (crash / OOM-kill / power loss) | 🔴 crash |
| Process alive but no heartbeat past the timeout | 🟠 hang |
| Fatal error / auto-abort (timeout, failed save job, camera/frame failure) | 🔴 error |
| Finished, but some save/job errors occurred | 🟠 completed-with-errors |
| Aborted by the user, the MCP server, or by closing the GUI mid-run | 🟡 aborted |
| Finished cleanly | *(silent)* |

Each alert includes the machine name, experiment, reason, and progress (e.g. "stopped at
timepoint 3/10").

## Good to know

- **Independent of the GUI.** Restarting the software neither starts nor stops the watchdog —
  it just picks up the next run. That decoupling is the whole point: a watchdog spawned by the
  GUI couldn't survive the GUI crashing.
- **Start order doesn't matter.** Start it before, during, or after the GUI. If it starts
  mid-run it monitors from there; if it starts *after* a crash already happened, it reads the
  stale `running` breadcrumb, sees the PID is dead, and alerts once.
- **One alert per run.** Alerted run IDs persist in `<state_dir>/alerted.json`, so it never
  double-alerts and never re-alerts after a restart.
- **Turn alerts off** on a machine (without disabling the GUI notifier): uncheck *"Enable
  watchdog alerts"*, or set `watchdog_enabled: false` in `cache/slack_settings.yaml`. Takes
  effect on the next check — no watchdog restart needed.
- **Runs on the same machine as the GUI** (it reads local breadcrumb files). For coverage of a
  full machine death / power loss, run it on another host pointed at a shared/synced state dir
  (see *Remote / power-loss coverage*).

## Troubleshooting

| Symptom | Check |
|---|---|
| No alerts at all | Is the watchdog process actually running? Are `bot_token`/`channel_id` set (GUI → *Test Connection*)? Is *"Enable watchdog alerts"* checked? |
| Log says Slack not configured | Run from `software/` so `cache/slack_settings.yaml` resolves, or pass `--slack-settings <path>`. |
| False "hang" alerts | Raise `--heartbeat-timeout` — a single very long exposure/fluidics step can exceed the default. |
| Crash reported slowly (~2 min) | `psutil` missing → falls back to the heartbeat timeout. Install `psutil` for instant PID-based detection. |

## State dir

Defaults to `platformdirs.user_state_path("squid", "cephla")/watchdog`. The GUI (writer) and
the watchdog (reader) must agree on it — run both as the same user, or set
`SQUID_WATCHDOG_STATE_DIR` on both (or `--state-dir` on the watchdog).

## Remote / power-loss coverage (future)

Point `--state-dir` at a shared/synced mount on another host and run this process there.
Per-machine `run-<machine>.json` naming and a clock-skew tolerance are needed first (see the
design spec).
