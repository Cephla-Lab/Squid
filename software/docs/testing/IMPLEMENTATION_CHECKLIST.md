# Integration Testing Implementation Checklist

This checklist tracks the implementation progress for the comprehensive integration testing plan. Each item should be checked off as it is completed.

---

## Phase 0: Documentation & Planning

- [x] Analyze existing codebase structure
- [x] Inventory existing simulators and completeness
- [x] Inventory existing test fixtures
- [x] Inventory widgets requiring tests
- [x] Create comprehensive testing plan (`INTEGRATION_TESTING_PLAN.md`)
- [x] Define test specifications for all categories
- [x] Create implementation checklist (this document)

---

## Phase 1: Infrastructure Setup

### 1.1 Test Utilities Module

| Task | Status | File | Notes |
|------|--------|------|-------|
| Create utils package init | [ ] | `tests/utils/__init__.py` | |
| Implement wait_for_event() | [ ] | `tests/utils/wait_helpers.py` | Block until event received |
| Implement wait_for_condition() | [ ] | `tests/utils/wait_helpers.py` | Poll until condition True |
| Implement capture_events() | [ ] | `tests/utils/wait_helpers.py` | Context manager |
| Implement EventCapture class | [ ] | `tests/utils/wait_helpers.py` | Multi-event capture |
| Create widget test helpers | [ ] | `tests/utils/widget_test_helpers.py` | Widget instantiation helpers |

### 1.2 Enhanced Test Fixtures

| Task | Status | File | Notes |
|------|--------|------|-------|
| Add enhanced qtbot fixture | [ ] | `tests/conftest.py` | Mouse/key simulation |
| Add camera service fixture | [ ] | `tests/conftest.py` | With event bus injection |
| Add stage service fixture | [ ] | `tests/conftest.py` | With event bus injection |
| Add peripheral service fixture | [ ] | `tests/conftest.py` | With event bus injection |
| Add trigger service fixture | [ ] | `tests/conftest.py` | With event bus injection |
| Add live service fixture | [ ] | `tests/conftest.py` | With event bus injection |
| Add EventCapture fixture | [ ] | `tests/conftest.py` | Convenience wrapper |
| Add navigation_widget fixture | [ ] | `tests/conftest.py` | With event bus |
| Add dac_widget fixture | [ ] | `tests/conftest.py` | With event bus |

### 1.3 pytest Configuration

| Task | Status | File | Notes |
|------|--------|------|-------|
| Add test markers | [ ] | `pyproject.toml` | unit, integration, qt, slow, e2e |
| Configure default options | [ ] | `pyproject.toml` | -v, --tb=short, -ra |
| Add timeout configuration | [ ] | `pyproject.toml` | 60 seconds default |
| Configure warning filters | [ ] | `pyproject.toml` | Ignore pyqtgraph deprecations |

---

## Phase 2: Event Bus Refactoring

### 2.1 Widget Refactoring for Event Bus Injection

| Widget | Status | File | Complexity |
|--------|--------|------|------------|
| NavigationWidget | [ ] | `control/widgets/stage/navigation.py` | Low |
| DACControWidget | [ ] | `control/widgets/hardware/dac.py` | Low |
| TriggerControlWidget | [ ] | `control/widgets/hardware/trigger.py` | Low |
| CameraSettingsWidget | [ ] | `control/widgets/camera/settings.py` | Medium |
| LiveControlWidget | [ ] | `control/widgets/camera/live_control.py` | Medium |
| LaserAutofocusSettingWidget | [ ] | `control/widgets/hardware/laser_autofocus.py` | Medium |
| WellplateFormatWidget | [ ] | `control/widgets/wellplate/format.py` | Low |
| TrackingControllerWidget | [ ] | `control/widgets/tracking/controller.py` | High |

### 2.2 Verify Backward Compatibility

| Task | Status | Notes |
|------|--------|-------|
| Verify widgets work with global event bus | [ ] | Default behavior preserved |
| Verify ApplicationContext creates widgets correctly | [ ] | No regressions |
| Run existing GUI integration test | [ ] | `test_HighContentScreeningGui.py` passes |

---

## Phase 3: Service Layer Tests

