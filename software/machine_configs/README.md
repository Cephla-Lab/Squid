# Machine Configurations

This directory contains hardware-specific configuration files for the microscope.
These files define the physical hardware setup and should be configured once per machine.

## Files

### `illumination_channel_config.yaml`
Defines all available illumination channels on this machine:
- LED matrix patterns (transillumination)
- Fluorescence laser lines (epi-illumination)
- Controller port mappings (D1-D8 for lasers, USB for LED matrix)
- Intensity calibration file references

### `confocal_config.yaml` (Optional)
Only create this file if the system has a confocal unit. Its presence indicates
that confocal settings should be included in acquisition configs.

Defines:
- Filter wheel slot to filter name mappings
- Properties available for configuration (public vs objective-specific)

### `intensity_calibrations/` (Optional, user-generated)
Contains CSV files mapping DAC percentage to optical power (mW) for each laser line.
Files are named by wavelength (e.g., `405.csv`, `488.csv`).

To generate calibration files, run: `tools/generate_intensity_calibrations.py`

### `calibration_tests/` (Optional, user-generated)
Contains CSV files with calibration test results (measured power at various set points).
Used to verify calibration accuracy.

To generate test files, run: `tools/evaluate_intensity_calibration.py`
