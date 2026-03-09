#!/usr/bin/env python3
"""
Test script for Workflow Runner.

This script simulates an external process (e.g., fluidics, robotic arm)
by sleeping for a configurable duration and printing status messages.
"""

import argparse
import time
import sys


def main():
    parser = argparse.ArgumentParser(description="Test script for Workflow Runner")
    parser.add_argument("--duration", type=float, default=3.0, help="Sleep duration in seconds (default: 3)")
    parser.add_argument("--port", type=int, help="Port number (for timepoint argument testing)")
    parser.add_argument("--name", type=str, default="Test Script", help="Name to display in output")
    parser.add_argument("--fail", action="store_true", help="Exit with error code 1")
    args = parser.parse_args()

    print(f"[{args.name}] Starting...", flush=True)
    if args.port is not None:
        print(f"[{args.name}] Port argument received: {args.port}", flush=True)

    print(f"[{args.name}] Sleeping for {args.duration} seconds...", flush=True)

    # Sleep in small increments to show progress
    elapsed = 0.0
    interval = 0.5
    while elapsed < args.duration:
        sleep_duration = min(interval, args.duration - elapsed)
        time.sleep(sleep_duration)
        elapsed += sleep_duration
        if elapsed < args.duration:
            print(f"[{args.name}] Progress: {elapsed:.1f}/{args.duration:.1f}s", flush=True)

    if args.fail:
        print(f"[{args.name}] Simulating failure!", flush=True)
        sys.exit(1)

    print(f"[{args.name}] Completed successfully!", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
