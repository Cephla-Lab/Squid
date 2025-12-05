# Phase 0: Codebase Refactoring

**Goal**: Reorganize and split large files before implementing stability improvements (Phases 1-5).

**Impact**: Makes subsequent phases easier by creating a more modular, navigable codebase.

**Estimated Effort**: 1-2 weeks

---

## Approach

- **Comprehensive refactoring**: All Tier 1 + 2 files (6 files, ~19,000 lines total)
- **Clean breaks**: Update all imports, no backwards compatibility shims
- **Reorganize folders**: Create logical subpackage structure

---

## New Directory Structure

```
software/
├── control/
│   ├── core/                    # (existing, keep)
│   │   ├── controllers/         # NEW: Controller classes
│   │   │   ├── __init__.py
│   │   │   ├── live_controller.py
│   │   │   ├── auto_focus_controller.py
│   │   │   ├── laser_auto_focus_controller.py
│   │   │   └── multi_point_controller.py
│   │   ├── workers/             # NEW: Worker classes
│   │   │   ├── __init__.py
│   │   │   ├── multi_point_worker.py
│   │   │   └── tracking_worker.py
│   │   ├── stream_handler.py
│   │   ├── scan_coordinates.py
│   │   ├── focus_map.py         # EXTRACTED from core.py
│   │   └── ...
│   │
│   ├── cameras/                 # NEW: All camera drivers
│   │   ├── __init__.py
│   │   ├── base.py              # camera.py renamed
│   │   ├── andor.py
│   │   ├── flir.py
│   │   ├── hamamatsu.py
│   │   ├── ids.py
│   │   ├── photometrics.py
│   │   ├── toupcam.py
│   │   ├── tucsen.py
│   │   └── tis.py
│   │
│   ├── peripherals/             # NEW: Hardware peripherals
│   │   ├── __init__.py
│   │   ├── serial_base.py       # EXTRACTED: Base SerialDevice
│   │   ├── lighting/            # EXTRACTED from serial_peripherals.py
│   │   │   ├── __init__.py
│   │   │   ├── xlight.py
│   │   │   ├── dragonfly.py
│   │   │   ├── ldi.py
│   │   │   ├── cellx.py
│   │   │   └── sci_led_array.py
│   │   ├── fluidics.py
│   │   ├── piezo.py
│   │   └── spectrometer.py
│   │
│   ├── widgets/                 # NEW: Split from widgets.py
│   │   ├── __init__.py
│   │   ├── base.py              # Utility widgets (WrapperWindow, CollapsibleGroupBox)
│   │   ├── config.py            # ConfigEditor widgets
│   │   ├── camera.py            # CameraSettingsWidget, LiveControlWidget
│   │   ├── stage.py             # NavigationWidget, PiezoWidget, StageUtils
│   │   ├── acquisition.py       # FlexibleMultiPointWidget, WellplateMultiPointWidget
│   │   ├── display.py           # Napari widgets, FocusMapWidget
│   │   ├── hardware.py          # Confocal, filter, laser AF widgets
│   │   ├── wellplate.py         # Well selection, calibration widgets
│   │   ├── fluidics.py          # FluidicsWidget
│   │   └── tracking.py          # TrackingControllerWidget, etc.
│   │
│   ├── gui/                     # NEW: GUI organization
│   │   ├── __init__.py
│   │   ├── main_window.py       # HighContentScreeningGui (slimmed)
│   │   ├── signal_manager.py    # EXTRACTED: Signal connection logic
│   │   ├── layout_manager.py    # EXTRACTED: Layout/dock setup
│   │   └── qt_controllers.py    # MovementUpdater, QtAutoFocusController, QtMultiPointController
│   │
│   ├── stage/                   # NEW: Stage controllers
│   │   ├── __init__.py
│   │   ├── microcontroller.py   # Main Microcontroller class
│   │   ├── serial.py            # EXTRACTED: Serial communication classes
│   │   └── xeryon.py
│   │
│   ├── processing/              # NEW: Image processing
│   │   ├── __init__.py
│   │   ├── stitcher.py          # Base stitching
│   │   ├── coordinate_stitcher.py  # EXTRACTED from stitcher.py
│   │   └── image_utils.py       # From utils_/image_processing.py
│   │
│   ├── _def.py                  # Keep (configuration constants)
│   ├── microscope.py            # Keep
│   └── utils.py                 # Keep
│
├── squid/                       # (existing, mostly keep)
│   ├── utils/                   # NEW: Phase 1 utilities go here
│   │   ├── __init__.py
│   │   ├── safe_callback.py
│   │   ├── thread_safe_state.py
│   │   └── worker_manager.py
│   └── ...
│
└── tests/                       # Update to mirror new structure
    ├── control/
    │   ├── cameras/
    │   ├── peripherals/
    │   ├── widgets/
    │   ├── gui/
    │   └── ...
    └── squid/
```

