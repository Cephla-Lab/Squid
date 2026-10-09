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
Illumination power calibrations, one per DAC-driven channel: `<λ>nm_<port>.csv` (a `# key: value` header, then the
sweep) and a `.png` of the curve and its verification. Make them with **Utils > Illumination Power Calibration...**
(a Thorlabs power meter at the sample plane), or headless with `tools/generate_intensity_calibrations.py`.
Power meter setup (Ubuntu, macOS, Windows): `docs/illumination-power-calibration.md`.
The illumination config's `intensity_calibration_file` names the file a channel uses; `<λ>.csv` files from before
2026-10 still apply (healthy ones unchanged, broken ones repaired in memory). Replaced files move to `backup/`.

### `fluidics_config.yaml` (Optional)
Only for instruments with the fluidics system (`RUN_FLUIDICS = True`). This is the Squid-Fluidics
library's own `FluidicsConfig` file — the standalone fluidics GUI opens the same file, so copy the
instrument's existing config from its fluidics installation here (the file is gitignored). Squid loads
it when the fluidics system is initialized from the Fluidics tab's Initialize button
(`FluidicsService.initialize()`, default path from `FLUIDICS_CONFIG_PATH`; see
`docs/fluidics-protocol.md`).
