# Per-region laser-AF offset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each well/region be focused at its own offset from the single global laser-AF reference plane, by capturing that offset at the focus point and driving laser AF to it during acquisition (instead of always to displacement 0).

**Architecture:** A `region_laser_af_offsets: dict[region_id → µm]` is captured in `FocusMapWidget` when the user records a focus point (one per well, the constant-z case), threaded to `MultiPointController` → `AcquisitionParameters` → `MultiPointWorker`, and consumed by changing the single `move_to_target(0)` call in `perform_autofocus` to `move_to_target(offset_for_region)`. The mode is gated behind an explicit checkbox that requires both Reflection AF and Use Focus Map. The focus map's absolute z stays as the coarse pre-position (no change to z-baking); the per-channel z-offset still composes additively because it is relative to wherever laser AF lands.

**Tech Stack:** Python 3, PyQt5, pytest. Hardware-abstraction via `squid/` simulated backends. No new dependencies.

Full design: `docs/superpowers/specs/2026-06-18-per-region-laser-af-offset-design.md`.

## Global Constraints

- Black line length is **120** (`pyproject.toml`); run `black --config pyproject.toml .` before each commit.
- Logging: GUI status via `self.status_label.setText(...)` (existing `FocusMapWidget` pattern); worker logs via `self._log`.
- Tests run under pytest/pytest-qt; CI command: `python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py`.
- Branch for this work: `per-region-laser-af-offset` (already created off `master`, design spec already committed).
- Offset is **µm displacement from the global laser-AF reference**, equal to `LaserAutofocusController.measure_displacement()` at the focus z. Empty dict → current behavior (target 0 everywhere).
- Mode is active only when ALL of: `checkbox_useFocusMap` ON, `checkbox_withReflectionAutofocus` ON, and `FocusMapWidget.checkbox_perRegionLaserAFOffset` ON (a Qt checkbox stays checked while disabled — verify all three at acquisition start).
- All feature unit tests live in `tests/control/test_per_region_laser_af_offset.py`.

---

### Task 1: Backend — thread offsets through params and apply them in the worker

**Files:**
- Modify: `control/core/multi_point_utils.py:1` (import) and `:28-65` (`AcquisitionParameters`)
- Modify: `control/core/multi_point_controller.py:228` (field), `:415` (setter), `:935-961` (`build_params`)
- Modify: `control/core/multi_point_worker.py:120-122` (unpack), `:1157-1158` (apply)
- Test: `tests/control/test_per_region_laser_af_offset.py`

**Interfaces:**
- Produces: `AcquisitionParameters.region_laser_af_offsets: Dict[str, float]` (default `{}`); `MultiPointController.set_region_laser_af_offsets(offsets)`; `MultiPointWorker.region_laser_af_offsets`; `MultiPointWorker.perform_autofocus(region_id, fov)` now targets the region's offset.
- Consumes (existing): `MultiPointWorker.laser_auto_focus_controller.move_to_target(target_um)` (already supports arbitrary targets).

- [ ] **Step 1: Write the failing test (dataclass field + default)**

Create `tests/control/test_per_region_laser_af_offset.py`:

```python
"""Unit tests for the per-region laser-AF offset feature.

Backend tests construct minimal MultiPointWorker-/FocusMapWidget-shaped stubs and
call the real methods in isolation, mirroring tests/control/test_MultiPointWorker_offsets.py.
"""

import math
from dataclasses import fields
from unittest.mock import MagicMock

from control.core.multi_point_utils import AcquisitionParameters
from control.core.multi_point_worker import MultiPointWorker


def test_acquisition_parameters_has_region_offsets_field():
    names = {f.name for f in fields(AcquisitionParameters)}
    assert "region_laser_af_offsets" in names


def test_region_offsets_default_factory_is_empty_dict():
    fld = next(f for f in fields(AcquisitionParameters) if f.name == "region_laser_af_offsets")
    assert fld.default_factory() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -v`
Expected: FAIL — `region_laser_af_offsets` not in fields (and `StopIteration`/AttributeError on the second test).

