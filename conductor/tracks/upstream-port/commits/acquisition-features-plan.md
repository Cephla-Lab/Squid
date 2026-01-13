# Acquisition Features Suite - Porting Plan

**STATUS: COMPLETED** (2026-01-12)

All commits ported:
- [x] 88db4da8 - YAML save/load → commit 8e032023
- [x] f8c05d0d - TCP command → commit 47b385e0
- [x] 47e7aff7 - Alignment button → commit b4aa7255
- [x] 57378358 - Throttling prefs → commit 6c9cb672
- [x] fc57e3da - Persist path → commit 6acb9b35
- [x] 98c7fbd6 - Camera settings → commit 6acb9b35

See tracking files `21-*` through `24-*` for implementation details.

---

## Overview

Port 6 commits implementing acquisition automation and UX improvements.

**Commits (in order of dependency):**
1. `88db4da8` - Save/load acquisition parameters via YAML (base)
2. `f8c05d0d` - run_acquisition_from_yaml TCP command (depends on 88db4da8)
3. `47e7aff7` - Alignment button for sample registration
4. `57378358` - Throttling settings in Preferences (ties into backpressure)
5. `fc57e3da` - Persist last used saving path
6. `98c7fbd6` - Save/restore camera settings

**Recommendation:** Port 88db4da8 → f8c05d0d first (form a unit), then remaining commits independently.

---

## File Mapping

### 88db4da8 - YAML Acquisition Parameters

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/acquisition_yaml_loader.py` | `backend/io/acquisition_yaml.py` | **Create** |
| `control/core/multi_point_controller.py` | `backend/controllers/multipoint/multi_point_controller.py` | Modify |
| `control/widgets.py` (YAMLAcquisitionWidget) | `ui/widgets/acquisition/yaml_acquisition_widget.py` | **Create** |
| `tests/control/test_acquisition_yaml_loader.py` | `tests/unit/backend/io/test_acquisition_yaml.py` | **Create** |

### f8c05d0d - TCP Command for YAML Acquisition

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/microscope_control_server.py` | `backend/services/control_server.py` | Modify |
| `scripts/run_acquisition.py` | `tools/run_acquisition.py` | **Create** |
| `docs/automation.md` | `docs/automation.md` | **Create** |

### 47e7aff7 - Alignment Button

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/core/core.py` | `backend/controllers/live_controller.py` | Modify |
| `control/core/multi_point_controller.py` | `backend/controllers/multipoint/multi_point_controller.py` | Modify |
| `control/core/multi_point_worker.py` | `backend/controllers/multipoint/multi_point_worker.py` | Modify |
| `control/gui_hcs.py` | `ui/main_window.py` | Modify |
| `control/widgets.py` (AlignmentWidget) | `ui/widgets/acquisition/alignment_widget.py` | **Create** |

### 57378358 - Throttling Preferences

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/_def.py` | `core/config/acquisition.py` | Modify |
| `control/widgets.py` (PreferencesDialog) | `ui/widgets/dialogs/preferences_dialog.py` | Modify |

**Note:** This ties into the backpressure suite - should be ported after backpressure.

### fc57e3da - Persist Saving Path

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/widgets.py` (MultiPointWidget) | `ui/widgets/acquisition/multipoint_widget.py` | Modify |

Uses QSettings to remember last saving path.

### 98c7fbd6 - Camera Settings Cache

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/gui_hcs.py` | `ui/main_window.py` | Modify |
| `squid/camera/settings_cache.py` | `backend/drivers/cameras/settings_cache.py` | **Create** |
| `tests/squid/test_settings_cache.py` | `tests/unit/backend/drivers/test_settings_cache.py` | **Create** |

---

## Implementation Phases

### Phase 1: YAML Acquisition (88db4da8)

1. Create `backend/io/acquisition_yaml.py`:
   - `AcquisitionConfig` dataclass/Pydantic model
   - `load_acquisition_yaml()` - parse YAML to config
   - `save_acquisition_yaml()` - dump config to YAML
   - Validation logic for required fields

2. Add to `MultiPointController`:
   - `load_acquisition_from_yaml(path)` method
   - `save_acquisition_to_yaml(path)` method

3. Create `ui/widgets/acquisition/yaml_acquisition_widget.py`:
   - Load/Save buttons
   - File picker dialog
   - EventBus integration for status updates

4. Write tests in `tests/unit/backend/io/test_acquisition_yaml.py`

### Phase 2: TCP Command (f8c05d0d)

1. Add to control server:
   - `run_acquisition_from_yaml` command handler
   - Response format for acquisition status

2. Create `tools/run_acquisition.py`:
   - CLI script for remote acquisition
   - TCP client implementation
   - Progress monitoring

3. Create `docs/automation.md`:
   - Document YAML format
   - Document TCP commands
   - Example workflows

### Phase 3: Alignment Button (47e7aff7)

1. Create alignment widget:
   - Load previous acquisition for alignment
   - Overlay display on live view
   - Apply alignment offset

2. Add alignment methods to controllers:
   - `LiveController.set_alignment_overlay()`
   - `MultiPointController.apply_alignment_offset()`

3. Integrate into main window

### Phase 4: Throttling Preferences (57378358)

**Prerequisite:** Backpressure suite must be ported first.

1. Add to preferences dialog:
   - Max pending jobs spinner
   - Max pending MB spinner
   - Throttle timeout spinner

2. Connect to backpressure controller configuration

### Phase 5: Persist Saving Path (fc57e3da)

1. Add QSettings usage to multipoint widget:
   - Save path on change
   - Load path on startup
   - Key: `"multipoint/last_save_path"`

### Phase 6: Camera Settings Cache (98c7fbd6)

1. Create `backend/drivers/cameras/settings_cache.py`:
   - `CameraSettingsCache` class
   - `save_settings(camera_id, settings)` method
   - `load_settings(camera_id)` method
   - JSON file storage in user data directory

2. Add to main window:
   - `closeEvent` - save current camera settings
   - Startup - restore camera settings

3. Write tests

---

## Key Considerations

1. **YAML Schema**: Define a stable schema for acquisition configs. Should be:
   - Human-readable and editable
   - Versioned for future compatibility
   - Include all necessary parameters (channels, positions, timing, etc.)

2. **TCP Security**: The control server exposes microscope control. Consider:
   - Localhost-only binding (default)
   - Optional authentication for remote access

3. **Alignment Reference**: The alignment feature needs to:
   - Store reference positions from previous acquisitions
   - Handle stage drift between sessions
   - Support manual adjustment of alignment offset

4. **Settings Cache Location**: Use platform-appropriate user data directory:
   - Linux: `~/.local/share/squid/`
   - macOS: `~/Library/Application Support/squid/`
   - Windows: `%APPDATA%\squid\`

---

## Dependencies

```
88db4da8 (YAML params)
    └─→ f8c05d0d (TCP command)

57378358 (throttling preferences)
    └─→ backpressure suite (must be ported first)

Independent:
- 47e7aff7 (alignment)
- fc57e3da (persist path)
- 98c7fbd6 (camera settings)
```

---

## Critical Files
- `backend/io/acquisition_yaml.py` (new)
- `backend/controllers/multipoint/multi_point_controller.py`
- `backend/services/control_server.py`
- `ui/widgets/acquisition/` (new widgets)
- `backend/drivers/cameras/settings_cache.py` (new)
