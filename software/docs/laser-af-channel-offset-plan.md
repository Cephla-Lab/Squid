# Per-Channel Z-Offset for Laser Autofocus — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply a per-channel z-offset from the laser-AF reference plane during acquisition and (opt-in) when switching channels in live view, with capture / reset UI affordances and a robust delta-tracking algorithm.

**Architecture:** Reuse the existing `AcquisitionChannel.z_offset_um` field. In acquisition, the worker tracks a running offset and emits only the minimum stage/piezo moves needed (`_apply_channel_z_offset` / `_reset_channel_z_offset`). In live view, the widget computes the absolute target z each time it switches channels, robust against manual jogs. Behavior gated on laser AF being the active AF method AND a new acquisition-widget checkbox.

**Tech Stack:** Python 3, PyQt5 (qtpy compat), Pydantic v2, pytest. CI uses `black==25.12.0`; run `black --config software/pyproject.toml software/` before commit.

**Spec:** `software/docs/laser-af-channel-offset-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `software/control/core/multi_point_utils.py` | Add `apply_channel_offset` to `AcquisitionParameters` |
| `software/control/core/config/repository.py` | Add `"ZOffset"` setting mapping; fix create-from-general bug |
| `software/control/core/laser_auto_focus_controller.py` | Add `signal_reference_changed` signal |
| `software/control/core/multi_point_worker.py` | Add `_apply/_reset/_move_z_for_offset` helpers; integrate; abort handling; logging; delete `handle_z_offset` |
| `software/control/core/multi_point_controller.py` | Plumb `apply_channel_offset` from widgets to worker via `AcquisitionParameters` |
| `software/control/widgets.py` | `LiveControlWidget` + `NapariLiveWidget` row, `LaserAutofocusSettingWidget` Reset-all button, three acquisition-widget checkboxes |
| `software/control/models/acquisition_config.py` | Field description tweak on `z_offset_um` |
| `software/tests/control/core/config/test_repository.py` | Tests for ZOffset persistence + create-from-general fix (extend existing file) |
| `software/tests/control/test_MultiPointWorker_offsets.py` (new) | Unit tests for delta tracking (stage, piezo, abort, time-lapse) |

---

## Pre-flight

- [ ] **Confirm worktree, baseline tests**

```bash
cd "/Users/hongquan/Cephla Dropbox/Hongquan Li/Github/AI/Squid-Claude2/worktrees/laser-af-channel-offset"
git status   # should be clean, branch feat/laser-af-channel-offset
source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid
pytest software/tests/control/core/config/test_repository.py -q
```

Expected: all tests pass on baseline. If any fail, stop and investigate before proceeding (so we can distinguish pre-existing failures from regressions).

---

## Task 1: Fix `repository.py` create-from-general path to preserve `z_offset_um`

**Files:**
- Modify: `software/control/core/config/repository.py:761-782`
- Modify (tests): `software/tests/control/core/config/test_repository.py`

This is the M1 fix from the spec — pre-existing bug exposed by the new feature.

- [ ] **Step 1: Write failing test for create-from-general z_offset_um preservation**

Append to `software/tests/control/core/config/test_repository.py`:

```python
class TestUpdateChannelSettingPreservesZOffset:
    """Regression tests for M1: create-from-general must preserve z_offset_um."""

    @pytest.fixture
    def repo_general_only(self, tmp_path):
        """Profile with general.yaml only (no objective override) where channels have non-zero z_offset_um."""
        machine = tmp_path / "machine_configs"
        machine.mkdir()
        (machine / "illumination_channel_config.yaml").write_text(
            "version: 1\n"
            "channels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
            '  - name: "561nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D3\n"
            "    wavelength_nm: 561\n"
        )
        profile = tmp_path / "user_profiles" / "default"
        (profile / "channel_configs").mkdir(parents=True)
        (profile / "laser_af_configs").mkdir()
        (profile / "channel_configs" / "general.yaml").write_text(
            "version: 1.0\n"
            "channels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings: {exposure_time_ms: 20.0, gain_mode: 10.0}\n"
            "    illumination_settings: {illumination_channel: \"488nm\", intensity: 20.0}\n"
            "    z_offset_um: 1.5\n"
            '  - name: "561nm"\n'
            '    display_color: "#FF8000"\n'
            "    camera_settings: {exposure_time_ms: 30.0, gain_mode: 5.0}\n"
            "    illumination_settings: {illumination_channel: \"561nm\", intensity: 25.0}\n"
            "    z_offset_um: -0.7\n"
        )
        repo = ConfigRepository(base_path=tmp_path)
        repo.set_profile("default")
        return repo

    def test_first_objective_update_preserves_other_channels_z_offset(self, repo_general_only):
        """Setting ExposureTime on one channel must not zero z_offset_um on others."""
        result = repo_general_only.update_channel_setting("20x", "488nm", "ExposureTime", 99.0)
        assert result is True

        obj = repo_general_only.get_objective_config("20x")
        ch488 = next(c for c in obj.channels if c.name == "488nm")
        ch561 = next(c for c in obj.channels if c.name == "561nm")

        # The channel we touched: ExposureTime updated, original z_offset_um preserved
        assert ch488.camera_settings.exposure_time_ms == 99.0
        assert ch488.z_offset_um == 1.5

        # The other channel: z_offset_um carried over from general.yaml
        assert ch561.z_offset_um == -0.7
