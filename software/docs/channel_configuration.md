# Channel Configuration System

This document explains how the channel configuration system works in Squid.

## Overview

The channel configuration system uses a **two-tier architecture**:

1. **Global Channel Definitions** - Define what channels exist (names, types, hardware mappings)
2. **Per-Objective Settings** - Store objective-specific settings (exposure, gain, intensity)

This eliminates duplication - you define each channel once, and only the settings that vary by objective are stored separately.

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
            ├── channel_settings.json       # Per-objective settings (not tracked)
            └── channel_configurations.xml  # Legacy format (auto-generated)
```

**Note:** The `channel_configurations.xml` files are automatically generated for backward compatibility with acquisition scripts. You don't need to edit them directly.

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

**Structure:**
```json
{
  "Fluorescence 488 nm Ex": {
    "exposure_time": 100.0,
    "analog_gain": 0.0,
    "illumination_intensity": 50.0,
    "z_offset": 0.0
  },
  ...
}
```

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

```
┌─────────────────────────────────────────────────────────────┐
│                    Channel Definition                        │
│  name: "Fluorescence 488 nm Ex"                             │
│  type: fluorescence                                          │
│  numeric_channel: 2  ─────────┐                             │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                 Numeric Channel Mapping                      │
│  "2": { illumination_source: 12, ex_wavelength: 488 }       │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                      Hardware                                │
│  Illumination source 12 = 488nm laser                       │
└─────────────────────────────────────────────────────────────┘
```

## Updating Defaults

When you `git pull`, the `channel_definitions.default.json` file may be updated with new channels or mappings. Your personal `channel_definitions.json` is **not affected**.

To incorporate new defaults:
1. Back up your `channel_definitions.json`
2. Delete it
3. Restart the app (copies fresh defaults)
4. Re-apply your customizations

Or manually merge changes from the default file.

## Troubleshooting

### Channel not appearing in dropdown
- Check if the channel is enabled in Settings → Channel Configuration

### Wrong illumination source
- Check the numeric channel mapping in Settings → Advanced → Channel Hardware Mapping

### Settings not saving
- Ensure you click "Save" in the dialog
- Check file permissions in the configurations folder

### Reset to defaults
1. Close the application
2. Delete `software/configurations/channel_definitions.json`
3. Restart - defaults will be restored