- [ ] **Step 3: Add the field to `AcquisitionParameters`**

In `control/core/multi_point_utils.py`, change line 1 import:

```python
from dataclasses import dataclass, field
```

Then add, immediately after the `xy_mode` field (currently line 65) inside `AcquisitionParameters`:

```python
    # Per-region laser-AF target offsets (µm from the global laser-AF reference plane),
    # keyed by region_id. Empty unless the focus-map + laser-AF combined mode is active,
    # in which case each FOV in a region targets that region's offset instead of 0.
    region_laser_af_offsets: Dict[str, float] = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing test (apply path)**

Append to `tests/control/test_per_region_laser_af_offset.py`:

```python
class _AFStub:
    """MultiPointWorker-ish object with just what perform_autofocus's laser-AF branch reads."""

    def __init__(self, offsets, move_result=True):
        self.do_reflection_af = True
        self.region_laser_af_offsets = offsets
        self._log = MagicMock()
        self.laser_auto_focus_controller = MagicMock()
        self.laser_auto_focus_controller.move_to_target.return_value = move_result
        self._laser_af_successes = 0
        self._laser_af_failures = 0
        # Only touched on the exception path:
        self.base_path = "/tmp"
        self.experiment_ID = "exp"
        self.time_point = 0

    perform_autofocus = MultiPointWorker.perform_autofocus


def test_perform_autofocus_uses_region_offset():
    w = _AFStub({"A1": 5.0})
    assert w.perform_autofocus("A1", 0) is True
    w.laser_auto_focus_controller.move_to_target.assert_called_once_with(5.0)
    assert w._laser_af_successes == 1


def test_perform_autofocus_defaults_to_zero_for_unmapped_region():
    w = _AFStub({"A1": 5.0})
    assert w.perform_autofocus("B2", 0) is True
    w.laser_auto_focus_controller.move_to_target.assert_called_once_with(0.0)


def test_perform_autofocus_failure_increments_and_returns_false():
    w = _AFStub({"A1": 5.0}, move_result=False)
    assert w.perform_autofocus("A1", 0) is False
    assert w._laser_af_failures == 1
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -k perform_autofocus -v`
Expected: FAIL — `move_to_target` called with `0`, not `5.0` (current code hardcodes `move_to_target(0)`).

- [ ] **Step 7: Apply the per-region target in `perform_autofocus`**

In `control/core/multi_point_worker.py`, replace lines 1157-1158:

```python
            try:
                af_succeeded = self.laser_auto_focus_controller.move_to_target(0)
```

with:

```python
            try:
                target_um = self.region_laser_af_offsets.get(region_id, 0.0)
                self._log.info(f"laser AF target for region '{region_id}': {target_um:.2f} µm")
                af_succeeded = self.laser_auto_focus_controller.move_to_target(target_um)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -v`
Expected: PASS (5 passed).

- [ ] **Step 9: Wire the plumbing (controller field, setter, build_params, worker unpack)**

In `control/core/multi_point_controller.py`, after line 228 (`self.focus_map = None`):

```python
        self.region_laser_af_offsets = {}
```

After `set_focus_map` (currently lines 415-416), add:

```python
    def set_region_laser_af_offsets(self, offsets):
        # region_id -> µm offset from the global laser-AF reference plane. Empty dict means
        # every FOV targets the reference (displacement 0), i.e. current behavior.
        self.region_laser_af_offsets = dict(offsets) if offsets else {}
```

In `build_params`, inside the `AcquisitionParameters(...)` call (after `xy_mode=self.xy_mode,` at line 960):

```python
            region_laser_af_offsets=self.region_laser_af_offsets,
```

In `control/core/multi_point_worker.py`, after line 122 (`self.apply_channel_offset = acquisition_parameters.apply_channel_offset`):

```python
        self.region_laser_af_offsets = acquisition_parameters.region_laser_af_offsets
