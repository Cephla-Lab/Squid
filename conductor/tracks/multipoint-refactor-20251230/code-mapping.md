# Code Mapping: Current Files → New Components

This document maps specific methods and line ranges from the current files to the proposed new components.

---

## 1. AcquisitionService (services layer)

**New file:** `software/src/squid/backend/services/acquisition_service.py`

### From `live_controller.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `turn_on_illumination()` | 213-229 | `AcquisitionService.turn_on_illumination(config)` |
| `turn_off_illumination()` | 231-244 | `AcquisitionService.turn_off_illumination(config)` |
| `update_illumination()` | 246-294 | `AcquisitionService.apply_configuration()` (power + filter) |
| `set_microscope_mode()` | 548-577 | `AcquisitionService.apply_configuration()` (exposure/gain part) |
| `_get_illumination_channel()` | 179-198 | `AcquisitionService._get_illumination_channel(config)` |
| `_get_illumination_intensity()` | 200-211 | `AcquisitionService._get_illumination_intensity(config)` |

### From `multi_point_worker.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `_apply_channel_mode()` | 1070-1107 | `AcquisitionService.apply_configuration()` |
| `_turn_on_illumination()` | 1109-1118 | `AcquisitionService.turn_on_illumination(config)` |
| `_turn_off_illumination()` | 1120-1128 | `AcquisitionService.turn_off_illumination(config)` |
| `_select_config()` | 1066-1068 | `AcquisitionService.apply_configuration()` + wait |

### Shared Interface Design

```python
class AcquisitionService:
    def apply_configuration(
        self,
        config: ChannelMode,
        trigger_mode: TriggerMode,
        enable_filter_switching: bool = True,
    ) -> None:
        # Lines from worker 1070-1107 + live 559-576
        # Sets: exposure, gain, illumination power, filter position

    def turn_on_illumination(self, config: ChannelMode) -> None:
        # Lines from worker 1109-1118, live 213-229

    def turn_off_illumination(self, config: ChannelMode) -> None:
        # Lines from worker 1120-1128, live 231-244

    @contextmanager
    def illumination_context(self, config: ChannelMode, trigger_mode: TriggerMode):
        # Pattern from worker 1370-1467 (software trigger illumination on/off)
```

---

## 2. ExperimentManager (controller layer)

**New file:** `software/src/squid/backend/controllers/multipoint/experiment_manager.py`

### From `multi_point_controller.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `start_new_experiment()` | 378-440 | `ExperimentManager.start_experiment()` |
| `_start_per_acquisition_log()` | 219-240 | `ExperimentManager.start_logging()` |
| `_stop_per_acquisition_log()` | 242-254 | `ExperimentManager.stop_logging()` |
| Metadata writing (in start_new_experiment) | 390-440 | `ExperimentManager._write_metadata()` |

### Extracted Code Structure

```python
@dataclass
class ExperimentContext:
    experiment_path: str
    experiment_id: str
    log_handler: Optional[logging.Handler]

class ExperimentManager:
    def start_experiment(
        self,
        base_path: str,
        experiment_id: str,
        configurations: List[ChannelMode],
        acquisition_params: AcquisitionParameters,
        objective_store: ObjectiveStore,
        camera_service: CameraService,
    ) -> ExperimentContext:
        # Lines 378-440: folder creation, metadata, config.xml

    def finalize_experiment(self, context: ExperimentContext) -> None:
        # Lines 242-254: close log handler
```

---

## 3. AcquisitionPlanner (controller layer)

**New file:** `software/src/squid/backend/controllers/multipoint/acquisition_planner.py`

### From `multi_point_controller.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `get_acquisition_image_count()` | 453-497 | `AcquisitionPlanner.calculate_image_count()` |
| `get_estimated_acquisition_disk_storage()` | 521-580 | `AcquisitionPlanner.estimate_disk_storage_bytes()` |
| `get_estimated_mosaic_ram_bytes()` | 582-654 | `AcquisitionPlanner.estimate_mosaic_ram_bytes()` |
| `validate_acquisition_settings()` | 1099-1113 | `AcquisitionPlanner.validate_settings()` |
| `_temporary_get_an_image_hack()` | 499-519 | Keep in controller or move to planner |

### Extracted Code Structure

```python
class AcquisitionPlanner:
    def calculate_image_count(
        self,
        scan_coordinates: ScanCoordinates,
        Nt: int,
        NZ: int,
        selected_configurations: List[ChannelMode],
    ) -> int:
        # Lines 453-497

    def estimate_disk_storage_bytes(
        self,
        image_count: int,
        sample_image: np.ndarray,
        config: ChannelMode,
    ) -> int:
        # Lines 521-580

    def estimate_mosaic_ram_bytes(
        self,
        scan_coordinates: ScanCoordinates,
        objective_store: ObjectiveStore,
        camera_service: CameraService,
        selected_configurations: List[ChannelMode],
    ) -> int:
        # Lines 582-654

    def validate_settings(
        self,
        do_reflection_af: bool,
        laser_af_controller: Optional[LaserAutofocusController],
    ) -> List[str]:  # Returns list of error messages
        # Lines 1099-1113
```

