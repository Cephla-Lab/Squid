# Wellplate Multipoint Z Coordinates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the wellplate multipoint widget's coordinate CSVs an optional `z (mm)` column (save stamps parfocality-corrected current stage Z; load produces per-FOV Z the acquisition engine already honors), wire the Load-button Clear toggle, and stop focus-map runs from mutating the GUI's coordinates.

**Architecture:** All GUI changes live in `control/widgets.py` (`WellplateMultiPointWidget` + a shared module-level CSV→regions helper also used by `MultiPointWithFluidicsWidget`). Parfocal correction reuses the objective switcher's existing per-machine Z offset (`XERYON_OBJECTIVE_SWITCHER_*` in `control/_def.py`). The focus-map leak is fixed by deep-copying per-region FOV lists in `ScanPositionInformation.from_scan_coordinates` and dropping the `update_fov_z_level` write-back in `MultiPointController`.

**Tech Stack:** PyQt5 (via qtpy), pandas, numpy, pytest + pytest-qt (needs an X display; CI uses Xvfb).

**Spec:** `docs/superpowers/specs/2026-08-11-wellplate-z-coordinates-design.md`

## Global Constraints

- Black, line length 120: run `black --config pyproject.toml .` before every commit. Never reformat the excluded vendor files (`control/gxipy/`, `RCM_API.py`, `TUCam.py`, `dcam*.py`, `toupcam*.py`, `control/ndviewer_light/`, `fluidics_v2/`).
- All commands run from `software/`. Tests: `python3 -m pytest <file> -x -vv` (X display required).
- FOV coordinate entries MUST be tuples, never lists (`NavigationViewer.register_fovs_to_image` branches on `isinstance(fov, tuple)`, `control/core/core.py:1789`).
- All FOVs within one region MUST have the same tuple length — mixed 2-/3-tuples crash `get_region_bounds`' `np.array` (`control/core/scan_coordinates.py:630`).
- Region-center values MUST be mutable lists (`update_fov_z_level` mutates them, `control/core/scan_coordinates.py:690-693`). Loaded centers are `[x, y]` — no Z (nothing reads center Z; the focus map appends one when needed).
- CSV column names are exactly `region`, `x (mm)`, `y (mm)`, `z (mm)`. Button labels are exactly "Load New Coords" / "Clear Coords".
- NaN must never reach a coordinate tuple (`stage.move_z_to(nan)` would be issued unguarded).
- Use `squid.logging.get_logger`-based `self._log` (already present on the widgets), not stdlib `logging`.
- Tests may construct simulated scopes freely: suite-wide autouse cleanup in `tests/conftest.py` closes leaked `Microscope`/MPC instances.

---

### Task 1: Focus-map snapshot fix (stop mutating GUI coordinates)

**Files:**
- Modify: `control/core/multi_point_utils.py:13-25`
- Modify: `control/core/multi_point_controller.py:776-786`
- Test: `tests/control/core/test_multi_point_utils.py` (create)
- Test: `tests/control/test_MultiPointController.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `ScanPositionInformation.from_scan_coordinates` returns per-region FOV **list copies**; the controller's focus-map branch mutates only that snapshot. `ScanCoordinates.update_fov_z_level` keeps existing signature (unchanged, just no longer called by the controller).

- [ ] **Step 1: Write the failing snapshot test**

Create `tests/control/core/test_multi_point_utils.py`:

```python
import control.microscope
from control.core.multi_point_utils import ScanPositionInformation
from control.core.scan_coordinates import ScanCoordinates


def test_from_scan_coordinates_snapshots_fov_lists():
    scope = control.microscope.Microscope.build_from_global_config(True)
    sc = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)
    sc.region_centers = {"A1": [10.0, 10.0]}
    sc.region_fov_coordinates = {"A1": [(10.0, 10.0), (10.5, 10.0)]}

    info = ScanPositionInformation.from_scan_coordinates(sc)
    info.scan_region_fov_coords_mm["A1"][0] = (10.0, 10.0, 3.0)

    assert sc.region_fov_coordinates["A1"][0] == (10.0, 10.0)
```

- [ ] **Step 2: Write the failing controller-level test**

Append to `tests/control/test_MultiPointController.py` (reuses that file's existing `TestAcquisitionTracker`, `add_some_coordinates`, `select_some_configs`; add `import copy` to the imports if missing):

```python
class StubFocusMap:
    def interpolate(self, x, y, region_id=None):
        return 3.0


