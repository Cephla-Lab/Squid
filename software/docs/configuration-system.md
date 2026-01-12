# Configuration System

This document describes Squid's YAML-based configuration system for managing microscope settings. The system separates hardware-level definitions (machine configs) from user preferences (user profiles), enabling type-safe configuration with Pydantic validation.

## Architecture Overview

The configuration system uses a hierarchical structure that separates concerns:

```
software/
├── machine_configs/                    # Hardware-specific (per machine)
│   ├── illumination_channel_config.yaml   # Illumination channels (required)
│   ├── confocal_config.yaml              # Optional: confocal settings
│   ├── camera_mappings.yaml              # Optional: multi-camera bindings
│   └── intensity_calibrations/           # Optional: power calibration CSVs
│
└── user_profiles/                      # User preferences (per profile)
    └── {profile_name}/
        ├── channel_configs/
        │   ├── general.yaml              # Shared channel settings
        │   └── {objective}.yaml          # Per-objective overrides
        └── laser_af_configs/
            └── {objective}.yaml          # Laser AF per objective
```

### Design Principles

1. **Separation of Concerns**
   - **Machine configs**: Define what hardware exists (rarely changes)
   - **User profiles**: Store user preferences (changes frequently)
   - **Objective configs**: Fine-tune settings per objective (merged with general)

2. **Hierarchical Merge**
   - `general.yaml` defines channel identity and shared settings
   - `{objective}.yaml` provides per-objective overrides
   - Final configuration = merge(general, objective)

3. **Type Safety**
   - All configs validated with Pydantic models
   - Invalid configurations fail fast with clear error messages

4. **Schema Versioning**
   - Every YAML file includes a `version` field (currently `1`)
   - Enables future schema migrations without breaking existing configs

---

## Machine Configs

Machine configs define the physical hardware setup. These files live in `machine_configs/` and are typically configured once per microscope.

### illumination_channel_config.yaml

Defines all available illumination channels on the microscope.

```yaml
version: 1

# Controller port to source code mapping
# D1-D8: Laser channels, USB1-USB8: LED matrix patterns
controller_port_mapping:
  D1: 11   # 405nm laser
  D2: 12   # 488nm laser
  D3: 13   # 638nm laser
  D4: 14   # 561nm laser
  D5: 15   # 730nm laser
  USB1: 0  # LED full
  USB2: 1  # LED left_half
  USB3: 2  # LED right_half
  USB4: 3  # LED dark_field
  USB5: 4  # LED low_na

channels:
  # Brightfield LED
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
    intensity_calibration_file: null

  # Fluorescence channels
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
    intensity_calibration_file: 405.csv

  - name: Fluorescence 488 nm Ex
    type: epi_illumination
    controller_port: D2
    wavelength_nm: 488
    intensity_calibration_file: 488.csv

  # ... additional channels
```

**Fields:**

| Field | Description |
|-------|-------------|
| `version` | Schema version (currently `1`) |
| `controller_port_mapping` | Maps port names to internal source codes |
| `channels[].name` | Unique identifier for the channel |
| `channels[].type` | `epi_illumination` (lasers) or `transillumination` (LED) |
| `channels[].controller_port` | Port name (D1-D8 for lasers, USB1-USB8 for LED) |
| `channels[].wavelength_nm` | Wavelength in nm (null for LED) |
| `channels[].intensity_calibration_file` | CSV file in `intensity_calibrations/` |

### confocal_config.yaml (Optional)

Only create this file if the system has a confocal unit. Its presence indicates that confocal settings should be included in acquisition configs.

```yaml
version: 1

# Filter wheel slot mappings (wheel_id -> slot -> filter_name)
filter_wheel_mappings:
  1:  # Filter wheel ID
    1: "Empty"
    2: "BP 525/50"
    3: "BP 600/50"
    4: "BP 700/75"
    5: "LP 650"

# Properties available for configuration
public_properties:
  - emission_filter_wheel_position

objective_specific_properties:
  - illumination_iris
  - emission_iris
```

**Fields:**

| Field | Description |
|-------|-------------|
| `filter_wheel_mappings` | Nested dict: wheel ID → slot number → filter name |
| `public_properties` | Properties available in `general.yaml` |
| `objective_specific_properties` | Properties only in objective-specific files |

> **Note**: The `confocal_config.yaml.example` file in `machine_configs/` uses a simplified format for reference. When creating your actual config, use the structure shown above which matches the Pydantic model.