```

- [ ] **Step 2: Run test and confirm it fails**

Run: `pytest software/tests/control/core/config/test_repository.py::TestUpdateChannelSettingPreservesZOffset -v`
Expected: FAIL with `ch488.z_offset_um == 0.0 != 1.5` (or similar) — the create-from-general path drops the field.

- [ ] **Step 3: Apply the one-line fix**

In `software/control/core/config/repository.py`, in the `AcquisitionChannel(...)` constructor inside the `obj_config = ObjectiveChannelConfig(...)` list comprehension (around line 763-781), add `z_offset_um=ch.z_offset_um`:

```python
obj_config = ObjectiveChannelConfig(
    version=1.1,
    channels=[
        AcquisitionChannel(
            name=ch.name,
            display_color=ch.display_color,
            camera=ch.camera,
            camera_settings=CameraSettings(
                exposure_time_ms=ch.camera_settings.exposure_time_ms,
                gain_mode=ch.camera_settings.gain_mode,
                pixel_format=ch.camera_settings.pixel_format,
            ),
            filter_wheel=None,
            filter_position=None,
            z_offset_um=ch.z_offset_um,            # <-- ADD THIS LINE (M1 fix)
            illumination_settings=IlluminationSettings(
                illumination_channel=None,
                intensity=ch.illumination_settings.intensity,
            ),
        )
        for ch in general_config.channels
    ],
)
```

- [ ] **Step 4: Run test again to confirm it passes**

Run: `pytest software/tests/control/core/config/test_repository.py::TestUpdateChannelSettingPreservesZOffset -v`
Expected: PASS.

- [ ] **Step 5: Run wider repo tests to confirm no regression**

Run: `pytest software/tests/control/core/config/test_repository.py -q`
Expected: All previous tests still pass.

- [ ] **Step 6: Commit**

```bash
black --config software/pyproject.toml software/control/core/config/repository.py software/tests/control/core/config/test_repository.py
git add software/control/core/config/repository.py software/tests/control/core/config/test_repository.py
git commit -m "fix(repository): preserve z_offset_um when creating objective config from general"
```

---

## Task 2: Add `"ZOffset"` setting mapping in `update_channel_setting`

**Files:**
- Modify: `software/control/core/config/repository.py:737-829`
- Modify: `software/tests/control/core/config/test_repository.py`

- [ ] **Step 1: Write failing test for ZOffset setting key**

Append to `TestUpdateChannelSettingPreservesZOffset` in `test_repository.py`:

```python
    def test_zoffset_setting_updates_and_persists(self, repo_general_only):
        """update_channel_setting('ZOffset', value) writes z_offset_um and persists to YAML."""
        result = repo_general_only.update_channel_setting("20x", "488nm", "ZOffset", 3.25)
        assert result is True

        # In-memory update
        obj = repo_general_only.get_objective_config("20x")
        ch488 = next(c for c in obj.channels if c.name == "488nm")
        assert ch488.z_offset_um == 3.25

        # Persisted to YAML — clear cache and reload
        repo_general_only.clear_profile_cache()
        obj_reloaded = repo_general_only.get_objective_config("20x")
        ch488_reloaded = next(c for c in obj_reloaded.channels if c.name == "488nm")
        assert ch488_reloaded.z_offset_um == 3.25

    def test_zoffset_zero_value_persists(self, repo_general_only):
        """Writing 0.0 must persist explicitly (not be omitted)."""
        # First set non-zero, then back to zero
        repo_general_only.update_channel_setting("20x", "488nm", "ZOffset", 2.0)
        assert repo_general_only.update_channel_setting("20x", "488nm", "ZOffset", 0.0) is True
        repo_general_only.clear_profile_cache()
        obj = repo_general_only.get_objective_config("20x")
        ch488 = next(c for c in obj.channels if c.name == "488nm")
        assert ch488.z_offset_um == 0.0
```

- [ ] **Step 2: Run tests and confirm both fail**

Run: `pytest software/tests/control/core/config/test_repository.py::TestUpdateChannelSettingPreservesZOffset -v`
Expected: 2 new tests FAIL with `Unknown setting: ZOffset` log warning and `result is False`.

- [ ] **Step 3: Add ZOffset mapping and a `location == "channel"` branch**

In `software/control/core/config/repository.py`, find `setting_mapping = {...}` (around line 738-744) and add the new key:

```python
setting_mapping = {
    "ExposureTime": ("camera", "exposure_time_ms"),
    "AnalogGain": ("camera", "gain_mode"),
    "IlluminationIntensity": ("illumination", "intensity"),
    "IlluminationIris": ("confocal_hw", "illumination_iris"),
    "EmissionIris": ("confocal_hw", "emission_iris"),
    "ZOffset": ("channel", "z_offset_um"),
}
```

Then find the `if location == "confocal_hw":` block (around line 791) and add a new branch handling the channel-level case (top-level field, ignoring confocal_mode since z_offset_um isn't a per-mode setting):

```python
# Iris settings go to confocal_hardware_settings (applies in both modes)
if location == "confocal_hw":
    ...
elif location == "channel":
    # Top-level channel field — single value regardless of confocal_mode
    setattr(acq_channel, field, value)
elif confocal_mode:
    ...
```

- [ ] **Step 4: Run tests and confirm they pass**

Run: `pytest software/tests/control/core/config/test_repository.py::TestUpdateChannelSettingPreservesZOffset -v`
Expected: all 3 tests in the class PASS.

- [ ] **Step 5: Run the full repo test file**

Run: `pytest software/tests/control/core/config/test_repository.py -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
black --config software/pyproject.toml software/control/core/config/repository.py software/tests/control/core/config/test_repository.py
git add software/control/core/config/repository.py software/tests/control/core/config/test_repository.py
git commit -m "feat(repository): add ZOffset setting key to update_channel_setting"
```

---

## Task 3: Update `AcquisitionChannel.z_offset_um` field description

**Files:**
- Modify: `software/control/models/acquisition_config.py:136`

Quick documentation-only change so anyone reading the model sees the new semantic.

- [ ] **Step 1: Update field description**

In `software/control/models/acquisition_config.py`, change line 136:

```python
# Old:
z_offset_um: float = Field(0.0, description="Z offset in micrometers")

# New:
z_offset_um: float = Field(
    0.0,
    description=(
        "Z offset (µm) from the laser AF reference plane. Applied during acquisition "
        "and (opt-in) on live channel switch only when laser autofocus is the active AF method. "
        "Sample-dependent — re-capture or reset when starting a new sample."
    ),
)
```

- [ ] **Step 2: Verify no test regression**

Run: `pytest software/tests/control/test_acquisition_config_models.py -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
black --config software/pyproject.toml software/control/models/acquisition_config.py
git add software/control/models/acquisition_config.py
git commit -m "docs(model): clarify z_offset_um semantics for laser AF integration"
```

---

## Task 4: Add `signal_reference_changed` to `LaserAutofocusController`

**Files:**
- Modify: `software/control/core/laser_auto_focus_controller.py`

This signal lets live widgets enable/disable the "Apply on channel switch" checkbox when the reference state flips.

- [ ] **Step 1: Add the signal definition**

In `software/control/core/laser_auto_focus_controller.py`, locate the signal block in `LaserAutofocusController` (around line 22-26) and add a new signal:

```python
class LaserAutofocusController(QObject):

    signal_displacement_um = Signal(float)
    signal_cross_correlation = Signal(float)
    signal_piezo_position_update = Signal()
    signal_reference_changed = Signal(bool)  # emitted when has_reference flips; arg = new has_reference
```

- [ ] **Step 2: Emit on successful set_reference**

In `set_reference()` (around line 388-453), at the end of the function (right before the final return / success path), emit the signal:

```python
        # ... existing reference-setting and save code ...

        self.signal_reference_changed.emit(True)
        return True
```

If there are multiple return paths in `set_reference`, ensure only the success path emits `True`. Failure paths (early returns with `False`) must not emit. Read the function carefully to confirm.

- [ ] **Step 3: Emit on reference clear paths**

Search the file for `has_reference=False` assignments and `set_reference_image(None)` calls. For each location that clears the reference *outside* of `set_reference()` (e.g., during `init_reference` setup, where the old reference is invalidated), add a single emit after the clearing operation:

```python
self.signal_reference_changed.emit(False)
```

If a path clears then immediately sets a new reference (e.g., re-calibration inside `set_reference`), do not emit two times; emit only the final state.

Run a grep to locate the relevant spots:

```bash
grep -n "has_reference=False\|set_reference_image(None)\|has_reference.*False" software/control/core/laser_auto_focus_controller.py
```

- [ ] **Step 4: Write a smoke test for the signal**

Append to `software/tests/control/test_squid_laser_engine.py` (or create `software/tests/control/test_laser_autofocus_signals.py` if more appropriate) — first check which file is more natural:

```bash
grep -n "LaserAutofocusController\|signal_displacement_um\|signal_cross_correlation" software/tests/control/test_squid_laser_engine.py
```

If the existing file already constructs the controller for tests, append:

```python
def test_signal_reference_changed_emitted_on_set_reference(qtbot):
    """signal_reference_changed fires True when set_reference succeeds."""
    controller = make_test_laser_af_controller()  # use whatever helper already exists
    with qtbot.waitSignal(controller.signal_reference_changed, timeout=2000) as blocker:
        success = controller.set_reference()
    assert success is True
    assert blocker.args == [True]
