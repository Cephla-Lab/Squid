# Port Plan: Save/Load Acquisition Parameters via YAML (88db4da8)

## Overview

**Upstream commit:** 88db4da8 - feat: Save and load acquisition parameters via YAML (#421)
**Lines changed:** ~1344 (largest deferred feature)
**Priority:** High - valuable workflow feature for users

### What It Does

1. **YAML Saving**: Automatically saves all acquisition parameters to `acquisition.yaml` when an acquisition starts
2. **YAML Loading**: Drag-and-drop saved YAML files onto multipoint widgets to restore settings
3. **Hardware Validation**: Blocks loading if objective or camera binning mismatch

## Architecture Mapping

### Upstream Files → arch_v2 Locations

| Upstream | arch_v2 Location | Notes |
|----------|------------------|-------|
| `control/acquisition_yaml_loader.py` | `backend/io/acquisition_yaml.py` | New module in io layer |
| `control/core/multi_point_controller.py` | `backend/controllers/multipoint_controller.py` | Add YAML save logic |
| `control/widgets.py` (mixin + apply) | `ui/widgets/acquisition/*.py` | Split to wellplate/flexible widgets |

### Key Differences to Handle

1. **Parameter Structure**: arch_v2 uses `ScanCoordinates` manager and `ChannelConfigurationManager` vs upstream's more direct attribute access
2. **ObjectiveStore**: arch_v2's `ObjectiveStore` is similar but check API compatibility
3. **Widget Structure**: arch_v2 has separate files for `FlexibleMultiPointWidget` and `WellplateMultiPointWidget`

---

## Implementation Checklist

### Phase 1: Create YAML Loader Module
**File:** `software/src/squid/backend/io/acquisition_yaml.py`

- [ ] Create `AcquisitionYAMLData` dataclass with all fields:
  - widget_type, xy_mode
  - objective_name, objective_magnification, objective_pixel_size_um, camera_binning
  - nz, delta_z_um, z_stacking_config
  - nt, delta_t_s
  - channel_names
  - contrast_af, laser_af
  - scan_size_mm, overlap_percent, scan_shape, wellplate_regions (wellplate-specific)
  - nx, ny, delta_x_mm, delta_y_mm, flexible_positions (flexible-specific)

- [ ] Create `parse_acquisition_yaml(file_path: str) -> AcquisitionYAMLData`:
  - Parse YAML sections: acquisition, objective, z_stack, time_series, channels, autofocus, wellplate_scan, flexible_scan
  - Validate widget_type is "wellplate" or "flexible"
  - Handle missing/optional fields with defaults
  - Convert delta_z_mm to delta_z_um (multiply by 1000)

- [ ] Create `ValidationResult` dataclass:
  - is_valid, objective_mismatch, binning_mismatch
  - current_objective, yaml_objective
  - current_binning, yaml_binning
  - message

- [ ] Create `validate_hardware(yaml_data, current_objective, current_binning) -> ValidationResult`

- [ ] Add exports to `backend/io/__init__.py`

### Phase 2: Add YAML Saving to Controller
**File:** `software/src/squid/backend/controllers/multipoint_controller.py`

- [ ] Add `_serialize_for_yaml(obj)` helper function:
  - Handle Enum, numpy types, dataclasses, Pydantic models
  - Recursive serialization for dicts and lists

- [ ] Add `_save_acquisition_yaml(params, experiment_path, ...)` function:
  - Build YAML dict with sections: acquisition, objective, sample, z_stack, time_series, autofocus, channels
  - Add widget-specific section (wellplate_scan or flexible_scan)
  - Add downsampled_views, plate, fluidics sections
  - Write to `{experiment_path}/acquisition.yaml`

- [ ] Add controller state attributes:
  - `widget_type: str = "wellplate"`
  - `scan_size_mm: float = 0.0`
  - `overlap_percent: float = 10.0`

- [ ] Add setter methods:
  - `set_widget_type(widget_type: str)`
  - `set_scan_size(scan_size_mm: float)`
  - `set_overlap_percent(overlap_percent: float)`

- [ ] Call `_save_acquisition_yaml()` in `run()` method after building acquisition params

### Phase 3: Create Drag-Drop Mixin
**File:** `software/src/squid/ui/widgets/acquisition/yaml_drop_mixin.py`

- [ ] Create `AcquisitionYAMLDropMixin` class:
  - `_is_valid_yaml_drop(file_path)` - check for .yaml/.yml or folder with acquisition.yaml
  - `_resolve_yaml_path(file_path)` - get actual yaml path
  - `dragEnterEvent(event)` - visual feedback with dashed border
  - `dragLeaveEvent(event)` - restore original stylesheet
  - `dropEvent(event)` - load first valid yaml file
  - `_load_acquisition_yaml(file_path)` - parse, validate, apply
  - Abstract methods: `_get_expected_widget_type()`, `_apply_yaml_settings(yaml_data)`

- [ ] Create `AcquisitionYAMLMismatchDialog(QDialog)`:
  - Show hardware mismatch details (objective, binning)
  - Instruction to update hardware settings

### Phase 4: Update FlexibleMultiPointWidget
**File:** `software/src/squid/ui/widgets/acquisition/flexible_multipoint.py`

- [ ] Add `AcquisitionYAMLDropMixin` to class inheritance
- [ ] Add `self.setAcceptDrops(True)` in `__init__`
- [ ] Add `self.multipointController.set_widget_type("flexible")` in acquisition start
- [ ] Implement `_get_expected_widget_type()` returning "flexible"
- [ ] Implement `_apply_yaml_settings(yaml_data)`:
  - Block signals on widgets during update
  - Set NX, NY, deltaX, deltaY, overlap
  - Set NZ, deltaZ, Nt, dt
  - Select channels in list
  - Set autofocus checkboxes
  - Load positions if present
  - Unblock signals and trigger UI updates

- [ ] Implement `_load_positions(positions)`:
  - Clear existing locations
  - Add each position to location_list, location_ids
  - Update dropdown and table UI
  - Add to scanCoordinates

### Phase 5: Update WellplateMultiPointWidget
**File:** `software/src/squid/ui/widgets/acquisition/wellplate_multipoint.py`

- [ ] Add `AcquisitionYAMLDropMixin` to class inheritance
- [ ] Add `self.setAcceptDrops(True)` in `__init__`
- [ ] Add controller setters in acquisition start:
  - `set_widget_type("wellplate")`
  - `set_scan_size(entry_scan_size.value())`
  - `set_overlap_percent(entry_overlap.value())`

- [ ] Implement `_get_expected_widget_type()` returning "wellplate"
- [ ] Implement `_apply_yaml_settings(yaml_data)`:
  - Block signals on widgets
  - Set Z-stack: checkbox_z, entry_NZ, entry_deltaZ
  - Set time series: checkbox_time, entry_Nt, entry_dt
  - Set overlap, scan_size, shape
  - Select channels
  - Set autofocus checkboxes
  - Set XY mode
  - Load well regions if present
  - Unblock signals and trigger UI updates

- [ ] Implement `_load_well_regions(regions)`:
  - Parse well names (e.g., "C4" → row=2, col=3)
  - Select wells in well_selection_widget
  - Emit signal_wellSelected

- [ ] Implement `_parse_well_name(well_name)` helper

### Phase 6: Write Tests
**File:** `software/tests/unit/backend/io/test_acquisition_yaml.py`

- [ ] Test `parse_acquisition_yaml`:
  - Parse wellplate YAML with all fields
  - Parse flexible YAML with all fields
  - Parse minimal YAML (defaults)
  - Empty YAML raises ValueError
  - Invalid widget_type raises ValueError
  - Handle invalid camera_binning formats

- [ ] Test `validate_hardware`:
  - Matching hardware returns is_valid=True
  - Objective mismatch detected
  - Binning mismatch detected
  - Both mismatches detected
  - Missing objective/binning in YAML still valid

**File:** `software/tests/unit/ui/widgets/test_yaml_drop_mixin.py`

- [ ] Test mixin validation methods
- [ ] Test path resolution

---

## YAML Schema Reference

```yaml
# acquisition.yaml schema
acquisition:
  experiment_id: string
  start_time: string
  widget_type: "wellplate" | "flexible"
  xy_mode: string
  skip_saving: bool

objective:
  name: string
  magnification: float
  NA: float
  pixel_size_um: float
  camera_binning: [int, int]
  sensor_pixel_size_um: float

sample:
  wellplate_format: string | null

z_stack:
  nz: int
  delta_z_mm: float
  config: string
  z_range_mm: [float, float] | null
  use_piezo: bool

time_series:
  nt: int
  delta_t_s: float

autofocus:
  contrast_af: bool
  laser_af: bool

channels:
  - name: string
    exposure_time_ms: float
    analog_gain: float
    illumination_source: string
    illumination_intensity: float
    # ... other channel fields

wellplate_scan:  # Only for widget_type="wellplate"
  scan_size_mm: float
  overlap_percent: float
  regions:
    - name: string  # e.g., "C4"
      center_mm: [float, float, float]
      shape: string

flexible_scan:  # Only for widget_type="flexible"
  nx: int
  ny: int
  delta_x_mm: float
  delta_y_mm: float
  overlap_percent: float
  positions:
    - name: string
      center_mm: [float, float, float]

downsampled_views:
  enabled: bool
  save_well_images: bool
  well_resolutions_um: list
  plate_resolution_um: float
  z_projection: string
  interpolation_method: string

plate:
  num_rows: int
  num_cols: int

fluidics:
  enabled: bool
```

---

## Testing Strategy

1. **Unit tests**: Parser, validator, serializer functions
2. **Integration tests**: End-to-end YAML save/load with mock controllers
3. **Manual tests**:
   - Run acquisition, verify YAML saved
   - Drag YAML onto widget, verify settings restored
   - Test hardware mismatch dialog
   - Test both wellplate and flexible modes

---

## Dependencies

- PyYAML (already in project)
- No new external dependencies

## Risk Assessment

- **Low risk**: Standalone feature, doesn't modify core acquisition logic
- **Moderate complexity**: Widget updates require careful signal blocking to prevent cascading updates