### camera_mappings.yaml (Optional)

Maps camera selections to hardware bindings (dichroic positions, filter wheels). This file is optional and typically only needed for advanced multi-camera setups.

```yaml
version: 1

hardware_connection_info:
  "1":
    filter_wheel_id: 1
    confocal_settings: null

property_bindings:
  camera_1:
    dichroic_position: 1
```

---

## User Profiles

User profiles store acquisition settings that vary by user or experiment. Each profile is a directory under `user_profiles/`.

### Profile Management

**Creating a Profile:**
- Profiles are directories under `user_profiles/`
- Contains `channel_configs/` and `laser_af_configs/` subdirectories
- Default configs are auto-generated if profile has no configs

**Switching Profiles:**
- Profile switch clears cached configs
- New profile's configs are loaded on demand

**Save As (Copy Profile):**
- Copies all YAML files from source to destination profile
- Useful for creating variants of existing configurations

### channel_configs/general.yaml

Defines channel identity and settings shared across all objectives.

```yaml
version: 1
channels:
  - name: Fluorescence 488 nm Ex
    illumination_settings:
      illumination_channels:
        - Fluorescence 488 nm Ex    # References illumination_channel_config.yaml
      intensity:
        Fluorescence 488 nm Ex: 20.0
      z_offset_um: 0.0
    camera_settings:
      '1':                          # Camera ID
        display_color: '#1FFF00'    # Green for 488nm
        exposure_time_ms: 20.0
        gain_mode: 10.0
        pixel_format: null
    emission_filter_wheel_position:
      '1': 1                        # Wheel ID → position
    confocal_settings: null
    confocal_override: null

  - name: BF LED matrix full
    illumination_settings:
      illumination_channels:
        - BF LED matrix full
      intensity:
        BF LED matrix full: 5.0
      z_offset_um: 0.0
    camera_settings:
      '1':
        display_color: '#FFFFFF'    # White for brightfield
        exposure_time_ms: 20.0
        gain_mode: 10.0
        pixel_format: null
    emission_filter_wheel_position:
      '1': 1
    confocal_settings: null
    confocal_override: null
```

**Fields owned by general.yaml:**

| Field | Description |
|-------|-------------|
| `illumination_channels` | Which illumination channels to use (references machine config) |
| `display_color` | Hex color for UI visualization |
| `z_offset_um` | Z offset applied when switching to this channel |
| `emission_filter_wheel_position` | Filter wheel positions |

### channel_configs/{objective}.yaml

Per-objective overrides. These settings are merged with `general.yaml`.

```yaml
version: 1
channels:
  - name: Fluorescence 488 nm Ex
    illumination_settings:
      illumination_channels: null   # NOT in objective files
      intensity:
        Fluorescence 488 nm Ex: 35.0   # Higher intensity for 20x
      z_offset_um: 0.0              # Placeholder (comes from general)
    camera_settings:
      '1':
        display_color: '#1FFF00'
        exposure_time_ms: 50.0      # Longer exposure for 20x
        gain_mode: 5.0              # Lower gain for 20x
        pixel_format: null
    emission_filter_wheel_position: null  # NOT in objective files
    confocal_settings: null
    confocal_override: null         # Only if confocal present
```

**Fields owned by objective files:**

| Field | Description |
|-------|-------------|
| `intensity` | Illumination intensity (0-100%) |
| `exposure_time_ms` | Camera exposure time |
| `gain_mode` | Camera analog gain |
| `pixel_format` | Camera pixel format |
| `confocal_override` | Settings for confocal mode |

### Merge Logic

When loading channels for an objective, the system merges `general.yaml` with `{objective}.yaml`:

| Field | Source | Rationale |
|-------|--------|-----------|
| `name` | general | Channel identity |
| `illumination_channels` | general | Hardware reference doesn't change |
| `display_color` | general | Consistent UI colors |
| `z_offset_um` | general | Usually constant per channel |
| `emission_filter_wheel_position` | general | Filter setup |
| `intensity` | objective | Varies by magnification |
| `exposure_time_ms` | objective | Varies by magnification |
| `gain_mode` | objective | Varies by magnification |
| `pixel_format` | objective | May vary by objective |
| `confocal_override` | objective | Objective-specific confocal tuning |

**Merge process:**
1. Start with channel from `general.yaml`
2. Find matching channel in `{objective}.yaml` by name
3. Replace objective-owned fields with objective values
4. If `confocal_mode` is active, apply `confocal_override`

