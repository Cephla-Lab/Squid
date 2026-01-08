# Tissue Planning Implementation Checklist

## Phase 1: Events and Data Structures
- [ ] Add `SaveTissuePlanningSessionCommand` to `core/events.py`
- [ ] Add `LoadTissuePlanningSessionCommand` to `core/events.py`
- [ ] Add `TissuePlanningSessionSaved` to `core/events.py`
- [ ] Add `TissuePlanningSessionLoaded` to `core/events.py`

## Phase 2: IO Module
- [ ] Create `backend/io/tissue_session_io.py`
- [ ] Implement `PolygonRegion` dataclass
- [ ] Implement `TissueSessionData` dataclass
- [ ] Implement `_save_mosaic()` - multi-page TIFF with tifffile
- [ ] Implement `_load_mosaic()` - read TIFF and metadata
- [ ] Implement `_save_regions()` - JSON polygon vertices
- [ ] Implement `_load_regions()` - parse JSON
- [ ] Implement `_save_coordinates()` - CSV in fluidics format
- [ ] Implement `_load_coordinates()` - parse CSV to dict
- [ ] Implement `_save_metadata()` - session settings JSON
- [ ] Implement `_load_metadata()` - parse settings
- [ ] Implement `save_session()` - orchestrate all saves
- [ ] Implement `load_session()` - orchestrate all loads
- [ ] Export from `backend/io/__init__.py`
- [ ] Unit tests for save/load round-trip

## Phase 3: Session Manager
- [ ] Create `backend/managers/tissue_planning_session.py`
- [ ] Implement `TissuePlanningSession.__init__()` with event subscriptions
- [ ] Implement `set_mosaic_state()` - receive mosaic data from UI
- [ ] Implement `_on_shapes_changed()` - store polygon regions
- [ ] Implement `_on_objective_changed()` - trigger tile recalculation
- [ ] Implement `_recalculate_tiles()` - use `get_points_for_manual_region()`
- [ ] Implement `_on_save()` - save session command handler
- [ ] Implement `_on_load()` - load session command handler
- [ ] Export from `backend/managers/__init__.py`

## Phase 4: UI Integration
- [ ] Add Save button to `napari_mosaic.py`
- [ ] Add Load button to `napari_mosaic.py`
- [ ] Implement `_on_save_session_clicked()` - file dialog + publish command
- [ ] Implement `_on_load_session_clicked()` - file dialog + publish command
- [ ] Implement `_push_mosaic_state_to_manager()` - extract compositor state
- [ ] Implement `restore_mosaic_from_session()` - restore napari layers
- [ ] Subscribe to `TissuePlanningSessionLoaded` for UI restoration

## Phase 5: Wiring
- [ ] Instantiate `TissuePlanningSession` in application.py
- [ ] Wire dependencies (event_bus, scan_coordinates, objective_store, camera)

## Phase 6: Testing
- [ ] Unit tests for `tissue_session_io.py`
- [ ] Integration test: save → load round-trip
- [ ] Manual test: full workflow in simulation mode
  - [ ] Create mosaic scan
  - [ ] Draw polygon regions
  - [ ] Save session
  - [ ] Load session (verify mosaic + coordinates restored)
  - [ ] Switch objective (verify tile recalculation)
  - [ ] Load coordinates.csv into fluidics_multipoint

## Optional Enhancements
- [ ] Add overlap % control to UI
- [ ] Show tile count preview when polygon drawn
- [ ] Add region naming/editing UI
- [ ] Persist last save/load path
