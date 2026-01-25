#!/usr/bin/env python3
"""Analyze acquisition logs to extract timing information.

Usage:
    python tools/analyze_acquisition_logs.py /path/to/acquisition.log
    python tools/analyze_acquisition_logs.py /path/to/acquisition_folder
    python tools/analyze_acquisition_logs.py /path/to/log1 /path/to/log2 --compare
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class AcquisitionTiming:
    """Parsed timing data from an acquisition log."""

    log_path: str
    total_acquisition_time_s: Optional[float] = None
    total_processing_time_s: Optional[float] = None
    total_reset_time_s: Optional[float] = None
    num_images: int = 0
    num_fovs: int = 0
    num_regions: int = 0
    num_timepoints: int = 0
    image_size: Optional[Tuple[int, int]] = None  # (height, width)
    bytes_per_pixel: int = 2  # Default to uint16
    file_format: str = "unknown"
    compression: str = "unknown"

    # Timer breakdowns (name -> list of durations in seconds)
    timer_stats: Dict[str, List[float]] = field(default_factory=dict)

    # Memory stats
    peak_memory_mb: Optional[float] = None
    start_memory_mb: Optional[float] = None
    end_memory_mb: Optional[float] = None

    @property
    def image_size_bytes(self) -> Optional[int]:
        """Size of a single image in bytes."""
        if self.image_size:
            return self.image_size[0] * self.image_size[1] * self.bytes_per_pixel
        return None

    @property
    def total_data_mb(self) -> Optional[float]:
        """Total raw data size in MB."""
        if self.image_size_bytes and self.num_images > 0:
            return (self.image_size_bytes * self.num_images) / (1024 * 1024)
        return None

    @property
    def throughput_mb_s(self) -> Optional[float]:
        """Throughput in MB/s (raw data, not compressed)."""
        if self.total_data_mb and self.total_acquisition_time_s and self.total_acquisition_time_s > 0:
            return self.total_data_mb / self.total_acquisition_time_s
        return None

    @property
    def images_per_second(self) -> Optional[float]:
        if self.total_acquisition_time_s and self.total_acquisition_time_s > 0:
            return self.num_images / self.total_acquisition_time_s
        return None

    @property
    def avg_time_per_image_ms(self) -> Optional[float]:
        if self.num_images > 0 and self.total_acquisition_time_s:
            return (self.total_acquisition_time_s / self.num_images) * 1000
        return None


def _dtype_to_bytes(dtype_str: str) -> int:
    """Convert dtype string to bytes per pixel."""
    dtype_bytes = {
        "uint8": 1,
        "int8": 1,
        "uint16": 2,
        "int16": 2,
        "uint32": 4,
        "int32": 4,
        "float32": 4,
        "uint64": 8,
        "int64": 8,
        "float64": 8,
    }
    # Handle numpy dtype strings like "dtype('uint16')"
    for key, value in dtype_bytes.items():
        if key in dtype_str.lower():
            return value
    return 2  # Default to uint16


def parse_acquisition_log(log_path: str) -> AcquisitionTiming:
    """Parse an acquisition log file and extract timing information."""
    timing = AcquisitionTiming(log_path=log_path)

    with open(log_path, "r") as f:
        content = f.read()

    # Extract total acquisition time
    # Pattern: "Time taken for acquisition: 17.690195333"
    match = re.search(r"Time taken for acquisition:\s+([\d.]+)", content)
    if match:
        timing.total_acquisition_time_s = float(match.group(1))

    # Pattern: "Time taken for acquisition/processing: 17.691041292 [s]"
    match = re.search(r"Time taken for acquisition/processing:\s+([\d.]+)", content)
    if match:
        timing.total_processing_time_s = float(match.group(1))

    # Pattern: "total time for acquisition + processing + reset: 20.401613235473633"
    match = re.search(r"total time for acquisition \+ processing \+ reset:\s+([\d.]+)", content)
    if match:
        timing.total_reset_time_s = float(match.group(1))

    # Extract num FOVs
    match = re.search(r"num fovs:\s+(\d+)", content)
    if match:
        timing.num_fovs = int(match.group(1))

    # Extract num regions
    match = re.search(r"num regions:\s+(\d+)", content)
    if match:
        timing.num_regions = int(match.group(1))

    # Count image acquisitions (send_trigger timers)
    trigger_times = re.findall(r"Stopping name=send_trigger with elapsed=([\d.]+)", content)
    timing.num_images = len(trigger_times)

    # Extract file format
    if "ZARR_V3 output" in content:
        timing.file_format = "zarr_v3"
        # Try to get compression from log
        match = re.search(r"compression=(\w+)", content)
        if match:
            timing.compression = match.group(1)
    elif "SaveOMETiffJob" in content or "OME-TIFF" in content:
        timing.file_format = "ome_tiff"
    elif "SaveImageJob" in content or "INDIVIDUAL_IMAGES" in content:
        timing.file_format = "individual_tiff"
    else:
        timing.file_format = "unknown"

    # Try to extract image dimensions from log
    # Pattern: "image shape: (2048, 2048)" or "WIDTH=2048, HEIGHT=2048" or similar
    match = re.search(r"image shape:\s*\((\d+),\s*(\d+)\)", content)
    if match:
        timing.image_size = (int(match.group(1)), int(match.group(2)))
    else:
        # Try WIDTH/HEIGHT pattern
        width_match = re.search(r"WIDTH[=:\s]+(\d+)", content, re.IGNORECASE)
        height_match = re.search(r"HEIGHT[=:\s]+(\d+)", content, re.IGNORECASE)
        if width_match and height_match:
            timing.image_size = (int(height_match.group(1)), int(width_match.group(1)))

    # Parse timer statistics from the summary block
    # Pattern: "            _image_callback: (N=144, total=1.1383 [s]): mean=0.0079 [s], ..."
    timer_pattern = r"^\s+(\S[^:]+):\s+\(N=(\d+),\s+total=([\d.]+)\s+\[s\]\)"
    for match in re.finditer(timer_pattern, content, re.MULTILINE):
        timer_name = match.group(1).strip()
        count = int(match.group(2))
        total_time = float(match.group(3))
        timing.timer_stats[timer_name] = {"count": count, "total_s": total_time}

    # Extract memory stats
    # Pattern: "[MEM] ACQUISITION START: main=515.0MB, children=10.1MB, total=525.1MB"
    match = re.search(r"\[MEM\] ACQUISITION START:.*total=([\d.]+)MB", content)
    if match:
        timing.start_memory_mb = float(match.group(1))

    match = re.search(r"\[MEM\] ACQUISITION COMPLETE:.*total=([\d.]+)MB", content)
    if match:
        timing.end_memory_mb = float(match.group(1))

    # Pattern: "sampled_peak=1441.4MB"
    match = re.search(r"sampled_peak=([\d.]+)MB", content)
    if match:
        timing.peak_memory_mb = float(match.group(1))

    return timing


def _extract_squid_metadata(squid_attrs: dict, timing: AcquisitionTiming) -> bool:
    """Extract _squid metadata into timing object.

    Args:
        squid_attrs: Dict that may contain "_squid" key
        timing: AcquisitionTiming object to populate

    Returns:
        True if _squid metadata was found and extracted
    """
    if "_squid" not in squid_attrs:
        return False

    squid = squid_attrs["_squid"]
    timing.compression = squid.get("compression", "unknown")
    timing.file_format = "zarr_v3"

    # Extract image size from shape (last two dims are Y, X)
    shape = squid.get("shape", [])
    if len(shape) >= 2:
        timing.image_size = (shape[-2], shape[-1])

    # Extract dtype for bytes_per_pixel
    timing.bytes_per_pixel = _dtype_to_bytes(squid.get("dtype", "uint16"))
    return True


def parse_acquisition_folder(folder_path: str) -> Tuple[AcquisitionTiming, dict]:
    """Parse acquisition folder to get timing and metadata.

    Returns:
        Tuple of (timing, metadata_dict)
    """
    folder = Path(folder_path)

    # Find and parse log file
    log_path = folder / "acquisition.log"
    if not log_path.exists():
        raise FileNotFoundError(f"No acquisition.log found in {folder_path}")

    timing = parse_acquisition_log(str(log_path))

    # Load acquisition parameters
    params_path = folder / "acquisition parameters.json"
    yaml_path = folder / "acquisition.yaml"

    metadata = {}
    if params_path.exists():
        with open(params_path, "r") as f:
            metadata["params"] = json.load(f)
    if yaml_path.exists():
        try:
            import yaml

            with open(yaml_path, "r") as f:
                metadata["yaml"] = yaml.safe_load(f)
        except ImportError:
            pass

    # Get compression from zarr metadata if available
    # Check both legacy .zattrs (Zarr v2 style) and zarr.json attributes (Zarr v3 style)
    # Also check both .zarr and .ome.zarr extensions
    zarr_attrs = (
        list(folder.glob("**/plate.zarr/**/.zattrs"))
        + list(folder.glob("**/plate.ome.zarr/**/.zattrs"))
        + list(folder.glob("**/*.ome.zarr/.zattrs"))
        + list(folder.glob("**/.zattrs"))
    )
    for zattr_path in zarr_attrs:
        try:
            with open(zattr_path, "r") as f:
                zattrs = json.load(f)
            if _extract_squid_metadata(zattrs, timing):
                break
        except (json.JSONDecodeError, IOError):
            pass

    # Check zarr.json files for Zarr v3 with OME-NGFF 0.5 (attributes inside zarr.json)
    if timing.file_format != "zarr_v3":
        zarr_jsons = (
            list(folder.glob("**/plate.zarr/**/zarr.json"))
            + list(folder.glob("**/plate.ome.zarr/**/zarr.json"))
            + list(folder.glob("**/*.ome.zarr/zarr.json"))
            + list(folder.glob("**/zarr.json"))
        )
        for zarr_json_path in zarr_jsons:
            try:
                with open(zarr_json_path, "r") as f:
                    zarr_json = json.load(f)
                if _extract_squid_metadata(zarr_json.get("attributes", {}), timing):
                    break
            except (json.JSONDecodeError, IOError):
                pass

    return timing, metadata


def print_timing_report(timing: AcquisitionTiming, verbose: bool = False) -> None:
    """Print a formatted timing report."""
    print(f"\n{'=' * 60}")
    print(f"ACQUISITION TIMING REPORT")
    print(f"{'=' * 60}")
    print(f"Log: {timing.log_path}")
    print(f"\nFormat: {timing.file_format}")
    if timing.compression != "unknown":
        print(f"Compression: {timing.compression}")

    print(f"\n--- Summary ---")
    print(f"  Images captured: {timing.num_images}")
    if timing.image_size:
        print(f"  Image size: {timing.image_size[1]} x {timing.image_size[0]} ({timing.bytes_per_pixel * 8}-bit)")
    if timing.total_data_mb:
        print(f"  Total raw data: {timing.total_data_mb:.1f} MB")
    print(f"  FOVs: {timing.num_fovs}")
    print(f"  Regions: {timing.num_regions}")

    if timing.total_acquisition_time_s:
        print(f"\n--- Timing ---")
        print(f"  Total acquisition time: {timing.total_acquisition_time_s:.3f} s")
        if timing.total_processing_time_s:
            print(f"  Including processing: {timing.total_processing_time_s:.3f} s")
        if timing.total_reset_time_s:
            print(f"  Including reset: {timing.total_reset_time_s:.3f} s")

        if timing.images_per_second:
            print(f"\n--- Throughput ---")
            print(f"  Images/second: {timing.images_per_second:.2f}")
            print(f"  Avg time/image: {timing.avg_time_per_image_ms:.2f} ms")
            if timing.throughput_mb_s:
                print(f"  Data rate: {timing.throughput_mb_s:.1f} MB/s (raw)")

    if timing.peak_memory_mb:
        print(f"\n--- Memory ---")
        if timing.start_memory_mb:
            print(f"  Start: {timing.start_memory_mb:.1f} MB")
        print(f"  Peak: {timing.peak_memory_mb:.1f} MB")
        if timing.end_memory_mb:
            print(f"  End: {timing.end_memory_mb:.1f} MB")

    if verbose and timing.timer_stats:
        print(f"\n--- Timer Breakdown ---")
        # Sort by total time descending
        sorted_timers = sorted(timing.timer_stats.items(), key=lambda x: x[1]["total_s"], reverse=True)
        for name, stats in sorted_timers[:10]:  # Top 10 timers
            avg_ms = (stats["total_s"] / stats["count"]) * 1000 if stats["count"] > 0 else 0
            print(f"  {name}: {stats['total_s']:.3f}s total, {avg_ms:.2f}ms avg (n={stats['count']})")

    print(f"{'=' * 60}\n")


def compare_timings(timings: List[AcquisitionTiming]) -> None:
    """Compare multiple acquisition timings."""
    print(f"\n{'=' * 90}")
    print(f"ACQUISITION COMPARISON")
    print(f"{'=' * 90}")

    # Header
    print(f"\n{'Format':<15} {'Compression':<10} {'Images':<7} {'Time (s)':<10} {'Img/s':<8} {'MB/s':<8} {'ms/img':<8}")
    print("-" * 90)

    for t in timings:
        format_str = t.file_format[:13]
        comp_str = t.compression[:8] if t.compression != "unknown" else "-"
        time_str = f"{t.total_acquisition_time_s:.2f}" if t.total_acquisition_time_s else "-"
        ips_str = f"{t.images_per_second:.2f}" if t.images_per_second else "-"
        mbs_str = f"{t.throughput_mb_s:.1f}" if t.throughput_mb_s else "-"
        tpi_str = f"{t.avg_time_per_image_ms:.2f}" if t.avg_time_per_image_ms else "-"
        print(
            f"{format_str:<15} {comp_str:<10} {t.num_images:<7} {time_str:<10} {ips_str:<8} {mbs_str:<8} {tpi_str:<8}"
        )

    print("-" * 90)

    # Calculate speedup if we have a baseline
    if len(timings) >= 2 and all(t.total_acquisition_time_s for t in timings):
        baseline = timings[0]
        print(f"\nSpeedup vs {baseline.file_format} ({baseline.compression}):")
        for t in timings[1:]:
            speedup = baseline.total_acquisition_time_s / t.total_acquisition_time_s
            print(f"  {t.file_format} ({t.compression}): {speedup:.2f}x")

    print(f"{'=' * 90}\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze acquisition log timing")
    parser.add_argument("paths", nargs="+", help="Path to acquisition log file(s) or folder(s)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed timer breakdown")
    parser.add_argument("--compare", action="store_true", help="Compare multiple acquisitions")
    args = parser.parse_args()

    timings = []

    for path in args.paths:
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            try:
                timing, _ = parse_acquisition_folder(path)
                timings.append(timing)
            except FileNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                continue
        elif os.path.isfile(path):
            timing = parse_acquisition_log(path)
            timings.append(timing)
        else:
            print(f"Error: Path not found: {path}", file=sys.stderr)
            continue

    if not timings:
        print("No valid logs found", file=sys.stderr)
        sys.exit(1)

    if args.compare and len(timings) > 1:
        compare_timings(timings)
    else:
        for timing in timings:
            print_timing_report(timing, verbose=args.verbose)


if __name__ == "__main__":
    main()