def test_focus_map_does_not_mutate_gui_scan_coordinates():
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)

    coords_before = copy.deepcopy(mpc.scanCoordinates.region_fov_coordinates)
    centers_before = copy.deepcopy(mpc.scanCoordinates.region_centers)

    mpc.set_focus_map(StubFocusMap())
    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    assert mpc.scanCoordinates.region_fov_coordinates == coords_before
    assert mpc.scanCoordinates.region_centers == centers_before


def test_acquisition_moves_to_per_fov_z():
    # Characterization/regression guard: the worker honors a 3-tuple coordinate's z
    # (multi_point_worker.py move_to_coordinate). Loaded-with-z plans rely on this.
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    stage = mpc.stage
    x = stage.get_config().X_AXIS.MIN_POSITION + 1.0
    y = stage.get_config().Y_AXIS.MIN_POSITION + 1.0
    z_target = 3.0
    # Inject regions the way a loaded CSV stores them: 3-tuple FOVs, [x, y] list centers.
    mpc.scanCoordinates.region_fov_coordinates = {"A1": [(x, y, z_target)]}
    mpc.scanCoordinates.region_centers = {"A1": [x, y]}

    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.run_acquisition()

    timeout_s = 5
    assert tt.started_event.wait(timeout_s)
    assert tt.finished_event.wait(timeout_s)

    # NZ defaults to 1, so the stage ends the run at the per-FOV z.
    assert mpc.stage.get_pos().z_mm == pytest.approx(z_target, abs=1e-3)
```

(Add `import copy` and `import pytest` to the file's imports if missing.)

- [ ] **Step 3: Run tests to verify the new behavior fails and the guard passes**

Run: `python3 -m pytest tests/control/core/test_multi_point_utils.py tests/control/test_MultiPointController.py::test_focus_map_does_not_mutate_gui_scan_coordinates tests/control/test_MultiPointController.py::test_acquisition_moves_to_per_fov_z -vv`
Expected: the snapshot test FAILS (`dict()` shares inner lists) and the focus-map test FAILS (the branch rewrites shared lists and calls `update_fov_z_level`); `test_acquisition_moves_to_per_fov_z` PASSES already — it is a characterization guard for existing worker behavior, not new behavior.

- [ ] **Step 4: Implement the deep copy**

In `control/core/multi_point_utils.py`, update the dataclass annotation and factory:

```python
@dataclass
class ScanPositionInformation:
    scan_region_coords_mm: List[List[float]]
    scan_region_names: List[str]
    scan_region_fov_coords_mm: Dict[str, List[Tuple[float, float, float]]]

    @staticmethod
    def from_scan_coordinates(scan_coordinates: ScanCoordinates):
        return ScanPositionInformation(
            scan_region_coords_mm=list(scan_coordinates.region_centers.values()),
            scan_region_names=list(scan_coordinates.region_centers.keys()),
            # Copy the inner lists too: the controller's focus-map branch rewrites
            # FOV entries in place, and sharing them would leak interpolated z into
            # the GUI's ScanCoordinates (and crash a later Save Coordinates).
            scan_region_fov_coords_mm={
                region_id: list(coords) for region_id, coords in scan_coordinates.region_fov_coordinates.items()
            },
        )
```

- [ ] **Step 5: Remove the controller write-back**

In `control/core/multi_point_controller.py:776-786`, the focus-map branch becomes (drop the `update_fov_z_level` line, fix the stale comment):

```python
            if self.focus_map and not acquire_current_fov:
                self._log.info("Using focus surface for Z interpolation")
                for region_id in scan_position_information.scan_region_names:
                    region_fov_coords = scan_position_information.scan_region_fov_coords_mm[region_id]
                    # Rewrite this acquisition's private snapshot; the GUI's
                    # ScanCoordinates keeps the user-configured coordinates.
                    for i, coords in enumerate(region_fov_coords):
                        x, y = coords[:2]  # This handles both (x,y) and (x,y,z) formats
                        z = self.focus_map.interpolate(x, y, region_id)
                        region_fov_coords[i] = (x, y, z)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/control/core/test_multi_point_utils.py tests/control/test_MultiPointController.py -vv`
Expected: PASS (run the whole MPC file to confirm no acquisition regression)

- [ ] **Step 7: Commit**

```bash
black --config pyproject.toml control/core/multi_point_utils.py control/core/multi_point_controller.py tests/control/core/test_multi_point_utils.py tests/control/test_MultiPointController.py
git add control/core/multi_point_utils.py control/core/multi_point_controller.py tests/control/core/test_multi_point_utils.py tests/control/test_MultiPointController.py
git commit -m "fix(multipoint): focus map interpolates a private snapshot, not GUI coordinates"
```

---

### Task 2: Load-button Clear toggle + dead Save↔Clear machinery removal

**Files:**
- Modify: `control/widgets.py` — `WellplateMultiPointWidget`: connects at `:7738-7739`, QTimer at `:8097`, `acquisition_is_finished` at `:8976`, methods at `:9066-9105` (and flag-set lines inside `load_coordinates` / `restore_cached_coordinates`)
- Test: `tests/control/test_widgets.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks (`has_loaded_coordinates` already exists, init `False` at `:7233`).
- Produces: `WellplateMultiPointWidget._set_has_loaded_coordinates(loaded: bool)`, `clear_loaded_coordinates()`, `on_load_or_clear_coordinates_clicked()`. Task 3's rewritten load/restore methods call `self._set_has_loaded_coordinates(True)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/control/test_widgets.py`:

```python
def _fake_wellplate_for_toggle(loaded: bool):
    fake = SimpleNamespace(
        scanCoordinates=MagicMock(),
        navigationViewer=MagicMock(),
        cached_loaded_coordinates_df=MagicMock(),
        cached_loaded_file_path="/tmp/coords.csv",
        text_loaded_coordinates=MagicMock(),
        btn_load_scan_coordinates=MagicMock(),
        has_loaded_coordinates=loaded,
        _log=MagicMock(),
    )
    fake._set_has_loaded_coordinates = lambda v: control.widgets.WellplateMultiPointWidget._set_has_loaded_coordinates(
        fake, v
    )
    return fake


def test_clear_loaded_coordinates_resets_state():
    fake = _fake_wellplate_for_toggle(loaded=True)

    control.widgets.WellplateMultiPointWidget.clear_loaded_coordinates(fake)

    fake.scanCoordinates.clear_regions.assert_called_once()
    fake.navigationViewer.clear_overlay.assert_called_once()
    assert fake.cached_loaded_coordinates_df is None
    assert fake.cached_loaded_file_path is None
    fake.text_loaded_coordinates.clear.assert_called_once()
    assert fake.has_loaded_coordinates is False
    fake.btn_load_scan_coordinates.setText.assert_called_with("Load New Coords")


def test_load_or_clear_click_clears_when_loaded():
    fake = _fake_wellplate_for_toggle(loaded=True)
    fake.clear_loaded_coordinates = MagicMock()

    control.widgets.WellplateMultiPointWidget.on_load_or_clear_coordinates_clicked(fake)

    fake.clear_loaded_coordinates.assert_called_once()


def test_load_or_clear_click_opens_dialog_when_not_loaded():
    fake = _fake_wellplate_for_toggle(loaded=False)
    fake.load_coordinates = MagicMock()

    with patch("control.widgets.QFileDialog.getOpenFileName", return_value=("/tmp/some.csv", "")):
        control.widgets.WellplateMultiPointWidget.on_load_or_clear_coordinates_clicked(fake)

    fake.load_coordinates.assert_called_once_with("/tmp/some.csv")


def test_load_or_clear_click_cancelled_dialog_loads_nothing():
    fake = _fake_wellplate_for_toggle(loaded=False)
    fake.load_coordinates = MagicMock()

    with patch("control.widgets.QFileDialog.getOpenFileName", return_value=("", "")):
        control.widgets.WellplateMultiPointWidget.on_load_or_clear_coordinates_clicked(fake)

    fake.load_coordinates.assert_not_called()


def test_dead_save_clear_toggle_machinery_removed():
    assert not hasattr(control.widgets.WellplateMultiPointWidget, "toggle_coordinate_controls")
    assert not hasattr(control.widgets.WellplateMultiPointWidget, "on_save_or_clear_coordinates_clicked")
```

