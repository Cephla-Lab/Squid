# YAML Configuration System - Porting Plan

**STATUS: SKIPPED** - Marked as superseded in upstream-status.yaml

arch_v2 keeps its own ChannelConfigurationManager which works differently.
The upstream YAML config refactor doesn't apply to this architecture.

---

## Overview

Port the YAML configuration system from upstream, adopting the full hierarchical structure with machine_configs and user_profiles.

**Commits (in order):**
1. `13eff115` - New design for illumination/acquisition configs (53 files)
2. `3866b183` - Remove legacy config managers
3. `98c50432` - Fix stale references

---

## New Directory Structure

```
configurations/
├── machine_configs/
│   └── illumination_channel_config.yaml    # Hardware-level settings
└── user_profiles/
    └── {profile}/
        └── channel_configs/
            ├── general.yaml                 # Shared across objectives
            └── {objective}.yaml             # Per-objective overrides
```

---

## File Mapping

### New Files to Create

| arch_v2 Path | Purpose |
|--------------|---------|
| `core/config/models/__init__.py` | Models package |
| `core/config/models/illumination.py` | IlluminationChannel, IlluminationChannelConfig |
| `core/config/models/acquisition.py` | AcquisitionChannel, GeneralChannelConfig, ObjectiveChannelConfig |
| `core/config/models/camera.py` | CameraMappingsConfig |
| `core/config/models/confocal.py` | ConfocalConfig |
| `core/config/models/laser_af.py` | LaserAFConfig |
| `core/config/repository.py` | ConfigRepository - centralized I/O |
| `backend/config_generator.py` | Default config generator |

### Files to Remove

| arch_v2 Path | Reason |
|--------------|--------|
| `backend/managers/channel_configuration_manager.py` | Replaced by ConfigRepository |
| `backend/managers/configuration_manager.py` | Replaced by ConfigRepository |

### Files to Modify

| arch_v2 Path | Changes |
|--------------|---------|
| `core/config/__init__.py` | Export new models |
| `backend/microscope.py` | Add `config_repo` property |
| `backend/controllers/live_controller.py` | Add `get_channels()` method |
| `application.py` | Use ConfigRepository |
| `ui/main_window.py` | Use ConfigRepository |

---

## Conflicts with Existing Code

| Current arch_v2 | Upstream | Resolution |
|-----------------|----------|------------|
| `core/config/channel_definitions.py` (JSON) | YAML-based IlluminationChannel | Replace with YAML models |
| `core/config/acquisition.py` | Different AcquisitionChannel | Keep for planning, rename upstream |
| `core/utils/config_utils.py` (ChannelMode) | AcquisitionChannel | Deprecate ChannelMode |
| `backend/managers/channel_configuration_manager.py` | ConfigRepository | Remove after transition |

---

## Implementation Phases

### Phase 1: Add New Models
1. Create `core/config/models/` package
2. Port Pydantic models from upstream
3. Create `core/config/repository.py`
4. Create `configurations/machine_configs/`

### Phase 2: Update Backend
1. Add `config_repo` to Microscope
2. Add `get_channels()` to LiveController
3. Update ApplicationContext to use ConfigRepository
4. Add deprecation warnings to old managers

### Phase 3: Remove Legacy Managers
1. Remove `channel_configuration_manager.py`
2. Remove `configuration_manager.py`
3. Update all imports

### Phase 4: Update UI
1. Profile management via ConfigRepository
2. Channel configuration via new API

### Phase 5: Migration
1. Create migration script for existing configs
2. Test with both old and new formats
3. Update tests

---

## Migration Strategy

1. **Detect old format**: Check for `channel_definitions.json`
2. **Convert**: JSON → YAML, flatten to hierarchical structure
3. **Backup**: Keep originals in `.backup/`
4. **Fallback**: ConfigRepository can load old format during transition

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking user configs | Migration script with backup |
| UI widgets break | Phase transition, keep old API |
| Import cycles | ConfigRepository in core (Layer 0) |
| confocal_mode state | Keep in LiveController |

---

## Critical Files
- `core/config/__init__.py`
- `backend/managers/channel_configuration_manager.py` (to replace)
- `backend/controllers/live_controller.py`
- `application.py`
- `backend/microscope.py`
