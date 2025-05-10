import time
import pandas as pd
import numpy as np
from pathlib import Path
from typing import List
import matplotlib.pyplot as plt

from tools.PM16 import PM16
from control.lighting import IlluminationController, IntensityControlMode, ShutterControlMode
import control.microcontroller as microcontroller
from control._def import *


def plot_calibration(data: pd.DataFrame, wavelength: int, output_dir: Path):
    """Generate and save calibration plot."""
    plt.figure(figsize=(10, 6))
    plt.plot(data["DAC Percent"], data["Optical Power (mW)"], "bo-", label="Measured Data")
    plt.xlabel("DAC Percent")
    plt.ylabel("Optical Power (mW)")
    plt.title(f"Intensity Calibration - {wavelength}nm")
    plt.grid(True)
    plt.legend()

    # Save plot
    plot_file = output_dir / f"{wavelength}_calibration.png"
    plt.savefig(plot_file, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved calibration plot to {plot_file}")


def generate_calibration(
    pm: PM16, controller: IlluminationController, wavelength: int, dac_steps: List[float], early_stop_mW: float = 500
) -> pd.DataFrame:
    """Generate calibration data for a specific wavelength."""
    print(f"\nGenerating calibration for {wavelength}nm...")

    # Set power meter wavelength
    pm.set_wavelength(wavelength)

    # Initialize data storage
    dac_values = []
    power_values = []

    controller.set_intensity(wavelength, 0)
    time.sleep(0.1)

    try:
        # Step through DAC values
        for dac in dac_steps:
            # set intensity
            print(f"Setting DAC to {dac:.1f}%...")
            controller.set_intensity(wavelength, dac)
            time.sleep(0.01)
            # turn on illumination
            controller.turn_on_illumination(wavelength)
            time.sleep(0.1)  # Wait for power to stabilize
            # measure power
            power = pm.read() * 1000
            print(f"Measured power: {power:.3f} mW")
            dac_values.append(dac)
            power_values.append(power)
            # turn off illumination
            controller.turn_off_illumination(wavelength)
            time.sleep(0.01)

            if power > early_stop_mW:
                break

    finally:
        pass

    # Create DataFrame
    return pd.DataFrame({"DAC Percent": dac_values, "Optical Power (mW)": power_values})


def main():
    # Calibration parameters
    WAVELENGTHS = [405, 470, 638]  # Wavelengths to calibrate
    DAC_STEPS = np.arange(0, 100.1, 0.5)  # DAC values to measure (0, 0.1, 0.2, ..., 100)
    OUTPUT_DIR = "intensity_calibrations"  # Output directory for calibration files
    EARLY_STOP_mW = 500  # Early stop at 500 mW

    # Create output directory if it doesn't exist
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize power meter
    print("Initializing power meter...")
    pm = PM16()
    pm.set_averaging(5)  # Set averaging
    pm.set_auto_range(True)  # Enable auto-ranging

    # Initialize illumination controller
    mcu = microcontroller.Microcontroller(
        serial_device=microcontroller.get_microcontroller_serial_device(version=CONTROLLER_VERSION, sn=CONTROLLER_SN)
    )
    controller = IlluminationController(
        mcu,
        intensity_control_mode=IntensityControlMode.SquidControllerDAC,
        shutter_control_mode=ShutterControlMode.TTL,
        disable_intensity_calibration=True,
    )

    # Generate calibrations for each wavelength
    for wavelength in WAVELENGTHS:
        print(f"\nCalibrating {wavelength} nm...")

        # Generate calibration data
        calibration_data = generate_calibration(pm, controller, wavelength, DAC_STEPS, EARLY_STOP_mW)

        # Save to CSV
        output_file = output_dir / f"{wavelength}.csv"
        calibration_data.to_csv(output_file, index=False)
        print(f"Saved calibration to {output_file}")

        # Generate and save calibration plot
        plot_calibration(calibration_data, wavelength, output_dir)

    print("\nCalibration complete!")


if __name__ == "__main__":
    main()