```

If no existing infrastructure makes constructing the controller easy in a unit test, **skip the test** and rely on manual verification in Task 18's smoke test. Note this in the commit message.

- [ ] **Step 5: Run test (if added) or do a smoke import check**

Either:
- `pytest software/tests/control/test_squid_laser_engine.py -k reference_changed -v` → PASS
- Or: `python -c "from control.core.laser_auto_focus_controller import LaserAutofocusController; print(LaserAutofocusController.signal_reference_changed)"` → prints `<unbound signal Signal>` or similar without error.

- [ ] **Step 6: Commit**

```bash
black --config software/pyproject.toml software/control/core/laser_auto_focus_controller.py
git add software/control/core/laser_auto_focus_controller.py software/tests/control/test_squid_laser_engine.py  # adjust test path if relevant
git commit -m "feat(laser-af): emit signal_reference_changed on reference set/clear"
```

---

## Task 5: Plumb `apply_channel_offset` through `AcquisitionParameters` and worker init

**Files:**
- Modify: `software/control/core/multi_point_utils.py:29-65` (`AcquisitionParameters` dataclass)
- Modify: `software/control/core/multi_point_worker.py:65-130` (worker `__init__`)
- Modify: `software/control/core/multi_point_controller.py` (build_params, controller state)

- [ ] **Step 1: Add field to `AcquisitionParameters`**

In `software/control/core/multi_point_utils.py`, add to the `AcquisitionParameters` dataclass (after `use_fluidics`, before optional fields):

```python
@dataclass
class AcquisitionParameters:
    # ... existing fields ...
    use_fluidics: bool
    apply_channel_offset: bool = True   # New: gates per-channel z-offset during acquisition
    skip_saving: bool = False
    # ... rest unchanged ...
```

The default `True` matches the GUI default and keeps remote (TCP/MCP) callers backward-compatible.

- [ ] **Step 2: Read it in `MultiPointWorker.__init__`**

In `software/control/core/multi_point_worker.py`, find the block around line 119-124 where `do_autofocus`, `do_reflection_af`, `use_piezo` are pulled from `acquisition_parameters`, and add:

```python
self.do_autofocus = acquisition_parameters.do_autofocus
self.do_reflection_af = acquisition_parameters.do_reflection_autofocus
self.use_piezo = acquisition_parameters.use_piezo
self.apply_channel_offset = acquisition_parameters.apply_channel_offset   # New
self.display_resolution_scaling = acquisition_parameters.display_resolution_scaling
```

Also initialize the offset tracker (near other one-time-per-FOV initialisation; placing alongside `_laser_af_successes` is fine):

```python
self._current_z_offset_um: float = 0.0
```

- [ ] **Step 3: Add controller state and plumb into build_params**

In `software/control/core/multi_point_controller.py`, around line 213-214 where `do_autofocus = False` / `do_reflection_af = False` are initialised:

```python
self.do_autofocus = False
self.do_reflection_af = False
self.apply_channel_offset = True   # New: default on; overridden by acquisition widgets
```

Then in `build_params(...)` (around line 945-946 where `do_autofocus=self.do_autofocus, do_reflection_autofocus=self.do_reflection_af` are passed), add:

```python
do_autofocus=self.do_autofocus,
do_reflection_autofocus=self.do_reflection_af,
apply_channel_offset=self.apply_channel_offset,   # New
```

Add a setter on `MultiPointController`:

```python
def set_apply_channel_offset(self, flag: bool):
    self.apply_channel_offset = bool(flag)
