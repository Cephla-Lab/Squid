# Wellplate Calibration Improvements - Center Point Method and Parameter Editing

**Status:** COMPLETED
**Started:** 2026-02-07

## Upstream Commits

- [x] `88c0065c` - feat: Wellplate calibration improvements - center point method and parameter editing (#490)

## Summary

Port of upstream commit `88c0065c` to the arch_v2 branch (`multipoint-refactor`). The upstream
commit modified `software/control/widgets.py` (monolithic file). The arch_v2 equivalents are:
- `software/src/squid/ui/widgets/wellplate/calibration.py` (WellplateCalibration dialog)
- `software/src/squid/ui/widgets/wellplate/format.py` (WellplateFormatWidget - no changes needed)

### Key Differences from Upstream

- arch_v2 uses `EventBusDialog` (inherits QDialog + EventBus integration) instead of plain QDialog
- Communication via `SaveWellplateCalibrationCommand` event instead of direct method calls
- Stage position comes from cached `StagePositionChanged` events, not direct `self.stage.get_pos()`
- Uses `_log = squid.core.logging.get_logger(__name__)` instead of `print()` for logging
- Uses `Qt.AlignmentFlag.AlignCenter` instead of `Qt.AlignCenter` (PyQt5 enum style)

## Implementation Checklist

### 1. Center Point Calibration Method
- [x] Add `center_point` field to `__init__`
- [x] Add calibration method radio buttons (edge points / center point)
- [x] Add `points_widget` wrapper for edge points UI
- [x] Add `center_point_widget` with set/clear button, status label, well size input
- [x] Add `toggle_calibration_method()` to switch between methods
- [x] Add `setCenterPoint()` to set/clear center point from cached position
- [x] Add `update_calibrate_button_state()` for method-aware button state
- [x] Add `_get_calibration_data()` helper for both methods

### 2. Update Parameters Without Recalibration
- [x] Add "Format Parameters" group box with well spacing and well size inputs
- [x] Add "Update Parameters" button with `update_existing_parameters()` handler
- [x] Add `load_existing_format_values()` to populate inputs from selected format
- [x] Add `on_existing_format_changed()` handler for format combo changes
- [x] Auto-select center point method for 384/1536 well plates

### 3. Display Name Bug Fix
- [x] Add `_format_display_name()` helper to prevent "xx well plate well plate" duplication
- [x] Update `populate_existing_formats()` to use the helper
- [x] Use `_format_display_name()` in `_calibrate_existing_format()` and `update_existing_parameters()`

### 4. Refactoring
- [x] Wrap form inputs in `new_format_widget` container
- [x] Extract `_calibrate_new_format()` from `calibrate()`
- [x] Extract `_calibrate_existing_format()` from `calibrate()`
- [x] Extract `_finish_calibration()` common completion logic
- [x] Add `reset_calibration_points()` helper
- [x] Refactor `toggle_input_mode()` to use widget visibility
- [x] Add `np.linalg.LinAlgError` catch for collinear points
- [x] Increase point display precision from `.2f` to `.3f`
- [x] Increase dialog minimum height to 580
- [x] Connect `existing_format_combo.currentIndexChanged` to handler
- [x] Add `QGroupBox` import and `_log` logger

### 5. Tests
- [x] 34 unit tests covering all new functionality
- [x] TestFormatDisplayName (4 tests)
- [x] TestCenterPointCalibration (4 tests)
- [x] TestCalibrationMethodToggle (3 tests)
- [x] TestCalibrateButtonState (5 tests)
- [x] TestGetCalibrationData (5 tests)
- [x] TestResetCalibrationPoints (3 tests)
- [x] TestToggleInputMode (2 tests)
- [x] TestPointPrecision (2 tests)
- [x] TestPopulateExistingFormats (2 tests)
- [x] TestCalibrateRefactored (4 tests)

## Files Modified

- `software/src/squid/ui/widgets/wellplate/calibration.py` -- All changes (center point UI, parameter editing, display name fix, refactored calibrate)

## Files Added

- `software/tests/unit/squid/ui/widgets/wellplate/test_wellplate_calibration.py` -- 34 unit tests
- `conductor/tracks/upstream-port/plans/2026-02-07-wellplate-calibration.md` -- This plan file