---

## Checklist

### Task 0.1: Create new directory structure ✅
- [x] Create `control/cameras/` directory with `__init__.py`
- [x] Create `control/peripherals/lighting/` directory with `__init__.py`
- [x] Create `control/widgets/` directory with `__init__.py`
- [x] Create `control/gui/` directory with `__init__.py`
- [x] Create `control/stage/` directory with `__init__.py`
- [x] Create `control/processing/` directory with `__init__.py`
- [x] Commit: "Create new directory structure for control/ reorganization"

### Task 0.2: Move camera drivers (low risk, isolated) ✅
- [x] Move `camera.py` → `cameras/base.py`
- [x] Move `camera_andor.py` → `cameras/andor.py`
- [x] Move `camera_flir.py` → `cameras/flir.py`
- [x] Move `camera_hamamatsu.py` → `cameras/hamamatsu.py`
- [x] Move `camera_ids.py` → `cameras/ids.py`
- [x] Move `camera_photometrics.py` → `cameras/photometrics.py`
- [x] Move `camera_toupcam.py` → `cameras/toupcam.py`
- [x] Move `camera_tucsen.py` → `cameras/tucsen.py`
- [x] Move `camera_TIS.py` → `cameras/tis.py`
- [x] Update `cameras/__init__.py` with exports
- [x] Update imports in `squid/camera/utils.py`
- [x] Update imports in `microscope.py`
- [x] Run tests
- [x] Commit: "Move camera drivers to control/cameras/"

### Task 0.3: Split serial_peripherals.py (low risk) ✅
- [x] Extract `SerialDevice`, `SerialDeviceError` → `peripherals/serial_base.py`
- [x] Extract `XLight`, `XLight_Simulation` → `peripherals/lighting/xlight.py`
- [x] Extract `Dragonfly`, `Dragonfly_Simulation` → `peripherals/lighting/dragonfly.py`
- [x] Extract `LDI`, `LDI_Simulation` → `peripherals/lighting/ldi.py`
- [x] Extract `CellX`, `CellX_Simulation` → `peripherals/lighting/cellx.py`
- [x] Extract `SciMicroscopyLEDArray` variants → `peripherals/lighting/sci_led_array.py`
- [x] Update `peripherals/lighting/__init__.py` with re-exports
- [x] Update imports throughout codebase
- [x] Run tests
- [x] Commit: "Split serial_peripherals.py into device-specific modules"

### Task 0.4: Split core.py (medium risk) ✅
- [x] Extract `QtStreamHandler`, `ImageSaver`, `ImageSaver_Tracking` → `core/stream_handler.py`
- [x] Extract `ImageDisplay`, `ImageDisplayWindow` → `core/image_display.py`
- [x] Extract `TrackingController`, `TrackingWorker` → `core/tracking.py`
- [x] Extract `FocusMap`, `NavigationViewer` → `core/focus_map.py`
- [x] Update imports in `gui_hcs.py`
- [x] Update imports in `widgets.py`
- [x] Run tests
- [x] Commit: "Split core.py into focused modules"

### Task 0.5: Split stitcher.py (low risk) ✅
- [x] Keep `Stitcher` in `processing/stitcher.py`
- [x] Extract `CoordinateStitcher` → `processing/coordinate_stitcher.py`
- [x] Update imports
- [x] Run tests
- [x] Commit: "Split stitcher.py into base and coordinate modules"

### Task 0.6: Split microcontroller.py (medium risk) ✅
- [x] Keep `Microcontroller`, `HomingDirection`, `CommandAborted` in `stage/microcontroller.py`
- [x] Extract `AbstractCephlaMicroSerial`, `SimSerial`, `MicrocontrollerSerial` → `stage/serial.py`
- [x] Update imports
- [x] Run tests
- [x] Commit: "Split microcontroller.py serial communication"

### Task 0.7: Split widgets.py - base and config (high risk, do incrementally) ✅
- [x] Extract `WrapperWindow`, `CollapsibleGroupBox`, `PandasTableModel` → `widgets/base.py`
- [x] Extract `ConfigEditor`, `ConfigEditorBackwardsCompatible`, `ProfileWidget` → `widgets/config.py`
- [x] Update imports in `gui_hcs.py`
- [x] Run tests
- [x] Commit: "Split widgets.py - base and config widgets"