---

## 4. PositionController + ZStackExecutor (controller layer)

**New file:** `software/src/squid/backend/controllers/multipoint/position_zstack.py`

### From `multi_point_worker.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `move_to_coordinate()` | 775-812 | `PositionController.move_to_coordinate()` |
| `move_to_z_level()` | 814-817 | `PositionController.move_z_with_stabilization()` |
| `initialize_z_stack()` | 733-743 | `ZStackExecutor.initialize()` |
| `prepare_z_stack()` | 1168-1174 | `ZStackExecutor.prepare_for_stack()` |
| `move_z_for_stack()` | 1693-1703 | `ZStackExecutor.advance_z_level()` |
| `move_z_back_after_stack()` | 1705-1722 | `ZStackExecutor.return_after_stack()` |
| `handle_z_offset()` | 1176-1186 | `ZStackExecutor.apply_z_offset()` |

### Duplicated Pattern (4 instances)

```python
# Pattern appears at lines: 780-785, 816-817, 1172-1174, 1702-1703
self._stage_move_*(value)
self._sleep(SCAN_STABILIZATION_TIME_MS_* / 1000)
```

### Extracted Code Structure

```python
class PositionController:
    STABILIZATION_MS = {
        'x': SCAN_STABILIZATION_TIME_MS_X,  # 90
        'y': SCAN_STABILIZATION_TIME_MS_Y,  # 90
        'z': SCAN_STABILIZATION_TIME_MS_Z,  # 20
    }

    def __init__(self, stage_service: StageService):
        self._stage = stage_service

    def move_to_coordinate(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: Optional[float] = None,
    ) -> None:
        # Lines 775-812: move X, wait, move Y, wait, optionally move Z

    def move_z_with_stabilization(self, z_mm: float) -> None:
        # Lines 814-817
        self._stage.move_z_to(z_mm)
        self._stage.wait_for_idle()
        time.sleep(self.STABILIZATION_MS['z'] / 1000)

class ZStackExecutor:
    def __init__(
        self,
        position_controller: PositionController,
        piezo_service: Optional[PiezoService],
    ):
        self._position = position_controller
        self._piezo = piezo_service

    def initialize(self, params: ZStackParameters) -> float:
        # Lines 733-743

    def prepare_for_stack(self, params: ZStackParameters) -> None:
        # Lines 1168-1174

    def advance_z_level(self, params: ZStackParameters) -> None:
        # Lines 1693-1703 (piezo or stage path)

    def return_after_stack(self, params: ZStackParameters) -> None:
        # Lines 1705-1722

    def apply_z_offset(self, config: ChannelMode, apply: bool) -> None:
        # Lines 1176-1186
```

---

## 5. FocusMapGenerator + AutofocusExecutor (controller layer)

**New file:** `software/src/squid/backend/controllers/multipoint/focus_operations.py`

### From `multi_point_controller.py`

| Code Section | Lines | Maps To |
|--------------|-------|---------|
| Focus map application (focus_map case) | 785-797 | `FocusMapGenerator.apply_to_coordinates()` |
| Focus map generation (gen_focus_map case) | 799-881 | `FocusMapGenerator.generate_from_bounds()` |
| Focus map save/restore | 851-856, 1014-1018 | `FocusMapContext` context manager |
| Grid calculation | 822-848 | `FocusMapGenerator._calculate_grid()` |

### From `multi_point_worker.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `perform_autofocus()` | 1131-1166 | `AutofocusExecutor.perform_autofocus()` |

### Extracted Code Structure

```python
@dataclass
class FocusMapContext:
    """Context manager for saving/restoring focus map state."""
    focus_map_coords: List[Tuple[float, float, float]]
    use_focus_map: bool

    @classmethod
    @contextmanager
    def preserve(cls, autofocus_controller: AutoFocusController):
        # Lines 851-856: save existing map
        # yield
        # Lines 1014-1018: restore map

class FocusMapGenerator:
    def __init__(self, autofocus_controller: AutoFocusController, stage_service: StageService):
        ...

    def apply_to_coordinates(
        self,
        focus_map: FocusSurface,
        scan_coords: Dict[str, List[Tuple[float, ...]]],
        scan_coordinates_target: ScanCoordinates,
    ) -> None:
        # Lines 785-797: interpolate Z for each coordinate

    def generate_from_bounds(
        self,
        bounds: Dict[str, Tuple[float, float]],
        dx: float,
        dy: float,
    ) -> bool:
        # Lines 799-881: calculate grid, run gen_focus_map

class AutofocusExecutor:
    def should_autofocus(
        self,
        af_fov_count: int,
        NZ: int,
        z_stacking_config: str,
        do_autofocus: bool,
        af_interval: int = NUMBER_OF_FOVS_PER_AF,
    ) -> bool:
        # Lines 1132-1137: determine if AF should run

    def perform_autofocus(
        self,
        do_reflection_af: bool,
        region_id: str,
        fov: int,
    ) -> bool:
        # Lines 1131-1166: run contrast or laser AF
```