### 3.1 Unit Tests for New Services

| Service | Status | File | Test Count |
|---------|--------|------|------------|
| TriggerService | [ ] | `tests/unit/squid/services/test_trigger_service.py` | ~15 |
| MicroscopeModeService | [ ] | `tests/unit/squid/services/test_microscope_mode_service.py` | ~12 |
| LiveService | [ ] | `tests/unit/squid/services/test_live_service.py` | ~18 |

**TriggerService Tests:**
- [ ] test_set_trigger_mode_command_handled
- [ ] test_set_trigger_fps_command_handled
- [ ] test_start_camera_trigger_command_handled
- [ ] test_stop_camera_trigger_command_handled
- [ ] test_trigger_mode_changed_published
- [ ] test_trigger_fps_changed_published
- [ ] test_switch_software_to_hardware_mode
- [ ] test_switch_hardware_to_continuous_mode
- [ ] test_switch_continuous_to_software_mode
- [ ] test_fps_clamped_to_min
- [ ] test_fps_clamped_to_max
- [ ] test_invalid_mode_raises_error
- [ ] test_service_handles_exception_gracefully
- [ ] test_shutdown_stops_trigger
- [ ] test_unsubscribes_on_shutdown

**MicroscopeModeService Tests:**
- [ ] test_set_microscope_mode_command_handled
- [ ] test_microscope_mode_changed_published
- [ ] test_switch_to_brightfield
- [ ] test_switch_to_fluorescence
- [ ] test_switch_to_confocal
- [ ] test_objective_change_triggers_mode_update
- [ ] test_mode_change_validates_objective_compatibility
- [ ] test_invalid_configuration_raises_error
- [ ] test_missing_configuration_handled
- [ ] test_shutdown_restores_default_mode
- [ ] test_unsubscribes_on_shutdown

**LiveService Tests:**
- [ ] test_start_live_command_handled
- [ ] test_stop_live_command_handled
- [ ] test_live_state_changed_published_on_start
- [ ] test_live_state_changed_published_on_stop
- [ ] test_start_live_with_configuration
- [ ] test_start_live_without_configuration
- [ ] test_configuration_change_during_live
- [ ] test_start_when_already_live
- [ ] test_stop_when_not_live
- [ ] test_is_live_property
- [ ] test_frame_callback_called
- [ ] test_frame_callback_with_exposure_change
- [ ] test_start_stop_rapid_succession
- [ ] test_multiple_start_commands
- [ ] test_camera_error_during_live
- [ ] test_graceful_recovery_from_error
- [ ] test_shutdown_stops_live
- [ ] test_unsubscribes_on_shutdown

### 3.2 Enhance Existing Service Tests

| Service | Status | File | Additional Tests |
|---------|--------|------|-----------------|
| CameraService | [ ] | `tests/unit/squid/services/test_camera_service.py` | ~5 error handling |
| StageService | [ ] | `tests/unit/squid/services/test_stage_service.py` | ~5 limit enforcement |
| PeripheralService | [ ] | `tests/unit/squid/services/test_peripheral_service.py` | ~5 AF laser, joystick |

### 3.3 Integration Tests for New Services

| Service | Status | File | Test Count |
|---------|--------|------|------------|
| TriggerService | [ ] | `tests/integration/squid/services/test_trigger_service_integration.py` | ~10 |
| MicroscopeModeService | [ ] | `tests/integration/squid/services/test_microscope_mode_integration.py` | ~8 |
| LiveService | [ ] | `tests/integration/squid/services/test_live_service_integration.py` | ~12 |

---

## Phase 4: Widget Unit Tests

### 4.1 Camera Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| LiveControlWidget | [ ] | `tests/unit/control/widgets/camera/test_live_control.py` | ~15 |
| CameraSettingsWidget | [ ] | `tests/unit/control/widgets/camera/test_settings.py` | ~12 |
| RecordingWidget | [ ] | `tests/unit/control/widgets/camera/test_recording.py` | ~10 |