### Task 0.8: Split widgets.py - camera and stage ✅
- [x] Extract `CameraSettingsWidget`, `LiveControlWidget`, `RecordingWidget`, `MultiCameraRecordingWidget` → `widgets/camera.py`
- [x] Extract `NavigationWidget`, `PiezoWidget`, `AutoFocusWidget`, `StageUtils` → `widgets/stage.py`
- [x] Update imports
- [x] Run tests
- [x] Commit: "Split widgets.py - camera and stage widgets"

### Task 0.9: Split widgets.py - acquisition ✅
- [x] Extract `FlexibleMultiPointWidget`, `WellplateMultiPointWidget`, `MultiPointWithFluidicsWidget` → `widgets/acquisition.py`
- [x] Update imports
- [x] Run tests
- [x] Commit: "Split widgets.py - acquisition widgets"

### Task 0.10: Split widgets.py - display ✅
- [x] Extract `NapariLiveWidget`, `NapariMultiChannelWidget`, `NapariMosaicDisplayWidget` → `widgets/display.py`
- [x] Extract `FocusMapWidget`, `StatsDisplayWidget`, `PlotWidget`, `WaveformDisplay`, `SurfacePlotWidget` → `widgets/display.py`
- [x] Update imports
- [x] Run tests
- [x] Commit: "Split widgets.py - display widgets"

### Task 0.11: Split widgets.py - hardware and wellplate
- [ ] Extract confocal widgets, filter widgets, laser AF widgets → `widgets/hardware.py`
- [ ] Extract well selection, calibration widgets → `widgets/wellplate.py`
- [ ] Extract `FluidicsWidget` → `widgets/fluidics.py`
- [ ] Extract tracking/plate reader widgets → `widgets/tracking.py`
- [ ] Update imports
- [ ] Run tests
- [ ] Commit: "Split widgets.py - hardware and wellplate widgets"

### Task 0.12: Split gui_hcs.py
- [ ] Extract `MovementUpdater`, `QtAutoFocusController`, `QtMultiPointController` → `gui/qt_controllers.py`
- [ ] Extract signal connection logic → `gui/signal_manager.py` (create `SignalManager` class)
- [ ] Extract layout/dock setup → `gui/layout_manager.py` (create `LayoutManager` class)
- [ ] Keep slimmed `HighContentScreeningGui` → `gui/main_window.py`
- [ ] Update `main_hcs.py` imports
- [ ] Run tests
- [ ] Commit: "Split gui_hcs.py into focused modules"

### Task 0.13: Update test structure
- [ ] Create `tests/control/cameras/` with moved tests
- [ ] Create `tests/control/peripherals/` with new tests
- [ ] Create `tests/control/widgets/` with new tests
- [ ] Create `tests/control/gui/` with moved tests
- [ ] Update all test imports
- [ ] Run full test suite
- [ ] Commit: "Update test structure to mirror new organization"

### Task 0.14: Final cleanup
- [ ] Remove empty/deprecated original files
- [ ] Update any remaining imports
- [ ] Run full test suite
- [ ] Run manual smoke test with simulation
- [ ] Commit: "Final cleanup and import fixes"

---

## Files to Split - Detailed Breakdown

### 1. widgets.py (10,671 lines → 10 files)

| New File | Classes | ~Lines |
|----------|---------|--------|
| `widgets/base.py` | WrapperWindow, CollapsibleGroupBox, PandasTableModel | 100 |
| `widgets/config.py` | ConfigEditor, ConfigEditorBackwardsCompatible, ProfileWidget | 200 |
| `widgets/camera.py` | CameraSettingsWidget, LiveControlWidget, RecordingWidget, MultiCameraRecordingWidget | 600 |
| `widgets/stage.py` | NavigationWidget, PiezoWidget, AutoFocusWidget, StageUtils | 450 |
| `widgets/acquisition.py` | FlexibleMultiPointWidget, WellplateMultiPointWidget, MultiPointWithFluidicsWidget | 3,700 |
| `widgets/display.py` | NapariLiveWidget, NapariMultiChannelWidget, NapariMosaicDisplayWidget, FocusMapWidget, StatsDisplayWidget, PlotWidget, WaveformDisplay, SurfacePlotWidget | 1,800 |
| `widgets/hardware.py` | LaserAutofocusSettingWidget, SpinningDiskConfocalWidget, DragonflyConfocalWidget, ObjectivesWidget, DACControWidget, FilterControllerWidget, LaserAutofocusControlWidget, TriggerControlWidget, LedMatrixSettingsDialog | 1,100 |
| `widgets/wellplate.py` | WellSelectionWidget, WellplateFormatWidget, Well1536SelectionWidget, WellplateCalibration, CalibrationLiveViewer, SampleSettingsWidget | 1,200 |
| `widgets/fluidics.py` | FluidicsWidget | 340 |
| `widgets/tracking.py` | TrackingControllerWidget, DisplacementMeasurementWidget, PlateReaderAcquisitionWidget, PlateReaderNavigationWidget, Joystick | 600 |