```

(Place it next to other small setters like `set_use_piezo` if one exists.)

- [ ] **Step 4: Smoke test that the field flows through**

Add to `software/tests/control/test_MultiPointController.py`:

```python
def test_apply_channel_offset_flows_to_worker(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = gts.get_test_qt_multi_point_controller(microscope=scope)
    controller.set_apply_channel_offset(False)
    # Build params and confirm the value lands on the dataclass
    scan_pos = ...  # use existing fixture / build a minimal one; mirror an existing test
    params = controller.build_params(scan_pos)
    assert params.apply_channel_offset is False
    scope.close()
```

If wiring up the test is non-trivial, replace with a minimal dataclass-construction test:

```python
def test_acquisition_parameters_has_apply_channel_offset_default_true():
    from control.core.multi_point_utils import AcquisitionParameters, ScanPositionInformation
    # Build the minimum-required fields; default for apply_channel_offset must be True.
    sp = ScanPositionInformation(scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={})
    p = AcquisitionParameters(
        experiment_ID=None, base_path=None, selected_configurations=[],
        acquisition_start_time=0.0, scan_position_information=sp,
        NX=1, deltaX=0, NY=1, deltaY=0, NZ=1, deltaZ=0, Nt=1, deltat=0,
        do_autofocus=False, do_reflection_autofocus=False,
        use_piezo=False, display_resolution_scaling=1.0,
        z_stacking_config="FROM CENTER", z_range=(0.0, 0.0),
        use_fluidics=False,
    )
    assert p.apply_channel_offset is True
```

- [ ] **Step 5: Run the new test**

Run: `pytest software/tests/control/test_MultiPointController.py -k apply_channel_offset -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
black --config software/pyproject.toml software/control/core/multi_point_utils.py software/control/core/multi_point_worker.py software/control/core/multi_point_controller.py software/tests/control/test_MultiPointController.py
git add software/control/core/multi_point_utils.py software/control/core/multi_point_worker.py software/control/core/multi_point_controller.py software/tests/control/test_MultiPointController.py
git commit -m "feat(acquisition): plumb apply_channel_offset flag through controller and worker"
```

---

## Task 6: Add `_move_z_for_offset`, `_apply_channel_z_offset`, `_reset_channel_z_offset` helpers to the worker

**Files:**
- Modify: `software/control/core/multi_point_worker.py`
- Create: `software/tests/control/test_MultiPointWorker_offsets.py`

This is the core acquisition logic. TDD-heavy.

- [ ] **Step 1: Create the new test file with mocks**

Create `software/tests/control/test_MultiPointWorker_offsets.py`:

```python
"""Unit tests for MultiPointWorker per-channel z-offset helpers.

These tests construct a MultiPointWorker-like minimal instance with mocked stage
and piezo to verify the delta-tracking algorithm in isolation. See
software/docs/laser-af-channel-offset-design.md §4 for the algorithm spec.
"""
from unittest.mock import MagicMock
import pytest

from control.core.multi_point_worker import MultiPointWorker


class _Stub:
    """Bare MultiPointWorker-ish object with just the attributes the helpers read."""

    def __init__(self, *, use_piezo: bool, do_reflection_af: bool, apply_channel_offset: bool):
        self.use_piezo = use_piezo
        self.do_reflection_af = do_reflection_af
        self.apply_channel_offset = apply_channel_offset
        self.stage = MagicMock()
        self.piezo = MagicMock()
        self.piezo.range_um = 400.0
        self.z_piezo_um = 100.0  # mid-range
        self.liveController = MagicMock()
        # default to software trigger so the piezo sleep branch is exercised
        self.liveController.trigger_mode = "SOFTWARE"   # value compared against TriggerMode.SOFTWARE
        self._current_z_offset_um = 0.0
        self._log = MagicMock()
        # Bind real methods from MultiPointWorker
        self.wait_till_operation_is_completed = MagicMock()
        self._sleep = MagicMock()

    # Attach the actual helper methods (after they exist on MultiPointWorker)
    _apply_channel_z_offset = MultiPointWorker._apply_channel_z_offset
    _reset_channel_z_offset = MultiPointWorker._reset_channel_z_offset
    _move_z_for_offset = MultiPointWorker._move_z_for_offset


def _config(z_offset_um):
    cfg = MagicMock()
    cfg.z_offset_um = z_offset_um
    return cfg


def test_apply_stage_path_single_channel():
    """Stage move issued for a non-zero offset; running offset updated."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.assert_called_once_with(2.0 / 1000)
    w.piezo.move_to.assert_not_called()
    assert w._current_z_offset_um == 2.0


def test_apply_skipped_when_laser_af_off():
    """No move when do_reflection_af is False."""
    w = _Stub(use_piezo=False, do_reflection_af=False, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.assert_not_called()
    w.piezo.move_to.assert_not_called()
    assert w._current_z_offset_um == 0.0


def test_apply_skipped_when_checkbox_off():
    """No move when apply_channel_offset is False."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=False)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.assert_not_called()
    assert w._current_z_offset_um == 0.0


def test_apply_no_move_for_zero_delta():
    """Two consecutive channels with the same offset → only one move."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w._apply_channel_z_offset(_config(2.0))
    assert w.stage.move_z.call_count == 1


def test_reset_undoes_remaining_offset():
    """Reset emits the inverse move and clears running offset."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.reset_mock()
    w._reset_channel_z_offset()
    w.stage.move_z.assert_called_once_with(-2.0 / 1000)
    assert w._current_z_offset_um == 0.0


def test_reset_noop_when_offset_is_zero():
    """Reset is a no-op if no offset has been applied."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._reset_channel_z_offset()
    w.stage.move_z.assert_not_called()


def test_piezo_path_uses_piezo_move_to():
    """With use_piezo=True, the offset goes via piezo.move_to, not stage."""
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(3.0))
    w.piezo.move_to.assert_called_once_with(103.0)
    w.stage.move_z.assert_not_called()
    assert w.z_piezo_um == 103.0


def test_piezo_clamped_when_out_of_range():
    """Piezo offset that would overflow the range is clamped and warned."""
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w.z_piezo_um = 380.0
    w._apply_channel_z_offset(_config(50.0))  # would land at 430, range_um=400
    w.piezo.move_to.assert_called_once_with(400.0)
    w._log.warning.assert_called_once()
    assert w.z_piezo_um == 400.0


def test_sequence_four_channels_delta_pattern():
    """Offsets [0, +2, +2, -1] produce moves [+2, -3, +1 (reset)] with running offset state."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    for off in [0, 2, 2, -1]:
        w._apply_channel_z_offset(_config(off))
    w._reset_channel_z_offset()
    rel_mm_args = [call.args[0] for call in w.stage.move_z.call_args_list]
    # All values in mm: +2µm, then -3µm, then +1µm reset (skip the zero-delta call entirely)
    assert rel_mm_args == pytest.approx([2 / 1000, -3 / 1000, 1 / 1000])
    assert w._current_z_offset_um == 0.0
```

- [ ] **Step 2: Run tests; they should fail with AttributeError**

Run: `pytest software/tests/control/test_MultiPointWorker_offsets.py -v`
Expected: FAIL — `AttributeError: type object 'MultiPointWorker' has no attribute '_apply_channel_z_offset'` (or similar). This confirms the helpers don't exist yet.

- [ ] **Step 3: Add the helper methods to `MultiPointWorker`**

In `software/control/core/multi_point_worker.py`, locate `handle_z_offset` (around line 1163) and add the new helpers right above it. Also import `TriggerMode` and `MULTIPOINT_PIEZO_DELAY_MS` if not already in scope (grep first):

```bash
grep -n "TriggerMode\|MULTIPOINT_PIEZO_DELAY_MS\|SCAN_STABILIZATION_TIME_MS_Z" software/control/core/multi_point_worker.py | head
```

If already present, just add the helpers:

```python
def _move_z_for_offset(self, delta_um: float) -> None:
    """Dispatch a relative z move via piezo when use_piezo, otherwise via stage.

    Piezo moves are clamped to [0, piezo.range_um] with a warning log if the offset
    would otherwise drive the piezo out of range. Stage moves inherit backlash
    compensation from CephlaStage.move_z().
    """
    if self.use_piezo:
        new_piezo_um = self.z_piezo_um + delta_um
        if new_piezo_um < 0 or new_piezo_um > self.piezo.range_um:
            self._log.warning(
                f"channel z-offset {delta_um:+.2f} µm would drive piezo out of range "
                f"({new_piezo_um:.2f} µm vs [0, {self.piezo.range_um}]); clamping"
            )
            new_piezo_um = max(0.0, min(self.piezo.range_um, new_piezo_um))
        self.z_piezo_um = new_piezo_um
        self.piezo.move_to(self.z_piezo_um)
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
    else:
        self.stage.move_z(delta_um / 1000)
        self.wait_till_operation_is_completed()
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

def _apply_channel_z_offset(self, config) -> None:
    """Move z by the delta needed to reach this channel's per-channel z-offset.

    No-op when laser AF is not the active AF method, when the 'Apply channel offset'
    flag is off, or when the resulting delta is zero.
    """
    if not (self.apply_channel_offset and self.do_reflection_af):
        return
    target_um = config.z_offset_um or 0.0
    delta_um = target_um - self._current_z_offset_um
    if delta_um == 0:
        return
    self._move_z_for_offset(delta_um)
    self._current_z_offset_um = target_um

def _reset_channel_z_offset(self) -> None:
    """Undo any remaining offset so z returns to the un-offset baseline."""
    if self._current_z_offset_um == 0:
        return
    self._move_z_for_offset(-self._current_z_offset_um)
    self._current_z_offset_um = 0.0
```

In the test stub, `liveController.trigger_mode` is a plain string `"SOFTWARE"`, but the helper compares against `TriggerMode.SOFTWARE`. Update the test stub's value to match the enum: change `_Stub.__init__` line `self.liveController.trigger_mode = "SOFTWARE"` to `self.liveController.trigger_mode = TriggerMode.SOFTWARE` and `from control._def import TriggerMode` at the top of the test file. (Required to make `test_piezo_path_uses_piezo_move_to` exercise the SOFTWARE-trigger sleep branch deterministically.)

- [ ] **Step 4: Run tests; all should pass**

Run: `pytest software/tests/control/test_MultiPointWorker_offsets.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
black --config software/pyproject.toml software/control/core/multi_point_worker.py software/tests/control/test_MultiPointWorker_offsets.py
git add software/control/core/multi_point_worker.py software/tests/control/test_MultiPointWorker_offsets.py
git commit -m "feat(worker): add delta-tracking helpers for per-channel z-offset"
```

---

## Task 7: Integrate offset helpers into the z-level loop and delete `handle_z_offset`

**Files:**
- Modify: `software/control/core/multi_point_worker.py:1050-1108` (per-z-level loop) and around line 1163 (delete old `handle_z_offset`)

- [ ] **Step 1: Replace `handle_z_offset` calls and wrap the channel loop in try/finally**

In `software/control/core/multi_point_worker.py`, around line 1050-1108, replace the inner `for config_idx, config in enumerate(self.selected_configurations):` block. The old code:

```python
for config_idx, config in enumerate(self.selected_configurations):
    if self.NZ == 1:  # TODO: handle z offset for z stack
        self.handle_z_offset(config, True)

    # acquire image
    with self._timing.get_timer("acquire_camera_image"):
        if "RGB" in config.name:
            self.acquire_rgb_image(config, file_ID, current_path, z_level, region_id, fov)
        else:
            self.acquire_camera_image(
                config, file_ID, current_path, z_level, region_id=region_id, fov=fov, config_idx=config_idx
            )

    if self.NZ == 1:  # TODO: handle z offset for z stack
        self.handle_z_offset(config, False)

    current_image = (
        fov * self.NZ * len(self.selected_configurations)
        + z_level * len(self.selected_configurations)
        + config_idx
        + 1
    )
    self.callbacks.signal_region_progress(
        RegionProgressUpdate(current_fov=current_image, region_fovs=self.total_scans)
    )
```

becomes:

```python
try:
    for config_idx, config in enumerate(self.selected_configurations):
        self._apply_channel_z_offset(config)

        # acquire image
        with self._timing.get_timer("acquire_camera_image"):
            if "RGB" in config.name:
                self.acquire_rgb_image(config, file_ID, current_path, z_level, region_id, fov)
            else:
                self.acquire_camera_image(
                    config, file_ID, current_path, z_level, region_id=region_id, fov=fov, config_idx=config_idx
                )

        current_image = (
            fov * self.NZ * len(self.selected_configurations)
            + z_level * len(self.selected_configurations)
            + config_idx
            + 1
        )
        self.callbacks.signal_region_progress(
            RegionProgressUpdate(current_fov=current_image, region_fovs=self.total_scans)
        )
finally:
    self._reset_channel_z_offset()
```

(Both `handle_z_offset(config, True)` and `handle_z_offset(config, False)` are removed.)

- [ ] **Step 2: Delete the now-unused `handle_z_offset` method**

In `software/control/core/multi_point_worker.py`, find the `def handle_z_offset(self, config, not_offset):` method (around line 1163) and remove it entirely.

- [ ] **Step 3: Verify no other call sites remain**

Run:

```bash
grep -rn "handle_z_offset\b" software/
```

Expected: zero matches. If any remain, remove them or update them to use the new helpers.

- [ ] **Step 4: Run worker tests + acquisition simulation test**

Run:

```bash
pytest software/tests/control/test_MultiPointWorker_offsets.py software/tests/control/test_MultiPointController.py -q
```

Expected: all PASS.

- [ ] **Step 5: Sanity-check imports in simulation mode**

```bash
cd software && source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid
python -c "from control.core.multi_point_worker import MultiPointWorker; print('OK')"
```

Expected: prints `OK` without traceback.

- [ ] **Step 6: Commit**

```bash
cd ..
black --config software/pyproject.toml software/control/core/multi_point_worker.py
git add software/control/core/multi_point_worker.py
git commit -m "feat(worker): apply per-channel z-offset via delta tracking; remove handle_z_offset"
```

---

## Task 8: Reset offset defensively in `handle_acquisition_abort`

**Files:**
- Modify: `software/control/core/multi_point_worker.py:1497` (handle_acquisition_abort)
- Modify: `software/tests/control/test_MultiPointWorker_offsets.py`

- [ ] **Step 1: Write a failing test for abort cleanup**

Append to `software/tests/control/test_MultiPointWorker_offsets.py`:

```python
def test_handle_acquisition_abort_resets_offset():
    """handle_acquisition_abort resets any stranded offset defensively."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    # Simulate a stranded offset (e.g., exception bypassed the inner finally)
    w._current_z_offset_um = 1.7
    # Wire up the real method
    w.handle_acquisition_abort = MultiPointWorker.handle_acquisition_abort.__get__(w)
    # Minimal mocks for the rest of handle_acquisition_abort's behavior
    w.coordinates_pd = MagicMock()
    w.microcontroller = MagicMock()
    w._wait_for_outstanding_callback_images = MagicMock()

    w.handle_acquisition_abort(current_path="/tmp/abort_test")

    # The first stage move must be the reset (-1.7 µm)
    assert w.stage.move_z.call_args_list[0].args[0] == pytest.approx(-1.7 / 1000)
    assert w._current_z_offset_um == 0.0
```

- [ ] **Step 2: Run test; expect failure**

Run: `pytest software/tests/control/test_MultiPointWorker_offsets.py -k abort_resets_offset -v`
Expected: FAIL (`stage.move_z` not called, or `_current_z_offset_um != 0`).

- [ ] **Step 3: Add reset call at the top of `handle_acquisition_abort`**

In `software/control/core/multi_point_worker.py`, modify `handle_acquisition_abort` (around line 1497):

```python
def handle_acquisition_abort(self, current_path):
    # Defensive: undo any stranded per-channel offset before saving state.
    self._reset_channel_z_offset()
    # Save coordinates.csv
    self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)
    self.microcontroller.enable_joystick(True)
    self._wait_for_outstanding_callback_images()
```

- [ ] **Step 4: Run test; expect pass**

Run: `pytest software/tests/control/test_MultiPointWorker_offsets.py -k abort_resets_offset -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
black --config software/pyproject.toml software/control/core/multi_point_worker.py software/tests/control/test_MultiPointWorker_offsets.py
git add software/control/core/multi_point_worker.py software/tests/control/test_MultiPointWorker_offsets.py
git commit -m "feat(worker): reset channel z-offset defensively on acquisition abort"
```

---

## Task 9: Add the "ignored offsets" startup log

**Files:**
- Modify: `software/control/core/multi_point_worker.py` (around the start of `run()` or where acquisition begins)

- [ ] **Step 1: Find the acquisition-start log site**

```bash
grep -n "STARTING ACQUISITION\|self._log.info.*start\|def run\b" software/control/core/multi_point_worker.py | head
```

Pick a location early in the acquisition that runs once (not per FOV).

- [ ] **Step 2: Add the log**

Add a method `_log_ignored_offsets()` and call it from the run-start path:

```python
def _log_ignored_offsets(self) -> None:
    """Log a notice if non-zero per-channel offsets exist but won't be applied."""
    if self.apply_channel_offset and self.do_reflection_af:
        return
    ignored = [(c.name, c.z_offset_um) for c in self.selected_configurations if (c.z_offset_um or 0.0) != 0.0]
    if not ignored:
        return
    summary = ", ".join(f"{name}: {off:+.2f}µm" for name, off in ignored)
    reason = "laser AF off" if not self.do_reflection_af else "'Apply channel offset' unchecked"
    self._log.info(f"[multi-point] {reason} — ignoring non-zero z-offsets on channels: [{summary}]")
```

Call once at acquisition start (find a sensible spot in `run()` — e.g., after `STARTING ACQUISITION` log).

- [ ] **Step 3: Smoke test the log doesn't crash**

Run any quick test that constructs a worker (e.g., `pytest software/tests/control/test_MultiPointWorker_offsets.py -q`); ensure no new test failures.

- [ ] **Step 4: Commit**

```bash
black --config software/pyproject.toml software/control/core/multi_point_worker.py
git add software/control/core/multi_point_worker.py
git commit -m "feat(worker): log ignored channel offsets at acquisition start"
```

---

## Task 10: Add "Apply per-channel z-offset" checkbox to `FlexibleMultiPointWidget`

**Files:**
- Modify: `software/control/widgets.py:5303` (`FlexibleMultiPointWidget`)

Acquisition widgets are PyQt5-heavy; we won't write Qt unit tests for the checkbox. Instead we wire the checkbox to `MultiPointController.set_apply_channel_offset` and verify behavior in the end-to-end smoke task.

- [ ] **Step 1: Locate the AF section of FlexibleMultiPointWidget**

```bash
grep -n "reflection_af\|laser.*autofocus\|AF.*checkbox\|self\.checkbox_useReflectionAF\|af_method" software/control/widgets.py | grep -i "5[0-9]\{3\}\|6[0-2][0-9]\{2\}" | head
```

Find the existing checkbox that toggles laser-AF / reflection-AF (e.g., `self.checkbox_useReflectionAF`). The new checkbox goes adjacent to it in the layout.

- [ ] **Step 2: Add the checkbox**

In `FlexibleMultiPointWidget.add_components` (or the equivalent UI-construction method, locate via `grep -n "def add_components\|class FlexibleMultiPointWidget" software/control/widgets.py | head`):

```python
self.checkbox_applyChannelOffset = QCheckBox("Apply per-channel z-offset")
self.checkbox_applyChannelOffset.setChecked(True)
self.checkbox_applyChannelOffset.setToolTip(
    "When laser autofocus is active, apply each channel's saved z-offset relative to the laser AF reference."
)
self.checkbox_applyChannelOffset.toggled.connect(self._on_apply_channel_offset_changed)
# Add to the AF row/column layout (find where checkbox_useReflectionAF was added and mirror its addWidget(...) call):
af_layout.addWidget(self.checkbox_applyChannelOffset)  # adapt name to the actual layout variable
```

Wire enable-state to mirror "laser AF is on":

```python
self.checkbox_useReflectionAF.toggled.connect(self._update_apply_channel_offset_enable_state)
self._update_apply_channel_offset_enable_state(self.checkbox_useReflectionAF.isChecked())

def _update_apply_channel_offset_enable_state(self, laser_af_on: bool):
    self.checkbox_applyChannelOffset.setEnabled(laser_af_on)
    if not laser_af_on:
        self.checkbox_applyChannelOffset.setToolTip("Requires laser autofocus")
    else:
        self.checkbox_applyChannelOffset.setToolTip(
            "When laser autofocus is active, apply each channel's saved z-offset relative to the laser AF reference."
        )

def _on_apply_channel_offset_changed(self, checked: bool):
    self.multipointController.set_apply_channel_offset(checked)
```

(Adapt the attribute name `self.multipointController` to whatever the existing widgets use — find via `grep "self\.multipointController\|self\.multiPointController" software/control/widgets.py | head`.)

- [ ] **Step 3: Sanity check imports + launch the app in simulation**

```bash
cd software && source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid
python -c "import control.widgets; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd ..
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): add 'Apply per-channel z-offset' checkbox to FlexibleMultiPointWidget"
```

---

## Task 11: Same checkbox in `WellplateMultiPointWidget`

**Files:**
- Modify: `software/control/widgets.py:6779` (`WellplateMultiPointWidget`)

- [ ] **Step 1: Add the checkbox + enable wiring**

Mirror Task 10 inside `WellplateMultiPointWidget.add_components` (or equivalent). Reuse the exact same code structure for `self.checkbox_applyChannelOffset`, `_update_apply_channel_offset_enable_state`, and `_on_apply_channel_offset_changed`. Connect to the same controller method.

If the two widgets share a common base / mixin (`AcquisitionYAMLDropMixin`?), consider extracting the helpers there — but only if straightforward; otherwise duplicate.

- [ ] **Step 2: Import sanity**

```bash
python -c "import control.widgets; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): add 'Apply per-channel z-offset' checkbox to WellplateMultiPointWidget"
```

---

## Task 12: Same checkbox in `MultiPointWithFluidicsWidget`

**Files:**
- Modify: `software/control/widgets.py:8997` (`MultiPointWithFluidicsWidget`)

- [ ] **Step 1: Add checkbox + wiring**

Mirror Tasks 10-11.

- [ ] **Step 2: Import sanity + commit**

```bash
python -c "import control.widgets; print('OK')"
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): add 'Apply per-channel z-offset' checkbox to MultiPointWithFluidicsWidget"
```

---

## Task 13: `LiveControlWidget` — add "Show Z-offset" toggle + offset row UI

**Files:**
- Modify: `software/control/widgets.py:3945` (`LiveControlWidget`)

- [ ] **Step 1: Add the show-toggle and a container for the offset row**

In `LiveControlWidget.add_components`, after the existing rows for exposure/gain/intensity (around line 4115 where `entry_exposureTime.valueChanged.connect(...)`), add:

```python
self.checkbox_showZOffset = QCheckBox("Show Z-offset controls")
self.checkbox_showZOffset.setChecked(False)
self.checkbox_showZOffset.toggled.connect(self._on_show_z_offset_toggled)