`tests/control/test_widgets.py` already imports `MagicMock`/`patch`; add `from types import SimpleNamespace` to its imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/control/test_widgets.py -k "toggle or load_or_clear" -vv`
Expected: FAIL with `AttributeError` (methods don't exist yet)

- [ ] **Step 3: Implement the toggle**

In `control/widgets.py`, `WellplateMultiPointWidget`:

(a) Replace `on_load_coordinates_clicked` (`:9097-9105`) with:

```python
    def on_load_or_clear_coordinates_clicked(self):
        """Toggle for btn_load_scan_coordinates: open the load dialog when nothing
        is loaded, clear the loaded coordinates otherwise."""
        if self.has_loaded_coordinates:
            self.clear_loaded_coordinates()
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            self._log.info(f"Loading coordinates from {file_path}")
            self.load_coordinates(file_path)

    def clear_loaded_coordinates(self):
        self.scanCoordinates.clear_regions()
        self.navigationViewer.clear_overlay()
        self.cached_loaded_coordinates_df = None
        self.cached_loaded_file_path = None
        self.text_loaded_coordinates.clear()
        self._set_has_loaded_coordinates(False)

    def _set_has_loaded_coordinates(self, loaded: bool):
        self.has_loaded_coordinates = loaded
        self.btn_load_scan_coordinates.setText("Clear Coords" if loaded else "Load New Coords")
```

(b) Update the two references to the old handler name:
- `:7739` → `self.btn_load_scan_coordinates.clicked.connect(self.on_load_or_clear_coordinates_clicked)`
- `:8097` → `QTimer.singleShot(100, self.on_load_or_clear_coordinates_clicked)`

(c) Delete the dead machinery:
- Delete `toggle_coordinate_controls` (`:9066-9083`) and `on_save_or_clear_coordinates_clicked` (`:9085-9095`) entirely.
- `:7738` → `self.btn_save_scan_coordinates.clicked.connect(self.save_coordinates)`
- In `acquisition_is_finished`, delete the line `self.toggle_coordinate_controls(self.has_loaded_coordinates)` (`:8976`).

(d) Set the flag on the load paths (Task 3 rewrites these methods but keeps these lines):
- At the end of the `try:` block in `load_coordinates` (after the `text_loaded_coordinates.setText(...)` line at `:9175`), add `self._set_has_loaded_coordinates(True)`.
- At the end of `restore_cached_coordinates` (after the `setText` at `:9134`), add `self._set_has_loaded_coordinates(True)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/control/test_widgets.py -vv`
Expected: PASS (whole file — confirms no regression in other widget tests)

- [ ] **Step 5: Commit**

```bash
black --config pyproject.toml control/widgets.py tests/control/test_widgets.py
git add control/widgets.py tests/control/test_widgets.py
git commit -m "feat(wellplate): Load New Coords button toggles to Clear Coords; drop dead Save/Clear machinery"
```

---

### Task 3: Shared CSV→regions helper and Z-aware loading

**Files:**
- Modify: `control/widgets.py` — new module-level helper; rewrite `WellplateMultiPointWidget.load_coordinates` (`:9136-9179`), `restore_cached_coordinates` (`:9107-9134`), `MultiPointWithFluidicsWidget.load_coordinates` (`:9829-9865`)
- Test: `tests/control/test_widgets.py` (append)

**Interfaces:**
- Consumes: `self._set_has_loaded_coordinates(True)` from Task 2.
- Produces: module-level `control.widgets.load_coordinate_regions_from_dataframe(scan_coordinates, df) -> Tuple[Dict[str, list], bool]` — Task 4's round-trip test uses it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/control/test_widgets.py` (add `import pandas as pd` and `from control.core.scan_coordinates import ScanCoordinates` to its imports if missing):

