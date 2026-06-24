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
        description="Alert on prematurely-ended Squid acquisitions (crash/hang/abort/error).",
    )
    parser.add_argument("--config", help="Path to the active configuration .ini ([SlackNotifications]).")
    parser.add_argument("--state-dir", help="Override the watchdog state directory.")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between checks (default 5).")
    parser.add_argument(
        "--heartbeat-timeout",
        type=float,
        default=120.0,
        help="Seconds of heartbeat silence (with a live PID) before declaring a hang (default 120).",
    )
    parser.add_argument("--once", action="store_true", help="Run a single check and exit.")
    args = parser.parse_args(argv)

    log = squid.logging.get_logger("acquisition_watchdog")
    monitor = Monitor(
        state_dir=Path(args.state_dir) if args.state_dir else None,
        cli_config=args.config,
        poll_interval=args.poll_interval,
        heartbeat_timeout=args.heartbeat_timeout,
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