---

## 6. ProgressTracker + CoordinateTracker (controller layer)

**New file:** `software/src/squid/backend/controllers/multipoint/progress_tracking.py`

### From `multi_point_worker.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `_publish_acquisition_started()` | 354-365 | `ProgressTracker.start_acquisition()` |
| `_publish_acquisition_finished()` | 367-381 | `ProgressTracker.finish_acquisition()` |
| `_publish_acquisition_progress()` | 383-423 | `ProgressTracker.update_progress()` |
| `_publish_worker_progress()` | 425-451 | Merged into `update_progress()` |
| `initialize_coordinates_dataframe()` | 744-749 | `CoordinateTracker.initialize()` |
| `update_coordinates_dataframe()` | 751-773 | `CoordinateTracker.record_position()` |
| `_last_time_point_z_pos` dict usage | 788-795, 987-988 | `CoordinateTracker.get/set_last_z_position()` |

### Extracted Code Structure

```python
class ProgressTracker:
    def __init__(self, event_bus: EventBus, experiment_id: str):
        self._event_bus = event_bus
        self._experiment_id = experiment_id
        self._start_time: Optional[float] = None

    def start_acquisition(self) -> None:
        # Lines 354-365

    def update_progress(
        self,
        current_fov: int,
        total_fovs: int,
        current_region: int,
        total_regions: int,
        current_channel: str,
        current_timepoint: int,
        total_timepoints: int,
    ) -> None:
        # Lines 383-451: calculate progress %, ETA, publish events

    def finish_acquisition(self, success: bool, error: Optional[Exception] = None) -> None:
        # Lines 367-381

class CoordinateTracker:
    def __init__(self):
        self._coordinates_df: Optional[pd.DataFrame] = None
        self._last_time_point_z_pos: Dict[Tuple[str, int], float] = {}

    def initialize(self, use_piezo: bool) -> None:
        # Lines 744-749

    def record_position(
        self,
        region_id: str,
        fov: int,
        z_level: int,
        pos: Pos,
        z_piezo_um: Optional[float] = None,
    ) -> None:
        # Lines 751-773

    def save_to_csv(self, path: str) -> None:
        self._coordinates_df.to_csv(path, index=False, header=True)

    def get_last_z_position(self, region_id: str, fov: int) -> Optional[float]:
        return self._last_time_point_z_pos.get((region_id, fov))

    def set_last_z_position(self, region_id: str, fov: int, z_mm: float) -> None:
        self._last_time_point_z_pos[(region_id, fov)] = z_mm
```

---

## 7. ImageCaptureExecutor (controller layer)

**New file:** `software/src/squid/backend/controllers/multipoint/image_capture.py`

### From `multi_point_worker.py`

| Method | Lines | Maps To |
|--------|-------|---------|
| `acquire_camera_image()` | 1356-1467 | `ImageCaptureExecutor.capture_single_image()` |
| CaptureInfo construction #1 | 1407-1428 | `build_capture_info()` factory |
| CaptureInfo construction #2 (RGB) | 1524-1545 | `build_capture_info()` factory |
| NL5 handling | 1374-1385 | `ImageCaptureExecutor._handle_nl5()` |
| Frame wait logic | 1386-1464 | `ImageCaptureExecutor._wait_for_frame()` |

### Extracted Code Structure