```python
def _scan_coordinates_for_test():
    scope = control.microscope.Microscope.build_from_global_config(True)
    return ScanCoordinates(scope.objective_store, scope.stage, scope.camera)


def test_load_regions_with_z_column_builds_3tuples_and_list_centers():
    sc = _scan_coordinates_for_test()
    df = pd.DataFrame(
        {
            "region": ["A1", "A1", "B2"],
            "x (mm)": [10.0, 10.5, 20.0],
            "y (mm)": [10.0, 10.0, 20.0],
            "z (mm)": [3.0, 3.0, 3.5],
        }
    )

    fovs, z_dropped = control.widgets.load_coordinate_regions_from_dataframe(sc, df)

    assert z_dropped is False
    assert sc.region_fov_coordinates["A1"] == [(10.0, 10.0, 3.0), (10.5, 10.0, 3.0)]
    assert sc.region_fov_coordinates["B2"] == [(20.0, 20.0, 3.5)]
    assert sc.region_centers["A1"] == [10.25, 10.0]
    assert isinstance(sc.region_centers["A1"], list)
    assert fovs == sc.region_fov_coordinates


def test_load_regions_without_z_column_builds_2tuples():
    sc = _scan_coordinates_for_test()
    df = pd.DataFrame({"region": ["A1"], "x (mm)": [10.0], "y (mm)": [10.0]})

    fovs, z_dropped = control.widgets.load_coordinate_regions_from_dataframe(sc, df)

    assert z_dropped is False
    assert sc.region_fov_coordinates["A1"] == [(10.0, 10.0)]


def test_load_regions_with_nan_z_drops_the_column():
    sc = _scan_coordinates_for_test()
    df = pd.DataFrame(
        {"region": ["A1", "A1"], "x (mm)": [10.0, 10.5], "y (mm)": [10.0, 10.0], "z (mm)": [3.0, float("nan")]}
    )

    fovs, z_dropped = control.widgets.load_coordinate_regions_from_dataframe(sc, df)

    assert z_dropped is True
    assert sc.region_fov_coordinates["A1"] == [(10.0, 10.0), (10.5, 10.0)]


def test_load_regions_with_out_of_range_z_raises():
    sc = _scan_coordinates_for_test()
    bad_z = control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE + 1.0
    df = pd.DataFrame({"region": ["A1"], "x (mm)": [10.0], "y (mm)": [10.0], "z (mm)": [bad_z]})

    with pytest.raises(ValueError, match="z"):
        control.widgets.load_coordinate_regions_from_dataframe(sc, df)


def test_load_regions_missing_required_columns_raises():
    sc = _scan_coordinates_for_test()
    df = pd.DataFrame({"region": ["A1"], "x (mm)": [10.0]})

    with pytest.raises(ValueError, match="region"):
        control.widgets.load_coordinate_regions_from_dataframe(sc, df)


def test_loaded_regions_survive_update_fov_z_level():
    # Regression: tuple centers used to crash update_fov_z_level (focus-map path).
    sc = _scan_coordinates_for_test()
    df = pd.DataFrame({"region": ["A1"], "x (mm)": [10.0], "y (mm)": [10.0]})
    control.widgets.load_coordinate_regions_from_dataframe(sc, df)

    sc.update_fov_z_level("A1", 0, 4.0)

    assert sc.region_fov_coordinates["A1"][0] == (10.0, 10.0, 4.0)
    assert sc.region_centers["A1"] == [10.0, 10.0, 4.0]


def test_fluidics_widget_load_coordinates_loads_z(tmp_path):
    # The fluidics widget's loader goes through the same shared helper.
    csv_path = tmp_path / "coords.csv"
    pd.DataFrame(
        {"region": ["A1"], "x (mm)": [10.0], "y (mm)": [10.0], "z (mm)": [3.0]}
    ).to_csv(csv_path, index=False)

    fake = SimpleNamespace(
        scanCoordinates=_scan_coordinates_for_test(),
        navigationViewer=MagicMock(),
        _log=MagicMock(),
    )

    control.widgets.MultiPointWithFluidicsWidget.load_coordinates(fake, str(csv_path))

    assert fake.scanCoordinates.region_fov_coordinates["A1"] == [(10.0, 10.0, 3.0)]
    assert fake.scanCoordinates.region_centers["A1"] == [10.0, 10.0]
    fake.navigationViewer.register_fovs_to_image.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/control/test_widgets.py -k load_regions -vv`
Expected: FAIL with `AttributeError: module 'control.widgets' has no attribute 'load_coordinate_regions_from_dataframe'`

- [ ] **Step 3: Implement the helper**

Add module-level in `control/widgets.py` (above the widget classes, near other module-level helpers):

