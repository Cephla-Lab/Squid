# Acquisition Features Suite

**Status:** COMPLETED
**Ported:** 2026-01-12

## Upstream Commits

- [x] `88db4da8` - feat: Save and load acquisition parameters via YAML
  - **Our Commit:** 8e032023
  - **Tracking:** `commits/21-8e032023-acquisition-yaml.md`

- [x] `f8c05d0d` - feat: Add run_acquisition_from_yaml TCP command and CLI
  - **Our Commit:** 47b385e0
  - **Tracking:** `commits/22-47b385e0-tcp-yaml-command.md`

- [x] `47e7aff7` - feat: Add alignment button for sample registration
  - **Our Commit:** b4aa7255
  - **Tracking:** `commits/24-b4aa7255-alignment-button.md`

- [x] `57378358` - feat: Add acquisition throttling settings in Preferences
  - **Our Commit:** 6c9cb672
  - **Tracking:** `commits/19-6c9cb672-simulation-throttling-ui.md`

- [x] `fc57e3da` - feat: Persist last used base saving path
  - **Our Commit:** 6acb9b35
  - **Tracking:** `commits/20-6acb9b35-persistence-features.md`

- [x] `98c7fbd6` - feat: Save and restore camera settings on close/startup
  - **Our Commit:** 6acb9b35
  - **Tracking:** `commits/20-6acb9b35-persistence-features.md`

## Implementation Checklist

### YAML Acquisition (88db4da8)
- [x] Create `backend/io/acquisition_yaml.py`
- [x] Implement parse_acquisition_yaml()
- [x] Implement save_acquisition_yaml()
- [x] Implement validate_hardware()
- [x] Create drag-drop mixin for widgets
- [x] Add tests (19 tests)

### TCP Command (f8c05d0d)
- [x] Create `backend/services/tcp_control_server.py`
- [x] Implement run_acquisition_from_yaml command
- [x] Use EventBus commands (not direct widget access)
- [x] Create `tools/run_acquisition.py` CLI
- [x] Add --start-server flag to main_hcs.py
- [x] Add tests (15 tests)

### Alignment Button (47e7aff7)
- [x] Create `ui/widgets/stage/alignment_widget.py`
- [x] Implement 3-state machine (ALIGN/CONFIRM/CLEAR)
- [x] Add napari reference layer overlay
- [x] Implement offset calculation and application
- [x] Integrate with MultiPointWorker.move_to_coordinate()
- [x] Add tests (24 tests)

### Throttling Preferences (57378358)
- [x] Add throttling settings to Preferences > Advanced
- [x] Connect to backpressure configuration

### Persistence (fc57e3da, 98c7fbd6)
- [x] Create `core/utils/cache.py`
- [x] Create `backend/drivers/cameras/settings_cache.py`
- [x] Implement saving path persistence
- [x] Implement camera settings save/restore
- [ ] Add tests for cache utilities

## Notes

TCP server uses EventBus commands instead of direct widget manipulation - proper arch_v2 pattern.
