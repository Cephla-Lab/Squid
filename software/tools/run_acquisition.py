#!/usr/bin/env python3
"""CLI tool for running acquisitions from YAML configuration files.

This script connects to a running Squid GUI with TCP control server enabled
and triggers acquisitions using previously saved YAML configurations.

Usage:
    python run_acquisition.py acquisition.yaml
    python run_acquisition.py acquisition.yaml --dry-run
    python run_acquisition.py acquisition.yaml --experiment-id my_exp --base-path /data

Requirements:
    - Squid GUI running with --start-server flag
    - A saved acquisition.yaml file from a previous acquisition

Example workflow:
    1. Configure and run an acquisition in the GUI
    2. The GUI saves acquisition.yaml to the experiment folder
    3. Later, run the same acquisition:
       python run_acquisition.py /data/exp_2026-01-12/acquisition.yaml
"""

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050
DEFAULT_TIMEOUT = 30.0


def send_command(
    host: str,
    port: int,
    command: str,
    timeout: float = DEFAULT_TIMEOUT,
    **params: Any,
) -> Dict[str, Any]:
    """Send a command to the TCP control server.

    Args:
        host: Server host address
        port: Server port
        command: Command name
        timeout: Socket timeout in seconds
        **params: Command parameters

    Returns:
        Server response as dictionary

    Raises:
        ConnectionError: If connection fails
        TimeoutError: If operation times out
    """
    request = {"command": command, **params}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)

    try:
        sock.connect((host, port))
        sock.sendall(json.dumps(request).encode("utf-8"))

        # Receive response
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            try:
                return json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue

        if not data:
            raise ConnectionError("Empty response from server")

        return json.loads(data.decode("utf-8"))

    except socket.timeout:
        raise TimeoutError("Connection timed out")
    except ConnectionRefusedError:
        raise ConnectionError(f"Connection refused: {host}:{port}")
    finally:
        sock.close()


def validate_yaml(yaml_path: str) -> bool:
    """Validate that YAML file exists and is readable.

    Args:
        yaml_path: Path to YAML file

    Returns:
        True if valid
    """
    path = Path(yaml_path)
    if not path.exists():
        print(f"Error: YAML file not found: {yaml_path}", file=sys.stderr)
        return False

    if not path.is_file():
        print(f"Error: Not a file: {yaml_path}", file=sys.stderr)
        return False

    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not data:
                print(f"Error: Empty YAML file: {yaml_path}", file=sys.stderr)
                return False
            if "acquisition" not in data:
                print(f"Error: Missing 'acquisition' section in YAML", file=sys.stderr)
                return False
    except Exception as e:
        print(f"Error: Failed to parse YAML: {e}", file=sys.stderr)
        return False

    return True


def run_acquisition(
    yaml_path: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    experiment_id: Optional[str] = None,
    base_path: Optional[str] = None,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> int:
    """Run an acquisition from a YAML configuration file.

    Args:
        yaml_path: Path to acquisition YAML file
        host: TCP server host
        port: TCP server port
        experiment_id: Override experiment ID
        base_path: Override save path
        dry_run: If True, validate only without running
        timeout: Socket timeout

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    # Validate YAML file locally first
    if not validate_yaml(yaml_path):
        return 1

    yaml_path_abs = str(Path(yaml_path).resolve())

    if dry_run:
        print(f"Dry run: Would execute acquisition from {yaml_path_abs}")
        print("Checking server connection...")

    try:
        # Check server status
        status = send_command(host, port, "get_status", timeout=5.0)
        if not status.get("success"):
            print(f"Error: Server error: {status.get('error')}", file=sys.stderr)
            return 1

        if dry_run:
            print(f"Server connected. Current objective: {status.get('current_objective')}")
            print("Dry run complete. Use without --dry-run to execute.")
            return 0

        # Build command parameters
        params: Dict[str, Any] = {"yaml_path": yaml_path_abs}
        if experiment_id:
            params["experiment_id"] = experiment_id
        if base_path:
            params["base_path"] = base_path

        print(f"Starting acquisition from: {yaml_path_abs}")

        # Send acquisition command
        result = send_command(host, port, "run_acquisition_from_yaml", timeout=timeout, **params)

        if not result.get("success"):
            print(f"Error: {result.get('error')}", file=sys.stderr)
            return 1

        # Print success info
        print("\nAcquisition started successfully!")
        print(f"  Experiment ID: {result.get('experiment_id')}")
        print(f"  Save directory: {result.get('save_dir')}")
        print(f"  Regions: {result.get('region_count')}")
        print(f"  Channels: {', '.join(result.get('channels', []))}")
        print(f"  Z-stack: {result.get('nz')} planes")
        print(f"  Time points: {result.get('nt')}")
        print(f"  Total FOVs: {result.get('total_fovs')}")
        print(f"  Total images: {result.get('total_images')}")

        return 0

    except ConnectionError as e:
        print(f"Error: Connection failed: {e}", file=sys.stderr)
        print(
            "\nMake sure the Squid GUI is running with --start-server flag:",
            file=sys.stderr,
        )
        print("  python main_hcs.py --start-server", file=sys.stderr)
        return 1
    except TimeoutError:
        print("Error: Connection timed out", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run microscope acquisitions from YAML configuration files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s acquisition.yaml
      Run acquisition using saved YAML settings

  %(prog)s acquisition.yaml --dry-run
      Validate YAML and server connection without running

  %(prog)s acquisition.yaml --experiment-id my_experiment
      Run with custom experiment ID

  %(prog)s acquisition.yaml --base-path /data/experiments
      Save to custom base path

Requirements:
  The Squid GUI must be running with TCP server enabled:
    python main_hcs.py --start-server
""",
    )

    parser.add_argument(
        "yaml_path",
        help="Path to acquisition.yaml file",
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
        "--experiment-id",
        help="Override experiment ID (default: auto-generated)",
    )
    parser.add_argument(
        "--base-path",
        help="Override save path (default: from _def.DEFAULT_SAVING_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate YAML and connection without running acquisition",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Socket timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )

    args = parser.parse_args()

    sys.exit(
        run_acquisition(
            yaml_path=args.yaml_path,
            host=args.host,
            port=args.port,
            experiment_id=args.experiment_id,
            base_path=args.base_path,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    )


if __name__ == "__main__":
    main()