```python
def load_coordinate_regions_from_dataframe(scan_coordinates, df):
    """Clear scan_coordinates and populate its regions from a coordinates dataframe.

    The dataframe must have 'region', 'x (mm)' and 'y (mm)' columns. If a 'z (mm)'
    column is present and fully populated, FOVs are loaded as (x, y, z) tuples so
    the acquisition moves Z per FOV; otherwise FOVs are (x, y). Region centers are
    stored as mutable [x, y] lists without z — nothing reads a center z, and
    ScanCoordinates.update_fov_z_level appends one when a focus map sets it.

    Returns:
        (region_fov_coords, z_dropped): dict of region_id -> list of loaded FOV
        tuples (for navigation-viewer registration by the caller), and whether a
        'z (mm)' column was present but ignored because it contained empty cells.

    Raises:
        ValueError: on missing required columns, or z values outside SOFTWARE_POS_LIMIT.
    """
    required_columns = ["region", "x (mm)", "y (mm)"]
    if not all(col in df.columns for col in required_columns):
        raise ValueError("CSV file must contain 'region', 'x (mm)', and 'y (mm)' columns")

    has_z = "z (mm)" in df.columns
    z_dropped = False
    if has_z and df["z (mm)"].isna().any():
        # Keep every region's tuples homogeneous and never let NaN reach the stage.
        has_z = False
        z_dropped = True

    if has_z:
        z_min = control._def.SOFTWARE_POS_LIMIT.Z_NEGATIVE
        z_max = control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE
        out_of_range = df[(df["z (mm)"] < z_min) | (df["z (mm)"] > z_max)]
        if not out_of_range.empty:
            raise ValueError(
                f"z (mm) values outside software limits [{z_min}, {z_max}] mm: "
                f"{sorted(out_of_range['z (mm)'].unique().tolist())}"
            )

    scan_coordinates.clear_regions()

    region_fov_coords = {}
    for region_id in df["region"].unique():
        region_points = df[df["region"] == region_id]
        if has_z:
            coords = [
                (float(x), float(y), float(z))
                for x, y, z in zip(region_points["x (mm)"], region_points["y (mm)"], region_points["z (mm)"])
            ]
        else:
            coords = [(float(x), float(y)) for x, y in zip(region_points["x (mm)"], region_points["y (mm)"])]
        scan_coordinates.region_fov_coordinates[region_id] = coords
        scan_coordinates.region_centers[region_id] = [
            float(region_points["x (mm)"].mean()),
            float(region_points["y (mm)"].mean()),
        ]
        region_fov_coords[region_id] = coords

    return region_fov_coords, z_dropped
```

- [ ] **Step 4: Rewrite the three loaders to use it**

(a) `WellplateMultiPointWidget.load_coordinates` (`:9136-9179`) becomes:

```python
    def load_coordinates(self, file_path: str):
        """Load scan coordinates (optionally with per-FOV z) from a CSV file."""
        try:
            df = pd.read_csv(file_path)
            region_fov_coords, z_dropped = load_coordinate_regions_from_dataframe(self.scanCoordinates, df)

            # Cache the dataframe and file path
            self.cached_loaded_coordinates_df = df.copy()
            self.cached_loaded_file_path = file_path

            for coords in region_fov_coords.values():
                self.navigationViewer.register_fovs_to_image(coords)

            if z_dropped:
                QMessageBox.warning(
                    self,
                    "Z column ignored",
                    "The 'z (mm)' column contains empty values; coordinates were loaded as XY-only.",
                )

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")
            self.text_loaded_coordinates.setText(f"Loaded: {file_path}")
            self._set_has_loaded_coordinates(True)

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")
```

(b) `WellplateMultiPointWidget.restore_cached_coordinates` (`:9107-9134`) becomes (this also fixes the pre-existing crash: it called the nonexistent `register_fov_to_image`):

```python
    def restore_cached_coordinates(self):
        """Restore previously loaded coordinates from the cached dataframe."""
        if self.cached_loaded_coordinates_df is None:
            return

        region_fov_coords, _ = load_coordinate_regions_from_dataframe(
            self.scanCoordinates, self.cached_loaded_coordinates_df
        )
        for coords in region_fov_coords.values():
            self.navigationViewer.register_fovs_to_image(coords)

        if self.cached_loaded_file_path:
            self.text_loaded_coordinates.setText(f"Loaded: {self.cached_loaded_file_path}")
        self._set_has_loaded_coordinates(True)
```

(c) `MultiPointWithFluidicsWidget.load_coordinates` (`:9829-9865`): keep its docstring and except-block; the body of the `try:` becomes:

```python
            df = pd.read_csv(file_path)
            region_fov_coords, z_dropped = load_coordinate_regions_from_dataframe(self.scanCoordinates, df)

            for coords in region_fov_coords.values():
                self.navigationViewer.register_fovs_to_image(coords)

            if z_dropped:
                QMessageBox.warning(
                    self,
                    "Z column ignored",
                    "The 'z (mm)' column contains empty values; coordinates were loaded as XY-only.",
                )

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/control/test_widgets.py -vv`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
black --config pyproject.toml control/widgets.py tests/control/test_widgets.py
git add control/widgets.py tests/control/test_widgets.py
git commit -m "feat(wellplate): load optional z (mm) from coordinate CSVs via shared helper"
```

---

### Task 4: Z-aware save with parfocal correction

**Files:**
- Modify: `control/widgets.py` — new module-level `_objective_relative_z_mm`, `parfocal_adjusted_z_mm`, and `coordinate_rows_for_save`; rewrite `WellplateMultiPointWidget.save_coordinates` (`:9181-9231`)
- Test: `tests/control/test_widgets.py` (append)

**Interfaces:**
- Consumes: existing `control._def` machine config (`USE_XERYON`, `XERYON_OBJECTIVE_SWITCHER_POS_2`, `XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM`); `load_coordinate_regions_from_dataframe` and the `_scan_coordinates_for_test()` test helper (already appended to `tests/control/test_widgets.py`) from Task 3.
- Produces: `control.widgets.parfocal_adjusted_z_mm(current_objective, target_objective, z_mm) -> float`; `control.widgets.coordinate_rows_for_save(region_fov_coordinates, z_default_mm) -> pd.DataFrame`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/control/test_widgets.py`:

```python
def test_parfocal_adjusted_z_mm_uses_xeryon_switcher_offset(monkeypatch):
    monkeypatch.setattr(control._def, "USE_XERYON", True)
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", ["4x", "10x"])
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", ["20x", "40x"])
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM", 2)

    # pos1 -> pos2: the changer parks the stage 2 mm lower for position-2 objectives.
    assert control.widgets.parfocal_adjusted_z_mm("10x", "20x", 3.0) == pytest.approx(1.0)
    # pos2 -> pos1: back up.
    assert control.widgets.parfocal_adjusted_z_mm("20x", "10x", 1.0) == pytest.approx(3.0)
    # Same position (either one): unchanged.
    assert control.widgets.parfocal_adjusted_z_mm("20x", "40x", 1.0) == pytest.approx(1.0)
    assert control.widgets.parfocal_adjusted_z_mm("4x", "10x", 3.0) == pytest.approx(3.0)
    # Target == current: unmodified.
    assert control.widgets.parfocal_adjusted_z_mm("20x", "20x", 1.0) == pytest.approx(1.0)


def test_parfocal_adjusted_z_mm_is_noop_without_switcher(monkeypatch):
    monkeypatch.setattr(control._def, "USE_XERYON", False)

    assert control.widgets.parfocal_adjusted_z_mm("10x", "20x", 3.0) == pytest.approx(3.0)


def test_coordinate_rows_for_save_stamps_default_z_and_keeps_existing_z():
    fovs = {"A1": [(10.0, 10.0), (10.5, 10.0, 4.0)], "B2": [(20.0, 20.0)]}

    df = control.widgets.coordinate_rows_for_save(fovs, 3.0)

    assert list(df.columns) == ["region", "x (mm)", "y (mm)", "z (mm)"]
    assert df["region"].tolist() == ["A1", "A1", "B2"]
    assert df["z (mm)"].tolist() == [3.0, 4.0, 3.0]


def test_save_load_round_trip_preserves_z():
    sc = _scan_coordinates_for_test()
    fovs = {"A1": [(10.0, 10.0), (10.5, 10.0)]}

    df = control.widgets.coordinate_rows_for_save(fovs, 3.25)
    control.widgets.load_coordinate_regions_from_dataframe(sc, df)

    assert sc.region_fov_coordinates["A1"] == [(10.0, 10.0, 3.25), (10.5, 10.0, 3.25)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/control/test_widgets.py -k "parfocal or coordinate_rows or round_trip" -vv`