### Confocal Override

When the system has a confocal unit and confocal mode is enabled, the `confocal_override` section replaces base settings:

```yaml
- name: Fluorescence 488 nm Ex
  # ... base settings ...
  confocal_override:
    illumination_settings:
      illumination_channels: null
      intensity:
        Fluorescence 488 nm Ex: 50.0   # Higher intensity for confocal
      z_offset_um: 0.0
    camera_settings:
      '1':
        display_color: '#1FFF00'
        exposure_time_ms: 100.0        # Longer exposure for confocal
        gain_mode: 2.0
        pixel_format: null
    confocal_settings:
      filter_wheel_id: 1
      emission_filter_wheel_position: 2  # Different filter for confocal
      illumination_iris: 50.0
      emission_iris: 50.0
```

### laser_af_configs/{objective}.yaml

Laser autofocus configuration per objective. Contains calibration data and detection parameters.

```yaml
version: 1

# Crop region
x_offset: 0
y_offset: 0
width: 1536
height: 256

# Calibration
pixel_to_um: 1.0
x_reference: null
has_reference: false
calibration_timestamp: ""
pixel_to_um_calibration_distance: 6.0

# Detection parameters
laser_af_range: 100.0
laser_af_averaging_n: 3
spot_detection_mode: dual_right
displacement_success_window_um: 1.0

# Spot detection
spot_crop_size: 100
correlation_threshold: 0.9
y_window: 96
x_window: 20
min_peak_width: 10.0
min_peak_distance: 10.0
min_peak_prominence: 0.25
spot_spacing: 100.0
filter_sigma: null

# Camera settings
focus_camera_exposure_time_ms: 0.2
focus_camera_analog_gain: 0.0

# Reference image (base64 encoded)
reference_image: null
reference_image_shape: null
reference_image_dtype: null
```

---

## Default Config Generation

When a profile has no existing configs, the system auto-generates defaults:

1. **Trigger**: Profile loaded without `general.yaml`
2. **Source**: Uses `illumination_channel_config.yaml` as template
3. **Process**:
   - Creates one acquisition channel per illumination channel
   - Sets display colors based on wavelength (fluorescence) or white (LED)
   - Uses default exposure (20ms), gain (10), intensity (20% fluorescence, 5% LED)
   - Creates objective files for standard objectives (2x, 4x, 10x, 20x, 40x, 50x, 60x)

**Note**: Default generation is skipped if legacy XML configs exist (migration should run first).

---

## Acquisition Output

When running an acquisition, the effective configuration is saved to the experiment directory:

```
experiment_output/
└── acquisition_channels.yaml
```

This file captures the exact settings used, including:
- Objective name
- Confocal mode state
- All channel configurations (merged and with overrides applied)

---

## Best Practices

### For Users

1. **Use profiles for different experiments**
   - Create a profile for each experiment type
   - Use "Save As" to create variants

2. **Tune settings per objective**
   - Higher magnification typically needs higher intensity/exposure
   - Lower magnification can use lower gain (less noise)

3. **Set z_offset for parfocal correction**
   - If channels aren't parfocal, set z_offset in general.yaml

### For System Administrators

1. **Machine configs are global**
   - Changes affect all users
   - Test changes before deploying

2. **Keep intensity calibrations updated**
   - Re-run calibration if laser power changes
   - Store calibration CSVs in `machine_configs/intensity_calibrations/`

3. **Confocal config presence matters**
   - Create `confocal_config.yaml` only if confocal exists
   - File presence enables confocal settings in acquisition configs

---

## Troubleshooting

### "No channels available"

- Verify `general.yaml` exists in profile's `channel_configs/`
- Check `illumination_channel_config.yaml` has channels defined
- Ensure illumination channel names match between files

### "Illumination channel not found"

- The `illumination_channels` field in `general.yaml` must reference channels defined in `illumination_channel_config.yaml`
- Check for typos in channel names

### "Profile not found"

- Profile directory must exist under `user_profiles/`
- Profile must have `channel_configs/` subdirectory

### Settings not persisting

- Changes to UI update `{objective}.yaml`, not `general.yaml`
- Verify the correct profile is active
- Check file permissions

---

## See Also

- [Configuration API Reference](configuration-api.md) - Developer documentation
- [Configuration Migration](configuration-migration.md) - Upgrading from legacy format
- [Machine Configs README](../machine_configs/README.md) - Hardware setup guide