### 4.2 Stage Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| NavigationWidget | [ ] | `tests/unit/control/widgets/stage/test_navigation.py` | ~20 |
| AutofocusWidget | [ ] | `tests/unit/control/widgets/stage/test_autofocus.py` | ~8 |
| PiezoWidget | [ ] | `tests/unit/control/widgets/stage/test_piezo.py` | ~6 |
| StageUtilsWidget | [ ] | `tests/unit/control/widgets/stage/test_utils.py` | ~8 |

**NavigationWidget Tests:**
- [ ] test_forward_x_button_publishes_move_command
- [ ] test_backward_x_button_publishes_negative_move
- [ ] test_forward_y_button_publishes_move_command
- [ ] test_backward_y_button_publishes_negative_move
- [ ] test_forward_z_button_publishes_move_command
- [ ] test_backward_z_button_publishes_negative_move
- [ ] test_delta_x_spinbox_affects_move_distance
- [ ] test_delta_y_spinbox_affects_move_distance
- [ ] test_delta_z_spinbox_converts_um_to_mm
- [ ] test_delta_rounds_to_microstep_boundary
- [ ] test_position_changed_event_updates_labels
- [ ] test_timer_updates_position_display
- [ ] test_z_label_displays_in_um
- [ ] test_click_to_move_checkbox_default_unchecked
- [ ] test_click_to_move_checkbox_toggle
- [ ] test_uses_injected_event_bus
- [ ] test_unsubscribes_on_close
- [ ] test_full_configuration_shows_all_controls
- [ ] test_minimal_configuration_hides_extras
- [ ] test_timer_stopped_on_cleanup

### 4.3 Hardware Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| DACControWidget | [ ] | `tests/unit/control/widgets/hardware/test_dac.py` | ~15 |
| TriggerControlWidget | [ ] | `tests/unit/control/widgets/hardware/test_trigger.py` | ~12 |
| LaserAutofocusSettingWidget | [ ] | `tests/unit/control/widgets/hardware/test_laser_autofocus.py` | ~10 |
| FilterControllerWidget | [ ] | `tests/unit/control/widgets/hardware/test_filter_controller.py` | ~8 |
| SpinningDiskConfocalWidget | [ ] | `tests/unit/control/widgets/hardware/test_confocal.py` | ~6 |
| ObjectivesWidget | [ ] | `tests/unit/control/widgets/hardware/test_objectives.py` | ~6 |

**DACControWidget Tests:**
- [ ] test_channel0_slider_publishes_set_dac_command
- [ ] test_channel1_slider_publishes_set_dac_command
- [ ] test_channel0_spinbox_publishes_set_dac_command
- [ ] test_channel1_spinbox_publishes_set_dac_command
- [ ] test_slider_change_updates_spinbox
- [ ] test_spinbox_change_updates_slider
- [ ] test_dac_value_changed_updates_ui
- [ ] test_dac_value_changed_blocks_signals
- [ ] test_external_change_doesnt_trigger_command
- [ ] test_value_clamped_to_0_100
- [ ] test_slider_range_0_100
- [ ] test_uses_injected_event_bus
- [ ] test_unsubscribes_on_close
- [ ] test_channel0_and_channel1_independent

### 4.4 Wellplate Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| WellplateFormatWidget | [ ] | `tests/unit/control/widgets/wellplate/test_format.py` | ~10 |
| CalibrationLiveViewer | [ ] | `tests/unit/control/widgets/wellplate/test_calibration.py` | ~12 |
| WellSelectionWidget | [ ] | `tests/unit/control/widgets/wellplate/test_well_selection.py` | ~8 |

### 4.5 Acquisition Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| WellplateMultiPointWidget | [ ] | `tests/unit/control/widgets/acquisition/test_wellplate_multipoint.py` | ~15 |
| FlexibleMultiPointWidget | [ ] | `tests/unit/control/widgets/acquisition/test_flexible_multipoint.py` | ~12 |

### 4.6 Display Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| NapariLiveViewer | [ ] | `tests/unit/control/widgets/display/test_napari_live.py` | ~10 |
| NapariMosaicDisplayWidget | [ ] | `tests/unit/control/widgets/display/test_napari_mosaic.py` | ~8 |
| PlottingWidget | [ ] | `tests/unit/control/widgets/display/test_plotting.py` | ~6 |

### 4.7 Tracking Widget Tests