# Container for the offset row, hidden by default
self.widget_zOffsetRow = QWidget()
zoff_layout = QHBoxLayout(self.widget_zOffsetRow)
zoff_layout.setContentsMargins(0, 0, 0, 0)

self.entry_zOffset = QDoubleSpinBox()
self.entry_zOffset.setKeyboardTracking(False)
self.entry_zOffset.setRange(-50.0, 50.0)
self.entry_zOffset.setSingleStep(0.1)
self.entry_zOffset.setDecimals(2)
self.entry_zOffset.setSuffix(" µm")
self.entry_zOffset.setToolTip(
    "Per-channel z-offset from the laser AF reference plane. "
    "Sample-dependent — re-capture or reset when starting a new sample."
)
self.entry_zOffset.valueChanged.connect(self.update_config_z_offset)

self.btn_captureZOffset = QPushButton("Capture current")
self.btn_captureZOffset.setToolTip(
    "Read displacement from the laser AF reference and save as this channel's offset."
)
self.btn_captureZOffset.clicked.connect(self.capture_current_z_offset)

self.btn_resetZOffset = QPushButton("Reset")
self.btn_resetZOffset.setToolTip("Set this channel's z-offset to 0.")
self.btn_resetZOffset.clicked.connect(self.reset_current_z_offset)

