#!/usr/bin/env python3
"""Count microcontroller command timeouts across Squid log files.

Scans every file in a log directory for the microcontroller's
"command timed out without an ack" message and reports per-file and
total counts.

Usage:
    python tools/count_mcu_timeouts.py
    python tools/count_mcu_timeouts.py /path/to/log/folder
    python tools/count_mcu_timeouts.py /path/to/log/folder --pattern "*.log*"
"""

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".local/state/squid/log"
DEFAULT_PATTERN = "*"

# Matches the microcontroller timeout log line, e.g.:
#   "command timed out without an ack after 0.5 [s], resending command"
TIMEOUT_RE = re.compile(r"command timed out without an ack")


def count_in_file(path: Path) -> int:
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if TIMEOUT_RE.search(line):
                    count += 1
    except OSError as e:
        print(f"warning: could not read {path}: {e}", file=sys.stderr)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "log_dir",
        nargs="?",
        default=str(DEFAULT_LOG_DIR),
        help=f"Log directory to scan (default: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"Glob pattern for log files relative to log_dir (default: {DEFAULT_PATTERN!r})",
    )
    parser.add_argument(
        "--sort",
        choices=("name", "count"),
        default="name",
        help="Sort output by file name or by timeout count (default: name)",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir).expanduser()
    if not log_dir.is_dir():
        print(f"error: {log_dir} is not a directory", file=sys.stderr)
        return 2

    files = sorted(p for p in log_dir.glob(args.pattern) if p.is_file())
    if not files:
        print(f"No files matching {args.pattern!r} in {log_dir}", file=sys.stderr)
        return 1

    results = [(p, count_in_file(p)) for p in files]

    if args.sort == "count":
        results.sort(key=lambda x: x[1], reverse=True)

    name_width = max(len(p.name) for p, _ in results)
    total = 0
    files_with_hits = 0
    for p, n in results:
        print(f"{p.name:<{name_width}}  {n}")
        total += n
        if n > 0:
            files_with_hits += 1

    print("-" * (name_width + 2 + 8))
    print(f"{'TOTAL':<{name_width}}  {total}")
    print(f"{len(results)} files scanned, {files_with_hits} with at least one timeout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
