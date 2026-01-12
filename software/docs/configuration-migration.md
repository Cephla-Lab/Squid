# Configuration Migration Guide

This guide explains how to migrate from the legacy XML/JSON configuration format to the new YAML-based system. Migration is required when upgrading from older versions of Squid that used XML acquisition configs.

## Overview

### What Changed

| Old Format | New Format |
|------------|------------|
| `configurations/channel_definitions.default.json` | `machine_configs/illumination_channel_config.yaml` |
| `acquisition_configurations/{profile}/{obj}/channel_configurations.xml` | `user_profiles/{profile}/channel_configs/{obj}.yaml` |
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
2. XML files named `channel_configurations.xml` (or `widefield_configurations.xml`)
3. JSON files named `laser_af_settings.json`

**Check for legacy configs:**
```bash
ls -la software/acquisition_configurations/
```

If this directory exists with profiles, you need to migrate.

---

## Automatic Migration

The easiest approach is automatic migration, which runs on first startup after upgrade.

### How It Works

1. On startup, the system checks for `acquisition_configurations/` directory
2. If found, migration runs automatically
3. A backup is created: `acquisition_configurations_backup_YYYYMMDD_HHMMSS/`
4. Configs are converted to new YAML format in `user_profiles/`
5. A marker file `.migration_complete` prevents re-migration

### Triggering Auto-Migration

Simply start the application:

```bash
cd software
python3 main_hcs.py --simulation
```

Check the logs for:
```
INFO: Auto-migrating legacy acquisition configurations...
INFO: Created backup at: acquisition_configurations_backup_20260112_143022
INFO: Migrating profile: default
INFO: Auto-migration complete: 1/1 profiles migrated
```

---

## Manual Migration

For more control, use the migration script directly.

### Basic Usage

```bash
cd software
python3 tools/migrate_acquisition_configs.py
```

### Options

| Option | Description |
|--------|-------------|
| `--source PATH` | Source directory (default: `acquisition_configurations/`) |
| `--machine-config` | Also generate `machine_configs/` from `channel_definitions` |
| `--backup` | Create backup before migration (default: True) |
| `--no-backup` | Skip backup creation |
| `--dry-run` | Show what would be migrated without making changes |
| `--force` | Overwrite existing files |
| `--profile NAME` | Migrate only a specific profile |

### Examples

**Preview migration (dry run):**
```bash
python3 tools/migrate_acquisition_configs.py --dry-run
```

**Migrate specific profile:**
```bash
python3 tools/migrate_acquisition_configs.py --profile default
```

**Force overwrite existing:**
```bash
python3 tools/migrate_acquisition_configs.py --force
```

**Include machine config migration:**
```bash
python3 tools/migrate_acquisition_configs.py --machine-config
```

---

## Migration Details

### Channel Name Mapping

The migration preserves your original channel names while linking them to illumination channels:

1. **Acquisition channel name**: Preserved exactly as in XML
2. **Illumination channel reference**: Determined by wavelength or source code

**Example:**
- XML has channel named `"488 nm"` with `IlluminationSource="12"`
- Migration extracts wavelength `488` from name
- Finds illumination channel with `wavelength_nm: 488` in machine config
- Links to `"Fluorescence 488 nm Ex"` in `illumination_channels` field
- Preserves `"488 nm"` as the acquisition channel `name`

### Field Mapping

**From XML to YAML:**

| XML Attribute | YAML Field |
|---------------|------------|
| `Name` | `name` |
| `ExposureTime` | `camera_settings.exposure_time_ms` |
| `AnalogGain` | `camera_settings.gain_mode` |
| `IlluminationSource` | Used to find illumination channel |
| `IlluminationIntensity` | `illumination_settings.intensity` |
| `ZOffset` | `illumination_settings.z_offset_um` |
| `EmissionFilterPosition` | `emission_filter_wheel_position` |
| `<mode>` text (color int) | `camera_settings.display_color` (hex) |