### 2. control/core/core.py (2,045 lines → 4 files)

| New File | Classes | ~Lines |
|----------|---------|--------|
| `core/stream_handler.py` | QtStreamHandler, ImageSaver, ImageSaver_Tracking | 400 |
| `core/image_display.py` | ImageDisplay, ImageDisplayWindow | 500 |
| `core/tracking.py` | TrackingController, TrackingWorker | 600 |
| `core/focus_map.py` | FocusMap, NavigationViewer | 500 |

### 3. gui_hcs.py (1,635 lines → 4 files)

| New File | Classes/Functions | ~Lines |
|----------|-------------------|--------|
| `gui/main_window.py` | HighContentScreeningGui (slimmed) | 600 |
| `gui/qt_controllers.py` | MovementUpdater, QtAutoFocusController, QtMultiPointController | 170 |
| `gui/signal_manager.py` | SignalManager class (extracted connect logic) | 400 |
| `gui/layout_manager.py` | LayoutManager class (extracted layout logic) | 300 |

### 4. serial_peripherals.py (1,271 lines → 7 files)

| New File | Classes | ~Lines |
|----------|---------|--------|
| `peripherals/serial_base.py` | SerialDevice, SerialDeviceError | 50 |
| `peripherals/lighting/xlight.py` | XLight, XLight_Simulation | 200 |
| `peripherals/lighting/dragonfly.py` | Dragonfly, Dragonfly_Simulation | 200 |
| `peripherals/lighting/ldi.py` | LDI, LDI_Simulation | 200 |
| `peripherals/lighting/cellx.py` | CellX, CellX_Simulation | 300 |
| `peripherals/lighting/sci_led_array.py` | SciMicroscopyLEDArray variants | 250 |
| `peripherals/lighting/__init__.py` | Re-exports | 20 |

### 5. stitcher.py (1,946 lines → 2 files)

| New File | Classes | ~Lines |
|----------|---------|--------|
| `processing/stitcher.py` | Stitcher | 1,000 |
| `processing/coordinate_stitcher.py` | CoordinateStitcher | 900 |

### 6. microcontroller.py (1,248 lines → 2 files)

| New File | Classes | ~Lines |
|----------|---------|--------|
| `stage/microcontroller.py` | Microcontroller, HomingDirection, CommandAborted | 600 |
| `stage/serial.py` | AbstractCephlaMicroSerial, SimSerial, MicrocontrollerSerial | 600 |

---

## Testing Strategy

After each task:
```bash
cd /Users/wea/src/allenlab/Squid/software
pytest --tb=short -v
python main_hcs.py --simulation  # Smoke test
```

---

## Import Update Pattern

When moving a class from `control/widgets.py` to `control/widgets/camera.py`:

**Before:**
```python
from control.widgets import CameraSettingsWidget
```

**After:**
```python
from control.widgets.camera import CameraSettingsWidget
```

**Or via `__init__.py` re-export:**
```python
# control/widgets/__init__.py
from control.widgets.camera import CameraSettingsWidget
# ... etc

# Then in consuming code:
from control.widgets import CameraSettingsWidget  # Still works
```

For clean breaks (our approach), update all imports directly without re-exports.

---

## Risk Mitigation

1. **Low-risk first**: Start with isolated modules (cameras, stitcher) before tackling interconnected ones (widgets, gui)
2. **Incremental commits**: One logical change per commit, tests after each
3. **Smoke test frequently**: Run `python main_hcs.py --simulation` after major changes
4. **Keep original files**: Don't delete original files until all imports are updated and tests pass

---

## Phase 0 Complete

After completing all tasks:

1. Run full test suite:
```bash
pytest --tb=short -v
```

2. Manual smoke test:
```bash
python main_hcs.py --simulation
# Verify application starts
# Run a simple acquisition
# Verify no crashes
```

3. Proceed to Phase 1 (Safety Foundation)
