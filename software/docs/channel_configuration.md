# Channel Configuration System

This document explains how the channel configuration system works in Squid.

## Overview

The channel configuration system uses a **two-tier architecture**:

1. **Global Channel Definitions** - Define what channels exist (names, types, hardware mappings)
2. **Per-Objective Settings** - Store objective-specific settings (exposure, gain, intensity)

This eliminates duplication - you define each channel once, and only the settings that vary by objective are stored separately.

### Confocal/Widefield Support

For microscopes with spinning disk confocal (XLight, Dragonfly), the system supports **separate settings for confocal and widefield modes**:

- **Widefield settings** are the base/default values
- **Confocal overrides** only store values that differ from widefield
- The system automatically detects which mode the hardware is in at startup

## File Locations

```
software/
├── configurations/
│   ├── channel_definitions.default.json   # Default channel definitions (tracked in git)
│   └── channel_definitions.json           # Your customized definitions (not tracked)
│
└── acquisition_configurations/
    └── <profile>/                          # e.g., "default_profile"
        └── <objective>/                    # e.g., "10x", "20x", "40x"
            └── channel_settings.json       # Per-objective settings (not tracked)
```

**Note:** Legacy `channel_configurations.xml` files are only generated in the **experiment folder** when an acquisition starts (via `write_configuration_selected()`). They are not kept in sync with JSON settings in the profile folders. The JSON format is the source of truth.

## Configuration Files

### 1. Channel Definitions (`channel_definitions.json`)

**Location:** `software/configurations/channel_definitions.json`

This file defines all available imaging channels. On first run, it's automatically copied from `channel_definitions.default.json`.

**Example:**
```json
{
  "max_fluorescence_channels": 5,
  "channels": [
    {
      "name": "Fluorescence 488 nm Ex",
      "type": "fluorescence",
      "enabled": true,
      "numeric_channel": 2,
      "illumination_source": null,
      "emission_filter_position": 1,
      "display_color": "#1FFF00",
      "ex_wavelength": null
    },
    {
      "name": "BF LED matrix full",
      "type": "led_matrix",
      "enabled": true,
      "numeric_channel": null,
      "illumination_source": 0,
      "emission_filter_position": 1,
      "display_color": "#FFFFFF",
      "ex_wavelength": null
    }
  ],
  "numeric_channel_mapping": {
    "1": { "illumination_source": 11, "ex_wavelength": 405 },
    "2": { "illumination_source": 12, "ex_wavelength": 488 }
  }
}
```

#### Fields:

| Field | Description |
|-------|-------------|
| `max_fluorescence_channels` | Maximum number of fluorescence channels (affects hardware mapping table) |
| `channels` | List of channel definitions |
| `numeric_channel_mapping` | Maps numeric channels (1-N) to illumination sources |

#### Channel Definition Fields:

| Field | Description |
|-------|-------------|
| `name` | Display name of the channel |
| `type` | Either `"fluorescence"` or `"led_matrix"` |
| `enabled` | Whether the channel appears in dropdowns (`true`/`false`) |
| `numeric_channel` | For fluorescence: which numeric channel (1-5) to use |
| `illumination_source` | For LED matrix: direct illumination source ID |
| `emission_filter_position` | Filter wheel position (1-8) |
| `display_color` | Hex color for display (e.g., `"#00FF00"`) |
| `ex_wavelength` | Optional: override excitation wavelength (normally from mapping) |

#### Numeric Channel Mapping:

Maps abstract numeric channels to actual hardware:

```json
"numeric_channel_mapping": {
  "1": { "illumination_source": 11, "ex_wavelength": 405 },
  "2": { "illumination_source": 12, "ex_wavelength": 488 },
  ...
}
```

This allows you to change hardware assignments without modifying every channel definition.

### 2. Per-Objective Settings (`channel_settings.json`)

**Location:** `software/acquisition_configurations/<profile>/<objective>/channel_settings.json`

Stores settings that vary by objective. Automatically created when you change settings.

**Basic Structure (Widefield Only):**
```json
{
  "Fluorescence 488 nm Ex": {
    "exposure_time": 25.0,
    "analog_gain": 0.0,
    "illumination_intensity": 20.0,
    "z_offset": 0.0
  }
}
```

**With Confocal Overrides:**
```json
{
  "Fluorescence 488 nm Ex": {
    "exposure_time": 25.0,
    "analog_gain": 0.0,
    "illumination_intensity": 20.0,
    "z_offset": 0.0,
    "confocal": {
      "exposure_time": 100.0,
      "illumination_intensity": 50.0
    }
  }
}
```

In this example:
- **Widefield mode:** exposure=25ms, intensity=20%
- **Confocal mode:** exposure=100ms, intensity=50%, other values inherited from widefield

The `confocal` block only needs to store values that differ from the base settings. Missing values are inherited from widefield.

## Channel Types

### Fluorescence Channels
- Use `numeric_channel` to reference the hardware mapping
- Excitation wavelength determined by the mapping
- Example: "Fluorescence 488 nm Ex" → numeric_channel 2 → illumination_source 12

### LED Matrix Channels
- Use `illumination_source` directly
- Fixed patterns (full, half, dark field, etc.)
- Names are read-only in the editor
- Cannot be removed from the configuration

## GUI Access

### Channel Configuration Editor
**Menu:** Settings → Channel Configuration

- Enable/disable channels (disabled = hidden from dropdowns)
- Edit channel names (fluorescence only)
- Change numeric channel assignments
- Modify filter positions
- Set display colors
- Reorder channels

