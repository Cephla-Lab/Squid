# Configuration System

This document describes Squid's YAML-based configuration system for managing microscope settings. The system separates hardware-level definitions (machine configs) from user preferences (user profiles), enabling type-safe configuration with Pydantic validation.

## Architecture Overview

The configuration system uses a hierarchical structure that separates concerns:

```
software/
├── machine_configs/                    # Hardware-specific (per machine)
│   ├── illumination_channel_config.yaml   # Illumination channels (required)
│   ├── cameras.yaml                      # Optional: camera registry
│   ├── filter_wheels.yaml                # Optional: standalone filter wheels
│   ├── hardware_bindings.yaml            # Optional: camera→wheel mappings
│   ├── confocal_config.yaml              # Optional: confocal settings + wheels
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
   - Every YAML file includes a `version` field (currently `1.0`)
   - Enables future schema migrations without breaking existing configs

---

## Machine Configs

Machine configs define the physical hardware setup. These files live in `machine_configs/` and are typically configured once per microscope.

### illumination_channel_config.yaml

Defines all available illumination channels on the microscope.

```yaml
version: 1.0

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
    # Optional: excitation filter (rare, most systems don't have this)
    excitation_filter_wheel: "Excitation Filter Wheel"
    excitation_filter_position: 2

  # ... additional channels
```

**Fields:**

| Field | Description |
|-------|-------------|
| `version` | Schema version (currently `1.0`) |
| `controller_port_mapping` | Maps port names to internal source codes |
| `channels[].name` | Unique identifier for the channel |
| `channels[].type` | `epi_illumination` (lasers) or `transillumination` (LED) |
| `channels[].controller_port` | Port name (D1-D8 for lasers, USB1-USB8 for LED) |
| `channels[].wavelength_nm` | Wavelength in nm (null for LED) |
| `channels[].intensity_calibration_file` | CSV file in `intensity_calibrations/` |
| `channels[].excitation_filter_wheel` | Optional: name of excitation filter wheel |
| `channels[].excitation_filter_position` | Optional: position in excitation filter wheel |

### cameras.yaml (Optional)

Maps camera IDs to hardware serial numbers. **Optional for single-camera systems.**

```yaml
version: 1.0

cameras:
  # Primary imaging camera
  - id: 1                          # Camera ID (used in channel configs and hardware_bindings)
    name: "Main Camera"            # User-friendly name for UI
    serial_number: "ABC12345"      # Camera serial number (from manufacturer)
    model: "Hamamatsu C15440"      # Optional: displayed in UI for reference

  # Secondary camera for simultaneous imaging
  - id: 2
    name: "Side Camera"
    serial_number: "DEF67890"
    model: "Basler acA2040"
```

**Fields:**

| Field | Description |
|-------|-------------|
| `version` | Schema version (`1.0`) |
| `cameras[].id` | Camera ID (must be unique, used in channel configs) |
| `cameras[].name` | User-friendly name for UI (must be unique) |
| `cameras[].serial_number` | Hardware serial number (must be unique) |
| `cameras[].model` | Optional: camera model for reference |

**Usage:**
- If `cameras.yaml` doesn't exist, the system assumes single-camera mode
- Single camera: `id` and `name` are optional (defaults applied)
- Multi-camera: `id` and `name` are required for all cameras
- Channel configs use the `id` field to reference cameras (e.g., `camera: 1`)

### filter_wheels.yaml (Optional)

Defines all filter wheels with their positions and installed filters. Channels reference filter wheels by name.

```yaml
version: 1.0

filter_wheels:
  # Emission filter wheel
  - name: "Emission Filter Wheel"
    id: 1                          # Hardware ID for controller
    type: emission                 # Filters light after sample
    positions:
      1: "Empty"
      2: "BP 525/50"               # GFP emission
      3: "BP 600/50"               # mCherry emission
      4: "BP 700/75"               # Far red emission
      5: "LP 650"                  # Long pass

  # Excitation filter wheel (optional)
  - name: "Excitation Filter Wheel"
    id: 2
    type: excitation              # Filters light before sample
    positions:
      1: "Empty"
      2: "BP 470/40"               # GFP excitation
      3: "BP 560/40"               # mCherry excitation