Expected: FAIL with `AttributeError` (functions don't exist yet)

- [ ] **Step 3: Implement the pure helpers**

Add module-level in `control/widgets.py`, next to `load_coordinate_regions_from_dataframe`:

```python
def _objective_relative_z_mm(objective_name):
    """Relative stage-Z frame of an objective under the Xeryon 2-position switcher:
    the changer parks the stage XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM lower while a
    position-2 objective is in use (objective_changer_2_pos_controller.moveToPosition2).
    0 for position-1 objectives, names not in the position lists, and machines without
    the switcher."""
    if not control._def.USE_XERYON:
        return 0.0
    if objective_name in control._def.XERYON_OBJECTIVE_SWITCHER_POS_2:
        return -float(control._def.XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM)
    return 0.0


def parfocal_adjusted_z_mm(current_objective, target_objective, z_mm):
    """Shift a stage z from the current objective's frame to the target objective's,
    using the objective switcher's per-machine Z offset (no-op without a switcher)."""
    return z_mm + (_objective_relative_z_mm(target_objective) - _objective_relative_z_mm(current_objective))


def coordinate_rows_for_save(region_fov_coordinates, z_default_mm):
    """Flatten region FOV coordinates into a coordinates dataframe with a z column.

    FOVs that already carry z (3-tuples) keep it; 2-tuple FOVs get z_default_mm.
    """
    rows = []
    for region_id, fov_coords in region_fov_coordinates.items():
        for coord in fov_coords:
            z = float(coord[2]) if len(coord) == 3 else z_default_mm
            rows.append([region_id, float(coord[0]), float(coord[1]), z])
    return pd.DataFrame(rows, columns=["region", "x (mm)", "y (mm)", "z (mm)"])
```

- [ ] **Step 4: Rewrite `save_coordinates`**

`WellplateMultiPointWidget.save_coordinates` (`:9181-9231`) becomes:

```python
    def save_coordinates(self):
        """Save scan coordinates to CSV files (one per objective).

        Each FOV row carries 'z (mm)': the current stage z for the objective in
        use, shifted for the other objectives by the objective switcher's
        per-machine Z offset (XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM).
        """
        folder_path, _ = QFileDialog.getSaveFileName(
            self, "Create Folder for Scan Coordinates", "", "Folder"  # Default directory
        )
        if not folder_path:
            return

        os.makedirs(folder_path, exist_ok=True)
        folder_name = os.path.basename(folder_path)
        current_objective = self.objectiveStore.current_objective
        z_current_mm = self.stage.get_pos().z_mm

        def _save_for_objective(objective_name):
            self.objectiveStore.set_current_objective(objective_name)
            self.update_coordinates()
            z_mm = parfocal_adjusted_z_mm(current_objective, objective_name, z_current_mm)
            df = coordinate_rows_for_save(self.scanCoordinates.region_fov_coordinates, z_mm)
            file_path = os.path.join(folder_path, f"{folder_name}_{objective_name}.csv")
            df.to_csv(file_path, index=False)
            self._log.info(f"Saved scan coordinates to {file_path}")

        try:
            for objective_name in self.objectiveStore.objectives_dict.keys():
                if objective_name != current_objective:
                    _save_for_objective(objective_name)
            _save_for_objective(current_objective)
        except Exception as e:
            self._log.error(f"Failed to save coordinates: {str(e)}")
            QMessageBox.warning(self, "Save Error", f"Failed to save coordinates to {folder_path}\nError: {str(e)}")
        finally:
            # Leave the store on the objective the user had selected even if a save failed.
            if self.objectiveStore.current_objective != current_objective:
                self.objectiveStore.set_current_objective(current_objective)
                self.update_coordinates()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/control/test_widgets.py -vv`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
black --config pyproject.toml control/widgets.py tests/control/test_widgets.py
git add control/widgets.py tests/control/test_widgets.py
git commit -m "feat(wellplate): save z (mm) with per-objective parfocal correction"
```

---

### Task 5: Full-suite verification and end-to-end smoke

**Files:**
- No new source changes expected (fix regressions if any surface).

**Interfaces:**
- Consumes: everything above.
- Produces: green suite, formatted tree.

- [ ] **Step 1: Run the full test suite (what CI runs)**

Run: `python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py`
Expected: PASS. Fix any regression before proceeding (each fix follows its own small test-first loop).

- [ ] **Step 2: Black check over the tree**

Run: `black --config pyproject.toml --check .`
Expected: clean. If not: `black --config pyproject.toml .` and include in the next commit.

- [ ] **Step 3: End-to-end smoke in simulation (manual — needs a human at the GUI)**

If executing agentically, ask the user to drive this step (or report it as pending). Run `python3 main_hcs.py --simulation`, then in the Wellplate Multipoint panel:
1. Select a couple of wells (Select Wells mode), click "Save Coordinates", pick a folder; verify the per-objective CSVs contain a `z (mm)` column (identical across files unless the machine config enables `USE_XERYON` with position-2 objectives).
2. Switch XY mode to "Load Coordinates", load the saved CSV for the current objective; button flips to "Clear Coords"; the map shows the FOVs.
3. Switch mode away and back; coordinates restore and the button still reads "Clear Coords".
4. Click "Clear Coords"; regions and overlay clear; button reverts to "Load New Coords".
5. Start a short acquisition from loaded coordinates (1 channel, NZ=1) and confirm the top-level `coordinates.csv` in the experiment folder carries the loaded z values.
Close the app.

- [ ] **Step 4: Commit anything outstanding**

```bash
git status
git add -A
git commit -m "chore: formatting and test fixes for wellplate z coordinates" || echo "nothing to commit"
```
