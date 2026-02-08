# Workflow Runner - Edit Button and Multiple Acquisition Sequences

**Status:** COMPLETED
**Started:** 2026-02-07

## Upstream Commits

- [x] `833ae523` - feat: Workflow Runner - Edit button and multiple Acquisition sequences with config paths (#493)

## Context

The upstream commit adds three key enhancements to the Workflow Runner:

1. **Edit button** - Allows modifying sequences in-place without remove/re-add
2. **Multiple Acquisition sequences** - Workflows can have 0, 1, or many acquisitions (e.g., Pre-scan, Main scan, Post-treatment scan)
3. **Config path support** - Acquisition sequences can load settings from an `acquisition.yaml` file

### Upstream vs arch_v2 Differences

- **Upstream** uses Qt signals (`signal_request_acquisition.emit(config_path)`) for communication
- **arch_v2** uses EventBus exclusively; new `WorkflowLoadConfigRequest`/`WorkflowLoadConfigResponse` events handle the config loading round-trip
- **Upstream** loads config in `gui_hcs.py._run_acquisition_for_workflow(config_path)`
- **arch_v2** loads config via WorkflowRunnerDialog's `_on_load_config_request` handler, which walks up to the main window's `recordTabWidget` to find the active widget

## Implementation Checklist

### Data Model (`models.py`)
- [x] Add `config_path: Optional[str] = None` field to `SequenceItem`
- [x] Update `to_dict()` to only include optional fields when they have values
- [x] Update `from_dict()` to handle `config_path`
- [x] Change `load_from_file()` to pass `ensure_acquisition=False` (workflows can have 0+ acquisitions)

### State Events (`state.py`)
- [x] Add `WorkflowLoadConfigRequest` event (backend -> UI, carries `config_path`)
- [x] Add `WorkflowLoadConfigResponse` event (UI -> backend, carries `success` and optional `error_message`)

### Controller (`workflow_runner_controller.py`)
- [x] Add config loading synchronization state (`_config_load_complete`, `_config_load_success`, `_config_load_error`)
- [x] Add `@handles(WorkflowLoadConfigResponse)` handler
- [x] Update `_run_acquisition()` to accept `config_path` parameter
- [x] Publish `WorkflowLoadConfigRequest` and wait for response before starting acquisition
- [x] Pass `seq.config_path` at the call site

### UI Dialog (`workflow_runner_dialog.py`)
- [x] Add `_confirm_missing_file()` helper function
- [x] Add `edit_data` parameter and `_populate_from_data()` to `AddSequenceDialog`
- [x] Add new `AddAcquisitionDialog` class (name + optional config file path)
- [x] Add Edit button between Insert Below and Remove
- [x] Add `_prompt_sequence_type()` to choose Script vs Acquisition
- [x] Add `_create_sequence_from_dialog()` factory method
- [x] Refactor `_insert_sequence()` to prompt for type and support both
- [x] Add `_edit_sequence()` method
- [x] Refactor `_remove_sequence()` to allow removing acquisitions
- [x] Add `_set_status()` helper method
- [x] Add `_get_table_text()` helper method
- [x] Update table column header from "Command" to "Command/Path"
- [x] Update `_create_command_item()` to show config filename or "(Current Settings)"
- [x] Refactor `_sync_table_to_workflow()` to use helpers
- [x] Include `btn_edit` in `_set_running_state()` disable list
- [x] Subscribe to `WorkflowLoadConfigRequest` and handle config loading
- [x] Refactor save/load/log methods for early returns

### YAML Drop Mixin (`yaml_drop_mixin.py`)
- [x] Update `_load_acquisition_yaml()` return type from `None` to `bool`

### Tests
- [x] Update roundtrip test to assert `config_path`
- [x] Update `test_load_file_ensures_acquisition` -> `test_load_file_without_acquisition` (no auto-add)
- [x] Add `test_acquisition_with_config_path` - serialization roundtrip
- [x] Add `test_multiple_acquisitions_save_load` - save/load with multiple acquisitions
- [x] Add `test_to_dict_only_includes_set_optional_fields` - verify sparse serialization
- [x] Add `test_acquisition_with_config_path` (controller) - config request published
- [x] Add `test_acquisition_config_load_failure` (controller) - fails when config load fails
- [x] Add `test_acquisition_without_config_path_skips_config_load` (controller) - no request when no path

## Files Changed

- `software/src/squid/backend/controllers/workflow_runner/models.py`
- `software/src/squid/backend/controllers/workflow_runner/state.py`
- `software/src/squid/backend/controllers/workflow_runner/workflow_runner_controller.py`
- `software/src/squid/ui/widgets/workflow/workflow_runner_dialog.py`
- `software/src/squid/ui/widgets/acquisition/yaml_drop_mixin.py`
- `software/tests/unit/workflow_runner/test_workflow_runner.py`

## Notes

- The `ensure_acquisition_exists()` method is preserved on `Workflow` for backward compatibility but is no longer called from `load_from_file()`. It still works for programmatic use cases where you want to guarantee an acquisition exists.
- The `_load_acquisition_yaml` return type change in `yaml_drop_mixin.py` is needed for the controller to know whether config loading succeeded. The method now returns `True` on success, `False` on any failure.
- Pre-existing flaky test `test_abort_via_command` occasionally fails due to timing; unrelated to this change.
