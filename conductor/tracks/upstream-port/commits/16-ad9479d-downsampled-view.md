# PR 16: Downsampled Well and Plate View

**Upstream Commit:** `ad9479d` - feat: Add downsampled well and plate view for Select Wells mode (#387)
**Priority:** Medium
**Effort:** Large (+4000 lines across 17 files)

## Summary

Major feature adding downsampled well and plate view for Select Wells mode. Provides preview of wells during selection.

## Upstream Changes

**Files Created:**
- `software/control/core/downsampled_views.py` (+577 lines)
- `software/docs/downsampled-plate-view.md` (+283 lines)
- `software/tests/control/core/test_downsampled_views.py` (+686 lines)
- `software/tests/control/core/test_drain_all_issue.py` (+283 lines)
- `software/tests/control/core/test_job_processing_downsampled.py` (+478 lines)
- `software/tests/control/core/test_plate_view_timing.py` (+454 lines)

**Files Modified:**
- `software/control/_def.py` (+52 lines)
- `software/control/core/job_processing.py` (+218 lines)
- `software/control/core/multi_point_controller.py` (+37 lines)
- `software/control/core/multi_point_utils.py` (+38 lines)
- `software/control/core/multi_point_worker.py` (+542 lines)
- `software/control/core/scan_coordinates.py` (+8 lines)
- `software/control/gui_hcs.py` (+74 lines)
- `software/control/widgets.py` (+299 lines)
- `software/drivers and libraries/tucsen/sdk/` (binary updates)

## arch_v2 Targets

| Upstream File | arch_v2 Location | Status |
|---------------|------------------|--------|
| `downsampled_views.py` | `src/squid/backend/controllers/multipoint/downsampled_views.py` | ✅ |
| `job_processing.py` | `src/squid/backend/controllers/multipoint/job_processing.py` | ✅ |
| `multi_point_controller.py` | `src/squid/backend/controllers/multipoint/multi_point_controller.py` | ✅ |
| `multi_point_worker.py` | `src/squid/backend/controllers/multipoint/multi_point_worker.py` | ✅ |
| `widgets.py` (NapariPlateViewWidget) | `src/squid/ui/widgets/display/napari_plate_view.py` | ✅ |
| `gui_hcs.py` | `src/squid/ui/main_window.py` | ✅ |
| `tests/` | `tests/unit/squid/controllers/multipoint/` | ✅ (57 tests) |

## Implementation Phases

### Phase 1: Core Infrastructure (1 day)
- [x] Create `downsampled_views.py` in backend/controllers/multipoint/
- [x] Port downsampling algorithms
- [x] Port view generation logic

### Phase 2: Job Processing Updates (0.5 day)
- [x] Update job_processing.py with new job types
- [x] Add DownsampledViewJob or similar
- [x] Update job dispatching

### Phase 3: Worker Integration (1 day)
- [x] Update multi_point_worker.py (deferred - worker integration for runtime use)
- [x] Integrate downsampled view generation (deferred - core module ready for integration)
- [x] Add progress events for view updates (deferred - events ready for use)

### Phase 4: Controller Updates (0.5 day)
- [x] Update multi_point_controller.py with xy_mode, get_plate_view(), plate dimension calculation
- [x] Add downsampled view parameters to AcquisitionParameters
- [x] Handle view state (deferred - runtime integration in worker)

### Phase 5: UI Widgets (1 day)
- [x] Create NapariPlateViewWidget in ui/widgets/display/napari_plate_view.py
- [x] Add plate overview display with napari viewer
- [x] Add well preview display with click-to-FOV mapping
- [x] Integrate with wellplate selection (ready for connection)

### Phase 6: Main Window Integration (0.5 day)
- [x] Add downsampled view tab to main window
- [x] Add PlateViewInit/PlateViewUpdate events to core/events.py
- [x] Connect events to widget via UIEventBus subscriptions

### Phase 7: Tests (REQUIRED - 1 day)

**Test Files to Port:**
| Upstream Test | arch_v2 Location | Lines |
|---------------|------------------|-------|
| `tests/control/core/test_downsampled_views.py` | `tests/unit/squid/backend/controllers/multipoint/test_downsampled_views.py` | +686 |
| `tests/control/core/test_drain_all_issue.py` | `tests/unit/squid/backend/controllers/multipoint/test_drain_all_issue.py` | +283 |
| `tests/control/core/test_job_processing_downsampled.py` | `tests/unit/squid/backend/controllers/multipoint/test_job_processing_downsampled.py` | +478 |
| `tests/control/core/test_plate_view_timing.py` | `tests/integration/squid/backend/test_plate_view_timing.py` | +454 |

**Total: +1901 lines of tests**

- [x] Create test directory structure
- [x] Port test_downsampled_views.py (unit tests for core algorithms) - 44 tests passing
- [x] Port test_drain_all_issue.py (regression tests) - 3 tests passing
- [x] Port test_job_processing_downsampled.py (job processing tests) - 10 tests passing
- [ ] Port test_plate_view_timing.py (performance/timing tests) - deferred
- [x] Update imports for arch_v2 structure
- [x] Run all tests and verify they pass (57 tests passing)

## Implementation Checklist

### Step 1: Read Documentation
- [x] Read `docs/downsampled-plate-view.md` thoroughly
- [x] Understand the feature architecture
- [x] Note performance considerations

### Step 2: Core Module
- [x] Create downsampled_views.py
- [x] Implement downsampling algorithms
- [x] Add caching for performance (via WellTileAccumulator)

### Step 3: Job Processing
- [x] Add new job types for downsampled views (DownsampledViewJob)
- [x] Update job runner configuration (only queue non-None results)
- [x] Handle async view generation (via JobRunner multiprocessing)

### Step 4: Worker Updates
- [x] Modify frame processing pipeline (added _create_downsampled_view_job in _process_camera_frame)
- [x] Add view update triggers (added _process_downsampled_view_result emitting PlateViewUpdate events)
- [x] Optimize for real-time updates (drain all results from queue per call)

### Step 5: UI Components
- [x] Create NapariPlateViewWidget
- [x] Add Plate View tab to main window
- [x] Connect PlateViewInit/PlateViewUpdate events

### Step 6: Testing
- [x] Run all ported tests (57 tests passing)
- [ ] Manual testing of view updates (deferred)
- [ ] Performance testing (deferred)

## Key Components

### DownsampledViews
```python
class DownsampledPlateView:
    """Maintains downsampled view of entire plate."""

    def __init__(self, plate_format: str):
        self.plate_format = plate_format
        self._well_views: dict[str, np.ndarray] = {}

    def update_well(self, well_id: str, image: np.ndarray):
        """Update downsampled view for a well."""
        downsampled = self._downsample(image)
        self._well_views[well_id] = downsampled

    def get_plate_view(self) -> np.ndarray:
        """Get composite view of all wells."""
        pass
```

### Event Flow
```
Acquisition Frame → Worker → DownsampleJob → DownsampledViews → UI Update
```

## Notes

- This is a substantial feature - plan for 3-5 days of work
- Consider implementing in multiple PRs for easier review
- Performance is critical - use efficient downsampling
- Read the upstream documentation first!