```

- [ ] **Step 10: Verify import smoke + format + full backend test**

Run: `python3 -c "import control.core.multi_point_controller, control.core.multi_point_worker, control.core.multi_point_utils"`
Expected: no error.
Run: `black --config pyproject.toml control/core/multi_point_utils.py control/core/multi_point_controller.py control/core/multi_point_worker.py tests/control/test_per_region_laser_af_offset.py`
Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -v`
Expected: PASS (5 passed).

- [ ] **Step 11: Commit**

```bash
git add control/core/multi_point_utils.py control/core/multi_point_controller.py control/core/multi_point_worker.py tests/control/test_per_region_laser_af_offset.py
git commit -m "feat: apply per-region laser-AF target offset in multipoint worker

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Capture logic in `FocusMapWidget`

**Files:**
- Modify: `control/core/__init__`? No. Modify: `control/widgets.py` (`FocusMapWidget`, `__init__` ~10374-10392; helpers added near 10670)
- Test: `tests/control/test_per_region_laser_af_offset.py`

**Interfaces:**
- Produces: `FocusMapWidget.region_laser_af_offsets: dict[str, float]`; `FocusMapWidget.capture_laser_af_offset_enabled: bool`; methods `_capture_region_offset(region_id)`, `_clear_region_offsets()`, `_sync_offsets_to_focus_points()`, `_on_laser_af_reference_changed(has_reference)`.
- Consumes: `self.laserAutofocusController` (may be `None`); its `.measure_displacement() -> float` and `.laser_af_properties.has_reference` / `.laser_af_range`.

- [ ] **Step 1: Write the failing test (capture matrix + clear + sync)**

Append to `tests/control/test_per_region_laser_af_offset.py`:

```python
from control.widgets import FocusMapWidget


def _laser_controller(displacement, has_reference=True, laser_af_range=200.0):
    c = MagicMock()
    c.laser_af_properties.has_reference = has_reference
    c.laser_af_properties.laser_af_range = laser_af_range
    c.measure_displacement.return_value = displacement
    return c


class _FMStub:
    """FocusMapWidget-ish object exposing just what the capture/persistence helpers read."""

    def __init__(self, *, enabled=True, controller=None, focus_points=None, offsets=None):
        self.capture_laser_af_offset_enabled = enabled
        self.laserAutofocusController = controller
        self.focus_points = focus_points if focus_points is not None else []
        self.region_laser_af_offsets = offsets if offsets is not None else {}
        self.status_label = MagicMock()

    _capture_region_offset = FocusMapWidget._capture_region_offset
    _clear_region_offsets = FocusMapWidget._clear_region_offsets
    _sync_offsets_to_focus_points = FocusMapWidget._sync_offsets_to_focus_points


def test_capture_stores_displacement_when_enabled():
    w = _FMStub(controller=_laser_controller(3.5))
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {"A1": 3.5}


def test_capture_noop_when_mode_disabled():
    ctrl = _laser_controller(3.5)
    w = _FMStub(enabled=False, controller=ctrl)
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {}
    ctrl.measure_displacement.assert_not_called()


def test_capture_noop_when_no_controller():
    w = _FMStub(controller=None)
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {}


def test_capture_noop_when_no_reference():
    ctrl = _laser_controller(3.5, has_reference=False)
    w = _FMStub(controller=ctrl)
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {}
    ctrl.measure_displacement.assert_not_called()


def test_capture_does_not_store_nan():
    w = _FMStub(controller=_laser_controller(float("nan")))
    w._capture_region_offset("A1")
    assert "A1" not in w.region_laser_af_offsets


def test_capture_stores_but_warns_when_out_of_range():
    w = _FMStub(controller=_laser_controller(500.0, laser_af_range=200.0))
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {"A1": 500.0}
    assert w.status_label.setText.called


def test_capture_replaces_stale_entry_when_disabled():
    # Re-capturing with mode off must not leave a stale value for that region.
    w = _FMStub(enabled=False, controller=_laser_controller(3.5), offsets={"A1": 9.0})
    w._capture_region_offset("A1")
    assert "A1" not in w.region_laser_af_offsets