### Advanced Hardware Mapping
**Menu:** Settings → Advanced → Channel Hardware Mapping

- Set maximum fluorescence channels
- Map numeric channels to illumination sources
- Set excitation wavelengths

## How It Works

### Hardware Mapping Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Channel Definition                       │
│  name: "Fluorescence 488 nm Ex"                             │
│  type: fluorescence                                         │
│  numeric_channel: 2  ─────────┐                             │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                 Numeric Channel Mapping                     │
│  "2": { illumination_source: 12, ex_wavelength: 488 }       │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                      Hardware                               │
│  Illumination source 12 = 488nm laser                       │
└─────────────────────────────────────────────────────────────┘
```

### Settings Resolution Flow (with Confocal Support)

```
┌─────────────────────────────────────────────────────────────┐
│                 Per-Objective Settings                      │
│  Base:     exposure=25ms,  intensity=20%                    │
│  Confocal: exposure=100ms, intensity=50%                    │
└─────────────────────────────────────────────────────────────┘
                                │
                      ┌─────────┴─────────┐
                      │  confocal_mode?   │
                      └─────────┬─────────┘
               ┌────────────────┼────────────────┐
               │                │                │
           [false]                           [true]
               │                                 │
               ▼                                 ▼
┌─────────────────────────────┐   ┌─────────────────────────────┐
│    Effective Settings       │   │    Effective Settings       │
│  exposure=25ms              │   │  exposure=100ms             │
│  intensity=20%              │   │  intensity=50%              │
└─────────────────────────────┘   └─────────────────────────────┘
```

## Confocal/Widefield Mode

### How Mode is Determined

The system queries the spinning disk hardware at startup:

- **XLight:** `get_disk_position()` returns 0 (widefield) or 1 (confocal)
- **Dragonfly:** `get_modality()` returns "BF" (widefield) or "CONFOCAL"

This works in both GUI and headless modes.

### GUI Mode

Use the spinning disk widget (Settings tab → Confocal) to toggle:
- Click "Switch to Confocal" / "Switch to Widefield"
- Channel settings automatically switch to the appropriate values
- Edits are saved to the correct settings (base or confocal override)

### Headless Mode

```python
from control.microscope import Microscope

# Initialize microscope (auto-syncs confocal mode from hardware)
microscope = Microscope(...)

# Check current mode
if microscope.is_confocal_mode():
    print("In confocal mode")

# Switch modes programmatically (moves hardware + updates settings)
microscope.set_confocal_mode(True)   # Switch to confocal
microscope.set_confocal_mode(False)  # Switch to widefield
```

### Acquisition Metadata

The imaging mode is saved to `acquisition parameters.json` in the experiment folder:

```json
{
  "dx(mm)": 0.5,
  "Nx": 1,
  ...
  "confocal_mode": true
}
```

This ensures reproducibility - you can verify which mode was used for each acquisition.

## Updating Defaults

When you `git pull`, the `channel_definitions.default.json` file may be updated with new channels or mappings. Your personal `channel_definitions.json` is **not affected**.

To incorporate new defaults:
1. Back up your `channel_definitions.json`
2. Delete it
3. Restart the app (copies fresh defaults)
4. Re-apply your customizations

Or manually merge changes from the default file.

## Validation

### Startup Validation

The system validates channel configurations when the application starts:

- **Numeric channel mapping validation:** All fluorescence channels must reference valid entries in `numeric_channel_mapping`. If a channel references a non-existent mapping (e.g., `numeric_channel: 3` but only mappings for 1 and 2 exist), the application will fail to start with a clear error message.

This "fail fast" approach catches configuration errors immediately rather than during an acquisition.

### Channel Name Constraints

When creating or renaming channels:

- **Maximum length:** 64 characters
- **Invalid characters:** `< > : " / \ | ? *` and null characters are not allowed (these cause issues on various file systems)

## Troubleshooting

### Configuration validation error at startup
- Check the error message for which channel has an invalid `numeric_channel` value
- Ensure the referenced numeric channel exists in `numeric_channel_mapping`
- Example: If channel has `"numeric_channel": 3`, verify `"3"` exists in the mapping

### Channel not appearing in dropdown
- Check if the channel is enabled in Settings → Channel Configuration

### Wrong illumination source
- Check the numeric channel mapping in Settings → Advanced → Channel Hardware Mapping

### Settings not saving
- Ensure you click "Save" in the dialog
- Check file permissions in the configurations folder

### Confocal settings not applied
- Verify the spinning disk is in confocal position (check the confocal widget)
- Check that `confocal` overrides exist in `channel_settings.json`
- In headless mode, ensure `microscope.set_confocal_mode(True)` was called

### Different settings in GUI vs headless
- The system auto-syncs from hardware at startup
- If you manually moved the disk, call `microscope.set_confocal_mode()` to sync

### Reset to defaults
1. Close the application
2. Delete `software/configurations/channel_definitions.json`
3. Restart - defaults will be restored

### Reset per-objective settings
1. Close the application
2. Delete `software/acquisition_configurations/<profile>/<objective>/channel_settings.json`
3. Restart - settings will be initialized from defaults

### Channel IDs and renaming
Channel IDs are generated from the channel name using a hash. **If you rename a channel, its ID will change.** This means:
- References to the old ID in saved acquisition configurations will no longer match
- The channel will appear as a "new" channel from the perspective of ID-based lookups

To avoid issues, prefer disabling unused channels rather than renaming them if you have existing acquisition configurations that reference them.