| Widget | Status | File | Test Count |
|--------|--------|------|------------|
| TrackingControllerWidget | [ ] | `tests/unit/control/widgets/tracking/test_controller.py` | ~10 |
| JoystickWidget | [ ] | `tests/unit/control/widgets/tracking/test_joystick.py` | ~6 |

---

## Phase 5: Widget-Service Integration Tests

| Test File | Status | Test Count |
|-----------|--------|------------|
| test_camera_widget_integration.py | [ ] | ~25 |
| test_stage_widget_integration.py | [ ] | ~20 |
| test_hardware_widget_integration.py | [ ] | ~20 |
| test_wellplate_widget_integration.py | [ ] | ~15 |
| test_acquisition_widget_integration.py | [ ] | ~15 |

**Camera Widget Integration Tests:**
- [ ] test_exposure_change_updates_camera_and_ui
- [ ] test_gain_change_updates_camera_and_ui
- [ ] test_live_start_triggers_camera_streaming
- [ ] test_live_stop_stops_camera_streaming
- [ ] test_exposure_spinbox_round_trip
- [ ] test_gain_spinbox_round_trip
- [ ] test_binning_dropdown_round_trip
- [ ] test_widget_exposure_to_camera_to_event_to_widget
- [ ] test_widget_gain_to_camera_to_event_to_widget
- [ ] test_programmatic_and_ui_exposure_change
- [ ] test_rapid_exposure_changes
- [ ] test_camera_error_reflected_in_widget

**Stage Widget Integration Tests:**
- [ ] test_button_click_moves_stage
- [ ] test_stage_position_updates_label
- [ ] test_click_to_move_triggers_stage_movement
- [ ] test_button_to_stage_to_event_to_label
- [ ] test_home_button_homes_stage
- [ ] test_zero_button_zeros_stage
- [ ] test_loading_position_button_workflow
- [ ] test_scanning_position_button_workflow
- [ ] test_um_to_mm_conversion_accurate
- [ ] test_position_display_precision

---

## Phase 6: E2E Workflow Tests

| Test File | Status | Test Count |
|-----------|--------|------------|
| test_live_acquisition.py | [ ] | ~12 |
| test_multipoint_acquisition.py | [ ] | ~15 |
| test_wellplate_scanning.py | [ ] | ~12 |
| test_autofocus_workflow.py | [ ] | ~10 |
| test_full_acquisition_pipeline.py | [ ] | ~8 |

**Live Acquisition Workflow Tests:**
- [ ] test_start_live_receives_frames
- [ ] test_stop_live_stops_frames
- [ ] test_exposure_change_during_live
- [ ] test_gain_change_during_live
- [ ] test_configuration_switch_during_live
- [ ] test_frame_rate_matches_trigger_fps
- [ ] test_frame_rate_with_different_exposures
- [ ] test_frames_displayed_in_viewer
- [ ] test_stats_updated_during_live
- [ ] test_recovery_from_camera_timeout
- [ ] test_restart_after_error

**Multipoint Acquisition Workflow Tests:**
- [ ] test_single_point_acquisition
- [ ] test_multiple_point_acquisition
- [ ] test_z_stack_acquisition
- [ ] test_z_stack_with_autofocus
- [ ] test_multi_channel_acquisition
- [ ] test_channel_switching_timing
- [ ] test_progress_events_emitted
- [ ] test_image_count_matches_expected
- [ ] test_abort_mid_acquisition
- [ ] test_resume_after_abort
- [ ] test_images_saved_to_correct_location
- [ ] test_metadata_saved_correctly
- [ ] test_camera_error_during_acquisition
- [ ] test_stage_error_during_acquisition

---

## Phase 7: Simulator Enhancements

### 7.1 SimulatedCamera Enhancements

| Enhancement | Status | File |
|-------------|--------|------|
| Add set_test_pattern() method | [ ] | `control/peripherals/cameras/camera_utils.py` |
| Add set_simulated_defocus() method | [ ] | `control/peripherals/cameras/camera_utils.py` |
| Add get_frame_statistics() method | [ ] | `control/peripherals/cameras/camera_utils.py` |

### 7.2 SimulatedStage Enhancements