```

**Fields:**

| Field | Description |
|-------|-------------|
| `version` | Schema version (`1.0`) |
| `filter_wheels[].name` | User-friendly name (must be unique) |
| `filter_wheels[].id` | Hardware ID for controller (must be unique) |
| `filter_wheels[].type` | Filter wheel type: `excitation` or `emission` (optional) |
| `filter_wheels[].positions` | Map of slot number → filter name |

**Usage:**
- If `filter_wheels.yaml` doesn't exist, filter wheel settings in channels are ignored
- Filter names appear in UI dropdowns for channel configuration
- Position numbers must be ≥ 1
- Wheels here are referenced with the `standalone` source prefix in `hardware_bindings.yaml` (e.g., `standalone.1`)

**Excitation vs Emission Filter Wheels:**
- **Emission filter wheels** (most common, 0-1 per system): Referenced by acquisition channels via `filter_wheel` and `filter_position` fields in user profile configs
- **Excitation filter wheels** (rare): Referenced by illumination channels via `excitation_filter_wheel` and `excitation_filter_position` fields in machine config

### confocal_config.yaml (Optional)

Only create this file if the system has a confocal unit. Its presence indicates that confocal settings should be included in acquisition configs. Filter wheels built into the confocal unit are defined here (not in `filter_wheels.yaml`).

> **Note**: Filter wheels in this file are referenced with the `confocal` source prefix in `hardware_bindings.yaml` (e.g., `confocal.1`), while wheels in `filter_wheels.yaml` use the `standalone` source prefix.

```yaml
version: 1

# Filter wheels built into the confocal unit
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
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
| `filter_wheels` | List of filter wheel definitions (same format as `filter_wheels.yaml`) |
| `public_properties` | Properties available in `general.yaml` |
| `objective_specific_properties` | Properties only in objective-specific files |

### hardware_bindings.yaml (Optional)

Maps cameras to their associated filter wheels using **source-qualified references**. This file is only needed for multi-camera systems where each camera uses a different emission filter wheel.

**Source-Qualified References:**

Filter wheels can come from two sources:
- **`standalone`**: Defined in `filter_wheels.yaml`
- **`confocal`**: Defined in `confocal_config.yaml`

References use the format `source.identifier` where identifier can be an ID or name:
- `confocal.1` - confocal wheel with ID 1
- `standalone.Emission Wheel` - standalone wheel named "Emission Wheel"

```yaml
version: 1.0

emission_filter_wheels:
  # Camera ID -> source-qualified wheel reference
  1: confocal.1                    # Camera 1 uses confocal wheel ID 1
  2: standalone.1                  # Camera 2 uses standalone wheel ID 1
  3: "standalone.Side Emission"    # Camera 3 uses standalone wheel by name
```

**Fields:**

| Field | Description |
|-------|-------------|
| `version` | Schema version (`1.0`) |
| `emission_filter_wheels` | Map of camera ID to source-qualified wheel reference |

**Implicit Binding (Single Camera + Single Wheel):**

If `hardware_bindings.yaml` doesn't exist and the system has exactly one camera and one emission filter wheel, the binding is implicit - no configuration needed.

**When to Create This File:**
- Multi-camera systems with separate emission wheels per camera
- Systems where camera 1 should use a confocal wheel and camera 2 a standalone wheel
- Any setup where automatic binding won't work correctly

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
version: 1.0
channels:
  - name: Fluorescence 488 nm Ex
    enabled: true
    display_color: '#1FFF00'        # Green for 488nm
    camera: null                    # null = single camera, or int ID for multi-camera
    filter_wheel: auto              # "auto" = use camera's hardware binding
    filter_position: 2              # Position in filter wheel (resolved via hardware_bindings)
    z_offset_um: 0.0                # Z offset applied when switching to this channel
    camera_settings:                # Required: camera exposure and gain
      exposure_time_ms: 20.0        # Default, overridden by objective
      gain_mode: 10.0               # Default, overridden by objective
    illumination_settings:
      illumination_channel: Fluorescence 488 nm Ex    # References illumination_channel_config.yaml
      intensity: 20.0               # Default, overridden by objective

  - name: BF LED matrix full
    enabled: true
    display_color: '#FFFFFF'        # White for brightfield
    camera: null
    filter_wheel: auto
    filter_position: 1
    z_offset_um: 0.0
    camera_settings:
      exposure_time_ms: 20.0
      gain_mode: 10.0
    illumination_settings:
      illumination_channel: BF LED matrix full
      intensity: 5.0                # Lower intensity for LED