```python
@dataclass
class CaptureContext:
    """All context needed to create a CaptureInfo."""
    position: Pos
    z_index: int
    config: ChannelMode
    save_directory: str
    file_id: str
    region_id: str
    fov: int
    config_idx: int
    time_point: int
    # Metadata
    Nt: int
    NZ: int
    selected_configurations: List[ChannelMode]
    experiment_path: str
    time_increment_s: Optional[float]
    physical_size_z_um: Optional[float]
    pixel_size_um: Optional[float]
    use_piezo: bool
    z_piezo_um: Optional[float]

def build_capture_info(context: CaptureContext) -> CaptureInfo:
    """Factory function - consolidates lines 1407-1428 and 1524-1545."""
    return CaptureInfo(
        position=context.position,
        z_index=context.z_index,
        capture_time=time.time(),
        z_piezo_um=context.z_piezo_um if context.use_piezo else None,
        configuration=context.config,
        save_directory=context.save_directory,
        file_id=context.file_id,
        region_id=context.region_id,
        fov=context.fov,
        configuration_idx=context.config_idx,
        time_point=context.time_point,
        total_time_points=context.Nt,
        total_z_levels=context.NZ,
        total_channels=len(context.selected_configurations),
        channel_names=[cfg.name for cfg in context.selected_configurations],
        experiment_path=context.experiment_path,
        time_increment_s=context.time_increment_s,
        physical_size_z_um=context.physical_size_z_um,
        physical_size_x_um=context.pixel_size_um,
        physical_size_y_um=context.pixel_size_um,
    )

class ImageCaptureExecutor:
    def __init__(
        self,
        acquisition_service: AcquisitionService,
        camera_service: CameraService,
        nl5_service: Optional[NL5Service],
    ):
        ...

    def capture_single_image(
        self,
        context: CaptureContext,
        trigger_mode: TriggerMode,
        ready_flag: ThreadSafeFlag,
        capture_info_holder: ThreadSafeValue[CaptureInfo],
    ) -> None:
        # Lines 1356-1467: the main capture logic
        # Uses AcquisitionService for illumination
```

---

## 8. Service Wrapper Removal

### Methods to DELETE from `multi_point_worker.py` (lines 254-338)

Replace all calls to these wrappers with direct service calls:

| Wrapper Method | Line | Replace With |
|----------------|------|--------------|
| `_camera_get_pixel_size_binned_um()` | 254-255 | `self._camera_service.get_pixel_size_binned_um()` |
| `_camera_add_frame_callback()` | 276-277 | `self._camera_service.add_frame_callback()` |
| `_camera_remove_frame_callback()` | 279-280 | `self._camera_service.remove_frame_callback()` |
| `_camera_start_streaming()` | 282-283 | `self._camera_service.start_streaming()` |
| `_camera_stop_streaming()` | 285-286 | `self._camera_service.stop_streaming()` |
| `_camera_send_trigger()` | 288-289 | `self._camera_service.send_trigger()` |
| `_camera_get_ready_for_trigger()` | 291-292 | `self._camera_service.get_ready_for_trigger()` |
| `_camera_get_total_frame_time()` | 294-295 | `self._camera_service.get_total_frame_time()` |
| `_camera_get_strobe_time()` | 297-298 | `self._camera_service.get_strobe_time()` |
| `_camera_read_frame()` | 300-301 | `self._camera_service.read_frame()` |
| `_camera_get_exposure_time()` | 303-304 | `self._camera_service.get_exposure_time()` |
| `_stage_get_pos()` | 306-307 | `self._stage_service.get_position()` |
| `_stage_move_x_to()` | 309-311 | `self._stage_service.move_x_to()` |
| `_stage_move_y_to()` | 313-315 | `self._stage_service.move_y_to()` |
| `_stage_move_z_to()` | 317-319 | `self._stage_service.move_z_to()` |
| `_stage_move_z()` | 321-323 | `self._stage_service.move_z()` |
| `_peripheral_enable_joystick()` | 325-326 | `self._peripheral_service.enable_joystick()` |
| `_peripheral_wait_till_operation_is_completed()` | 328-329 | `self._peripheral_service.wait_till_operation_is_completed()` |
| `_piezo_get_position()` | 331-334 | `self._piezo_service.get_position()` |
| `_piezo_move_to()` | 336-338 | `self._piezo_service.move_to()` |

---

## Summary: Line Count Impact

### multi_point_controller.py (1,268 lines)

| Extraction | Lines Removed | Target |
|------------|---------------|--------|
| ExperimentManager | ~80 | experiment_manager.py |
| AcquisitionPlanner | ~130 | acquisition_planner.py |
| FocusMapGenerator | ~95 | focus_operations.py |
| State publishing cleanup | ~30 | (inline simplification) |
| **Total Removed** | **~335** | |
| **Remaining** | **~933** | |

### multi_point_worker.py (1,723 lines)

| Extraction | Lines Removed | Target |
|------------|---------------|--------|
| Service wrappers | ~85 | DELETE |
| ProgressTracker | ~100 | progress_tracking.py |
| CoordinateTracker | ~50 | progress_tracking.py |
| PositionController | ~60 | position_zstack.py |
| ZStackExecutor | ~80 | position_zstack.py |
| AutofocusExecutor | ~40 | focus_operations.py |
| ImageCaptureExecutor | ~120 | image_capture.py |
| Illumination methods | ~40 | AcquisitionService |
| **Total Removed** | **~575** | |
| **Remaining** | **~1,148** | |

### live_controller.py (757 lines)

| Extraction | Lines Removed | Target |
|------------|---------------|--------|
| Illumination methods | ~80 | AcquisitionService |
| update_illumination() filter part | ~30 | AcquisitionService |
| **Total Removed** | **~110** | |
| **Remaining** | **~647** | |
