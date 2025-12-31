# PR 17: Channel Configuration Refactor

**Upstream Commit:** `b385904` - feat: Refactor channel configuration to eliminate duplication across... (#392)
**Priority:** High (User requested full port)
**Effort:** Large (+2900 lines across 15 files)

## Summary

Major refactor of channel configuration system to a two-tier architecture:
1. Global channel definitions (shared across all objectives)
2. Per-objective settings (exposure, gain, etc.)

Eliminates 100+ lines of duplicated channel metadata per objective.

## Upstream Changes

**Files Created:**
- `software/configurations/channel_definitions.default.json` (+187 lines)
- `software/docs/channel_configuration.md` (+355 lines)
- `software/tests/control/core/test_channel_configuration.py` (+788 lines)

**Files Modified:**
- `.gitignore` (+1 line)
- `software/.gitignore` (+2 lines)
- `software/control/_def.py` (+1 line)
- `software/control/core/channel_configuration_mananger.py` (+545 lines, major expansion)
- `software/control/core/configuration_mananger.py` (+2 lines)
- `software/control/core/multi_point_controller.py` (+5 lines)
- `software/control/gui_hcs.py` (+43 lines)
- `software/control/microscope.py` (+96 lines)
- `software/control/utils_config.py` (+376 lines)
- `software/control/widgets.py` (+567 lines)
- `software/tests/control/gui_test_stubs.py` (+8 lines)
- `software/tests/control/test_stubs.py` (+4 lines)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `channel_definitions.default.json` | `software/configurations/channel_definitions.default.json` |
| `channel_configuration_mananger.py` | `src/squid/backend/managers/channel_configuration_manager.py` |
| `utils_config.py` | `src/squid/core/utils/config_utils.py` |
| `widgets.py` (channel config) | `src/squid/ui/widgets/` (new dialog) |
| `gui_hcs.py` | `src/squid/ui/main_window.py` |
| `test_channel_configuration.py` | `tests/unit/squid/backend/managers/` |

## Two-Tier Architecture

### Tier 1: Global Channel Definitions
```json
// channel_definitions.default.json
{
  "channels": [
    {
      "name": "BF LED matrix  left half",
      "type": "led_matrix",
      "display_color": "#808080",
      "enabled": true,
      "illumination_source": 0
    },
    {
      "name": "Fluorescence 405 nm Ex",
      "type": "fluorescence",
      "display_color": "#0000FF",
      "enabled": true,
      "numeric_channel": 1
    }
  ],
  "numeric_channel_mapping": {
    "1": {"illumination_source": 11, "excitation_wavelength": 405},
    "2": {"illumination_source": 12, "excitation_wavelength": 488}
  }
}
```

### Tier 2: Per-Objective Settings
```json
// acquisition_configurations/{objective}/channel_settings.json
{
  "BF LED matrix  left half": {
    "exposure_time_ms": 10,
    "analog_gain": 0,
    "illumination_intensity": 50,
    "z_offset_um": 0
  },
  "Fluorescence 405 nm Ex": {
    "exposure_time_ms": 100,
    "analog_gain": 5,
    "illumination_intensity": 80,
    "z_offset_um": 0,
    "confocal_overrides": {
      "exposure_time_ms": 200
    }
  }
}
```

## Implementation Steps

### Step 1: Add Pydantic Models (0.5 day) - COMPLETED
- [x] Create `src/squid/core/config/channel_definitions.py`
- [x] Define models:

```python
from enum import Enum
from pydantic import BaseModel

class ChannelType(str, Enum):
    FLUORESCENCE = "fluorescence"
    LED_MATRIX = "led_matrix"

class NumericChannelMapping(BaseModel):
    illumination_source: int
    excitation_wavelength: int

class ChannelDefinition(BaseModel):
    name: str
    type: ChannelType
    display_color: str
    enabled: bool = True
    numeric_channel: int | None = None
    illumination_source: int | None = None

class ConfocalOverrides(BaseModel):
    exposure_time_ms: float | None = None
    analog_gain: float | None = None
    illumination_intensity: float | None = None
    z_offset_um: float | None = None

class ObjectiveChannelSettings(BaseModel):
    exposure_time_ms: float
    analog_gain: float
    illumination_intensity: float
    z_offset_um: float = 0
    confocal_overrides: ConfocalOverrides | None = None

class ChannelDefinitionsConfig(BaseModel):
    channels: list[ChannelDefinition]
    numeric_channel_mapping: dict[str, NumericChannelMapping]
```

### Step 2: Create Default Definitions (0.5 day) - COMPLETED
- [x] Create `software/configurations/channel_definitions.default.json`
- [x] Define all standard channels
- [x] Add to .gitignore: `channel_definitions.json` (user copy)

### Step 3: Update Manager (1 day) - COMPLETED
- [x] Refactor `channel_configuration_manager.py`
- [x] Add `_load_channel_definitions()`
- [x] Add `_load_objective_settings()`
- [x] Add `_save_objective_settings()`
- [x] Implement two-stage lookup
- [x] Add `sync_confocal_mode_from_hardware()` if needed
- [x] Maintain XML backward compatibility

### Step 4: Add Migration Utility (0.5 day) - COMPLETED
- [x] Create `migrate_all_profiles()` function
- [x] Convert existing XML profiles to JSON
- [x] Handle validation and edge cases

### Step 5: Update UI (1 day) - DEFERRED
- [ ] Create Channel Configuration Editor dialog
- [ ] Add enable/disable functionality
- [ ] Update channel dropdowns to filter disabled
- [ ] Add to Settings menu

Note: UI updates deferred to follow-up work. Core backend infrastructure is complete.

### Step 6: Port Tests (REQUIRED - 0.5 day) - COMPLETED

**Test Files to Port:**
| Upstream Test | arch_v2 Location | Lines |
|---------------|------------------|-------|
| `tests/control/core/test_channel_configuration.py` | `tests/unit/squid/backend/managers/test_channel_configuration.py` | +788 |
| `tests/control/gui_test_stubs.py` | `tests/stubs/gui_test_stubs.py` | +8 |
| `tests/control/test_stubs.py` | `tests/stubs/test_stubs.py` | +4 |

**Total: +800 lines of tests**

- [x] Create test file `test_channel_configuration.py`
- [x] Port all test cases (channel loading, saving, migration)
- [x] Update test stubs for arch_v2
- [x] Add migration tests for XML->JSON conversion
- [x] Add validation tests for Pydantic models
- [x] Run tests and verify they pass (56 tests passing)

## Implementation Checklist

### Models - COMPLETED
- [x] Create `src/squid/core/config/channel_definitions.py`
- [x] Add ChannelType enum
- [x] Add NumericChannelMapping model
- [x] Add ChannelDefinition model
- [x] Add ObjectiveChannelSettings model
- [x] Add ConfocalOverrides model
- [x] Add ChannelDefinitionsConfig root model

### Configuration Files - COMPLETED
- [x] Create `channel_definitions.default.json`
- [x] Add to appropriate .gitignore
- [x] Document file locations

### Manager Updates - COMPLETED
- [x] Implement two-tier loading
- [x] Add settings save functionality
- [x] Add confocal sync (if applicable)
- [x] Maintain backward compatibility

### Migration - COMPLETED
- [x] Implement `migrate_all_profiles()`
- [x] Test migration on existing profiles
- [x] Handle errors gracefully

### UI - DEFERRED
- [ ] Create editor dialog
- [ ] Implement enable/disable
- [ ] Filter disabled channels in dropdowns
- [ ] Add Settings menu entry

### Tests - COMPLETED
- [x] Port all test cases
- [x] Add new test cases for migration
- [x] Verify all tests pass

## Notes

- Read `docs/channel_configuration.md` first!
- This is a significant refactor - plan carefully
- Consider doing as a series of smaller PRs:
  1. Models and definitions
  2. Manager refactor
  3. Migration utility
  4. UI updates
- Maintain XML compatibility for transition period
- Test thoroughly with existing configurations
