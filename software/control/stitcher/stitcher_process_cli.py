#!/usr/bin/env python3
import argparse
import signal
import sys
import time
from multiprocessing import Queue, Event
from queue import Empty
from control.stitcher.stitcher_parameters import StitchingParameters
from control.stitcher.stitcher_process import StitcherProcess

"""
Cephla-Lab: Squid Microscopy Image Stitching CLI (soham mukherjee)

Usage:
    # Full pipeline example:
    python3 -m control.stitcher.stitcher_process_cli -i /path/to/microscopy/data \
                                                    --apply-flatfield \
                                                    --use-registration \
                                                    --output-format .ome.tiff \
                                                    --registration-channel "Fluroescence 488 nm Ex" \
                                                    --registration-z-level 1 \
                                                    --dynamic-registration \
                                                    --scan-pattern "S-Pattern" \
                                                    --merge-timepoints \
                                                    --merge-hcs-regions

    # Real example (BF channel and z-level 0 for registration):
    python3 -m control.stitcher.stitcher_process_cli -i /Users/soham/Downloads/_2025-01-02_22-35-20.424781 \
                                                    -ff \
                                                    -r \
                                                    -rc "BF LED matrix full" \
                                                    -rz 0
"""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Microscopy Image Stitching CLI")

    # Required arguments
    parser.add_argument("--input-folder", "-i", required=True, help="Input folder containing images to stitch")

    # Output format
    parser.add_argument(
        "--output-format",
        "-f",  # Output format (tiff or zarr)
        choices=[".ome.zarr", ".ome.tiff"],
        default=".ome.zarr",
        help="Output format for stitched data (default: .ome.zarr)",
    )

    # Image processing options
    parser.add_argument(
        "--apply-flatfield", "-ff", action="store_true", help="Apply flatfield correction"  # Apply flatfield correction
    )

    # Registration options
    parser.add_argument(
        "--use-registration", "-r", action="store_true", help="Enable image registration"  # Use registration
    )

    parser.add_argument(
        "--registration-channel",
        "-rc",  # Channel for registration
        help="Channel to use for registration (default: first available channel)",
    )

    parser.add_argument(
        "--registration-z-level",
        "-rz",  # Z-level for registration
        type=int,
        default=0,
        help="Z-level to use for registration (default: 0)",
    )

    parser.add_argument(
        "--dynamic-registration",  # Enable dynamic registration (in development)
        action="store_true",
        help="Use dynamic registration for improved accuracy",
    )

    # Scanning pattern
    parser.add_argument(
        "--scan-pattern",
        "-s",  #  Unidirectional or Bidirectional Scan
        choices=["Unidirectional", "S-Pattern"],
        default="Unidirectional",
        help="Microscope scanning pattern (default: Unidirectional)",
    )

    # Merging options
    parser.add_argument(
        "--merge-timepoints",  # Merge timepoints to create time series output (in development)
        action="store_true",
        help="Merge all timepoints into a single dataset",
    )

    parser.add_argument(
        "--merge-hcs-regions",  # Merge HCS regions(wells) to create full wellplate HCS output (in development)
        action="store_true",
        help="Merge all high-content screening regions (wells)",
    )

    # Advanced options
    parser.add_argument(
        "--params-json",  # JSON file parameters
        help="Path to a JSON file containing stitching parameters (overrides other arguments)",
    )

    return parser.parse_args()


def create_params(args: argparse.Namespace) -> StitchingParameters:
    """Create stitching parameters from parsed arguments."""
    if args.params_json:
        return StitchingParameters.from_json(args.params_json)

    # Construct parameters dictionary from CLI arguments
    params_dict = {
        "input_folder": args.input_folder,
        "output_format": args.output_format,
        "apply_flatfield": args.apply_flatfield,
        "use_registration": args.use_registration,
        "registration_channel": args.registration_channel,
        "registration_z_level": args.registration_z_level,
        "scan_pattern": args.scan_pattern,
        "merge_timepoints": args.merge_timepoints,
        "merge_hcs_regions": args.merge_hcs_regions,
        "dynamic_registration": args.dynamic_registration,
    }

    return StitchingParameters.from_dict(params_dict)


def monitor_process(
    progress_queue: Queue, status_queue: Queue, complete_queue: Queue, stop_event: Event, stitcher: StitcherProcess
) -> bool:
    """Monitor and display progress from the stitching process."""
    status_line = ""
    progress_line = ""

    def print_status():
        if status_line or progress_line:
            sys.stdout.write("\033[2K\033[A\033[2K\r")
        sys.stdout.write(f"{status_line}\n{progress_line}\r")
        sys.stdout.flush()

    try:
        while stitcher.is_alive():
            # Check progress updates
            try:
                while True:
                    msg_type, data = progress_queue.get_nowait()
                    if msg_type == "progress":
                        current, total = data
                        progress_line = f"Progress: [{current}/{total}]"
                        print_status()
            except Empty:
                pass

            # Check status updates
            try:
                while True:
                    msg_type, data = status_queue.get_nowait()
                    if msg_type == "status":
                        status, is_saving = data
                        status_line = status
                        if is_saving:
                            progress_line = "Saving..."
                        print_status()
                    elif msg_type == "error":
                        print(f"\nError: {data}", file=sys.stderr)
                        return False
            except Empty:
                pass

            # Check completion updates
            try:
                msg_type, data = complete_queue.get_nowait()
                if msg_type == "complete":
                    output_path, _ = data
                    print(f"\nStitching completed successfully.")
                    print(f"Output saved to: {output_path}")
                    return True
            except Empty:
                pass

            time.sleep(0.1)  # Prevent busy waiting

    except KeyboardInterrupt:
        print("\nStopping stitching process...")
        stop_event.set()

        # Give process time to clean up
        try:
            stitcher.join(timeout=3)
        except:
            pass

        # Force terminate if still alive
        if stitcher.is_alive():
            stitcher.terminate()
            stitcher.join(timeout=1)

        return False

    return False


def main():
    """Main CLI entry point."""
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    args = parse_args()

    try:
        # Create stitching parameters
        params = create_params(args)

        # Create communication queues
        progress_queue = Queue()
        status_queue = Queue()
        complete_queue = Queue()
        stop_event = Event()

        # Create and start the stitcher process
        stitcher = StitcherProcess(
            params=params,
            progress_queue=progress_queue,
            status_queue=status_queue,
            complete_queue=complete_queue,
            stop_event=stop_event,
        )

        print("Starting stitching process...")
        stitcher.start()

        # Monitor the process
        success = monitor_process(progress_queue, status_queue, complete_queue, stop_event, stitcher)

        # Ensure process is cleaned up
        if stitcher.is_alive():
            stop_event.set()
            stitcher.join(timeout=3)
            if stitcher.is_alive():
                stitcher.terminate()

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
