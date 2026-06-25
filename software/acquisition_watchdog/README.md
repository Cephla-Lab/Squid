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
    python3 -m acquisition_watchdog --config ./configuration.ini

Options: `--state-dir`, `--poll-interval` (5s), `--heartbeat-timeout` (120s), `--once`.

Slack credentials are read from the `[SlackNotifications]` section of the active
`.ini` (same `bot_token` / `channel_id` the GUI uses). Set `watchdog_enabled = False`
in that section to disable watchdog alerts on a machine.

## Install as an always-on service
- **Linux:** see `systemd/squid-acquisition-watchdog.service` (header has steps).
- **Windows:** run `windows/install.ps1` (registers a logon-triggered task).

## State dir
Defaults to `platformdirs.user_state_path("squid","cephla")/watchdog`. Override with
`SQUID_WATCHDOG_STATE_DIR` (must match the GUI's environment) or `--state-dir`.

## Remote / power-loss coverage (future)
Point `--state-dir` at a shared/synced mount on another host and run this process
there. Per-machine `run-<machine>.json` naming and clock-skew tolerance are needed
first (see the design spec, "Future work").
