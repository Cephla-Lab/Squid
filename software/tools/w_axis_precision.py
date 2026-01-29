#!/usr/bin/env python3
"""
W Axis (Filter Wheel) Precision Test

This script tests the positioning precision of the W axis by:
1. Moving to each position multiple times
2. Checking for cumulative position drift
3. Verifying return-to-home accuracy

Usage:
    cd software
    python tools/w_axis_precision.py --cycles 50
    python tools/w_axis_precision.py --cycles 100 --verbose
"""

import argparse
import logging
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

log = squid.logging.get_logger("w_axis_precision")


def usteps_per_mm() -> float:
    """Calculate microsteps per millimeter."""
    return FULLSTEPS_PER_REV_W * MICROSTEPPING_DEFAULT_W / SCREW_PITCH_W_MM


def mm_to_usteps(mm: float) -> int:
    """Convert millimeters to microsteps."""
    return int(mm * usteps_per_mm())


class WAxisPrecisionTest:
    """W Axis precision test controller."""

    def __init__(self, simulated: bool = False):
        self.simulated = simulated
        self.mcu = None
        self.errors = []

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

    def wait_till_operation_is_completed(self):
        """Wait for operation to complete."""
        while self.mcu.is_busy():
            time.sleep(SLEEP_TIME_S)

    def home(self):
        """Home the W axis."""
        log.info("Homing W axis...")
        self.mcu.home_w()
        self.wait_till_operation_is_completed()
        log.info("Homing complete")

    def get_position(self) -> int:
        """Get current W axis position in microsteps."""
        # Read position from microcontroller
        # This depends on the microcontroller API
        return 0  # Placeholder - actual position tracking is internal

    def run_cycle_test(self, num_positions: int, num_cycles: int) -> dict:
        """
        Run precision test by cycling through positions.

        Args:
            num_positions: Number of filter wheel positions
            num_cycles: Number of complete cycles

        Returns:
            dict with test results
        """
        usteps_per_position = mm_to_usteps(SCREW_PITCH_W_MM / num_positions)
        total_moves = 0

        log.info(f"Precision Test Configuration:")
        log.info(f"  Positions: {num_positions}")
        log.info(f"  Cycles: {num_cycles}")
        log.info(f"  Microstepping: {MICROSTEPPING_DEFAULT_W}")
        log.info(f"  Usteps per position: {usteps_per_position}")
        log.info(f"  Total moves: {num_cycles * num_positions}")
        log.info("")

        # Track expected position
        expected_position = 0

        for cycle in range(num_cycles):
            if (cycle + 1) % 10 == 0 or cycle == 0:
                log.info(f"Cycle {cycle + 1}/{num_cycles}")

            # Forward through all positions
            for pos in range(num_positions - 1):
                self.mcu.move_w_usteps(usteps_per_position)
                self.wait_till_operation_is_completed()
                expected_position += usteps_per_position
                total_moves += 1

            # Return to start (move back to position 0)
            return_steps = -usteps_per_position * (num_positions - 1)
            self.mcu.move_w_usteps(return_steps)
            self.wait_till_operation_is_completed()
            expected_position += return_steps
            total_moves += 1

        # Final check: expected_position should be 0
        log.info("")
        log.info(f"Test completed: {total_moves} moves")
        log.info(f"Expected final position: {expected_position} usteps (should be 0)")

        return {
            "total_moves": total_moves,
            "num_cycles": num_cycles,
            "expected_final_position": expected_position,
            "microstepping": MICROSTEPPING_DEFAULT_W,
        }

    def run_drift_test(self, distance_usteps: int, repeats: int) -> dict:
        """
        Test for cumulative drift by moving back and forth.

        Args:
            distance_usteps: Distance to move in each direction
            repeats: Number of back-and-forth cycles

        Returns:
            dict with test results
        """
        log.info(f"Drift Test Configuration:")
        log.info(f"  Distance: {distance_usteps} usteps")
        log.info(f"  Repeats: {repeats}")
        log.info("")

        for i in range(repeats):
            if (i + 1) % 20 == 0 or i == 0:
                log.info(f"  Repeat {i + 1}/{repeats}")

            # Move forward
            self.mcu.move_w_usteps(distance_usteps)
            self.wait_till_operation_is_completed()

            # Move back
            self.mcu.move_w_usteps(-distance_usteps)
            self.wait_till_operation_is_completed()

        log.info(f"Drift test completed: {repeats * 2} moves")
        log.info("Check if filter wheel returned to exact starting position")

        return {
            "total_moves": repeats * 2,
            "distance_usteps": distance_usteps,
        }

    def close(self):
        """Close the connection."""
        if self.mcu:
            self.mcu.close()
            log.info("Connection closed")


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    test = WAxisPrecisionTest(simulated=args.simulated)

    try:
        test.connect()
        test.initialize()

        if not args.no_home:
            test.home()

        log.info("")
        log.info("=" * 60)
        log.info("PRECISION TEST")
        log.info("=" * 60)

        # Run cycle test
        result = test.run_cycle_test(
            num_positions=args.positions,
            num_cycles=args.cycles,
        )

        log.info("")
        log.info("=" * 60)
        log.info("DRIFT TEST")
        log.info("=" * 60)

        # Run drift test
        usteps_per_position = mm_to_usteps(SCREW_PITCH_W_MM / args.positions)
        drift_result = test.run_drift_test(
            distance_usteps=usteps_per_position * 4,  # Move 4 positions
            repeats=args.cycles,
        )

        log.info("")
        log.info("=" * 60)
        log.info("TEST SUMMARY")
        log.info("=" * 60)
        log.info(f"Microstepping: {MICROSTEPPING_DEFAULT_W}")
        log.info(f"Total cycle moves: {result['total_moves']}")
        log.info(f"Total drift moves: {drift_result['total_moves']}")
        log.info(f"Grand total: {result['total_moves'] + drift_result['total_moves']} moves")
        log.info("")
        log.info("VISUAL CHECK REQUIRED:")
        log.info("  - Is the filter wheel at the exact starting position?")
        log.info("  - Any visible drift or misalignment?")
        log.info("=" * 60)

    except KeyboardInterrupt:
        log.info("Test interrupted by user")
    except Exception as e:
        log.error(f"Test failed: {e}")
        raise
    finally:
        test.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="W Axis (Filter Wheel) precision test")

    ap.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    ap.add_argument("--simulated", action="store_true", help="Use simulated microcontroller")
    ap.add_argument("--no_home", action="store_true", help="Skip homing before test")
    ap.add_argument("--positions", type=int, default=8, help="Number of filter wheel positions (default: 8)")
    ap.add_argument("--cycles", type=int, default=50, help="Number of test cycles (default: 50)")

    sys.exit(main(ap.parse_args()) or 0)