self.checkbox_applyOnChannelSwitch = QCheckBox("Apply on channel switch")
self.checkbox_applyOnChannelSwitch.setToolTip(
    "When checked and laser AF has a reference, switching channels moves z to "
    "the laser AF reference + the new channel's offset."
)

zoff_layout.addWidget(QLabel("Z offset:"))
zoff_layout.addWidget(self.entry_zOffset)
zoff_layout.addWidget(self.btn_captureZOffset)
zoff_layout.addWidget(self.btn_resetZOffset)
zoff_layout.addWidget(self.checkbox_applyOnChannelSwitch)
zoff_layout.addStretch()

self.widget_zOffsetRow.setVisible(False)
# Insert into the parent layout — adapt to the actual layout variable used by add_components
# Example (must locate the correct layout):
#   main_layout.addWidget(self.checkbox_showZOffset)
#   main_layout.addWidget(self.widget_zOffsetRow)
```

Locate the actual main layout var via:

```bash
grep -n "main_layout\|setLayout\|self\.layout\|QVBoxLayout\|QGridLayout" software/control/widgets.py | sed -n '/3945,/^class/p' | head
```

- [ ] **Step 2: Implement the handlers**

Add to `LiveControlWidget`:

```python
def _on_show_z_offset_toggled(self, checked: bool):
    self.widget_zOffsetRow.setVisible(checked)

