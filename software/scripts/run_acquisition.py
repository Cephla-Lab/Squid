#!/usr/bin/env python3
"""
Automated Acquisition Script for Squid Microscope

Launches the GUI, connects via the REST API (squid_service), and runs an
acquisition from a YAML config file that was saved during a previous
acquisition, or from a named server-side method.

Usage:
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --simulation --wait
    python scripts/run_acquisition.py --method my_method --wait
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --wells "A1:B3" --wait
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --wait
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --dry-run
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

# Constants
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8060
CONNECTION_TIMEOUT = 120  # seconds to wait for server
CONNECTION_RETRY_INTERVAL = 2.0  # seconds
JOB_POLL_INTERVAL = 2.0  # seconds
MAX_CONSECUTIVE_POLL_ERRORS = 10


def api(host: str, port: int) -> str:
    """Base URL for the squid_service REST API."""
    return f"http://{host}:{port}"


def auth_headers() -> dict:
    """Bearer auth header built from SQUID_API_TOKEN, if set.

    Non-loopback binds require auth (the service refuses to start otherwise), so
    set SQUID_API_TOKEN when talking to a remote host. Empty dict on loopback.
    """
    token = os.environ.get("SQUID_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def wait_for_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout_s: float = CONNECTION_TIMEOUT,
    retry_interval: float = CONNECTION_RETRY_INTERVAL,
    verbose: bool = False,
) -> bool:
    """Wait for the REST API to become available (GET /v1/healthz)."""
    start_time = time.monotonic()
    deadline = start_time + timeout_s
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        try:
            if httpx.get(f"{api(host, port)}/v1/healthz", headers=auth_headers(), timeout=2.0).status_code == 200:
                elapsed = time.monotonic() - start_time
                print(f"Server ready after {attempt} attempts ({elapsed:.1f}s)")
                return True
        except httpx.TransportError:
            if verbose:
                print(f"Waiting for server... (attempt {attempt})")
        time.sleep(retry_interval)

    elapsed = time.monotonic() - start_time
    print(f"Server connection failed after {attempt} attempts ({elapsed:.1f}s)")
    return False


def launch_gui(simulation: bool = False, verbose: bool = False) -> subprocess.Popen:
    """Launch the Squid GUI as a subprocess with control server enabled."""
    # Find main_hcs.py relative to this script
    script_dir = Path(__file__).parent.parent  # software/
    main_hcs = script_dir / "main_hcs.py"

    if not main_hcs.exists():
        raise FileNotFoundError(f"Could not find main_hcs.py at {main_hcs}")

    cmd = [sys.executable, str(main_hcs), "--start-server"]
    if simulation:
        cmd.append("--simulation")
    if verbose:
        cmd.append("--verbose")

    env = os.environ.copy()
    env["QT_API"] = "pyqt5"

    if verbose:
        print(f"Launching GUI: {' '.join(cmd)}")

    process = subprocess.Popen(
        cmd,
        cwd=str(script_dir),
        env=env,
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
    )
    return process


def build_body(args) -> dict:
    """Build the JSON body for POST /v1/acquisitions[/preflight]."""
    body = {"overrides": {}}
    if args.method:
        body["method"] = args.method
    else:
        body["yaml_path"] = os.path.abspath(args.yaml)
    if args.wells:
        body["overrides"]["wells"] = args.wells
    if args.base_path:
        body["overrides"]["output_path"] = args.base_path
    return body


def _error_message(payload: dict) -> str:
    error = payload.get("error", payload)
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(error)


def run_preflight(args) -> int:
    """POST /v1/acquisitions/preflight and print the check results."""
    print("\n=== DRY RUN (server-side preflight) ===")
    try:
        r = httpx.post(
            f"{api(args.host, args.port)}/v1/acquisitions/preflight",
            json=build_body(args),
            headers=auth_headers(),
            timeout=30.0,
        )
    except httpx.TransportError as e:
        print(f"Error contacting server: {e}")
        return 1
    try:
        payload = r.json()
    except ValueError:
        print(f"Unexpected response ({r.status_code}): {r.text}")
        return 1
    if r.status_code != 200:
        print(f"Preflight request failed: {_error_message(payload)}")
        return 1

    for check in payload.get("checks", []):
        print(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {check['message'] or 'ok'}")

    ok = payload.get("ok", False)
    print(f"\nDry run complete - no acquisition started. Overall: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def print_acquisition_result(payload: dict) -> None:
    """Print the job handle returned by POST /v1/acquisitions."""
    print("Acquisition started!")
    print(f"  Job ID: {payload.get('job_id')}")
    print(f"  Experiment ID: {payload.get('experiment_id')}")
    print(f"  Output directory: {payload.get('output_dir')}")
    print(f"  Expected FOVs: {payload.get('expected_fov_count')}")
    print(f"  Expected images: {payload.get('expected_image_count')}")


def start_and_wait(args) -> int:
    """POST /v1/acquisitions, then (if --wait) poll GET /v1/jobs/{id} until COMPLETED.

    Returns 0 iff the acquisition was accepted (and, with --wait, completed with
    outcome SUCCESS); 1 otherwise.
    """
    base = api(args.host, args.port)
    try:
        r = httpx.post(f"{base}/v1/acquisitions", json=build_body(args), headers=auth_headers(), timeout=60.0)
    except httpx.TransportError as e:
        print(f"Error starting acquisition: {e}")
        return 1
    try:
        payload = r.json()
    except ValueError:
        print(f"Unexpected response ({r.status_code}): {r.text}")
        return 1
    if r.status_code != 202:
        print(f"Failed to start acquisition: {_error_message(payload)}")
        return 1

    print_acquisition_result(payload)
    job_id = payload["job_id"]

    if not args.wait:
        return 0

    print("\nMonitoring acquisition progress...")
    consecutive_errors = 0
    start_time = time.time()
    while True:
        try:
            job = httpx.get(f"{base}/v1/jobs/{job_id}", headers=auth_headers(), timeout=10.0).json()
            consecutive_errors = 0
        except httpx.TransportError as e:
            consecutive_errors += 1
            print(f"\nWarning: Connection error polling job: {e}")
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                print(f"Lost contact with server after {consecutive_errors} consecutive errors")
                return 1
            time.sleep(JOB_POLL_INTERVAL)
            continue

        progress = job.get("progress", {})
        print(
            f"  {job.get('state')}: {progress.get('images_acquired', 0)}/{progress.get('total_images', '?')} images",
            flush=True,
        )

        if job.get("state") == "COMPLETED":
            outcome = job.get("outcome")
            end_reason = (job.get("result") or {}).get("end_reason")
            print(f"\nOutcome: {outcome} ({end_reason})")
            return 0 if outcome == "SUCCESS" else 1

        if args.timeout and (time.time() - start_time) > args.timeout:
            print(f"\nAcquisition timed out after {args.timeout:.0f}s (job {job_id} still running)")
            return 1

        time.sleep(JOB_POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(
        description="Run automated acquisition on Squid microscope via the REST API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with simulation mode, wait for completion
  python run_acquisition.py --yaml /path/to/acquisition.yaml --simulation --wait

  # Run a named server-side method instead of a YAML file
  python run_acquisition.py --method my_method --wait

  # Run with different wells than saved in YAML
  python run_acquisition.py --yaml /path/to/acquisition.yaml --wells "A1:A3" --wait

  # Connect to already-running GUI (don't launch new one)
  python run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --wait

  # Validate a config against the live instrument without starting it
  python run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --dry-run
        """,
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--yaml",
        "-y",
        default=None,
        help="Path to acquisition.yaml file saved by the GUI",
    )
    source_group.add_argument(
        "--method",
        default=None,
        help="Name of a server-side acquisition method under machine_configs/acquisition_methods/ "
        "(alternative to --yaml)",
    )

    parser.add_argument(
        "--wells",
        "-w",
        default=None,
        help="Override wells from YAML (e.g., 'A1:B3' for range or 'A1,A2,B1' for list)",
    )
    parser.add_argument(
        "--base-path",
        "-b",
        default=None,
        help="Override save path for acquired images",
    )
    parser.add_argument(
        "--simulation",
        action="store_true",
        help="Run in simulation mode (no real hardware)",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for acquisition to complete (blocking mode)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Acquisition timeout in seconds (only with --wait)",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Don't launch GUI, assume it's already running with the REST API enabled",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run server-side preflight checks only (POST /v1/acquisitions/preflight); " "do not start the acquisition",
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"REST API host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"REST API port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    if args.yaml:
        yaml_path = os.path.abspath(args.yaml)
        if not os.path.exists(yaml_path):
            print(f"Error: YAML file not found: {yaml_path}")
            sys.exit(1)
        args.yaml = yaml_path
        print(f"Using YAML config: {yaml_path}")
    else:
        print(f"Using method: {args.method}")

    gui_process = None

    # Track exit code for cleanup function
    exit_code = 0

    def cleanup(signum=None, frame=None):
        """Clean up on exit, warning if acquisition is still running.

        Called from signal handlers and explicit exit paths (including normal
        completion). Always exits. When gui_process is None (e.g., --no-launch
        mode), just exits with the current exit_code.
        """
        if gui_process:
            try:
                status = httpx.get(
                    f"{api(args.host, args.port)}/v1/system/status", headers=auth_headers(), timeout=2.0
                ).json()
                if status.get("current_job_id"):
                    print("\nWARNING: Acquisition is still in progress!")
                    print("Terminating GUI will abort the acquisition and may result in data loss.")
            except httpx.TransportError:
                pass  # Server may not be reachable during cleanup - expected
            except Exception as e:
                print(f"\nWarning: Unexpected error checking acquisition status: {e}")

            print("\nTerminating GUI...")
            gui_process.terminate()
            try:
                gui_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gui_process.kill()
        sys.exit(exit_code)

    # Register signal handlers
    signal.signal(signal.SIGINT, cleanup)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, cleanup)

    try:
        # Launch GUI if needed
        if not args.no_launch:
            print("Launching GUI...")
            gui_process = launch_gui(simulation=args.simulation, verbose=args.verbose)
            print(f"GUI started (PID: {gui_process.pid})")

        # Wait for server
        print("Waiting for control server...")
        if not wait_for_server(host=args.host, port=args.port, verbose=args.verbose):
            print("Error: Control server did not become available within timeout")
            print("Make sure the GUI is running with the REST API enabled (--start-server flag or " "Settings)")
            exit_code = 1
            cleanup()

        print("Control server ready!")

        if args.dry_run:
            exit_code = run_preflight(args)
            cleanup()

        print("Starting acquisition...")
        exit_code = start_and_wait(args)

        if not args.wait and gui_process:
            print("\nAcquisition running in background. GUI will remain open.")
            print("Press Ctrl+C to exit (this will close the GUI)")
            gui_process.wait()
        else:
            cleanup()

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        cleanup()
    except Exception as e:
        print(f"Error: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        exit_code = 1
        cleanup()


if __name__ == "__main__":
    main()
