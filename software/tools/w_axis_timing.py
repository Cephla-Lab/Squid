#!/usr/bin/env python3
"""
W Axis (Filter Wheel) Performance Timing Test

This script measures the movement performance of the W axis (filter wheel),
including move time, settling time, and position accuracy.

It also compares linear vs shortest path movement strategies to demonstrate
the efficiency improvement from the shortest path algorithm.

Usage:
    cd software
    python tools/w_axis_timing.py --count 20 --positions 8
    python tools/w_axis_timing.py --verbose --no_home
    python tools/w_axis_timing.py --shortest_path_test  # Compare linear vs shortest path
"""

import argparse
import logging
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

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

log = squid.logging.get_logger("w_axis_timing")


@dataclass
class MoveResult:
    """Result of a single move operation."""

    move_index: int
    from_position: int
    to_position: int
    distance_usteps: int
    move_time_ms: float
    wait_time_ms: float
    total_time_ms: float


@dataclass
class TestSummary:
    """Summary statistics for all moves."""

    total_moves: int
    total_time_s: float
    avg_move_time_ms: float
    avg_wait_time_ms: float
    avg_total_time_ms: float
    min_total_time_ms: float
    max_total_time_ms: float
    positions_tested: int
    usteps_per_position: int


def usteps_per_mm() -> float:
    """Calculate microsteps per millimeter."""
    return FULLSTEPS_PER_REV_W * MICROSTEPPING_DEFAULT_W / SCREW_PITCH_W_MM


def mm_to_usteps(mm: float) -> int:
    """Convert millimeters to microsteps."""
    return int(mm * usteps_per_mm())


def usteps_to_mm(usteps: int) -> float:
    """Convert microsteps to millimeters."""
    return usteps / usteps_per_mm()


