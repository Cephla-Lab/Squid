# Acquisition Watchdog

Independent process that alerts (via Slack) when a Squid acquisition ends
prematurely — process crash/hang/kill, fatal error, or user abort. Covers runs
launched from the GUI and from the MCP control server.

## How it works
The Squid GUI writes a `run.json` breadcrumb (start / throttled heartbeat / end)
into a shared state dir. This watchdog polls it and posts one Slack alert when a
run dies, hangs, or ends with a non-clean reason. Clean completions are silent.

## Run it
    cd software
    python3 -m acquisition_watchdog

Options: `--slack-settings`, `--state-dir`, `--poll-interval` (5s), `--heartbeat-timeout` (120s), `--once`.

Slack credentials are read from `cache/slack_settings.yaml` — the same file the GUI's
Slack settings dialog writes (keys `bot_token`, `channel_id`, `enabled`). Run the
watchdog from the `software/` directory (so the default `cache/slack_settings.yaml`
path resolves), or pass `--slack-settings <path>`. To disable watchdog alerts on a
machine without disabling the GUI's notifications, add `watchdog_enabled: false` to
that YAML.

## Install as an always-on service
- **Linux:** see `systemd/squid-acquisition-watchdog.service` (header has steps).
- **Windows:** run `windows/install.ps1` (registers a logon-triggered task). Ensure
  `pythonw.exe` is on `PATH`, or edit the `-Execute` value in `install.ps1` to the full
  Python path.

## State dir
Defaults to `platformdirs.user_state_path("squid","cephla")/watchdog`. Override with
`SQUID_WATCHDOG_STATE_DIR` (must match the GUI's environment) or `--state-dir`.

## Remote / power-loss coverage (future)
Point `--state-dir` at a shared/synced mount on another host and run this process
there. Per-machine `run-<machine>.json` naming and clock-skew tolerance are needed
first (see the design spec, "Future work").
