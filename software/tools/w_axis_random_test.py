#!/usr/bin/env python3
"""
W Axis (Filter Wheel) Random Position Test

This script tests the filter wheel by randomly moving between positions
for a specified duration. It homes the wheel at the start and tracks
movement statistics.

Usage:
    cd software
    python tools/w_axis_random_test.py --duration 30
    python tools/w_axis_random_test.py --duration 60 --verbose
    python tools/w_axis_random_test.py --compare  # Compare shortest path vs linear
"""

import argparse
import logging
import random
import sys
import time
from pathlib import Path

# Add software directory to path for imports
_software_dir = Path(__file__).resolve().parent.parent
if str(_software_dir) not in sys.path:
    sys.path.insert(0, str(_software_dir))

import squid.logging
from control._def import (
    CONTROLLER_SN,
    CONTROLLER_VERSION,
    FULLSTEPS_PER_REV_W,
    MICROSTEPPING_DEFAULT_W,
    SCREW_PITCH_W_MM,
    SLEEP_TIME_S,
)
import control.microcontroller as microcontroller

log = squid.logging.get_logger("w_axis_random_test")


def usteps_per_mm() -> float:
    """Calculate microsteps per millimeter."""
    return FULLSTEPS_PER_REV_W * MICROSTEPPING_DEFAULT_W / SCREW_PITCH_W_MM


def mm_to_usteps(mm: float) -> int:
    """Convert millimeters to microsteps."""
    return int(mm * usteps_per_mm())