class WAxisTimingTest:
    """W Axis timing test controller."""

    def __init__(self, simulated: bool = False):
        self.simulated = simulated
        self.mcu = None
        self.results: List[MoveResult] = []

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
        self.mcu.configure_squidfilter()  # Configure W axis velocity, acceleration, current, etc.
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
        wait_time = self.wait_till_operation_is_completed()
        total_time = (time.perf_counter() - t0) * 1000
        log.info(f"Homing complete: {total_time:.1f} ms")

    def move_w_usteps(self, usteps: int) -> MoveResult:
        """Move W axis by specified microsteps and measure timing."""
        # Record start position (approximate, since we don't have direct position readback)
        from_pos = 0  # Placeholder

        # Start timing
        t_start = time.perf_counter()

        # Send move command
        self.mcu.move_w_usteps(usteps)
        t_after_send = time.perf_counter()

        # Wait for completion
        wait_time_ms = self.wait_till_operation_is_completed()
        t_end = time.perf_counter()

        move_time_ms = (t_after_send - t_start) * 1000
        total_time_ms = (t_end - t_start) * 1000

        return MoveResult(
            move_index=len(self.results),
            from_position=from_pos,
            to_position=from_pos + usteps,
            distance_usteps=usteps,
            move_time_ms=move_time_ms,
            wait_time_ms=wait_time_ms,
            total_time_ms=total_time_ms,
        )

    def run_position_cycle_test(self, num_positions: int, num_cycles: int) -> TestSummary:
        """
        Run a test cycling through filter wheel positions.

        Args:
            num_positions: Number of positions on the filter wheel (e.g., 8)
            num_cycles: Number of complete cycles to run
        """
        # Calculate usteps per position
        usteps_per_position = mm_to_usteps(SCREW_PITCH_W_MM / num_positions)

        log.info(f"Test configuration:")
        log.info(f"  Positions: {num_positions}")
        log.info(f"  Cycles: {num_cycles}")
        log.info(f"  Usteps per position: {usteps_per_position}")
        log.info(f"  Distance per position: {usteps_to_mm(usteps_per_position):.4f} mm")
        log.info("")

        self.results = []
        t_test_start = time.perf_counter()

        for cycle in range(num_cycles):
            log.info(f"Cycle {cycle + 1}/{num_cycles}")

            # Forward through all positions
            for pos in range(num_positions - 1):
                result = self.move_w_usteps(usteps_per_position)
                self.results.append(result)
                log.debug(f"  Pos {pos} -> {pos + 1}: {result.total_time_ms:.1f} ms")

            # Return to start
            result = self.move_w_usteps(-usteps_per_position * (num_positions - 1))
            self.results.append(result)
            log.debug(f"  Return to start: {result.total_time_ms:.1f} ms")

        t_test_end = time.perf_counter()
        total_time_s = t_test_end - t_test_start

        # Calculate statistics
        total_times = [r.total_time_ms for r in self.results]
        wait_times = [r.wait_time_ms for r in self.results]
        move_times = [r.move_time_ms for r in self.results]

        summary = TestSummary(
            total_moves=len(self.results),
            total_time_s=total_time_s,
            avg_move_time_ms=sum(move_times) / len(move_times),
            avg_wait_time_ms=sum(wait_times) / len(wait_times),
            avg_total_time_ms=sum(total_times) / len(total_times),
            min_total_time_ms=min(total_times),
            max_total_time_ms=max(total_times),
            positions_tested=num_positions,
            usteps_per_position=usteps_per_position,
        )

        return summary

    def run_variable_distance_test(self, distances_mm: List[float], repeats: int) -> None:
        """
        Test moves of various distances to understand velocity/acceleration impact.

        Args:
            distances_mm: List of distances to test
            repeats: Number of times to repeat each distance
        """
        log.info("Variable distance test:")
        log.info(f"  Distances: {distances_mm} mm")
        log.info(f"  Repeats per distance: {repeats}")
        log.info("")

        for distance_mm in distances_mm:
            usteps = mm_to_usteps(distance_mm)
            times = []

            for i in range(repeats):
                # Move forward
                result = self.move_w_usteps(usteps)
                times.append(result.total_time_ms)

                # Move back
                self.move_w_usteps(-usteps)

            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)

            log.info(
                f"  {distance_mm:.3f} mm ({usteps} usteps): "
                f"avg={avg_time:.1f} ms, min={min_time:.1f} ms, max={max_time:.1f} ms"
            )

    def _calculate_move_distance(
        self, current_pos: int, target_pos: int, num_positions: int, use_shortest_path: bool
    ) -> Tuple[int, str]:
        """
        Calculate the move distance in positions.

        Args:
            current_pos: Current position (0-indexed)
            target_pos: Target position (0-indexed)
            num_positions: Total number of positions
            use_shortest_path: If True, choose shorter direction; if False, always go forward

        Returns:
            Tuple of (steps to move, direction description)
        """
        if current_pos == target_pos:
            return 0, "none"

        forward_steps = (target_pos - current_pos) % num_positions
        backward_steps = (current_pos - target_pos) % num_positions

        if use_shortest_path:
            if forward_steps <= backward_steps:
                return forward_steps, "forward"
            else:
                return -backward_steps, "backward"
        else:
            # Always go forward (linear)
            return forward_steps, "forward"

    def run_shortest_path_comparison(self, num_positions: int, num_cycles: int, access_pattern: str = "random") -> dict:
        """
        Compare linear vs shortest path movement strategies.

        Args:
            num_positions: Number of positions on the filter wheel
            num_cycles: Number of complete cycles to run
            access_pattern: "random", "worst_case", or "typical"

        Returns:
            Dict with comparison results
        """
        usteps_per_position = mm_to_usteps(SCREW_PITCH_W_MM / num_positions)

        # Generate access pattern
        if access_pattern == "random":
            # Random positions
            positions_list = []
            for _ in range(num_cycles):
                cycle_positions = random.sample(range(num_positions), num_positions)
                positions_list.extend(cycle_positions)
        elif access_pattern == "worst_case":
            # Alternating between position 0 and position num_positions-1
            # This pattern maximizes the benefit of shortest path
            positions_list = [0]  # Start at 0
            for _ in range(num_cycles * 4):
                positions_list.append(num_positions - 1)  # 0 -> 7
                positions_list.append(0)  # 7 -> 0
        else:  # typical - imaging cycle visiting every other position
            positions_list = []
            for _ in range(num_cycles):
                # Visit positions 0, 2, 4, 6, ... then back to 0
                for i in range(0, num_positions, 2):
                    positions_list.append(i)
                positions_list.append(0)

        log.info("")
        log.info("=" * 60)
        log.info("SHORTEST PATH COMPARISON TEST")
        log.info("=" * 60)
        log.info(f"Access pattern: {access_pattern}")
        log.info(f"Positions: {num_positions}")
        log.info(f"Total moves: {len(positions_list) - 1}")
        log.info("")

        # Test LINEAR method (always forward)
        log.info("Testing LINEAR method (always forward)...")
        self.home()
        current_pos = 0
        linear_times = []
        linear_total_steps = 0

        for target_pos in positions_list[1:]:
            steps, direction = self._calculate_move_distance(
                current_pos, target_pos, num_positions, use_shortest_path=False
            )
            if steps != 0:
                usteps = steps * usteps_per_position
                result = self.move_w_usteps(usteps)
                linear_times.append(result.total_time_ms)
                linear_total_steps += abs(steps)
            current_pos = target_pos

        linear_total_time = sum(linear_times)
        linear_avg_time = linear_total_time / len(linear_times) if linear_times else 0

        # Test SHORTEST PATH method
        log.info("Testing SHORTEST PATH method...")
        self.home()
        current_pos = 0
        shortest_times = []
        shortest_total_steps = 0

        for target_pos in positions_list[1:]:
            steps, direction = self._calculate_move_distance(
                current_pos, target_pos, num_positions, use_shortest_path=True
            )
            if steps != 0:
                usteps = steps * usteps_per_position
                result = self.move_w_usteps(usteps)
                shortest_times.append(result.total_time_ms)
                shortest_total_steps += abs(steps)
            current_pos = target_pos

        shortest_total_time = sum(shortest_times)
        shortest_avg_time = shortest_total_time / len(shortest_times) if shortest_times else 0

        # Calculate improvement
        time_saved = linear_total_time - shortest_total_time
        time_improvement = (time_saved / linear_total_time * 100) if linear_total_time > 0 else 0
        steps_saved = linear_total_steps - shortest_total_steps
        steps_improvement = (steps_saved / linear_total_steps * 100) if linear_total_steps > 0 else 0

        # Print results
        log.info("")
        log.info("-" * 60)
        log.info("RESULTS")
        log.info("-" * 60)
        log.info(f"{'Method':<20} {'Total Time':<15} {'Avg Time':<15} {'Total Steps':<15}")
        log.info("-" * 60)
        log.info(
            f"{'Linear':<20} {linear_total_time:.1f} ms{'':<6} {linear_avg_time:.1f} ms{'':<6} {linear_total_steps}"
        )
        log.info(
            f"{'Shortest Path':<20} {shortest_total_time:.1f} ms{'':<6} {shortest_avg_time:.1f} ms{'':<6} {shortest_total_steps}"
        )
        log.info("-" * 60)
        log.info(f"{'Saved':<20} {time_saved:.1f} ms{'':<6} {'':<15} {steps_saved}")
        log.info(f"{'Improvement':<20} {time_improvement:.1f}%{'':<9} {'':<15} {steps_improvement:.1f}%")
        log.info("=" * 60)

        return {
            "linear_total_time_ms": linear_total_time,
            "linear_avg_time_ms": linear_avg_time,
            "linear_total_steps": linear_total_steps,
            "shortest_total_time_ms": shortest_total_time,
            "shortest_avg_time_ms": shortest_avg_time,
            "shortest_total_steps": shortest_total_steps,
            "time_saved_ms": time_saved,
            "time_improvement_pct": time_improvement,
            "steps_saved": steps_saved,
            "steps_improvement_pct": steps_improvement,
        }

    def print_summary(self, summary: TestSummary):
        """Print test summary."""
        log.info("")
        log.info("=" * 60)
        log.info("TEST SUMMARY")
        log.info("=" * 60)
        log.info(f"Total moves:          {summary.total_moves}")
        log.info(f"Total test time:      {summary.total_time_s:.2f} s")
        log.info(f"Positions tested:     {summary.positions_tested}")
        log.info(f"Usteps per position:  {summary.usteps_per_position}")
        log.info("")
        log.info("Timing Statistics:")
        log.info(f"  Avg command send:   {summary.avg_move_time_ms:.2f} ms")
        log.info(f"  Avg wait time:      {summary.avg_wait_time_ms:.2f} ms")
        log.info(f"  Avg total time:     {summary.avg_total_time_ms:.2f} ms")
        log.info(f"  Min total time:     {summary.min_total_time_ms:.2f} ms")
        log.info(f"  Max total time:     {summary.max_total_time_ms:.2f} ms")
        log.info("")
        log.info(f"Throughput:           {summary.total_moves / summary.total_time_s:.1f} moves/s")
        log.info("=" * 60)

    def close(self):
        """Close the connection."""
        if self.mcu:
            self.mcu.close()
            log.info("Connection closed")


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    test = WAxisTimingTest(simulated=args.simulated)

    try:
        test.connect()
        test.initialize()

        if not args.no_home:
            test.home()

        # Run position cycle test
        summary = test.run_position_cycle_test(
            num_positions=args.positions,
            num_cycles=args.count,
        )
        test.print_summary(summary)

        # Optionally run variable distance test
        if args.distance_test:
            log.info("")
            test.run_variable_distance_test(
                distances_mm=[0.01, 0.05, 0.1, 0.2, 0.5],
                repeats=5,
            )

        # Run shortest path comparison test
        if args.shortest_path_test:
            for pattern in ["typical", "random", "worst_case"]:
                test.run_shortest_path_comparison(
                    num_positions=args.positions,
                    num_cycles=args.count,
                    access_pattern=pattern,
                )

    except KeyboardInterrupt:
        log.info("Test interrupted by user")
    except Exception as e:
        log.error(f"Test failed: {e}")
        raise
    finally:
        test.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="W Axis (Filter Wheel) performance timing test")

    ap.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    ap.add_argument("--simulated", action="store_true", help="Use simulated microcontroller")
    ap.add_argument("--no_home", action="store_true", help="Skip homing before test")
    ap.add_argument("--positions", type=int, default=8, help="Number of filter wheel positions (default: 8)")
    ap.add_argument("--count", type=int, default=5, help="Number of complete cycles to run (default: 5)")
    ap.add_argument("--distance_test", action="store_true", help="Also run variable distance test")
    ap.add_argument("--shortest_path_test", action="store_true", help="Run shortest path vs linear comparison test")

    sys.exit(main(ap.parse_args()) or 0)