### Color Conversion

XML stores colors as integers (RGB packed). Migration converts to hex:

```
16711680 (RGB int) → #FF0000 (hex red)
65280 (RGB int) → #00FF00 (hex green)
```

### Laser AF Migration

Laser AF settings are migrated from JSON to YAML with minimal changes:

- Field names preserved
- Reference images encoded as base64
- New fields added with defaults

---

## Directory Structure After Migration

```
software/
├── acquisition_configurations_backup_YYYYMMDD_HHMMSS/   # Backup
│   └── default/
│       └── 20x/
│           ├── channel_configurations.xml
│           └── laser_af_settings.json
│
├── machine_configs/
│   └── illumination_channel_config.yaml
│
└── user_profiles/
    └── default/
        ├── channel_configs/
        │   ├── general.yaml
        │   └── 20x.yaml
        └── laser_af_configs/
            └── 20x.yaml
```

---

## Troubleshooting

### "Legacy configs detected, migration required"

The system found old XML configs but migration hasn't run yet.

**Solution**: Start the application to trigger auto-migration, or run manually:
```bash
python3 tools/migrate_acquisition_configs.py
```

### "Could not find illumination channel for X"

The migration couldn't match an XML channel to an illumination channel.

**Causes:**
- Channel name doesn't contain recognizable wavelength pattern
- Source code doesn't match any channel in illumination config

**Solution:**
1. Check `machine_configs/illumination_channel_config.yaml` has the expected channels
2. Manually edit the generated YAML if needed
3. The original channel name is preserved; only the illumination reference may be incorrect

### "Target already exists"

Migration won't overwrite existing YAML files without `--force`.

**Solution:**
```bash
python3 tools/migrate_acquisition_configs.py --force
```

Or manually delete the target files first.

### Migration Ran But Settings Are Different

The merge logic means settings come from different files:

| Setting | Source |
|---------|--------|
| Display color | `general.yaml` |
| Z offset | `general.yaml` |
| Intensity | `{objective}.yaml` |
| Exposure | `{objective}.yaml` |
| Gain | `{objective}.yaml` |

If a setting seems wrong, check both files.

### Re-Running Migration

To re-run migration after it's already completed:

1. Delete the marker file:
   ```bash
   rm software/acquisition_configurations/.migration_complete
   ```

2. Run migration with `--force`:
   ```bash
   python3 tools/migrate_acquisition_configs.py --force
   ```

---

## Verifying Migration

### Check Directory Structure

```bash
ls -la software/user_profiles/
ls -la software/user_profiles/default/channel_configs/
```

Expected:
```
general.yaml
20x.yaml
40x.yaml
...
```

### Check YAML Content

```bash
cat software/user_profiles/default/channel_configs/general.yaml
```

Verify:
- `version: 1` is present
- Channel names match your expectations
- `illumination_channels` references valid illumination channels

### Test in Application

1. Start the application:
   ```bash
   python3 main_hcs.py --simulation
   ```

2. Check that:
   - Profile loads without errors
   - Channels appear in the live view
   - Switching objectives loads correct settings
   - Settings persist across restarts

---

## Rolling Back

If migration failed or produced incorrect results:

1. **Restore from backup:**
   ```bash
   rm -rf software/user_profiles/
   mv software/acquisition_configurations_backup_YYYYMMDD_HHMMSS \
      software/acquisition_configurations
   rm software/acquisition_configurations/.migration_complete
   ```

2. **Fix issues and re-migrate**

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

Copy `general.yaml` as starting point, then:
- Set `illumination_channels: null`
- Set `emission_filter_wheel_position: null`
- Adjust `intensity`, `exposure_time_ms`, `gain_mode`

### 4. Validate

Start the application and verify channels load correctly.

---

## See Also

- [Configuration System](configuration-system.md) - Full configuration reference
- [Configuration API](configuration-api.md) - Developer documentation
- Migration script: `tools/migrate_acquisition_configs.py`
