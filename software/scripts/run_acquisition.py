#!/usr/bin/env python3
"""
Automated Acquisition Script for Squid Microscope

Launches the GUI, connects via TCP, and runs acquisition from a YAML config file
that was saved during a previous acquisition.

Usage:
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --simulation --wait
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --wells "A1:B3" --wait
    python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --wait
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# Constants
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050
MAX_BUFFER_SIZE = 10 * 1024 * 1024  # 10 MB
CONNECTION_TIMEOUT = 120  # seconds to wait for server
CONNECTION_RETRY_INTERVAL = 2.0  # seconds


def send_command(
    command: str,
    params: dict = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 60.0,
) -> dict:
    """Send a command to the microscope control server and return response."""
    request = {"command": command, "params": params or {}}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))

        # Receive response
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > MAX_BUFFER_SIZE:
                raise ValueError("Response too large")
            if b"\n" in buffer:
                break

        if not buffer:
            raise ConnectionError("Server closed connection without response")

        return json.loads(buffer.decode("utf-8").strip())


def wait_for_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = CONNECTION_TIMEOUT,
    retry_interval: float = CONNECTION_RETRY_INTERVAL,
    verbose: bool = False,
) -> bool:
    """Wait for the TCP control server to become available."""
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1
        try:
            response = send_command("ping", host=host, port=port, timeout=5.0)
            if response.get("success"):
                # Always log connection success (useful for debugging)
                elapsed = time.time() - start_time
                print(f"Server ready after {attempt} attempts ({elapsed:.1f}s)")
                return True
        except (socket.error, ConnectionRefusedError, socket.timeout):
            if verbose:
                print(f"Waiting for server... (attempt {attempt})")
            time.sleep(retry_interval)
        except Exception as e:
            # Always show unexpected errors - they may indicate a real problem
            print(f"Unexpected error connecting to server: {e}")
            time.sleep(retry_interval)

    # Log failure details even when not verbose
    elapsed = time.time() - start_time
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


def monitor_acquisition(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    poll_interval: float = 5.0,
    timeout: float = None,
    verbose: bool = False,
) -> dict:
    """Monitor acquisition progress until completion or timeout."""
    start_time = time.time()
    last_fov = -1
    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            response = send_command("get_acquisition_status", host=host, port=port, timeout=10.0)

            if not response.get("success"):
                print(f"\nWarning: Error getting status: {response.get('error', 'Unknown error')}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    return {
                        "completed": False,
                        "error": f"Lost connection to server after {consecutive_errors} consecutive errors",
                    }
                time.sleep(poll_interval)
                continue

            # Reset error counter on success
            consecutive_errors = 0

            result = response.get("result", {})
            in_progress = result.get("in_progress", False)

            if not in_progress:
                if verbose:
                    print("\nAcquisition completed!")
                return {"completed": True, "status": result}

            # Print progress
            current_fov = result.get("current_fov", 0)
            total_fovs = result.get("total_fovs", 0)

            if current_fov != last_fov:
                elapsed = time.time() - start_time
                if total_fovs > 0:
                    progress = current_fov / total_fovs * 100
                    print(f"\rProgress: {current_fov}/{total_fovs} FOVs ({progress:.1f}%) - {elapsed:.0f}s", end="")
                else:
                    print(f"\rProgress: FOV {current_fov} - {elapsed:.0f}s", end="")
                last_fov = current_fov
                sys.stdout.flush()

        except (socket.error, ConnectionRefusedError, socket.timeout) as e:
            # Connection errors during monitoring - warn and continue trying
            consecutive_errors += 1
            print(f"\nWarning: Connection error polling status: {e}")
            if consecutive_errors >= max_consecutive_errors:
                return {
                    "completed": False,
                    "error": f"Lost connection to server after {consecutive_errors} consecutive errors",
                }
        except Exception as e:
            # Unexpected errors - always show them
            consecutive_errors += 1
            print(f"\nWarning: Unexpected error polling status: {e}")
            if verbose:
                import traceback

                traceback.print_exc()
            if consecutive_errors >= max_consecutive_errors:
                return {
                    "completed": False,
                    "error": f"Too many errors polling status: {e}",
                }

        # Check timeout
        if timeout and (time.time() - start_time) > timeout:
            return {"completed": False, "timeout": True, "elapsed": time.time() - start_time}

        time.sleep(poll_interval)


def handle_dry_run(yaml_path: str, wells: str = None) -> None:
    """Validate YAML and print configuration without running acquisition."""
    import yaml

    print("\n=== DRY RUN MODE ===")
    print("Validating YAML file...")

    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)

    print("\nAcquisition Configuration:")
    print(f"  Widget type: {config.get('acquisition', {}).get('widget_type', 'unknown')}")
    print(f"  Objective: {config.get('objective', {}).get('name', 'unknown')}")
    print(f"  Channels: {[ch.get('name') for ch in config.get('channels', [])]}")
    print(f"  Z-stack: nz={config.get('z_stack', {}).get('nz', 1)}")
    print(f"  Time series: nt={config.get('time_series', {}).get('nt', 1)}")

    regions = config.get("wellplate_scan", {}).get("regions", [])
    if regions:
        print(f"  Regions: {[r.get('name') for r in regions]}")
    else:
        positions = config.get("flexible_scan", {}).get("positions", [])
        if positions:
            print(f"  Positions: {[p.get('name') for p in positions]}")

    if wells:
        print(f"\n  Wells override: {wells}")

    print("\nYAML validation: OK")
    print("Dry run complete - no acquisition started.")


def print_acquisition_result(result: dict) -> None:
    """Print acquisition start result."""
    print("Acquisition started!")
    print(f"  Experiment ID: {result.get('experiment_id')}")
    print(f"  Save directory: {result.get('save_dir')}")
    print(f"  Regions: {result.get('region_count')}")
    print(f"  Channels: {result.get('channels')}")
    print(f"  Z-stack: {result.get('nz')} slices")
    print(f"  Timepoints: {result.get('nt')}")
    print(f"  Total images: {result.get('total_images')}")


def main():
    parser = argparse.ArgumentParser(
        description="Run automated acquisition on Squid microscope using saved YAML settings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with simulation mode, wait for completion
  python run_acquisition.py --yaml /path/to/acquisition.yaml --simulation --wait

  # Run with different wells than saved in YAML
  python run_acquisition.py --yaml /path/to/acquisition.yaml --wells "A1:A3" --wait

  # Connect to already-running GUI (don't launch new one)
  python run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --wait
        """,
    )

    parser.add_argument(
        "--yaml",
        "-y",
        required=True,
        help="Path to acquisition.yaml file saved by the GUI",
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
    # Launch mode options (mutually exclusive)
    launch_group = parser.add_mutually_exclusive_group()
    launch_group.add_argument(
        "--no-launch",
        action="store_true",
        help="Don't launch GUI, assume it's already running with server enabled",
    )
    launch_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate YAML and print what would be executed without actually running",
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"TCP server host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP server port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    # Validate YAML file exists
    yaml_path = os.path.abspath(args.yaml)
    if not os.path.exists(yaml_path):
        print(f"Error: YAML file not found: {yaml_path}")
        sys.exit(1)

    print(f"Using YAML config: {yaml_path}")

    if args.dry_run:
        try:
            handle_dry_run(yaml_path, args.wells)
        except Exception as e:
            print(f"Error validating YAML: {e}")
            sys.exit(1)
        sys.exit(0)

    gui_process = None

    # Track exit code for cleanup function
    exit_code = 0

    def cleanup(signum=None, frame=None):
        """Clean up on exit, warning if acquisition is still running.

        Called from signal handlers and explicit error paths. Always exits.
        When gui_process is None (e.g., --no-launch mode), just exits cleanly.
        Uses the exit_code variable from the enclosing scope.
        """
        if gui_process:
            # Check if acquisition is still running before terminating
            try:
                response = send_command("get_acquisition_status", host=args.host, port=args.port, timeout=2.0)
                if response.get("success") and response.get("result", {}).get("in_progress"):
                    print("\nWARNING: Acquisition is still in progress!")
                    print("Terminating GUI will abort the acquisition and may result in data loss.")
            except (socket.error, ConnectionRefusedError, socket.timeout, json.JSONDecodeError):
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
            print("Make sure the GUI is running and 'Enable MCP Control Server' is checked in Settings")
            exit_code = 1
            cleanup()

        print("Control server ready!")

        # Build acquisition parameters
        params = {"yaml_path": yaml_path}
        if args.wells:
            params["wells"] = args.wells
        if args.base_path:
            params["base_path"] = args.base_path

        # Start acquisition
        print("Starting acquisition...")
        response = send_command(
            "run_acquisition_from_yaml",
            params=params,
            host=args.host,
            port=args.port,
            timeout=30.0,
        )

        if not response.get("success"):
            print(f"Error starting acquisition: {response.get('error', 'Unknown error')}")
            exit_code = 1
            cleanup()

        result = response.get("result", {})
        print_acquisition_result(result)

        # Monitor if requested
        if args.wait:
            print("\nMonitoring acquisition progress...")
            status = monitor_acquisition(
                host=args.host,
                port=args.port,
                timeout=args.timeout,
                verbose=args.verbose,
            )

            if status.get("completed"):
                print("\nAcquisition completed successfully!")
            elif status.get("timeout"):
                print(f"\nAcquisition timed out after {status.get('elapsed', 0):.0f}s")
                exit_code = 1
            elif status.get("error"):
                print(f"\nAcquisition error: {status.get('error')}")
                exit_code = 1

            # Terminate GUI after --wait completes
            if gui_process:
                cleanup()

        # If not waiting and we launched the GUI, inform user
        if not args.wait and gui_process:
            print("\nAcquisition running in background. GUI will remain open.")
            print("Press Ctrl+C to exit (this will close the GUI)")

            # Wait for GUI to exit
            gui_process.wait()

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