class WAxisRandomTest:
    """W Axis random position test controller."""

    def __init__(self, simulated: bool = False):
        self.simulated = simulated
        self.mcu = None
        self.num_positions = 8
        self.current_position = 0

    def connect(self):
        """Connect to the microcontroller."""
        log.info("Connecting to microcontroller...")
        serial_device = microcontroller.get_microcontroller_serial_device(
            version=CONTROLLER_VERSION,
            sn=CONTROLLER_SN,
            simulated=self.simulated,
        )
        self.mcu = microcontroller.Microcontroller(serial_device=serial_device)
        log.info("Connected successfully")

    def initialize(self):
        """Initialize the microcontroller and W axis."""
        log.info("Initializing microcontroller...")
        self.mcu.reset()
        time.sleep(0.5)

        log.info("Initializing filter wheel (W axis)...")
        self.mcu.init_filter_wheel()
        time.sleep(0.5)

        self.mcu.initialize_drivers()
        time.sleep(0.5)

        self.mcu.configure_actuators()

        log.info("Configuring W axis parameters...")
        self.mcu.configure_squidfilter()
        log.info(f"Microstepping: {MICROSTEPPING_DEFAULT_W}")
        log.info("Initialization complete")

    def wait_till_operation_is_completed(self) -> float:
        """Wait for operation to complete. Returns wait time in ms."""
        t0 = time.perf_counter()
        while self.mcu.is_busy():
            time.sleep(SLEEP_TIME_S)
        return (time.perf_counter() - t0) * 1000

    def home(self):
        """Home the W axis."""
        log.info("Homing W axis...")
        t0 = time.perf_counter()
        self.mcu.home_w()
        self.wait_till_operation_is_completed()
        total_time = (time.perf_counter() - t0) * 1000
        log.info(f"Homing complete: {total_time:.1f} ms")
        self.current_position = 0

    def move_to_position(self, target_pos: int, use_shortest_path: bool = True) -> tuple:
        """
        Move to target position.

        Args:
            target_pos: Target position (0-indexed, 0 to num_positions-1)
            use_shortest_path: If True, use shortest path; if False, always go forward

        Returns:
            Tuple of (time in ms, steps moved)
        """
        if target_pos == self.current_position:
            return 0.0, 0

        usteps_per_position = mm_to_usteps(SCREW_PITCH_W_MM / self.num_positions)

        # Calculate forward and backward distances
        forward_steps = (target_pos - self.current_position) % self.num_positions
        backward_steps = (self.current_position - target_pos) % self.num_positions

        if use_shortest_path:
            # Choose shortest path
            if forward_steps <= backward_steps:
                steps = forward_steps
            else:
                steps = -backward_steps
        else:
            # Always go forward (linear)
            steps = forward_steps

        usteps = steps * usteps_per_position

        t0 = time.perf_counter()
        self.mcu.move_w_usteps(usteps)
        self.wait_till_operation_is_completed()
        move_time = (time.perf_counter() - t0) * 1000

        self.current_position = target_pos
        return move_time, abs(steps)

    def run_random_test(self, duration_seconds: float, use_shortest_path: bool = True) -> dict:
        """
        Run random position test for specified duration.

        Args:
            duration_seconds: Test duration in seconds
            use_shortest_path: If True, use shortest path algorithm

        Returns:
            Dict with test statistics
        """
        mode = "SHORTEST PATH" if use_shortest_path else "LINEAR (always forward)"

        log.info("")
        log.info("=" * 60)
        log.info(f"RANDOM POSITION TEST - {mode}")
        log.info("=" * 60)
        log.info(f"Duration: {duration_seconds} seconds")
        log.info(f"Positions: {self.num_positions}")
        log.info(f"Mode: {mode}")
        log.info("")

        move_times = []
        move_count = 0
        total_steps = 0
        position_visits = [0] * self.num_positions

        start_time = time.perf_counter()
        end_time = start_time + duration_seconds

        log.info("Starting random movement test...")
        log.info("")

        while time.perf_counter() < end_time:
            # Generate random target position (different from current)
            available_positions = [p for p in range(self.num_positions) if p != self.current_position]
            target_pos = random.choice(available_positions)

            # Move to target
            move_time, steps = self.move_to_position(target_pos, use_shortest_path)
            move_times.append(move_time)
            total_steps += steps
            move_count += 1
            position_visits[target_pos] += 1

            # Progress update every 10 moves
            if move_count % 10 == 0:
                elapsed = time.perf_counter() - start_time
                remaining = duration_seconds - elapsed
                log.info(f"  Moves: {move_count}, Elapsed: {elapsed:.1f}s, Remaining: {remaining:.1f}s")

        total_time = time.perf_counter() - start_time

        # Calculate statistics
        avg_time = sum(move_times) / len(move_times) if move_times else 0
        min_time = min(move_times) if move_times else 0
        max_time = max(move_times) if move_times else 0
        throughput = move_count / total_time if total_time > 0 else 0
        avg_steps = total_steps / move_count if move_count > 0 else 0

        # Print results
        log.info("")
        log.info("=" * 60)
        log.info(f"TEST RESULTS - {mode}")
        log.info("=" * 60)
        log.info(f"Total moves:        {move_count}")
        log.info(f"Total steps:        {total_steps}")
        log.info(f"Avg steps/move:     {avg_steps:.2f}")
        log.info(f"Total time:         {total_time:.2f} s")
        log.info(f"Throughput:         {throughput:.1f} moves/s")
        log.info("")
        log.info("Timing Statistics:")
        log.info(f"  Average:          {avg_time:.1f} ms")
        log.info(f"  Minimum:          {min_time:.1f} ms")
        log.info(f"  Maximum:          {max_time:.1f} ms")
        log.info(f"  Per step:         {avg_time/avg_steps:.1f} ms/step" if avg_steps > 0 else "")
        log.info("")
        log.info("Position Visit Count:")
        for pos in range(self.num_positions):
            log.info(f"  Position {pos}: {position_visits[pos]} visits")
        log.info("=" * 60)

        return {
            "mode": mode,
            "total_moves": move_count,
            "total_steps": total_steps,
            "avg_steps": avg_steps,
            "total_time_s": total_time,
            "throughput": throughput,
            "avg_time_ms": avg_time,
            "min_time_ms": min_time,
            "max_time_ms": max_time,
            "position_visits": position_visits,
        }

    def close(self):
        """Close the connection."""
        if self.mcu:
            self.mcu.close()
            log.info("Connection closed")


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    test = WAxisRandomTest(simulated=args.simulated)

    try:
        test.connect()
        test.initialize()

        # Always home at start
        test.home()

        if args.compare:
            # Run both tests for comparison
            log.info("\n" + "=" * 60)
            log.info("COMPARISON MODE: Running both shortest path and linear tests")
            log.info("=" * 60)

            # Use fixed random seed for fair comparison
            random.seed(42)
            result_shortest = test.run_random_test(duration_seconds=args.duration, use_shortest_path=True)

            # Home again before second test
            test.home()

            # Use same random seed
            random.seed(42)
            result_linear = test.run_random_test(duration_seconds=args.duration, use_shortest_path=False)

            # Print comparison
            log.info("")
            log.info("=" * 60)
            log.info("COMPARISON SUMMARY")
            log.info("=" * 60)
            log.info(f"{'Metric':<20} {'Shortest Path':<15} {'Linear':<15} {'Difference':<15}")
            log.info("-" * 60)
            log.info(f"{'Total moves':<20} {result_shortest['total_moves']:<15} {result_linear['total_moves']:<15}")
            log.info(
                f"{'Total steps':<20} {result_shortest['total_steps']:<15} {result_linear['total_steps']:<15} {result_linear['total_steps'] - result_shortest['total_steps']:>+15}"
            )
            log.info(
                f"{'Avg time (ms)':<20} {result_shortest['avg_time_ms']:<15.1f} {result_linear['avg_time_ms']:<15.1f} {result_linear['avg_time_ms'] - result_shortest['avg_time_ms']:>+15.1f}"
            )
            log.info(
                f"{'Throughput':<20} {result_shortest['throughput']:<15.1f} {result_linear['throughput']:<15.1f} {result_shortest['throughput'] - result_linear['throughput']:>+15.1f}"
            )

            time_saved_pct = (
                (result_linear["avg_time_ms"] - result_shortest["avg_time_ms"]) / result_linear["avg_time_ms"] * 100
            )
            steps_saved_pct = (
                (result_linear["total_steps"] - result_shortest["total_steps"]) / result_linear["total_steps"] * 100
            )
            log.info("")
            log.info(f"Time saved:         {time_saved_pct:.1f}%")
            log.info(f"Steps saved:        {steps_saved_pct:.1f}%")
            log.info("=" * 60)
        else:
            # Run single test
            result = test.run_random_test(duration_seconds=args.duration, use_shortest_path=not args.no_shortest_path)

        return 0

    except KeyboardInterrupt:
        log.info("Test interrupted by user")
        return 1
    except Exception as e:
        log.error(f"Test failed: {e}")
        raise
    finally:
        test.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="W Axis (Filter Wheel) random position test")

    ap.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    ap.add_argument("--simulated", action="store_true", help="Use simulated microcontroller")
    ap.add_argument("--duration", type=float, default=30, help="Test duration in seconds (default: 30)")
    ap.add_argument(
        "--no_shortest_path",
        action="store_true",
        help="Disable shortest path algorithm (always move forward)",
    )
    ap.add_argument("--compare", action="store_true", help="Run both modes and compare results")

    sys.exit(main(ap.parse_args()))
