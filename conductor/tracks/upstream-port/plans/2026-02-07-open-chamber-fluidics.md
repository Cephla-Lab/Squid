# Open Chamber Fluidics Support - Upstream Port Plan

**Source commit:** `644ac383` (feat: Open Chamber fluidics support and FluidicsWidget enhancements)
**Target branch:** `multipoint-refactor` (arch_v2)
**Date:** 2026-02-07

## Context

The upstream commit adds Open Chamber application type support, editable sequence
tables, a Save Log button, and abort-safety improvements to the fluidics system.
In arch_v2, the architecture is fundamentally different:
- FluidicsController uses YAML protocols instead of CSV sequences
- FluidicsWidget communicates via EventBus, not Qt signals
- Orchestrator handles multi-round fluidics, not MultiPointWorker directly

## Implementation Steps

### 1. Driver: Open Chamber validation + hardware init refactor
**File:** `software/src/squid/backend/drivers/fluidics/fluidics.py`

- [x] Refactor `_initialize_hardware()` to reduce duplication (sim vs real)
- [x] Update `_validate_sequences()` to support Open Chamber sequence names
- [x] Extract `_validate_int_field()` helper
- [x] Add null-safety to `emergency_stop()`

### 2. FluidicsWidget: Save Log button
**File:** `software/src/squid/ui/widgets/fluidics.py`

- [x] Add "Save Log" button to status log panel
- [x] Implement `_save_log()` method with file dialog

### 3. FluidicsWidget: Disable controls during acquisition
**File:** `software/src/squid/ui/widgets/fluidics.py`

- [x] Subscribe to `FluidicsProtocolStarted` / `FluidicsProtocolCompleted` to
  disable/enable editing during protocol execution (already handled by
  `_on_protocol_started` / `_on_protocol_completed`)
- [x] Verified existing handlers already disable/enable controls correctly

### 4. MultiPointController: Stop fluidics on abort
**File:** `software/src/squid/backend/controllers/multipoint/multi_point_controller.py`

- [x] Add FluidicsController stop call in `request_abort_aquisition()`

### 5. Abort check after fluidics (verification only)
**File:** `software/src/squid/backend/controllers/orchestrator/experiment_runner.py`

- [x] VERIFIED: `cancel_token.check_point()` is called at the top of every step
  iteration (line 192), which naturally handles abort between fluidics and imaging
  steps. No changes needed.

### 6. Tests

- [x] Unit test for `_validate_sequences()` with Open Chamber names
- [x] Unit test for `_validate_int_field()` helper
- [x] Unit test for Save Log button
- [x] Unit test for fluidics stop on abort in MultiPointController

## Items NOT Ported (architectural mismatch)

- **Editable sequence table columns**: The upstream commit makes CSV-based sequence
  table columns (flow_rate, volume, incubation_time, repeat) editable via
  PandasTableModel. In arch_v2, protocols are defined in YAML and displayed in a
  read-only QTableWidget. Making these editable would require a different approach
  (editing the FluidicsProtocol model), which is out of scope for this port.

- **Hide Manual Control panel for Open Chamber**: In arch_v2, the FluidicsWidget
  doesn't have access to the Fluidics driver config directly. The manual flow
  panel uses FluidicsService, which works with both MERFISH and Open Chamber.
  Hiding the panel requires knowing the application type, which would need a new
  event or config mechanism. Deferred.

- **PandasTableModel editing infrastructure**: The upstream adds `flags()`,
  `setData()`, `_is_valid_column_value()`, `set_editable()` to PandasTableModel.
  This model doesn't exist in arch_v2 (protocols use QTableWidget). Not applicable.