def update_config_z_offset(self, new_value: float):
    if self.is_switching_mode:
        return
    if self.currentConfiguration is None:
        return
    self.currentConfiguration.z_offset_um = new_value
    self.liveController.microscope.config_repo.update_channel_setting(
        self.objectiveStore.current_objective,
        self.currentConfiguration.name,
        "ZOffset",
        new_value,
        confocal_mode=self.liveController.is_confocal_mode(),
    )

def capture_current_z_offset(self):
    if self.currentConfiguration is None:
        return
    laser_af = getattr(self.liveController.microscope, "laser_autofocus_controller", None)
    if laser_af is None or not laser_af.laser_af_properties.has_reference:
        QMessageBox.warning(
            self, "Capture failed",
            "Laser autofocus has no reference set. Set a reference before capturing channel offsets.",
        )
        return
    try:
        displacement_um = laser_af.measure_displacement()
    except Exception as e:
        QMessageBox.warning(self, "Capture failed", f"Could not read laser AF spot: {e}\nOffset unchanged.")
        return
    self.currentConfiguration.z_offset_um = displacement_um
    self.liveController.microscope.config_repo.update_channel_setting(
        self.objectiveStore.current_objective,
        self.currentConfiguration.name,
        "ZOffset",
        displacement_um,
        confocal_mode=self.liveController.is_confocal_mode(),
    )
    try:
        self.is_switching_mode = True
        self.entry_zOffset.setValue(displacement_um)
    finally:
        self.is_switching_mode = False

def reset_current_z_offset(self):
    if self.currentConfiguration is None:
        return
    self.currentConfiguration.z_offset_um = 0.0
    self.liveController.microscope.config_repo.update_channel_setting(
        self.objectiveStore.current_objective,
        self.currentConfiguration.name,
        "ZOffset",
        0.0,
        confocal_mode=self.liveController.is_confocal_mode(),
    )
    try:
        self.is_switching_mode = True
        self.entry_zOffset.setValue(0.0)
    finally:
        self.is_switching_mode = False
```

- [ ] **Step 3: Make the spinbox reflect the current channel on mode switch**

Find `update_ui_for_mode` in `LiveControlWidget` (around line 4225). Inside the `try:` block, add:

```python
self.entry_zOffset.setValue(self.currentConfiguration.z_offset_um or 0.0)
```

Place it next to `self.entry_exposureTime.setValue(...)` so all per-channel settings update together.

- [ ] **Step 4: Import sanity check**

```bash
python -c "import control.widgets; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): add Z-offset spinbox, Capture, and Reset to LiveControlWidget"
```

---

## Task 14: `LiveControlWidget` — "Apply on channel switch" with absolute positioning

**Files:**
- Modify: `software/control/widgets.py:3945` (`LiveControlWidget`)

- [ ] **Step 1: Wire enable state to laser AF reference availability**

In `LiveControlWidget.__init__` (after the offset row is constructed), add subscription to `signal_reference_changed`. Find the laser AF controller via the microscope handle (it lives at `self.liveController.microscope.laser_autofocus_controller` or similar; verify with `grep -n "laser_autofocus_controller\|laser_auto_focus_controller" software/control/widgets.py | head`):

```python
laser_af = getattr(self.liveController.microscope, "laser_autofocus_controller", None)
if laser_af is not None:
    laser_af.signal_reference_changed.connect(self._on_laser_af_reference_changed)
    initial_has_ref = laser_af.laser_af_properties.has_reference
else:
    initial_has_ref = False

# Initial state: enabled and checked if there's a reference, disabled otherwise
self.checkbox_applyOnChannelSwitch.setEnabled(initial_has_ref)
self.checkbox_applyOnChannelSwitch.setChecked(initial_has_ref)
self.btn_captureZOffset.setEnabled(initial_has_ref)
```

Adjust the attribute name if `laser_autofocus_controller` is different. The CLAUDE.md mentions `microscope` is a key attribute; use that as the entry point.

- [ ] **Step 2: Add the handler**

```python
def _on_laser_af_reference_changed(self, has_reference: bool):
    self.checkbox_applyOnChannelSwitch.setEnabled(has_reference)
    self.btn_captureZOffset.setEnabled(has_reference)
    # Don't change the checkbox checked state — preserve the user's choice across re-calibration.
```

- [ ] **Step 3: Apply offset on dropdown change via absolute positioning**

Find the channel-dropdown change handler in `LiveControlWidget` (search `dropdown_modeSelection.currentTextChanged` or `update_microscope_mode`):

```bash
grep -n "dropdown_modeSelection\|update_microscope_mode\|currentIndexChanged\|update_ui_for_mode" software/control/widgets.py | head
```

Inside that handler (after the existing config change is applied), insert:

```python
# Apply per-channel z-offset if enabled
self._maybe_apply_live_channel_offset(self.currentConfiguration)
```

Add the helper:

```python
def _maybe_apply_live_channel_offset(self, new_config):
    if not self.checkbox_applyOnChannelSwitch.isChecked():
        return
    laser_af = getattr(self.liveController.microscope, "laser_autofocus_controller", None)
    if laser_af is None or not laser_af.laser_af_properties.has_reference:
        return
    try:
        displacement_um = laser_af.measure_displacement()
    except Exception as e:
        self._log.warning(f"Could not read laser AF spot for live offset: {e}")
        return
    current_z_mm = self.liveController.microscope.stage.get_pos().z_mm
    reference_z_mm = current_z_mm - displacement_um / 1000
    target_z_mm = reference_z_mm + (new_config.z_offset_um or 0.0) / 1000
    self.liveController.microscope.stage.move_z_to(target_z_mm)
