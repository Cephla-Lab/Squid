#!/usr/bin/env python3
"""Measure subprocess startup time for JobRunner.

This script measures the cold start time of the JobRunner subprocess to determine
if pre-warming provides meaningful benefit. The measurement includes:
- Time to fork/spawn the subprocess
- Time to initialize (imports, logging setup, memory monitoring)
- Time to signal ready

Usage:
    python tools/measure_subprocess_startup.py

Results help decide whether ~135 lines of pre-warming complexity is justified.
"""

import multiprocessing
import os
import sys
import time
import statistics

# Add the software directory to Python path
software_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, software_dir)

# Suppress Qt warnings when importing control modules
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false")

from control.core.job_processing import JobRunner


def measure_startup_time(iteration: int) -> dict:
    """Measure a single JobRunner startup cycle.

    Returns dict with timing measurements in milliseconds.
    """
    # Time point 1: Before creating JobRunner
    t1_create_start = time.perf_counter()

    runner = JobRunner()

    # Time point 2: After creation (before start)
    t2_created = time.perf_counter()

    # Time point 3: Start subprocess
    runner.start()
    t3_started = time.perf_counter()

    # Time point 4: Wait for ready signal
    ready = runner.wait_ready(timeout_s=10.0)
    t4_ready = time.perf_counter()

    if not ready:
        raise RuntimeError(f"JobRunner failed to become ready within 10s (iteration {iteration})")

    # Shutdown
    runner.shutdown(timeout_s=2.0)
    t5_shutdown = time.perf_counter()

    return {
        "create_ms": (t2_created - t1_create_start) * 1000,
        "start_ms": (t3_started - t2_created) * 1000,
        "ready_ms": (t4_ready - t3_started) * 1000,
        "total_ms": (t4_ready - t1_create_start) * 1000,
        "shutdown_ms": (t5_shutdown - t4_ready) * 1000,
    }


def run_measurements(iterations: int = 10) -> None:
    """Run multiple iterations and print statistics."""
    print(f"\n{'='*60}")
    print("JobRunner Subprocess Startup Time Measurement")
    print(f"{'='*60}")
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Multiprocessing start method: {multiprocessing.get_start_method()}")
    print(f"Iterations: {iterations}")
    print(f"{'='*60}\n")

    results = []

    # Warmup iteration (not counted)
    print("Warmup iteration...", end=" ", flush=True)
    try:
        measure_startup_time(0)
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")
        return

    print()

    for i in range(1, iterations + 1):
        print(f"Iteration {i}/{iterations}...", end=" ", flush=True)
        try:
            result = measure_startup_time(i)
            results.append(result)
            print(
                f"total={result['total_ms']:.1f}ms (create={result['create_ms']:.1f}, "
                f"start={result['start_ms']:.1f}, ready={result['ready_ms']:.1f})"
            )
        except Exception as e:
            print(f"FAILED: {e}")

    if not results:
        print("\nNo successful measurements!")
        return

    # Calculate statistics
    print(f"\n{'='*60}")
    print("Results Summary")
    print(f"{'='*60}")

    metrics = ["create_ms", "start_ms", "ready_ms", "total_ms", "shutdown_ms"]
    labels = {
        "create_ms": "JobRunner() creation",
        "start_ms": "subprocess .start()",
        "ready_ms": "wait_ready()",
        "total_ms": "TOTAL (create→ready)",
        "shutdown_ms": "shutdown()",
    }

    print(f"\n{'Metric':<25} {'Mean':>10} {'StdDev':>10} {'Min':>10} {'Max':>10}")
    print("-" * 65)

    for metric in metrics:
        values = [r[metric] for r in results]
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0
        min_val = min(values)
        max_val = max(values)
        print(f"{labels[metric]:<25} {mean:>10.1f} {stdev:>10.1f} {min_val:>10.1f} {max_val:>10.1f}")

    # Calculate key insight
    total_mean = statistics.mean([r["total_ms"] for r in results])

    print(f"\n{'='*60}")
    print("Interpretation")
    print(f"{'='*60}")
    print(f"\nCold subprocess startup takes ~{total_mean:.0f}ms on average.")
    print("\nPre-warming saves this time by starting the subprocess in advance.")
    print(f"The pre-warming code adds ~135 lines of complexity for this {total_mean:.0f}ms benefit.")

    if total_mean < 100:
        print("\n→ Startup is FAST (<100ms). Pre-warming benefit is minimal.")
        print("  Consider removing pre-warming to simplify the code.")
    elif total_mean < 300:
        print("\n→ Startup is MODERATE (100-300ms). Pre-warming provides some benefit.")
        print("  Keep pre-warming if acquisition start latency is critical.")
    else:
        print("\n→ Startup is SLOW (>300ms). Pre-warming provides significant benefit.")
        print("  Keep pre-warming for better user experience.")


if __name__ == "__main__":
    run_measurements(iterations=10)
