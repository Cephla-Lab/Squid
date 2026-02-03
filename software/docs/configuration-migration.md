# Configuration Migration Guide

This guide explains how to migrate from the legacy JSON/XML configuration format to the new YAML-based system.

## Overview

### What Changed

| Old Format | New Format |
|------------|------------|
| `configurations/channel_definitions.json` | `machine_configs/illumination_channel_config.yaml` |
| `acquisition_configurations/{profile}/{obj}/channel_settings.json` | `user_profiles/{profile}/channel_configs/general.yaml` + `{obj}.yaml` |
| `acquisition_configurations/{profile}/{obj}/laser_af_settings.json` | `user_profiles/{profile}/laser_af_configs/{obj}.yaml` |

### Why the Change

1. **Type Safety**: Pydantic models catch configuration errors at load time
2. **Separation of Concerns**: Machine configs (hardware) vs user profiles (preferences)
3. **Hierarchical Settings**: Per-objective overrides without duplicating shared settings
4. **Easier Maintenance**: YAML is more readable than XML

---

## When Migration is Needed

Migration is needed if you have:

1. `acquisition_configurations/` directory with profile subdirectories
2. JSON files named `channel_settings.json`
3. JSON files named `laser_af_settings.json`

**Check for legacy configs:**
```bash
ls -la software/acquisition_configurations/
```

If this directory exists with profiles, you need to migrate.

---

## Automatic Migration

Migration runs automatically on first startup if no YAML profile exists.

### How It Works

1. On startup, the system checks if `user_profiles/{profile}/` exists
2. If not, the migration script is invoked automatically
3. Configs are converted to new YAML format in `user_profiles/`
4. Old files are preserved (not deleted)

### Triggering Auto-Migration

Simply start the application:

```bash
cd software/src
python main_hcs.py --simulation
```

Check the logs for:
```
INFO: Migrating profile 'default_profile': 7 migrated, 0 skipped
```

---

## Manual Migration

For more control, use the migration script directly.

### Basic Usage

```bash
cd software
python tools/migrate_to_yaml_configs.py
```

### Options

| Option | Description |
|--------|-------------|
| `--base-path PATH` | Path to the `software/` directory (default: auto-detected) |
| `--verbose` / `-v` | Enable verbose logging |

### Example

```bash
python tools/migrate_to_yaml_configs.py --verbose
```

---

## Migration Details

### What Gets Migrated

**1. Illumination Channel Config**

`configurations/channel_definitions.json` is converted to `machine_configs/illumination_channel_config.yaml`:

- Channel names preserved
- Controller ports mapped from numeric `illumination_source` codes
- Wavelength extracted from channel names
- Type inferred (`epi_illumination` for lasers, `transillumination` for LED)

**2. Per-Profile Channel Settings**

For each profile in `acquisition_configurations/{profile}/`:

- All objectives are scanned to build a union of channel names
- `general.yaml` is created with shared settings (display color, illumination channel, filter position)
- `{objective}.yaml` files are created with per-objective overrides (exposure, gain, intensity)

**3. Laser AF Settings**

`laser_af_settings.json` files are converted to `laser_af_configs/{objective}.yaml`.

### Field Mapping

| Old Field (JSON) | New Field (YAML) |
|-------------------|-------------------|
| `exposure_time` | `camera_settings.exposure_time_ms` |
| `analog_gain` | `camera_settings.gain_mode` |
| `illumination_intensity` | `illumination_settings.intensity` |
| `z_offset` | `z_offset_um` |
| `display_color` (int) | `display_color` (hex string) |
| `emission_filter_position` | `filter_position` |

### Color Conversion

JSON stores colors as integers (RGB packed). Migration converts to hex:

```
16711680 (RGB int) -> #FF0000 (hex red)
65280 (RGB int)    -> #00FF00 (hex green)
```

---

## Directory Structure After Migration

```
software/
├── acquisition_configurations/          # Preserved (not deleted)
│   └── default_profile/
│       └── 20x/
│           ├── channel_settings.json
│           └── laser_af_settings.json
│
├── machine_configs/
│   └── illumination_channel_config.yaml
│
└── user_profiles/
    └── default_profile/
        ├── channel_configs/
        │   ├── general.yaml
        │   ├── 10x.yaml
        │   ├── 20x.yaml
        │   └── ...
        └── laser_af_configs/
            └── 20x.yaml
```

---

## Troubleshooting

### "No channels available" after migration

- Check `user_profiles/{profile}/channel_configs/general.yaml` exists and has channels
- Verify `illumination_channel` names match `illumination_channel_config.yaml`
- Check that the correct profile is set (e.g., `default_profile`, not `default`)

### Migration runs but creates empty files

- Verify `acquisition_configurations/{profile}/{objective}/channel_settings.json` files exist and contain valid JSON
- Run with `--verbose` to see detailed migration output

### Settings look different after migration

The merge logic means settings come from different files:

| Setting | Source |
|---------|--------|
| Display color | `general.yaml` |
| Z offset | `general.yaml` |
| Illumination channel | `general.yaml` |
| Filter position | `general.yaml` |
| Intensity | `{objective}.yaml` |
| Exposure time | `{objective}.yaml` |
| Gain | `{objective}.yaml` |

If a setting seems wrong, check both files.

### Re-Running Migration

The migration is idempotent -- it skips files that already exist. To force re-migration:

1. Delete the target directory:
   ```bash
   rm -rf software/user_profiles/default_profile/
   ```

2. Run migration again:
   ```bash
   python tools/migrate_to_yaml_configs.py
   ```

---

## Verifying Migration

### Check Directory Structure

```bash
ls -la software/user_profiles/
ls -la software/user_profiles/default_profile/channel_configs/
```

Expected:
```
general.yaml
10x.yaml
20x.yaml
40x.yaml
...
```

### Check YAML Content

```bash
cat software/user_profiles/default_profile/channel_configs/general.yaml
```

Verify:
- `version: 1` (or `1.0`) is present
- Channel names match your expectations
- `illumination_channel` references valid illumination channels

### Test in Application

1. Start the application:
   ```bash
   cd software/src
   python main_hcs.py --simulation
   ```

2. Check that:
   - Profile loads without errors
   - Channels appear in the live view
   - Switching objectives loads correct settings
   - Settings persist across restarts

---

## Rolling Back

If migration produced incorrect results:

1. **Delete migrated files:**
   ```bash
   rm -rf software/user_profiles/
   ```

2. **Original files are preserved** in `acquisition_configurations/` -- no data is lost.

3. **Fix issues and re-migrate.**

---

## Advanced: Manual YAML Creation

If automatic migration doesn't work for your setup, you can create YAML configs manually:

### 1. Create Directory Structure

```bash
mkdir -p software/user_profiles/myprofile/channel_configs
mkdir -p software/user_profiles/myprofile/laser_af_configs
```

### 2. Create general.yaml

Use the template from [Configuration System](configuration-system.md#channel_configsgeneralyaml).

### 3. Create Objective Files

Create `{objective}.yaml` with only the settings that differ per objective (exposure, gain, intensity).

### 4. Validate

Start the application and verify channels load correctly.

---

## See Also

- [Configuration System](configuration-system.md) - Full configuration reference
- [Configuration API](configuration-api.md) - Developer documentation
- Migration script: `tools/migrate_to_yaml_configs.py`
