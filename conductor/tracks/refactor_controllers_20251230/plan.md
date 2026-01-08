# Implementation Plan: Controller Refactoring

## Phase 1: Foundation - `AcquisitionService`
**Goal:** Centralize hardware interaction logic.

- [ ] **Create `AcquisitionService`**:
    - [ ] Create `software/src/squid/backend/services/acquisition_service.py`.
    - [ ] Implement `apply_configuration(config, trigger_mode)`.
    - [ ] Implement `trigger_acquisition(config, trigger_mode)`.
    - [ ] Implement `wait_for_ready()`.
    - [ ] Add unit tests in `software/tests/unit/services/test_acquisition_service.py`.
- [ ] **Migrate `LiveController`**:
    - [ ] Update `LiveController` to accept `AcquisitionService` (optional dependency for now to ease transition).
    - [ ] Replace direct `camera_service` and `illumination_service` calls with `AcquisitionService` methods in `start_live`, `trigger_acquisition`, and `set_microscope_mode`.
    - [ ] Verify `tests/unit/control/core/test_live_controller_services.py` passes.

## Phase 2: Logic Extraction - Managers & Planners
**Goal:** Slim down `MultiPointController` by moving "business logic" out.

- [ ] **Extract `AcquisitionPlanner`**:
    - [ ] Create `software/src/squid/backend/controllers/multipoint/acquisition_planner.py`.
    - [ ] Move `get_estimated_acquisition_disk_storage`, `get_estimated_mosaic_ram_bytes`, and `get_acquisition_image_count` logic here.
    - [ ] Update `MultiPointController` to delegate to this class.
    - [ ] Add unit tests.
- [ ] **Extract `ExperimentManager`**:
    - [ ] Create `software/src/squid/backend/controllers/multipoint/experiment_manager.py`.
    - [ ] Move `start_new_experiment`, `_start_per_acquisition_log`, and metadata writing logic here.
    - [ ] Update `MultiPointController` to delegate to this class.

## Phase 3: Core Refactoring - `MultiPointWorker`
**Goal:** Decouple `MultiPointWorker` from low-level details.

- [ ] **Integrate `AcquisitionService`**:
    - [ ] Pass `AcquisitionService` to `MultiPointWorker`.
    - [ ] Replace `acquire_camera_image` logic with calls to `AcquisitionService.apply_configuration` and `AcquisitionService.trigger_acquisition`.
- [ ] **Extract `PlateViewHandler`**:
    - [ ] Create a separate class/listener that subscribes to `DownsampledViewResult` and updates the `DownsampledViewManager`.
    - [ ] Remove `_process_downsampled_view_result` and `_initialize_plate_view` from `MultiPointWorker`.

## Phase 4: Loop Decomposition (Optional/Stretch)
**Goal:** Break the nested loops in `MultiPointWorker.run` into composed tasks.

- [ ] Create `AcquisitionTask` interface.
- [ ] Implement `TimepointSequence` and `RegionSequence`.
- [ ] Refactor `run` to execute these sequences.

## Phase 5: Verification
- [ ] Run full integration test suite: `software/tests/integration/control/test_MultiPointController.py`.
- [ ] Manual verification (if possible/requested).