```

(Adapt `self._log` to the logger attribute used in the widget — typically `self._log = squid.logging.get_logger(__class__.__name__)`.)

- [ ] **Step 4: Import sanity check**

```bash
python -c "import control.widgets; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): apply per-channel z-offset on channel switch in LiveControlWidget"
```

---

## Task 15: `NapariLiveWidget` — mirror Task 13 + Task 14

**Files:**
- Modify: `software/control/widgets.py:10857` (`NapariLiveWidget`)

`NapariLiveWidget` is the napari-based live view; it duplicates much of `LiveControlWidget`'s per-channel controls (see the existing exposure/gain/intensity blocks around lines 10995-11017).

- [ ] **Step 1: Replicate the offset row UI**

In `NapariLiveWidget.add_components` (find via `grep -n "def add_components\|class NapariLiveWidget" software/control/widgets.py | head` after line 10857), add the same `checkbox_showZOffset`, `widget_zOffsetRow`, `entry_zOffset`, `btn_captureZOffset`, `btn_resetZOffset`, `checkbox_applyOnChannelSwitch` block as in Task 13.

Use `self.live_configuration` instead of `self.currentConfiguration` (matches the existing NapariLiveWidget pattern at line 10999 et al).

- [ ] **Step 2: Replicate handlers + dropdown integration**

Mirror `update_config_z_offset`, `capture_current_z_offset`, `reset_current_z_offset`, `_on_laser_af_reference_changed`, `_maybe_apply_live_channel_offset`. Adapt attribute names (`live_configuration`, `liveController` vs whatever NapariLiveWidget uses).

In `NapariLiveWidget`'s `update_ui_for_mode` (find around line 11220-11230), add:

```python
self.entry_zOffset.setValue(self.live_configuration.z_offset_um or 0.0)
```

In its dropdown-change handler (find by searching same area for `currentIndexChanged` / `dropdown_modeSelection`), call `self._maybe_apply_live_channel_offset(self.live_configuration)` after the config switch.

- [ ] **Step 3: Import sanity check**

```bash
python -c "import control.widgets; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): mirror Z-offset UI and live-apply in NapariLiveWidget"
```

---

## Task 16: `LaserAutofocusSettingWidget` — "Reset all channel offsets" button

**Files:**
- Modify: `software/control/widgets.py:2661` (`LaserAutofocusSettingWidget`)

- [ ] **Step 1: Add the button and handler**

In `LaserAutofocusSettingWidget.add_components` (find via `grep -n "class LaserAutofocusSettingWidget\|def add_components" software/control/widgets.py | sed -n '/2661/,/3038/p' | head`), near the existing "Set Reference" button, add:

```python
self.btn_resetAllChannelOffsets = QPushButton("Reset all channel offsets")
self.btn_resetAllChannelOffsets.setToolTip(
    "Set z-offset to 0 for every channel of the current objective. Recommended when starting a new sample."
)
self.btn_resetAllChannelOffsets.clicked.connect(self._reset_all_channel_offsets)
# add to the same row/column as Set Reference (mirror the existing addWidget call)
```

And the handler:

```python
def _reset_all_channel_offsets(self):
    objective = self.objectiveStore.current_objective
    general = self.config_repo.get_general_config()
    obj = self.config_repo.get_objective_config(objective)
    channels = (obj.channels if obj else None) or (general.channels if general else None) or []
    if not channels:
        QMessageBox.information(self, "No channels", f"No channels found for objective '{objective}'.")
        return
    confirm = QMessageBox.question(
        self,
        "Reset channel offsets",
        f"Set z-offset to 0 for all {len(channels)} channels of objective '{objective}'?\n"
        f"Recommended when starting a new sample.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        return
    for ch in channels:
        self.config_repo.update_channel_setting(objective, ch.name, "ZOffset", 0.0)
    self._log.info(f"Reset z-offset to 0 for {len(channels)} channels of objective '{objective}'.")
```

(Adapt the `self.config_repo` / `self.objectiveStore` references to whatever the widget already uses — find via `grep -n "self\.config_repo\|self\.objectiveStore" software/control/widgets.py | sed -n '/2661,/3038/p' | head`.)

If the widget doesn't already have a logger attribute, add `self._log = squid.logging.get_logger(__class__.__name__)` in `__init__`.

- [ ] **Step 2: Import sanity check + commit**

```bash
python -c "import control.widgets; print('OK')"
black --config software/pyproject.toml software/control/widgets.py
git add software/control/widgets.py
git commit -m "feat(widget): add 'Reset all channel offsets' button to LaserAutofocusSettingWidget"
```

---

## Task 17: End-to-end simulation smoke test

**Files:** none (manual verification)

- [ ] **Step 1: Launch the app in simulation**

```bash
cd "/Users/hongquan/Cephla Dropbox/Hongquan Li/Github/AI/Squid-Claude2/worktrees/laser-af-channel-offset/software"
source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid
python3 main_hcs.py --simulation --verbose 2>&1 | tee /tmp/laf-channel-offset-smoke.log
```

- [ ] **Step 2: UI walkthrough**

In the running app:

1. In Live Controller: tick **Show Z-offset controls** → the offset row appears.
2. Enter `1.5` µm in the spinbox; confirm it sticks. Switch channel and back; value should still be 1.5.
3. Quit and relaunch; the value should persist (proves YAML write-through).
4. Open Laser AF widget: click **Set Reference** (simulation will fake it). Confirm:
   - **Capture current** button is now enabled in Live Controller.
   - "Apply on channel switch" checkbox is enabled and checked by default.
5. Click **Capture current** for a channel; the spinbox should update with the simulated displacement (likely 0 in sim).
6. Click **Reset all channel offsets** in the Laser AF widget; confirm the Live Controller spinbox returns to 0.
7. Open an acquisition widget (Flexible or Wellplate); confirm the **Apply per-channel z-offset** checkbox is present, defaults to checked when laser AF is on, and disables when laser AF is off.

- [ ] **Step 3: Run an acquisition with offsets**

1. Set non-zero offsets for two channels via Live Controller (e.g., DAPI=0, GFP=+2).
2. Configure a small acquisition (1 region, 1 FOV, NZ=3) with laser AF as the AF method, checkbox on.
3. Run the acquisition. Inspect the log for:
   - `[multi-point]` lines showing the offset moves.
   - No errors or stranded offsets.
4. Re-run with the checkbox unchecked; confirm log shows the "ignoring non-zero z-offsets" notice and no offset moves occur.

- [ ] **Step 4: Run the full test suite**

```bash
cd "/Users/hongquan/Cephla Dropbox/Hongquan Li/Github/AI/Squid-Claude2/worktrees/laser-af-channel-offset"
pytest software/tests/ -q
```

Expected: all tests pass (or only pre-existing skips/failures present on baseline).

- [ ] **Step 5: Run black to confirm formatting is clean**

```bash
black --config software/pyproject.toml --check software/
```

Expected: zero files would be reformatted.

- [ ] **Step 6: Final commit (only if anything was modified during smoke)**

If the smoke test surfaced fixes, commit them with a descriptive message. Otherwise no commit needed.

---

## Done criteria

- [ ] All tests pass: `pytest software/tests/ -q`
- [ ] Black clean: `black --config software/pyproject.toml --check software/`
- [ ] App launches in simulation; UI changes visible
- [ ] An acquisition runs with offsets applied (visible in stage move log)
- [ ] Acquisition runs unchanged with checkbox off (log notes ignored offsets)
- [ ] Spec file `software/docs/laser-af-channel-offset-design.md` requirements traced to tasks above

If any of these are red, debug before declaring done.
