# Actor Refactor Simplification — Step 06: MultiPoint Correctness + Cleanup

## Goal

Fix concrete MultiPoint acquisition bugs found in audit and align acquisition with the simplified architecture.

## Bugs/risks to fix

1. **Focus‑map bounds branch indentation bug**
   - In `MultiPointController.run_acquisition`, the `elif self.gen_focus_map` block is followed by an `if not bounds:` at wrong indentation.
   - `bounds` can be undefined, and part of the focus-map generation code is unreachable.
   - File: `src/squid/ops/acquisition/multi_point_controller.py` around the `elif self.gen_focus_map` section.

2. **Duplicate/legacy flags**
   - `published_started`, `transitioned_preparing`, etc. are partially unused.
   - Several booleans mirror state machine state.

3. **Finalize cleanup threading**
   - Ensure `_on_worker_finished` is the *only* path calling `_on_acquisition_completed`.
   - Confirm no worker thread ever invokes controller methods directly.

4. **Mode gate integration**
   - Replace coordinator resource acquisition with mode gate changes from Step 03.

5. **Remove “compat” acquisition events**
   - `MultiPointWorker` currently publishes `AcquisitionFinished` “for backwards compatibility”.
   - Pick a single canonical set of events (recommended):
     - `AcquisitionWorkerFinished` for controller cleanup/state machine decisions.
     - `AcquisitionProgress` / `AcquisitionWorkerProgress` for UI.
     - `AcquisitionStateChanged` for coarse UI state.
   - Delete redundant legacy events once UI is migrated.

## Implementation checklist

### 6.1 Fix bounds + focus map generation
- [ ] Re-indent as:
  - If `self.focus_map`: use existing surface interpolation.
  - `elif self.gen_focus_map and not self.do_reflection_af:`:
    - compute `bounds = self.scanCoordinates.get_scan_bounds()`
    - if bounds invalid: publish failure + reset state.
    - else generate AF map and restore position.
- [ ] Add a targeted unit test for the gen_focus_map path (simulation ok).

### 6.2 Remove legacy flags and duplicate state
- [ ] Delete unused local flags in `run_acquisition`.
- [ ] Derive `acquisition_in_progress` solely from state machine.
- [ ] Replace `abort_acqusition_requested` with state or a single Event/flag guarded by state machine.

### 6.3 Ensure single completion path
- [ ] Audit for any call sites of `_on_acquisition_completed` besides `_on_worker_finished`.
- [ ] Remove any remaining direct invocations.
- [ ] Ensure worker only publishes `AcquisitionWorkerFinished`.

### 6.4 Mode gate
- [ ] On acquisition start: `mode_gate.set_mode(ACQUIRING)`.
- [ ] On abort request: `mode_gate.set_mode(ABORTING)`.
- [ ] On completion: restore previous mode (IDLE or LIVE).

### 6.5 Remove redundant compatibility publishing
- [ ] Remove `AcquisitionFinished` publishing from `MultiPointWorker` (keep `AcquisitionWorkerFinished`).
- [ ] Audit UI subscriptions and migrate any listeners to the canonical events.
- [ ] Remove any remaining “emit signal to re-enable UI” comments/paths that imply callbacks.

## Verification

- Unit: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/ops/test_multi_point* -v`
- Integration smoke:
  - run one acquisition, then a second acquisition immediately.
  - attempt stage move during acquisition and confirm it is rejected by services.

## Exit criteria

- Focus-map generation path works and cannot reference undefined `bounds`.
- Completion/cleanup only runs on EventBus thread.
- Acquisition obeys mode gate rules.