def test_clear_region_offsets():
    w = _FMStub(offsets={"A1": 1.0, "B2": 2.0})
    w._clear_region_offsets()
    assert w.region_laser_af_offsets == {}


def test_sync_drops_orphaned_offsets():
    w = _FMStub(focus_points=[("A1", 0.0, 0.0, 1.0)], offsets={"A1": 1.0, "B2": 2.0})
    w._sync_offsets_to_focus_points()
    assert w.region_laser_af_offsets == {"A1": 1.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -k "capture or clear or sync" -v`
Expected: FAIL — `AttributeError: type object 'FocusMapWidget' has no attribute '_capture_region_offset'`.

- [ ] **Step 3: Add capture state to `FocusMapWidget.__init__`**

In `control/widgets.py`, change the constructor signature (line 10374):

```python
    def __init__(self, stage: AbstractStage, navigationViewer, scanCoordinates, focusMap, laserAutofocusController=None):
```

After `self.focusMap = focusMap` (line 10383) add:

```python
        self.laserAutofocusController = laserAutofocusController
```

After `self.enabled = False  # toggled when focus map enabled for next acquisition` (line 10387) add:

```python
        # Per-region laser-AF offsets (µm from the global laser-AF reference), keyed by
        # region_id. Captured at each focus point when the combined mode is enabled.
        self.region_laser_af_offsets = {}
        self.capture_laser_af_offset_enabled = False
        self._reflection_af_available = False
```

At the end of `__init__` (after `self.add_margin = True`, line 10392) add:

```python
        if self.laserAutofocusController is not None:
            self.laserAutofocusController.signal_reference_changed.connect(self._on_laser_af_reference_changed)
```

- [ ] **Step 4: Add the capture/clear/sync helper methods**

In `control/widgets.py`, add these methods to `FocusMapWidget` (e.g. immediately after `get_region_points_dict`, around line 10678):

```python
    def _capture_region_offset(self, region_id):
        """Record the laser-AF displacement at the current z as this region's offset.

        No-op (and clears any stale entry for region_id) unless the combined mode is
        enabled, a laser-AF controller exists, and a reference is set. Never stores NaN
        (failed spot detection). Warns — but still stores — when the offset exceeds the
        laser-AF range, since that region would fail AF during acquisition.
        """
        self.region_laser_af_offsets.pop(region_id, None)
        if not self.capture_laser_af_offset_enabled or self.laserAutofocusController is None:
            return
        if not self.laserAutofocusController.laser_af_properties.has_reference:
            self.status_label.setText("Laser AF reference not set — per-region offset not captured")
            return
        offset_um = self.laserAutofocusController.measure_displacement()
        if math.isnan(offset_um):
            self.status_label.setText(f"Laser AF spot not detected — offset not captured for {region_id}")
            return
        laser_af_range = self.laserAutofocusController.laser_af_properties.laser_af_range
        if abs(offset_um) > laser_af_range:
            self.status_label.setText(
                f"Warning: region {region_id} offset {offset_um:.1f} µm exceeds laser AF range "
                f"({laser_af_range:.1f} µm); it may fail AF during acquisition"
            )
        self.region_laser_af_offsets[region_id] = offset_um

    def _clear_region_offsets(self):
        self.region_laser_af_offsets = {}

    def _sync_offsets_to_focus_points(self):
        """Drop offsets for regions that no longer have any focus point."""
        live_regions = {rid for rid, _, _, _ in self.focus_points}
        for rid in list(self.region_laser_af_offsets.keys()):
            if rid not in live_regions:
                self.region_laser_af_offsets.pop(rid, None)

    def _on_laser_af_reference_changed(self, has_reference):
        # The reference plane moved; previously-captured offsets are relative to the old
        # reference and are now meaningless. Clear them so they cannot be applied stale.
        if self.region_laser_af_offsets:
            self._clear_region_offsets()
            self.status_label.setText("Laser AF reference changed — captured per-region offsets cleared")
```

- [ ] **Step 5: Hook capture into the focus-point lifecycle**

In `add_current_point`, after `self.navigationViewer.register_focus_point(pos.x_mm, pos.y_mm)` (line 10636) add:

```python
            self._capture_region_offset(region_id)
```

In `update_current_z`, after `self.update_point_list()` (line 10670) add:

```python
            self._capture_region_offset(region_id)
```

Replace `remove_current_point` (lines 10640-10645) with:

```python
    def remove_current_point(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            self.focus_points.pop(index)
            self._sync_offsets_to_focus_points()
            self.update_point_list()
            self.update_focus_point_display()
```

In `generate_grid`, after `self.focus_points.clear()` (line 10570) add:

```python
            self._clear_region_offsets()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -k "capture or clear or sync" -v`
Expected: PASS (9 passed).

- [ ] **Step 7: Format + full feature test**

Run: `black --config pyproject.toml control/widgets.py tests/control/test_per_region_laser_af_offset.py`
Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -v`
Expected: PASS (14 passed total).

- [ ] **Step 8: Commit**

```bash
git add control/widgets.py tests/control/test_per_region_laser_af_offset.py
git commit -m "feat: capture per-region laser-AF offset in FocusMapWidget

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Persist offsets in the focus-points CSV

**Files:**
- Modify: `control/widgets.py` (`FocusMapWidget.export_focus_points` 10729-10754, `import_focus_points` 10756-10826; add two pure helpers)
- Test: `tests/control/test_per_region_laser_af_offset.py`

**Interfaces:**
- Produces: `FocusMapWidget._write_focus_points_csv(file_path)`, `FocusMapWidget._read_focus_points_csv(file_path) -> (points, offsets)`.

- [ ] **Step 1: Write the failing test (CSV round-trip incl. offsets + back-compat)**

Append to `tests/control/test_per_region_laser_af_offset.py`:

```python
def test_csv_roundtrip_includes_offsets(tmp_path):
    src = _FMStub(
        focus_points=[("A1", 1.0, 2.0, 0.5), ("B2", 3.0, 4.0, 0.6)],
        offsets={"A1": 7.0},  # B2 intentionally has no offset
    )
    src._write_focus_points_csv = FocusMapWidget._write_focus_points_csv.__get__(src)
    path = str(tmp_path / "fp.csv")
    src._write_focus_points_csv(path)

    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)
    points, offsets = dst._read_focus_points_csv(path)
    assert points == [("A1", 1.0, 2.0, 0.5), ("B2", 3.0, 4.0, 0.6)]
    assert offsets == {"A1": 7.0}


def test_csv_read_back_compat_without_offset_column(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("Region_ID,X_mm,Y_mm,Z_um\nA1,1.0,2.0,0.5\n")
    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)
    points, offsets = dst._read_focus_points_csv(str(path))
    assert points == [("A1", 1.0, 2.0, 0.5)]
    assert offsets == {}


def test_csv_read_rejects_missing_required_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Region_ID,X_mm\nA1,1.0\n")
    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)
    import pytest

    with pytest.raises(ValueError):
        dst._read_focus_points_csv(str(path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -k csv -v`
Expected: FAIL — `AttributeError: ... has no attribute '_write_focus_points_csv'`.

- [ ] **Step 3: Add the pure CSV helpers**

In `control/widgets.py`, add to `FocusMapWidget` (near the export/import methods):

```python
    def _write_focus_points_csv(self, file_path):
        with open(file_path, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Region_ID", "X_mm", "Y_mm", "Z_um", "Offset_um"])
            for region_id, x, y, z in self.focus_points:
                offset = self.region_laser_af_offsets.get(region_id, "")
                writer.writerow([region_id, x, y, z, offset])

    def _read_focus_points_csv(self, file_path):
        """Parse a focus-points CSV. Returns (points, offsets).

        points: list of (region_id, x, y, z). offsets: {region_id: float} for rows that
        carry a non-empty Offset_um (column optional, for back-compat). Raises ValueError
        if any required column is missing.
        """
        points = []
        offsets = {}
        with open(file_path, "r", newline="") as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader)
            required_columns = ["Region_ID", "X_mm", "Y_mm", "Z_um"]
            if not all(col in header for col in required_columns):
                raise ValueError(f"CSV file must contain columns: {', '.join(required_columns)}")
            region_idx = header.index("Region_ID")
            x_idx = header.index("X_mm")
            y_idx = header.index("Y_mm")
            z_idx = header.index("Z_um")
            offset_idx = header.index("Offset_um") if "Offset_um" in header else None
            for row in reader:
                if len(row) >= 4:
                    try:
                        region_id = str(row[region_idx])
                        x = float(row[x_idx])
                        y = float(row[y_idx])
                        z = float(row[z_idx])
                    except (ValueError, IndexError):
                        continue
                    points.append((region_id, x, y, z))
                    if offset_idx is not None and offset_idx < len(row) and row[offset_idx] != "":
                        try:
                            offsets[region_id] = float(row[offset_idx])
                        except ValueError:
                            pass
        return points, offsets
```

- [ ] **Step 4: Route the dialog methods through the helpers**

Replace the body of `export_focus_points` (the `try`/`except` block, lines 10741-10754) with:

```python
        try:
            self._write_focus_points_csv(file_path)
            self.status_label.setText(f"Exported {len(self.focus_points)} points to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export focus points: {str(e)}")
```

Replace the **entire** `import_focus_points` method (lines 10756-10826) with this version, which routes parsing through the helper and loads the offsets (the by-region validation flow is preserved verbatim):

```python
    def import_focus_points(self):
        """Import focus points (and optional per-region offsets) from a CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Focus Points", "", "CSV Files (*.csv);;All Files (*)")
        if not file_path:
            return

        try:
            imported_points, imported_offsets = self._read_focus_points_csv(file_path)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Format", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import focus points: {str(e)}")
            return

        # If by_region is checked, validate regions
        if self.by_region_checkbox.isChecked():
            scan_regions = set(self.scanCoordinates.region_centers.keys())
            focus_regions = set(region_id for region_id, _, _, _ in imported_points)
            if not focus_regions == scan_regions:
                response = QMessageBox.warning(
                    self,
                    "Region Mismatch",
                    f"The imported focus points have regions: {', '.join(sorted(focus_regions))}\n\n"
                    f"Current scan has regions: {', '.join(sorted(scan_regions))}\n\n"
                    "Import anyway (disable 'By Region') or cancel?",
                    QMessageBox.Ok | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if response == QMessageBox.Cancel:
                    return
                else:
                    # User chose to continue, uncheck by_region
                    self.by_region_checkbox.setChecked(False)

        # Clear existing points and add imported ones
        self.focus_points = imported_points
        self.region_laser_af_offsets = imported_offsets
        self.update_point_list()
        self.update_focus_point_display()
        self.status_label.setText(f"Imported {len(imported_points)} focus points")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -k csv -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Format + full feature test + import smoke**

Run: `black --config pyproject.toml control/widgets.py tests/control/test_per_region_laser_af_offset.py`
Run: `python3 -c "import control.widgets"`
Run: `python3 -m pytest tests/control/test_per_region_laser_af_offset.py -v`
Expected: PASS (17 passed total).

- [ ] **Step 7: Commit**

```bash
git add control/widgets.py tests/control/test_per_region_laser_af_offset.py
git commit -m "feat: persist per-region laser-AF offsets in focus-points CSV

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: GUI wiring — checkbox, controller handle, multipoint connections, acquisition push

This task is GUI integration (Qt-heavy); it is verified by a `--simulation` smoke test rather than unit tests. Implement all sub-steps, then run the smoke test before committing.

**Files:**
- Modify: `control/widgets.py` (`FocusMapWidget.setup_ui` ~10456-10459, `make_connections` ~10486; new handlers; FlexibleMultiPointWidget `make_connections` ~6154 + init ~6188 + acquisition block 6480-6484; WellplateMultiPointWidget `make_connections` ~7693 + init ~7742 + acquisition block 8827-8838)
- Modify: `control/gui_hcs.py:976` (pass the controller)

**Interfaces:**
- Consumes: `FocusMapWidget.set_reflection_af_available(bool)`, `FocusMapWidget.checkbox_perRegionLaserAFOffset`, `FocusMapWidget.region_laser_af_offsets`, `MultiPointController.set_region_laser_af_offsets`.

- [ ] **Step 1: Add the checkbox to `FocusMapWidget.setup_ui`**

In `control/widgets.py`, after `settings_layout.addWidget(self.by_region_checkbox)` (line 10458) add:

```python
        self.checkbox_perRegionLaserAFOffset = QCheckBox("Per-region laser AF offset")
        self.checkbox_perRegionLaserAFOffset.setChecked(False)
        self.checkbox_perRegionLaserAFOffset.setEnabled(False)
        self.checkbox_perRegionLaserAFOffset.setToolTip(
            "With laser AF and focus map both on: capture each region's offset from the laser AF "
            "reference at its focus point, and drive laser AF to that per-region target during acquisition."
        )
        settings_layout.addWidget(self.checkbox_perRegionLaserAFOffset)
```

- [ ] **Step 2: Wire the checkbox + add handlers**

In `FocusMapWidget.make_connections`, after `self.fit_method_combo.currentTextChanged.connect(self._match_by_region_box)` (line 10486) add:

```python
        self.checkbox_perRegionLaserAFOffset.toggled.connect(self._on_per_region_offset_toggled)
```

Add these methods to `FocusMapWidget` (near the capture helpers from Task 2):

```python
    def _on_per_region_offset_toggled(self, checked):
        self.capture_laser_af_offset_enabled = checked
        if not checked:
            self._clear_region_offsets()

    def set_reflection_af_available(self, available):
        # The per-region offset only makes sense with laser AF on. Disable + uncheck (and
        # via the toggle handler, clear) the checkbox when reflection AF is off — mirrors
        # the per-channel offset checkbox behavior in the multipoint widgets.
        self._reflection_af_available = bool(available)
        self.checkbox_perRegionLaserAFOffset.setEnabled(self._reflection_af_available)
        if not self._reflection_af_available and self.checkbox_perRegionLaserAFOffset.isChecked():
            self.checkbox_perRegionLaserAFOffset.setChecked(False)
```

- [ ] **Step 3: Pass the laser-AF controller into the widget**

In `control/gui_hcs.py`, replace the `FocusMapWidget(...)` construction (lines 976-978):

```python
        self.focusMapWidget = widgets.FocusMapWidget(
            self.stage, self.navigationViewer, self.scanCoordinates, core.FocusMap(), self.laserAutofocusController
        )
```

(`self.laserAutofocusController` is defined at gui_hcs.py:647 — `Optional`, `None` when `SUPPORT_LASER_AUTOFOCUS` is off — so this is safe; the widget tolerates `None`.)

- [ ] **Step 4: Connect Reflection-AF availability from both multipoint widgets**

In FlexibleMultiPointWidget `make_connections`, after line 6157 (`...connect(self._update_apply_channel_offset_enable_state)`) add:

```python
        self.checkbox_withReflectionAutofocus.toggled.connect(self.focusMapWidget.set_reflection_af_available)
```

After line 6188 (`self._update_apply_channel_offset_enable_state(self.checkbox_withReflectionAutofocus.isChecked())`) add:

```python
        self.focusMapWidget.set_reflection_af_available(self.checkbox_withReflectionAutofocus.isChecked())
```

In WellplateMultiPointWidget `make_connections`, after line 7693 add the same `set_reflection_af_available` connect line, and after line 7742 add the same init call.

- [ ] **Step 5: Push offsets at acquisition start (Flexible path)**

In `control/widgets.py`, replace the Flexible focus-map block (lines 6480-6484):

```python
            if self.checkbox_useFocusMap.isChecked():
                self.focusMapWidget.fit_surface()
                self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
            else:
                self.multipointController.set_focus_map(None)
```

with:

```python
            if self.checkbox_useFocusMap.isChecked():
                self.focusMapWidget.fit_surface()
                self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
                if (
                    self.checkbox_withReflectionAutofocus.isChecked()
                    and self.focusMapWidget.checkbox_perRegionLaserAFOffset.isChecked()
                ):
                    self.multipointController.set_region_laser_af_offsets(self.focusMapWidget.region_laser_af_offsets)
                else:
                    self.multipointController.set_region_laser_af_offsets({})
            else:
                self.multipointController.set_focus_map(None)
                self.multipointController.set_region_laser_af_offsets({})
```

- [ ] **Step 6: Push offsets at acquisition start (Wellplate path)**

Replace the Wellplate focus-map block (lines 8827-8838):

```python
            if self.checkbox_useFocusMap.isChecked():
                # Try to fit the surface
                if self.focusMapWidget.fit_surface():
                    # If fit successful, set the surface fitter in controller
                    self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
                else:
                    QMessageBox.warning(self, "Warning", "Failed to fit focus surface")
                    self.btn_startAcquisition.setChecked(False)
                    return
            else:
                # If checkbox not checked, set surface fitter to None
                self.multipointController.set_focus_map(None)
```

with:

```python
            if self.checkbox_useFocusMap.isChecked():
                # Try to fit the surface
                if self.focusMapWidget.fit_surface():
                    # If fit successful, set the surface fitter in controller
                    self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
                    if (
                        self.checkbox_withReflectionAutofocus.isChecked()
                        and self.focusMapWidget.checkbox_perRegionLaserAFOffset.isChecked()
                    ):
                        self.multipointController.set_region_laser_af_offsets(
                            self.focusMapWidget.region_laser_af_offsets
                        )
                    else:
                        self.multipointController.set_region_laser_af_offsets({})
                else:
                    QMessageBox.warning(self, "Warning", "Failed to fit focus surface")
                    self.btn_startAcquisition.setChecked(False)
                    return
            else:
                # If checkbox not checked, set surface fitter to None
                self.multipointController.set_focus_map(None)
                self.multipointController.set_region_laser_af_offsets({})
```

- [ ] **Step 7: Format + import smoke + full suite**

Run: `black --config pyproject.toml control/widgets.py control/gui_hcs.py`
Run: `python3 -c "import control.widgets, control.gui_hcs"`
Expected: no error.
Run: `python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py -q`
Expected: PASS (no regressions; the 17 feature tests included).

- [ ] **Step 8: Manual `--simulation` smoke test**

Run: `python3 main_hcs.py --simulation`
Verify by hand:
1. With Reflection AF OFF, the "Per-region laser AF offset" checkbox in the Focus Map panel is disabled.
2. Turn Reflection AF ON → checkbox becomes enabled. Turn it OFF → checkbox disables and unchecks.
3. With Use Focus Map ON + Reflection AF ON + the new checkbox ON, set a laser-AF reference, define one focus point per region (constant + Fit by Region, 1×1), and confirm Add/Update-Z populates `focusMapWidget.region_laser_af_offsets` (visible via the status label messages / a debugger).
4. Re-setting the laser-AF reference clears the captured offsets (status label message).
5. Export then import a focus-points CSV and confirm the `Offset_um` column round-trips.

Note any deviations; none should occur.

- [ ] **Step 9: Commit**

```bash
git add control/widgets.py control/gui_hcs.py
git commit -m "feat: per-region laser-AF offset GUI wiring (checkbox + acquisition push)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Line numbers are from the current `per-region-laser-af-offset` branch HEAD; if a prior task shifted lines, re-locate by the quoted code, not the number.
- Do not touch `move_to_target`, the focus-map z-baking (`multi_point_controller.py:755-765`), or `_apply_channel_z_offset` — composition is by construction (the per-channel offset is relative to wherever laser AF lands, which now includes the per-region target).
- The third multipoint widget that uses `_ApplyChannelOffsetMixin` (widgets.py ~9433) has no focus map, so it needs no changes.
