# acquisition_watchdog/__main__.py
"""CLI entry point: python -m acquisition_watchdog"""
import argparse
import time
from pathlib import Path
from typing import Optional, Sequence

import squid.logging
from acquisition_watchdog.monitor import Monitor


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="acquisition_watchdog",
        description="Alert on prematurely-ended Squid acquisitions (crash/hang/abort/error) and on "
        "fluidics protocol runs left HELD or no longer responding.",
    )
    parser.add_argument(
        "--slack-settings",
        help="Path to the Slack settings YAML (defaults to ./cache/slack_settings.yaml, "
        "the same file the GUI writes).",
    )
    parser.add_argument("--state-dir", help="Override the watchdog state directory.")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between checks (default 5).")
    parser.add_argument(
        "--heartbeat-timeout",
        type=float,
        default=120.0,
        help="Seconds of heartbeat silence (with a live PID) before declaring a hang (default 120); "
        "also how long a fluidics protocol run's heartbeat may be silent.",
    )
    parser.add_argument(
        "--protocol-held-alert-minutes",
        type=float,
        default=10.0,
        help="Alert when a fluidics protocol run has been HELD this long (default 10).",
    )
    parser.add_argument(
        "--protocol-held-reminder-minutes",
        type=float,
        default=60.0,
        help="Remind once, this long after the held alert, if the run is still HELD (default 60; 0 = no reminder).",
    )
    parser.add_argument("--once", action="store_true", help="Run a single check and exit.")
    args = parser.parse_args(argv)

    log = squid.logging.get_logger("acquisition_watchdog")
    monitor = Monitor(
        state_dir=Path(args.state_dir) if args.state_dir else None,
        slack_settings=args.slack_settings,
        poll_interval=args.poll_interval,
        heartbeat_timeout=args.heartbeat_timeout,
        protocol_held_alert_s=args.protocol_held_alert_minutes * 60.0,
        protocol_held_reminder_s=args.protocol_held_reminder_minutes * 60.0,
    )
    if args.once:
        monitor.check_once(time.time())
    else:
        try:
            monitor.run_forever()
        except KeyboardInterrupt:
            log.info("Acquisition watchdog stopped.")


if __name__ == "__main__":
    main()