| Enhancement | Status | File |
|-------------|--------|------|
| Add set_movement_callback() method | [ ] | `control/peripherals/stage/simulated.py` |
| Add get_movement_history() method | [ ] | `control/peripherals/stage/simulated.py` |
| Add clear_movement_history() method | [ ] | `control/peripherals/stage/simulated.py` |
| Add optional backlash simulation | [ ] | `control/peripherals/stage/simulated.py` |

### 7.3 New Simulators

| Simulator | Status | File |
|-----------|--------|------|
| CELESTA_Simulation | [ ] | `control/peripherals/lighting/celesta_simulation.py` |
| AndorLaser_Simulation | [ ] | `control/peripherals/lighting/andor_simulation.py` |
| FluidicsSimulation (enhanced) | [ ] | `control/peripherals/fluidics.py` |

### 7.4 SimSerial Command Verification

| Command Category | Status | Notes |
|-----------------|--------|-------|
| DAC commands (SET_DAC0, SET_DAC1) | [ ] | Verify implementation |
| Trigger commands (START/STOP) | [ ] | Verify implementation |
| LED commands (ON/OFF) | [ ] | Verify implementation |
| AF laser commands (ON/OFF) | [ ] | Verify implementation |

---

## Phase 8: CI/CD Configuration

| Task | Status | File |
|------|--------|------|
| Create GitHub Actions workflow | [ ] | `.github/workflows/test.yml` |
| Configure offscreen Qt testing | [ ] | `.github/workflows/test.yml` |
| Add test coverage reporting | [ ] | `.github/workflows/test.yml` |
| Configure test result caching | [ ] | `.github/workflows/test.yml` |
| Add test matrix for configurations | [ ] | `.github/workflows/test.yml` |

---

## Progress Summary

| Phase | Total Tasks | Completed | Percentage |
|-------|-------------|-----------|------------|
| Phase 0: Documentation | 7 | 7 | 100% |
| Phase 1: Infrastructure | 15 | 0 | 0% |
| Phase 2: Event Bus Refactoring | 10 | 0 | 0% |
| Phase 3: Service Tests | 60 | 0 | 0% |
| Phase 4: Widget Unit Tests | 150 | 0 | 0% |
| Phase 5: Widget-Service Integration | 95 | 0 | 0% |
| Phase 6: E2E Workflow Tests | 57 | 0 | 0% |
| Phase 7: Simulator Enhancements | 15 | 0 | 0% |
| Phase 8: CI/CD | 5 | 0 | 0% |
| **Total** | **~414** | **7** | **~2%** |

---

## Quick Reference: Test File Locations

### Unit Tests
```
tests/unit/
├── squid/services/
│   ├── test_trigger_service.py         # NEW
│   ├── test_microscope_mode_service.py # NEW
│   └── test_live_service.py            # NEW
└── control/widgets/
    ├── camera/test_*.py                # 3 files NEW
    ├── stage/test_*.py                 # 4 files NEW
    ├── hardware/test_*.py              # 6 files NEW
    ├── wellplate/test_*.py             # 3 files NEW
    ├── acquisition/test_*.py           # 2 files NEW
    ├── display/test_*.py               # 3 files NEW
    └── tracking/test_*.py              # 2 files NEW
```

### Integration Tests
```
tests/integration/
├── squid/services/
│   ├── test_trigger_service_integration.py    # NEW
│   ├── test_microscope_mode_integration.py    # NEW
│   └── test_live_service_integration.py       # NEW
└── control/widgets/
    ├── test_camera_widget_integration.py      # NEW
    ├── test_stage_widget_integration.py       # NEW
    ├── test_hardware_widget_integration.py    # NEW
    ├── test_wellplate_widget_integration.py   # NEW
    └── test_acquisition_widget_integration.py # NEW
```

### E2E Tests
```
tests/e2e/
├── workflows/
│   ├── test_live_acquisition.py       # NEW
│   ├── test_multipoint_acquisition.py # NEW
│   ├── test_wellplate_scanning.py     # NEW
│   └── test_autofocus_workflow.py     # NEW
└── test_full_acquisition_pipeline.py  # NEW
```

---

## Notes & Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| | | |

Use this section to track important decisions made during implementation.
