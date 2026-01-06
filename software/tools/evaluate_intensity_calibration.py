import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
import sys
import os
import time
from typing import List

software_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(software_dir)
os.chdir(software_dir)

from PM16 import PM16
from control.lighting import IlluminationController, IntensityControlMode, ShutterControlMode
import control.microcontroller as microcontroller
from control._def import *


def measure_power(pm: PM16, num_measurements: int = 5, delay: float = 0.1) -> float:
    """Take multiple power measurements and return the average."""
    measurements = []
    for _ in range(num_measurements):
        measurements.append(pm.read())
        time.sleep(delay)
    return sum(measurements) / len(measurements)


def test_calibration(
    pm: PM16,
    controller: IlluminationController,
    wavelength: int,
    power_percentages: List[float],
    num_measurements: int = 5,
    delay: float = 0.1,
) -> pd.DataFrame:
    """Test calibration by measuring power at different set points."""
    print(f"\nTesting calibration for {wavelength}nm...")

    # Set power meter wavelength
    pm.set_wavelength(wavelength)

    # Initialize data storage
    requested_powers = []
    measured_powers = []

    try:
        # Test each power percentage
        for power_pct in power_percentages:
            print(f"Setting power to {power_pct:.1f}%...")

            # Set power using calibration
            controller.set_intensity(wavelength, power_pct)
            time.sleep(0.1)  # Wait for power to stabilize

            # Turn on illumination
            controller.turn_on_illumination(wavelength)
            time.sleep(0.1)

            # Measure power
            power = measure_power(pm, num_measurements, delay) * 1000  # Convert to mW
            print(f"Measured power: {power:.3f} mW")

            requested_powers.append(power_pct)
            measured_powers.append(power)

            # Turn off illumination
            controller.turn_off_illumination(wavelength)
            time.sleep(0.01)

    finally:
        # Ensure illumination is turned off
        controller.turn_off_illumination(wavelength)

    # Create DataFrame
    return pd.DataFrame({"Requested Power (%)": requested_powers, "Measured Power (mW)": measured_powers})


def plot_calibration_test(data: pd.DataFrame, wavelength: int, output_dir: Path):
    """Generate and save calibration test plot."""
    plt.figure(figsize=(12, 8))

    # Plot measured vs requested power
    plt.plot(data["Requested Power (%)"], data["Measured Power (mW)"], "bo-", label="Measured Power", alpha=0.7)

    # Calculate ideal linear relationship
    max_power = data["Measured Power (mW)"].max()
    ideal_power = max_power * data["Requested Power (%)"] / 100

    plt.plot(data["Requested Power (%)"], ideal_power, "r--", label="Ideal Linear Power", alpha=0.7)

    # Calculate error metrics
    mse = np.mean((data["Measured Power (mW)"] - ideal_power) ** 2)
    max_error = np.max(np.abs(data["Measured Power (mW)"] - ideal_power))

    plt.title(f"Calibration Test - {wavelength}nm\n" f"MSE: {mse:.2f} mW², Max Error: {max_error:.2f} mW")
    plt.xlabel("Requested Power (%)")
    plt.ylabel("Measured Power (mW)")
    plt.grid(True)
    plt.legend()

    # Save plot
    plot_file = output_dir / f"{wavelength}_calibration_test.png"
    plt.savefig(plot_file, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved test plot to {plot_file}")


def main():
    # Parameters
    WAVELENGTHS = [405, 470, 555, 638, 730]  # Wavelengths to test
    POWER_PERCENTAGES = np.arange(0, 101, 5)  # Test every 5% from 0 to 100%
    NUM_MEASUREMENTS = 5  # Number of measurements to average at each power level
    DELAY = 0.1  # Delay between measurements
    OUTPUT_DIR = "machine_configs/calibration_tests"  # Output directory for test results

    # Create output directory
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize power meter
    print("Initializing power meter...")
    pm = PM16()
    pm.set_averaging(5)
    pm.set_auto_range(True)

    # Initialize illumination controller
    mcu = microcontroller.Microcontroller(
        serial_device=microcontroller.get_microcontroller_serial_device(version=CONTROLLER_VERSION, sn=CONTROLLER_SN)
    )
    controller = IlluminationController(
        mcu,
        intensity_control_mode=IntensityControlMode.SquidControllerDAC,
        shutter_control_mode=ShutterControlMode.TTL,
        disable_intensity_calibration=False,  # Enable calibration
    )

    # Test each wavelength
    for wavelength in WAVELENGTHS:
        print(f"\nTesting {wavelength}nm...")

        # Run calibration test
        test_data = test_calibration(pm, controller, wavelength, POWER_PERCENTAGES, NUM_MEASUREMENTS, DELAY)

        # Save test data
        output_file = output_dir / f"{wavelength}_test_data.csv"
        test_data.to_csv(output_file, index=False)
        print(f"Saved test data to {output_file}")

        # Generate and save test plot
        # plot_calibration_test(test_data, wavelength, output_dir)

    print("\nCalibration testing complete!")


if __name__ == "__main__":
    main()
