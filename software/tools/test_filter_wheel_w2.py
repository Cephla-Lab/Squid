#!/usr/bin/env python3
"""
Test script for the second filter wheel (W2).

Usage:
    cd software
    python tools/test_filter_wheel_w2.py

This script tests:
1. Microcontroller connection
2. W2 initialization (INITFILTERWHEEL_W2 command)
3. W2 homing
4. W2 movement to different positions

Press Ctrl+C to exit at any time.
"""

import sys
import time

# Add parent directory to path for imports
sys.path.insert(0, ".")

from control._def import *
from control.microcontroller import Microcontroller, get_microcontroller_serial_device


def wait_for_completion(mcu, timeout=15, description="operation"):
    """Wait for MCU operation to complete."""
    print(f"  Waiting for {description}...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        if not mcu.is_busy():
            elapsed = time.time() - start
            print(f" done ({elapsed:.1f}s)")
            return True
        time.sleep(0.05)
    print(f" TIMEOUT after {timeout}s!")
    return False


def test_w2():
    print("=" * 60)
    print("Second Filter Wheel (W2) Test Script")
    print("=" * 60)

    # Connect to microcontroller
    print("\n[1] Connecting to microcontroller...")
    try:
        serial_device = get_microcontroller_serial_device(simulated=False)
        mcu = Microcontroller(serial_device, reset_and_initialize=True)
        print("  Connected successfully")
    except Exception as e:
        print(f"  ERROR: Failed to connect: {e}")
        return False

    # Initialize W2
    print("\n[2] Initializing W2 (INITFILTERWHEEL_W2)...")
    try:
        mcu.init_filter_wheel(AXIS.W2)
        time.sleep(0.5)
        print("  W2 initialized")
    except Exception as e:
        print(f"  ERROR: Failed to initialize W2: {e}")
        return False

    # Configure W2
    print("\n[3] Configuring W2 motor...")
    try:
        mcu.configure_squidfilter(AXIS.W2)
        print("  W2 configured")
    except Exception as e:
        print(f"  ERROR: Failed to configure W2: {e}")
        return False

    # Home W2
    print("\n[4] Homing W2...")
    try:
        mcu.home_w2()
        if not wait_for_completion(mcu, timeout=15, description="W2 homing"):
            print("  WARNING: Homing timed out - check limit switch")
            return False
        print("  W2 homed successfully")
    except Exception as e:
        print(f"  ERROR: Failed to home W2: {e}")
        return False

    # Move W2 to different positions
    print("\n[5] Testing W2 movement...")

    # Calculate step size for 8-position filter wheel
    num_positions = 8
    step_size_mm = SCREW_PITCH_W_MM / num_positions
    usteps_per_position = int(
        STAGE_MOVEMENT_SIGN_W * step_size_mm / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W))
    )

    print(f"  Step size: {step_size_mm:.3f} mm ({usteps_per_position} usteps)")

    for pos in range(1, 5):  # Test positions 1-4
        print(f"\n  Moving to position {pos}...")
        mcu.move_w2_usteps(usteps_per_position)
        if not wait_for_completion(mcu, timeout=5, description=f"move to position {pos}"):
            print("  WARNING: Movement timed out")
        time.sleep(0.3)

    # Return home
    print("\n[6] Returning to home position...")
    mcu.home_w2()
    if not wait_for_completion(mcu, timeout=15, description="return home"):
        print("  WARNING: Return home timed out")

    print("\n" + "=" * 60)
    print("W2 TEST COMPLETE")
    print("=" * 60)

    # Cleanup
    mcu.close()
    return True


if __name__ == "__main__":
    try:
        success = test_w2()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