```

**Camera field:**
- Single camera: `camera: null` (no `cameras.yaml` needed)
- Multi-camera: `camera: 1` or `camera: 2` (integer ID from `cameras.yaml`)

**Filter wheel resolution:**
- `filter_wheel: auto` uses the camera's bound wheel from `hardware_bindings.yaml`
- Override with specific wheel name if needed (e.g., `filter_wheel: "Emission Wheel"`)
- `filter_position` specifies which slot in the resolved wheel

**Fields owned by general.yaml:**

| Field | Description |
|-------|-------------|
| `name` | Channel name (unique identifier) |
| `enabled` | Whether channel is available for acquisition |
| `display_color` | Hex color for UI visualization |
| `camera` | Camera ID (null = single camera, int for multi-camera) |
| `filter_wheel` | "auto" (use hardware binding) or wheel name override |
| `filter_position` | Filter position in the wheel |
| `z_offset_um` | Z offset applied when switching to this channel |
| `illumination_channel` | Which illumination channel to use (references machine config) |
| `camera_settings` | Required: exposure_time_ms and gain_mode (defaults, overridden by objective) |
| `intensity` | Required: illumination intensity (default, overridden by objective) |

### channel_configs/{objective}.yaml

Per-objective overrides. These settings are merged with `general.yaml`.

```yaml
version: 1.0
channels:
  - name: Fluorescence 488 nm Ex
    illumination_settings:
      intensity: 35.0                  # Higher intensity for 20x
    camera_settings:
      exposure_time_ms: 50.0           # Longer exposure for 20x
      gain_mode: 5.0                   # Lower gain for 20x
    confocal_override:                 # Only if confocal present
      confocal_settings:
        illumination_iris: 50.0        # Iris aperture (0-100%)
        emission_iris: 75.0
```

**Fields owned by objective files:**

| Field | Description |
|-------|-------------|
| `intensity` | Illumination intensity (0-100%) |
| `exposure_time_ms` | Camera exposure time |
| `gain_mode` | Camera analog gain |
| `pixel_format` | Camera pixel format |
| `confocal_override` | Confocal mode settings (iris aperture) |

### Merge Logic

When loading channels for an objective, the system merges `general.yaml` with `{objective}.yaml`:

| Field | Source | Rationale |
|-------|--------|-----------|
| `name` | general | Channel identity |
| `illumination_channel` | general | Hardware reference doesn't change |
| `display_color` | general | Consistent UI colors |
| `camera` | general | Camera assignment (ID) |
| `z_offset_um` | general | Usually constant per channel |
| `filter_wheel, filter_position` | general | Filter setup (wheel resolved via hardware_bindings) |
| `intensity` | objective | Varies by magnification |
| `exposure_time_ms` | objective | Varies by magnification |
| `gain_mode` | objective | Varies by magnification |
| `pixel_format` | objective | May vary by objective |
| `confocal_override` | objective | Objective-specific iris settings |

**Merge process:**
1. Start with channel from `general.yaml`
2. Find matching channel in `{objective}.yaml` by name
3. Replace objective-owned fields with objective values
4. If `confocal_mode` is active, apply `confocal_override` (iris settings)

### Confocal Override

When the system has a confocal unit and confocal mode is enabled, the `confocal_override` section can override base settings:

```yaml
- name: Fluorescence 488 nm Ex
  # ... base settings ...
  confocal_override:
    illumination_settings:
      intensity: 50.0              # Higher intensity for confocal
    camera_settings:
      exposure_time_ms: 100.0      # Longer exposure for confocal
      gain_mode: 2.0
    confocal_settings:
      illumination_iris: 50.0      # Confocal iris aperture (0-100%)
      emission_iris: 75.0
```

Note: Filter wheel selection is resolved via `hardware_bindings.yaml` based on camera ID. The confocal's filter wheel is used when the camera is bound to a confocal wheel reference (e.g., `confocal.1`).

### laser_af_configs/{objective}.yaml

Laser autofocus configuration per objective. Contains calibration data and detection parameters.

```yaml
version: 1.0

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

- The `illumination_channel` field in `general.yaml` must reference a channel defined in `illumination_channel_config.yaml`
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
