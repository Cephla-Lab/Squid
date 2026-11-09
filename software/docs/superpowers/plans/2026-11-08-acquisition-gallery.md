# Acquisition Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every acquisition Squid runs in a per-machine history with metadata and a preview, and show that history in a Gallery tab from which a run opens in the NDViewer tab or a standalone ndviewer_light window.

**Architecture:** A stdlib-only history store (`squid/acquisition_history.py`, JSON-lines events plus PNG previews under the per-user state dir) is written by a recorder hooked into `MultiPointController` beside the existing run-state breadcrumb, and read by the Gallery widget. Previews are a colour composite of the mosaic overview, which the mosaic view now builds as a max projection across z. Opening in a window spawns ndviewer_light as a separate process; opening in Squid reuses `NDViewerTab.set_dataset_path`. Folder import reuses the current gallery's folder-reading code, moved into its own module.

**Tech Stack:** Python 3.10+, qtpy over PyQt5/PyQt6, numpy, cv2, tifffile, yaml, platformdirs, pytest + pytest-qt.

**Spec:** `software/docs/superpowers/specs/2026-11-08-acquisition-gallery-design.md`

## Global Constraints

- Qt only through `qtpy`; no `PyQt5`/`PyQt6` imports, no enum spellings that exist in one binding only (write `Qt.UserRole`, `QListWidget.IconMode`, `QImage.Format_RGB888` as the existing code does; qtpy promotes them under PyQt6). Installations run either binding.
- History and previews live under `platformdirs.user_state_path("squid", "cephla") / "acquisition_history"`, overridable with the `SQUID_ACQUISITION_HISTORY_DIR` environment variable (tests set it to `tmp_path`). Never under `software/cache/`.
- Nothing new runs inside the acquisition loop. History writes are one-line appends; preview generation runs on a `ThreadPoolExecutor` after `acquisition_finished` or during import; the gallery never blocks the GUI thread on disk.
- A missing viewer, a missing folder or an unreadable file is shown as a reason in the gallery; never a silent no-op, never an uncaught exception reaching the GUI thread.
- Entries never expire; "Remove from history" never touches data on disk.
- Windows and Linux paths: compare folders with `os.path.normcase(os.path.normpath(os.path.abspath(p)))`.
- Formatting: `black` with the repo's line length 120 (`software/pyproject.toml`). Run it on every file you touch before committing.
- Tests run from `software/` with `QT_QPA_PLATFORM=offscreen`. On this machine the interpreter with pytest is `C:\Users\Cephla\AppData\Local\Temp\claude\c7f0ab82b4e71462\40bb304f-8b10-4c66-87d5-9a7cc0b245af\scratchpad\venv\Scripts\python.exe` (a venv over the app's Python 3.12); `pytest` below means `<that python> -m pytest -p no:cacheprovider -W ignore`. `tests/control/test_HighContentScreeningGui.py::test_create_simulated_hcs_with_or_without_piezo` fails on this machine before any change; ignore it.
- Commits end with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Work on the branch `feat/acquisition-gallery` (Task 0); never commit to `master`.

## Review Focus

1. A `history.jsonl` whose last line is cut mid-write (power loss) must still load, and later appends must not glue onto the cut line: the store terminates a partial last line before writing, and the reader skips any malformed line wherever it sits. Pinned in Task 1. A run whose process died (crash, power loss) must not stay Running forever: pinned in Task 4 (`reconcile_orphans`).
2. A folder on a drive letter that is no longer present (`D:\data\run`) must show as Drive offline without any exception or a hang; a folder under an existing drive that is gone must show Folder missing. Pinned in Task 1 (`folder_availability`) and Task 8 (badge).
3. Two runs with the same experiment name in different folders must be two entries; importing the same folder twice must be one. Pinned in Task 1 and Task 5.
4. A run aborted before its first image, or one that failed to start, must record `aborted`/`failed` with fraction 0 and must not crash the preview job on the missing overview. Pinned in Task 4.
5. An unwritable history directory must never break an acquisition: the recorder logs once and the run proceeds. Pinned in Task 4.

---

### Task 0: Baseline branch

The current uncommitted gallery build is the starting point. Put it on a branch so every later task commits on top of it.

**Files:**
- Modify: `.gitignore` (repo root)

- [ ] **Step 1: Create the branch from master with the working tree as it is**

Run (from the repo root `C:\Users\Cephla\Desktop\Squid`):
```bash
git checkout -b feat/acquisition-gallery
```
Expected: `Switched to a new branch 'feat/acquisition-gallery'`, uncommitted files carried along.

- [ ] **Step 2: Ignore the brainstorm companion's files**

Append `.superpowers/` to `.gitignore`.

- [ ] **Step 3: Run the existing gallery tests to confirm the baseline is green**

Run: `pytest tests/control/test_widgets_gallery.py -q`
Expected: `34 passed`

- [ ] **Step 4: Commit the baseline**

```bash
git add .gitignore software/control/widgets_gallery.py software/control/gui_hcs.py software/control/widgets.py software/tests/control/test_widgets_gallery.py software/tests/control/test_HighContentScreeningGui.py software/docs/gallery-tab.md software/docs/superpowers/
git commit -m "feat(gallery): Gallery tab listing the saving folder's acquisitions, with design doc and plan" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: History store

**Files:**
- Create: `squid/acquisition_history.py`
- Test: `tests/squid/test_acquisition_history.py`

**Interfaces:**
- Consumes: nothing from the project beyond `squid.logging` and `platformdirs` (stdlib-only leaf module like `squid/acquisition_state.py`; it must not import `control`).
- Produces:
  - `HISTORY_FILE_NAME = "history.jsonl"`, `PREVIEWS_DIR_NAME = "previews"`, `ENV_VAR = "SQUID_ACQUISITION_HISTORY_DIR"`
  - `history_dir() -> Path`: the env var when set, else `Path(platformdirs.user_state_path("squid", "cephla")) / "acquisition_history"`.
  - `normalize_folder(folder: str) -> str`: `os.path.normcase(os.path.normpath(os.path.abspath(folder)))`.
  - `entry_id_for(folder: str) -> str`: `f"{basename}-{sha1(normalize_folder(folder))[:8]}"`.
  - `folder_availability(folder: str, *, exists=os.path.exists) -> str`: `"online"` when the folder exists; `"drive_offline"` when the folder's anchor (`Path(folder).anchor`, e.g. `D:\`, or `/` on POSIX) does not exist; else `"folder_missing"`. Pure path tests through `exists`, no listing.
  - `OUTCOMES = ("running", "complete", "aborted", "failed", "unknown")`
  - `@dataclass HistoryEntry`: `id: str`, `kind: str` (`"run"` | `"snap"` | `"protocol_run"`), `name: str`, `folder: str`, `started_at: Optional[float]`, `ended_at: Optional[float]`, `outcome: str`, `reason: Optional[str]`, `fraction_done: Optional[float]`, `images: Optional[int]`, `bytes_on_disk: Optional[int]`, `metadata: dict`, `preview: Optional[str]` (file name under previews/), `preview_channels: list[str]`, `parent: Optional[str]`, `protocol: Optional[dict]`, `rounds: list["HistoryEntry"]`, `source: str` (`"recorded"` | `"imported"`); property `is_running -> bool` (`outcome == "running"`).
  - `class HistoryStore`: `__init__(self, directory: Path)`; `path -> Path` (the jsonl); `previews_dir -> Path` (created on demand); `append(self, event: dict) -> None` (adds `"at": time.time()` when absent; opens in `"ab"`, and when the file is non-empty and its last byte is not `\n` writes `\n` first so a cut line is terminated rather than extended; then one JSON line with `\n`, `flush`); `events(self) -> list[dict]` (skips every line that is not a JSON object, wherever it sits, and logs one warning per load with the count of skipped lines); `load(self) -> list[HistoryEntry]` (folded, newest first by `started_at`, entries with no `started_at` sorted by the event time); `entry(self, entry_id: str) -> Optional[HistoryEntry]`.
  - Event shapes the whole plan uses (all carry `"type"`, `"id"`, `"at"`):
    - `started`: `kind`, `name`, `folder`, `metadata`, `parent` (id or None), `protocol` (dict or None), `pid` (int), `pid_started_at` (float, the process creation time, or None), `machine` (str) → outcome `running`.
    - `finished`: `outcome` (`complete`|`aborted`|`failed`), `reason`, `fraction_done`, `images`, `ended_at`.
    - `preview`: `file`, `channels`, `bytes_on_disk` (int or None).
    - `imported`: `kind`, `name`, `folder`, `metadata`, `started_at`, `outcome` (`complete` | `unknown`), `parent`, `protocol` → `source = "imported"`.
    - `protocol_started`: `name`, `folder` (the run dir), `protocol`, `pid`, `pid_started_at`, `machine` → a `protocol_run` entry with outcome `running`.
    - `protocol_finished`: `outcome` (`complete`|`aborted`|`failed`), `reason`, `ended_at` → the parent's outcome from then on.
    - `relocated`: `folder`.
    - `removed`: drops the entry (and, for a parent, its rounds) from `load()`.
  - Folding rules: a `started` or `imported` for an id already present replaces its metadata but keeps earlier `preview`; `finished` without a prior `started` is ignored; rounds (entries with `parent`) are attached to the parent's `rounds` sorted by `started_at` and not listed at top level. A parent that has a `protocol_started` event follows the protocol lifecycle: `running` until its `protocol_finished`, then that event's outcome, whatever its rounds say (fluidics runs between and after rounds). A parent that was never `protocol_started` (imported or older data) is created from its first round (`name` = `protocol["run_name"]`, `folder` = `os.path.dirname(round.folder)`) and derives its outcome: `running` if any round is running, else `complete` if every round is `complete`, else `aborted`. Either way parent `started_at` = the `protocol_started` time or the first round's, `ended_at` = the `protocol_finished` time or the last round's, `preview` = the latest round's preview. A later `preview` event for an id replaces the earlier one (the fold keeps the latest). `HistoryEntry` carries `pid: Optional[int]`, `pid_started_at: Optional[float]` and `machine: Optional[str]` from `started`/`protocol_started`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/squid/test_acquisition_history.py
import json, os
import squid.acquisition_history as ah

def test_history_dir_honours_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv(ah.ENV_VAR, str(tmp_path / "h"))
    assert ah.history_dir() == tmp_path / "h"

def test_entry_id_differs_by_folder_and_is_stable():
    a = ah.entry_id_for(r"D:\data\plate_3_2026-11-06_09-12-33")
    b = ah.entry_id_for(r"E:\other\plate_3_2026-11-06_09-12-33")
    assert a != b and a.startswith("plate_3_2026-11-06_09-12-33-")
    assert a == ah.entry_id_for(r"D:\data\plate_3_2026-11-06_09-12-33")

def test_folder_availability(tmp_path):
    assert ah.folder_availability(str(tmp_path)) == "online"
    assert ah.folder_availability(str(tmp_path / "gone")) == "folder_missing"
    assert ah.folder_availability(r"Q:\data\run", exists=lambda p: False) == "drive_offline"

def test_started_then_finished_folds_into_one_complete_entry(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append({"type": "started", "id": "r-1", "kind": "run", "name": "plate_3", "folder": str(tmp_path / "plate_3"), "at": 100.0, "metadata": {"nz": 5}, "parent": None, "protocol": None})
    [running] = store.load()
    assert running.is_running and running.outcome == "running" and running.started_at == 100.0
    store.append({"type": "finished", "id": "r-1", "at": 160.0, "outcome": "complete", "reason": "completed", "fraction_done": 1.0, "images": 40, "ended_at": 160.0})
    store.append({"type": "preview", "id": "r-1", "at": 161.0, "file": "r-1.png", "channels": ["BF LED matrix full"], "bytes_on_disk": 2048})
    [done] = store.load()
    assert (done.outcome, done.ended_at, done.images, done.preview, done.preview_channels, done.bytes_on_disk) == ("complete", 160.0, 40, "r-1.png", ["BF LED matrix full"], 2048)
    assert done.metadata == {"nz": 5} and done.source == "recorded"

def test_truncated_last_line_is_dropped_and_later_appends_stay_valid(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append({"type": "started", "id": "a", "kind": "run", "name": "a", "folder": "x", "at": 1.0, "metadata": {}, "parent": None, "protocol": None})
    with open(store.path, "a", encoding="utf-8") as f:
        f.write('{"type": "finished", "id": "a", "at": 2.0, "outc')  # cut mid-write, no newline
    assert [e.id for e in store.load()] == ["a"] and store.load()[0].outcome == "running"
    store.append({"type": "finished", "id": "a", "at": 3.0, "outcome": "complete", "reason": "completed", "fraction_done": 1.0, "images": 1, "ended_at": 3.0})
    store.append({"type": "preview", "id": "a", "at": 4.0, "file": "a.png", "channels": [], "bytes_on_disk": 1})
    [a] = store.load()
    assert a.outcome == "complete" and a.preview == "a.png"  # both appends applied; the cut line was terminated, not extended
    assert len(store.events()) == 3 and store.path.read_text(encoding="utf-8").count("\n") == 4

def test_newest_first_and_removed_entries_disappear(tmp_path): ...  # started a@10, b@20, imported c (started_at 15, outcome "complete") → ids [b, c, a]; removed b → [c, a]; c.source == "imported"

def test_relocated_changes_the_folder(tmp_path): ...  # relocated → entry.folder == new path

def test_rounds_group_under_a_protocol_run_and_the_parent_summarises_them(tmp_path):
    store = ah.HistoryStore(tmp_path)
    proto = {"protocol": "seqFISH", "run_name": "run_7", "round": 1, "step": "image", "attempt": 1}
    run_dir = str(tmp_path / "run_7")
    store.append({"type": "started", "id": "r1", "kind": "run", "name": "round_1_image", "folder": os.path.join(run_dir, "round_1_image"), "at": 10.0, "metadata": {}, "parent": ah.entry_id_for(run_dir), "protocol": proto})
    store.append({"type": "finished", "id": "r1", "at": 20.0, "outcome": "complete", "reason": "completed", "fraction_done": 1.0, "images": 8, "ended_at": 20.0})
    store.append({"type": "preview", "id": "r1", "at": 21.0, "file": "r1.png", "channels": ["488"]})
    store.append({"type": "started", "id": "r2", "kind": "run", "name": "round_2_image", "folder": os.path.join(run_dir, "round_2_image"), "at": 30.0, "metadata": {}, "parent": ah.entry_id_for(run_dir), "protocol": {**proto, "round": 2}})
    [parent] = store.load()
    assert parent.kind == "protocol_run" and parent.name == "run_7" and parent.folder == run_dir
    assert [r.id for r in parent.rounds] == ["r1", "r2"] and parent.outcome == "running"
    assert parent.started_at == 10.0 and parent.preview == "r1.png"
    store.append({"type": "finished", "id": "r2", "at": 40.0, "outcome": "aborted", "reason": "user_abort", "fraction_done": 0.5, "images": 4, "ended_at": 40.0})
    assert store.load()[0].outcome == "aborted" and store.load()[0].ended_at == 40.0  # implicit parent: derived from its rounds

def test_a_recorded_protocol_run_follows_its_own_lifecycle_not_its_rounds(tmp_path):
    # protocol_started@5 → running; round r1 complete@20 → parent still running (fluidics continues between rounds);
    # protocol_finished@90 outcome "failed" → parent failed, ended_at 90, even though every round completed
    ...

def test_finished_without_started_is_ignored(tmp_path): ...
def test_started_carries_pid_and_machine(tmp_path): ...  # entry.pid == 4321, entry.pid_started_at == 1700000000.5, entry.machine == "scope-1"
def test_a_later_preview_event_replaces_the_earlier_one(tmp_path): ...  # preview a.png then b.png → entry.preview == "b.png"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/squid/test_acquisition_history.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'squid.acquisition_history'`

- [ ] **Step 3: Implement `squid/acquisition_history.py` with the interfaces above**

Folding is one pass over `events()` into a `dict[id, HistoryEntry]` plus a `removed` set, then a second pass attaching rounds and building missing parents. Keep `append` synchronous (`open(..., "a", encoding="utf-8")`, one `json.dumps(event)` line, `ensure_ascii=False`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/squid/test_acquisition_history.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/squid/acquisition_history.py software/tests/squid/test_acquisition_history.py
git commit -m "feat(history): append-only acquisition history store with protocol-run grouping" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Preview generation

**Files:**
- Create: `control/gallery_previews.py`
- Modify: `control/widgets_gallery.py` (import `load_thumbnail_array`, `array_to_pixmap` from the new module; delete the moved bodies)
- Test: `tests/control/test_gallery_previews.py`; move the four thumbnail tests out of `tests/control/test_widgets_gallery.py` into it (same assertions, new import).

**Interfaces:**
- Consumes: `control._def.CHANNEL_COLORS_MAP`, `control.utils_channel.extract_wavelength_from_config_name`; the overview file layout `<timepoint>/mosaic_view/mosaic_<mode>_<res>um.ome.tiff` with sidecar `mosaic_<mode>_<res>um.yaml` carrying `channel_names`; individual images named `<region>_<fov>_<z>_<channel-with-underscores>.<tiff|bmp|png|jpg>`.
- Produces:
  - `PREVIEW_MAX_SIDE = 512`
  - `@dataclass PreviewSource`: `kind: str` (`"overview"` | `"images"` | `"none"`), `overview_path: Optional[str]`, `image_paths: dict[str, str]` (channel name → file, first FOV of the first timepoint, one per channel), `channels: list[str]`.
  - `find_preview_source(folder: str) -> PreviewSource`: the latest timepoint's overview when any timepoint has one (channel names from the sidecar yaml, else from the OME `Channel.Name` metadata, else `["channel 1", ...]`), else per-channel first images (channel name = the file-name segment after `<region>_<fov>_<z>_` with underscores turned back to spaces), else `kind="none"`.
  - `load_channel_planes(source: PreviewSource, max_side: int) -> dict[str, np.ndarray]`: one 2D array per channel, downsampled with `cv2.INTER_AREA` so the longer side is `max_side` (never upsampled here), dtype as read; an RGB plane is converted to grayscale with `cv2.cvtColor(..., COLOR_RGB2GRAY)`.
  - `channel_rgb(channel_name: str) -> tuple[int, int, int]`: the `hex` from `CHANNEL_COLORS_MAP` for the channel's wavelength, white `(255, 255, 255)` for anything unmatched (brightfield included).
  - `stretch_to_unit(plane: np.ndarray) -> np.ndarray`: float32 in 0..1 from the 0.5/99.5 percentiles; a flat plane maps to its value over the dtype range (so an empty channel stays black).
  - `composite(planes: dict[str, np.ndarray], visible: Optional[set[str]] = None) -> np.ndarray`: `(H, W, 3) uint8`, additive sum of `stretch_to_unit(plane)[..., None] * rgb`, clipped; `visible` limits the channels; an empty dict gives a 1×1 black image.
  - `write_preview_png(folder: str, out_path: str, max_side: int = PREVIEW_MAX_SIDE) -> Optional[list[str]]`: writes the composite with `cv2.imwrite` (RGB→BGR) and returns the channel list, or returns None and writes nothing when `kind == "none"`; never raises for unreadable files (logs and treats the channel as missing).
  - `load_thumbnail_array(source: str, max_size: int) -> Optional[np.ndarray]` and `array_to_pixmap(array) -> QPixmap`: moved here unchanged from `widgets_gallery.py` (`array_to_pixmap` is the module's only Qt import, from `qtpy.QtGui`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/control/test_gallery_previews.py
def test_channel_rgb_uses_the_channel_colour_map():
    assert channel_rgb("Fluorescence 488 nm Ex") == (0x1F, 0xFF, 0x00)
    assert channel_rgb("BF LED matrix full") == (255, 255, 255)

def test_find_preview_source_prefers_the_latest_overview(saving_root):  # write_acquisition(..., nt=2) with overviews in 0/ and 1/
    src = find_preview_source(str(folder))
    assert src.kind == "overview" and src.overview_path.endswith(os.path.join("1", "mosaic_view", "mosaic_plate_5um.ome.tiff"))
    assert src.channels == ["BF LED matrix full", "Fluorescence 488 nm Ex"]  # from the sidecar yaml

def test_find_preview_source_falls_back_to_first_fov_images(saving_root):  # tiff images, no overview
    src = find_preview_source(str(folder))
    assert src.kind == "images" and set(src.image_paths) == {"BF LED matrix full", "Fluorescence 488 nm Ex"}
    assert all(os.path.basename(p).startswith("A1_0_0_") for p in src.image_paths.values())

def test_find_preview_source_none_for_zarr_without_overview(saving_root): ...

def test_composite_colours_channels_and_clips():
    # Both channels span their range, so the stretch maps 0 → 0 and 4000 → 1 (a flat channel would map by dtype range instead).
    planes = {"Fluorescence 488 nm Ex": np.array([[0, 4000]], np.uint16), "Fluorescence 638 nm Ex": np.array([[4000, 0]], np.uint16)}
    rgb = composite(planes)
    assert rgb.shape == (1, 2, 3)
    assert rgb[0, 0].tolist() == [255, 0, 0]  # 638 only
    assert rgb[0, 1].tolist() == [0x1F, 0xFF, 0x00]  # 488 only, its map colour
    assert composite(planes, visible={"Fluorescence 488 nm Ex"})[0, 0].tolist() == [0, 0, 0]
    both = composite({"Fluorescence 488 nm Ex": np.array([[0, 4000]], np.uint16), "Fluorescence 638 nm Ex": np.array([[0, 4000]], np.uint16)})
    assert both[0, 1].tolist() == [255, 255, 0]  # additive, clipped

def test_composite_keeps_a_flat_channel_dark_and_handles_rgb_planes(): ...  # flat 0 → black; (H,W,3) plane accepted

def test_write_preview_png_writes_a_bounded_rgb_png(saving_root, tmp_path):  # overview 400x600 → longest side 512, returns channels
def test_write_preview_png_returns_none_without_a_source(saving_root, tmp_path): ...
def test_write_preview_png_survives_an_unreadable_image(saving_root, tmp_path): ...  # junk bytes in one channel file → still writes, that channel black
```
Reuse `write_acquisition` from `tests/control/test_widgets_gallery.py` by importing it (Task 5 moves it to `tests/control/gallery_fixtures.py`; until then import from the test module). Give `write_acquisition` a `with_overview` sidecar: write `mosaic_plate_5um.yaml` with `channel_names` beside the overview, and an `nt` loop that creates `0/`, `1/` when `nt > 1`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_gallery_previews.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.gallery_previews'`

- [ ] **Step 3: Implement `control/gallery_previews.py`; move the thumbnail functions out of `control/widgets_gallery.py` and import them back**

Reading the overview: `tifffile.TiffFile(path).series[0]` gives `(C, Y, X)` (or `(Y, X)` for one channel); downsample each plane before stretching. Keep `_MAX_OVERVIEW_BYTES_FOR_MIP` logic out of here (the composite replaces it).

- [ ] **Step 4: Run both test files**

Run: `pytest tests/control/test_gallery_previews.py tests/control/test_widgets_gallery.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/gallery_previews.py software/control/widgets_gallery.py software/tests/control/test_gallery_previews.py software/tests/control/test_widgets_gallery.py
git commit -m "feat(gallery): colour composite previews from the mosaic overview or first images" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: MIP projection in the mosaic view

**Files:**
- Modify: `control/_def.py` (`ZProjectionMode` at line 635; the Views flags near line 1165; the `[VIEWS]` loader near line 2034)
- Modify: `control/core/multi_point_utils.py:142-165` (`MosaicTileUpdate`)
- Modify: `control/gui_hcs.py:423-432` (`_signal_new_image_fn` builds the update)
- Modify: `control/widgets_mosaic.py` (`blit_tiles_to_canvas` at line 87; `updateTile` at 444; `_update_mosaic_layer` at 561)
- Test: `tests/control/test_unified_mosaic_widget.py`

**Interfaces:**
- Produces:
  - `ZProjectionMode` members become `MIP = "mip"` and `LAST_PLANE = "last_plane"` (`MIDDLE` is removed: nothing reads it; `convert_to_enum` error text lists the two).
  - `control._def.MOSAIC_Z_PROJECTION: ZProjectionMode = ZProjectionMode.MIP`, loaded from `[VIEWS] mosaic_z_projection` (`mip` | `last_plane`) when present.
  - `MosaicTileUpdate` gains `z_index: int = 0` and `time_point: int = 0`; `gui_hcs._signal_new_image_fn` fills them from `info.z_index` and `info.time_point or 0`.
  - `blit_tiles_to_canvas(canvas, tiles, combine: str = "replace")`: `"max"` writes `np.maximum(dst, src)` into the clipped region instead of assigning.
  - `UnifiedMosaicWidget.updateTile`: `combine = "max" if control._def.MOSAIC_Z_PROJECTION is ZProjectionMode.MIP and update.z_index > 0 else "replace"`, passed through `_update_mosaic_layer(..., combine)` and the plate-mode blit. The first z-plane of any FOV (z_index 0) always replaces, so a new timepoint starts fresh.

- [ ] **Step 1: Write the failing tests**

```python
# tests/control/test_unified_mosaic_widget.py (append)
class TestZProjection:
    def test_mip_keeps_the_brighter_plane(self, mosaic_widget, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_Z_PROJECTION", control._def.ZProjectionMode.MIP)
        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0, z_index=0))
        widget.updateTile(_tile_update(np.full((100, 100), 50, dtype=np.uint8), 10.0, 10.0, z_index=1))
        assert int(widget.viewer.layers["BF"].data.max()) == 200
        widget.updateTile(_tile_update(np.full((100, 100), 230, dtype=np.uint8), 10.0, 10.0, z_index=2))
        assert int(widget.viewer.layers["BF"].data.max()) == 230

    def test_last_plane_overwrites(self, mosaic_widget, monkeypatch):  # LAST_PLANE: 200 then 50 at z 1 → max 50

    def test_first_plane_of_the_next_timepoint_replaces(self, mosaic_widget, monkeypatch):  # MIP: 200 (t0 z0), 50 (t1 z0) → max 50

    def test_blit_max_combines_only_the_overlap(self):  # canvas 100 at [0:50], tile 30 → stays 100; tile 120 → 120; outside untouched

def test_z_projection_enum_round_trips():
    assert control._def.ZProjectionMode.convert_to_enum("last_plane") is control._def.ZProjectionMode.LAST_PLANE
    with pytest.raises(ValueError):
        control._def.ZProjectionMode.convert_to_enum("middle")
```
`_tile_update` already forwards extra keyword fields; the fake update carries `z_index`/`time_point`. Also add to `tests/control/test_MultiPointController.py` (or a new small test next to it) an assertion that the `MosaicTileUpdate` emitted for a captured frame carries `z_index == info.z_index` and `time_point == info.time_point` — use the existing simulated-controller test pattern that collects `mosaic_tile_update` emissions, if one exists; otherwise construct `MosaicTileUpdate(image=..., x_mm=0, y_mm=0, channel_name="BF", z_index=3, time_point=2)` and assert the fields (the dataclass test is the floor).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_unified_mosaic_widget.py -q -k "ZProjection or z_projection"`
Expected: FAIL (`AttributeError: MOSAIC_Z_PROJECTION` / unexpected keyword `combine`)

- [ ] **Step 3: Implement the enum, flag, loader key, dataclass fields, the `combine` argument and the `updateTile` rule**

- [ ] **Step 4: Run the mosaic tests and the GUI construction tests**

Run: `pytest tests/control/test_unified_mosaic_widget.py -q` then `pytest tests/control/test_HighContentScreeningGui.py -q -k "not with_or_without_piezo"`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/_def.py software/control/core/multi_point_utils.py software/control/gui_hcs.py software/control/widgets_mosaic.py software/tests/control/test_unified_mosaic_widget.py
git commit -m "feat(mosaic): max-intensity projection across z for the mosaic view and its saved overview" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: History recorder and controller hooks

**Files:**
- Create: `control/acquisition_history_recorder.py`
- Create: `squid/version.py`
- Modify: `control/core/multi_point_controller.py` (`__init__` near line 242; `run_acquisition` at 714-1050; `_on_acquisition_completed` at 1100)
- Test: `tests/control/test_acquisition_history_recorder.py`, `tests/squid/test_version.py`

**Interfaces:**
- Consumes: `HistoryStore`, `entry_id_for` (Task 1); `write_preview_png` (Task 2); `AcquisitionParameters` (`control/core/multi_point_utils.py`, fields `experiment_ID`, `base_path`, `acquisition_start_time`, `NZ`, `deltaZ`, `Nt`, `deltat`, `selected_configurations`, `scan_position_information`, `do_autofocus`, `do_reflection_autofocus`, `laser_af_per_stack`, `xy_mode`, `skip_saving`, `z_stacking_config`, `use_piezo`); the saved `acquisition.yaml` for objective and sample fields.
- Produces:
  - `squid.version.software_version() -> str`: `git describe --tags --always --dirty` run in the software folder, cached after the first call, `"unknown"` when git is missing or fails (2 s timeout).
  - `class AcquisitionHistoryRecorder`: `__init__(self, store: HistoryStore, *, preview_executor: Executor, machine: Optional[str] = None, software_version: Optional[str] = None, snaps_enabled: Callable[[], bool] = lambda: control._def.RECORD_SNAPS_IN_HISTORY)`.
    - `started(self, params, experiment_path: str, *, snap: bool, skip_saving: bool, protocol: Optional[dict]) -> Optional[str]`: returns None (records nothing) when `skip_saving`, or when `snap` and `snaps_enabled()` is False; otherwise appends `started` with `kind = "snap" if snap else "run"`, `id = entry_id_for(experiment_path)`, `name = os.path.basename(experiment_path)`, `parent = entry_id_for(os.path.dirname(experiment_path)) if protocol else None`, `pid = os.getpid()`, `pid_started_at = psutil.Process().create_time()`, `machine`, `metadata = metadata_from_params(params, experiment_path)` plus `{"machine", "software_version", "file_format": control._def.FILE_SAVING_OPTION.value, "expected_images": int}`; returns the id. Every store failure is caught, logged once per recorder (`self._failed_once`), and returns None.
    - `finished(self, entry_id: Optional[str], reason: str, *, images: int, expected_images: int) -> None`: no-op for None; maps `completed`/`completed_with_errors` → `complete`, `user_abort` → `aborted`, anything else (`error`, `failed_to_start`) → `failed`; `fraction_done = min(1.0, images / expected_images)` (None when `expected_images == 0`); appends `finished`. It does NOT start the preview: the overview the preview is made from is written by the mosaic widget's own save executor, which may still be running (or not yet dispatched) when the controller completes, so the GUI decides when (Task 11).
    - `make_preview(self, entry_id: str, folder: str, *, replace: bool = False) -> Optional[Future]`: submits `_make_preview(entry_id, folder)` to the executor, which calls `write_preview_png(folder, previews_dir / f"{entry_id}.png")` (overwriting the file), sums the folder's file sizes with a recursive `os.scandir` walk (unreadable entries skipped), and appends a `preview` event with `channels` (the list, or `[]` and no `file` when `write_preview_png` returned None) and `bytes_on_disk`. With `replace=False`: a call while a job for that id is pending, or for an id that already has a `preview` event, returns None. With `replace=True`: when no job is pending it regenerates and appends a new `preview` event, which the fold takes over the old one; when a job for that id is pending it records `self._replace_after[entry_id] = folder` and returns None, and the pending job's done-callback submits one more `_make_preview` for that folder (so a replacement requested while the fallback preview is still rendering or counting file sizes is never lost). That is how a preview made by the GUI's timeout path gets upgraded when the awaited overview save finally lands. Preview failures are logged, never raised.
    - `history_entry_id` property on `MultiPointController` (the id of the running entry, None otherwise) for the GUI.
    - `failed_start(self, experiment_path: Optional[str], reason: str, *, snap: bool = False, skip_saving: bool = False, protocol: Optional[dict] = None) -> None`: applies the same snap and skip-saving rules as `started`; when the folder exists, appends `started` (metadata from the folder, `parent` from `protocol` as in `started`) and `finished` (`failed`, fraction 0, `reason`) in one go; else nothing.
    - `protocol_started(self, run_dir: str, run_name: str, protocol_name: str) -> None`: appends `protocol_started` with `id = entry_id_for(run_dir)`, `pid`, `pid_started_at`, `machine`. `protocol_finished(self, run_dir: str, outcome: str) -> None`: maps the runner's `finished` → `complete`, `stopped` → `aborted`, `failed` → `failed`, and appends `protocol_finished` with `reason` = the runner's word. `map_protocol_outcome(word: str) -> str` is that mapping as a module function (Task 5's import uses it).
    - `reconcile_orphans(self, *, pid_alive: Callable[[int], bool] = psutil.pid_exists, process_started_at: Callable[[int], Optional[float]] = <psutil.Process(pid).create_time(), None when the process is gone or inaccessible>) -> int`: for every entry (run, snap or protocol run) still `running` whose `machine` equals this recorder's machine, the recording process is alive only if `pid` is set, `pid_alive(pid)` is true, and either the recorded `pid_started_at` is None (an old event) or `process_started_at(pid)` returns a float within 2.0 s of it; a None from `process_started_at` (the process vanished between the two lookups, or cannot be inspected) counts as not alive. Otherwise the entry is closed with `finished` (or `protocol_finished`), outcome `failed`, reason `interrupted`, `fraction_done` None. Both callables are wrapped so an exception from psutil counts as "not alive" and never escapes into GUI startup. Returns the count. Entries from other machines are left alone, as are live processes (another Squid checkout on this PC mid-run). A reused pid after a reboot fails the creation-time check and is closed. Called once at GUI start, before the gallery's first load.
    - `shutdown(self) -> None`: `preview_executor.shutdown(wait=False, cancel_futures=True)`.
  - `class NullAcquisitionHistoryRecorder` with the same methods as no-ops (`started` returns None, `make_preview` returns None, `reconcile_orphans` returns 0).
  - `metadata_from_params(params, experiment_path: str) -> dict` with keys: `experiment_id`, `widget_type` (from the yaml), `xy_mode`, `objective`, `magnification`, `pixel_size_um`, `wellplate_format`, `channels` (names), `num_regions`, `num_fovs`, `nz`, `dz_um`, `nt`, `dt_s`, `z_stacking_config`, `use_piezo`, `contrast_af`, `laser_af`, `laser_af_per_stack`, `start_time`; yaml-only fields come from `acquisition.yaml` when readable, else None. (Task 5's `metadata_from_folder` produces the same keys from files alone; both live in `control/gallery_import.py`'s sibling? No: put `metadata_from_folder` in `control/gallery_import.py` in Task 5 and have `metadata_from_params` call it for the yaml-only fields, so the key set is defined once. For this task, define `metadata_from_params` here with the yaml read inline; Task 5 refactors it to call `metadata_from_folder`.)
  - `MultiPointController`: new attributes `self.history_recorder = NullAcquisitionHistoryRecorder()` and `self._history_entry_id: Optional[str] = None` (exposed read-only as the `history_entry_id` property), `self._expected_image_count = 0`; `run_acquisition` calls `self.history_recorder.started(acquisition_params, experiment_path, snap=acquire_current_fov, skip_saving=self.skip_saving, protocol=run_protocol_info)` right after the `RunStateWriter.start` block (inside its own `try/except Exception` that logs), with `self._expected_image_count = expected["fovs"] * expected["timepoints"] * expected["z"] * expected["channels"]`. Every unsuccessful start is recorded through one helper, `self._record_failed_start(reason, acquire_current_fov, run_protocol_info)`: when `self._history_entry_id` is set it calls `finished(self._history_entry_id, reason, images=0, expected_images=self._expected_image_count)`, otherwise `failed_start(os.path.join(self.base_path, self.experiment_ID) if self.experiment_ID else None, reason, snap=acquire_current_fov, skip_saving=self.skip_saving, protocol=run_protocol_info)`; in both cases it then sets `self._history_entry_id = None`, so the next attempt never finishes a previous entry again. It is called from the two early returns before the `try` (validation, start blocker) and from the `finally: if not thread_started:` branch, which also covers the MCU idle-wait timeout return, the two focus-map returns and any pre-worker exception (all of which run before `started`). `_on_acquisition_completed` calls `self.history_recorder.finished(self._history_entry_id, self.last_end_reason, images=self.last_image_count, expected_images=self._expected_image_count)` before the restores, then sets `self._history_entry_id = None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/squid/test_version.py
def test_software_version_is_a_non_empty_string_and_cached(monkeypatch): ...  # two calls, one subprocess.run (monkeypatch subprocess.run counting)
def test_software_version_unknown_when_git_fails(monkeypatch): ...  # subprocess.run raises FileNotFoundError → "unknown"

# tests/control/test_acquisition_history_recorder.py
# A fake AcquisitionParameters via types.SimpleNamespace with the fields listed in Interfaces; an experiment folder
# from write_acquisition (Task 2's helper) so acquisition.yaml exists; store on tmp_path; a synchronous executor
# (concurrent.futures.ThreadPoolExecutor(1) plus future.result() through a captured list, or a tiny inline executor).

def test_started_and_finished_write_a_complete_entry_with_metadata_and_preview(tmp_path, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    recorder.finished(entry_id, "completed", images=16, expected_images=16)
    assert store.load()[0].preview is None  # finished alone never makes the preview
    recorder.make_preview(entry_id, str(folder)).result(timeout=10)
    [e] = store.load()
    assert e.outcome == "complete" and e.fraction_done == 1.0 and e.metadata["channels"] == ["BF LED matrix full", "Fluorescence 488 nm Ex"]
    assert e.metadata["software_version"] == "v-test" and e.metadata["machine"] == "scope-1" and e.pid == os.getpid()
    assert e.preview == f"{entry_id}.png" and (store.previews_dir / e.preview).is_file() and e.bytes_on_disk > 0

def test_make_preview_is_idempotent_per_entry_unless_replacing(...):  # second plain call returns None, one preview event; replace=True → a second preview event and a rewritten file
def test_a_replacement_requested_during_a_pending_job_runs_after_it(...):
    # write_preview_png patched to block on a threading.Event; make_preview(id, folder_a) → pending; make_preview(id, folder_b, replace=True) → None;
    # release the event; wait → two preview events, the second produced from folder_b (patched writer records the folder it was called with)
def test_protocol_started_and_finished_drive_the_parent_outcome(...):  # protocol_started → running; rounds complete → still running; protocol_finished("stopped") → aborted, reason "stopped"
def test_reconcile_orphans_closes_dead_runs_of_this_machine_only(...):
    # running entries: pid dead on this machine → failed/"interrupted"; pid alive with matching creation time → untouched;
    # other machine, dead pid → untouched; a running protocol_started with a dead pid → protocol_finished failed/"interrupted"
def test_reconcile_orphans_treats_a_reused_pid_as_dead(...):
    # pid_alive True but process_started_at(pid) differs from the recorded pid_started_at by hours → closed as interrupted;
    # an old event with pid_started_at None and a live pid → untouched
def test_reconcile_orphans_handles_a_vanished_or_unreadable_process(...):
    # pid_alive True but process_started_at returns None → closed as interrupted; process_started_at raising psutil.AccessDenied → closed, no exception

def test_user_abort_is_aborted_with_the_fraction_done(...):  # images=4 of 16 → aborted, 0.25; images=0 → aborted, 0.0
def test_error_and_failed_to_start_are_failed(...):
def test_finished_without_a_preview_source_records_size_but_no_file(...):  # folder with metadata only → preview event has file None/absent, channels [], bytes_on_disk >= 0; entry.preview is None
def test_snaps_are_skipped_unless_enabled(...):  # snaps_enabled False → None; True → kind "snap"
def test_skip_saving_runs_are_not_recorded(...):
def test_failed_start_records_a_failed_entry_only_when_the_folder_exists(...):  # also: snap=True with snaps off → nothing; protocol → parent set
def test_protocol_round_carries_its_parent(...):  # protocol dict → entry.parent == entry_id_for(dirname(folder)); load() groups it
def test_store_failure_never_raises_and_logs_once(tmp_path, params, folder, caplog):  # store.append raising OSError → started returns None, finished no-op, one warning
def test_preview_failure_is_contained(...):  # write_preview_png raising → finished entry still there, no preview event

# controller level (simulated hardware; pattern of tests/control/test_MultiPointController.py):
def test_simulated_acquisition_records_started_and_finished(tmp_path, monkeypatch, qtbot?):
    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path))
    mpc = ts.get_test_multi_point_controller(microscope=scope); mpc.history_recorder = AcquisitionHistoryRecorder(HistoryStore(tmp_path), preview_executor=ThreadPoolExecutor(1))
    ... configure one region, one channel, run_acquisition(), wait for finish as the existing tests do ...
    [e] = HistoryStore(tmp_path).load(); assert e.outcome == "complete" and e.images > 0

def test_an_early_exit_before_the_watchdog_block_is_recorded_as_failed(tmp_path, monkeypatch):
    # same setup; monkeypatch mpc.microcontroller.wait_till_operation_is_completed to raise TimeoutError
    # → run_acquisition() returns False; the store has one entry, outcome "failed", reason "failed_to_start", fraction 0.0

def test_consecutive_failed_starts_each_get_their_own_entry(tmp_path, monkeypatch):
    # 1st attempt: monkeypatch MultiPointWorker.__init__ to raise after `started` ran → entry A failed; mpc.history_entry_id is None
    # 2nd attempt with a new experiment folder: monkeypatch validate_acquisition_settings to return False → entry B failed;
    # A has exactly one finished event (the second refusal did not finish A again)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_acquisition_history_recorder.py tests/squid/test_version.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `squid/version.py`, `control/acquisition_history_recorder.py`, and the controller hooks**

- [ ] **Step 4: Run the new tests and the controller suite**

Run: `pytest tests/control/test_acquisition_history_recorder.py tests/squid/test_version.py tests/control/test_MultiPointController.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/squid/version.py software/control/acquisition_history_recorder.py software/control/core/multi_point_controller.py software/tests/control/test_acquisition_history_recorder.py software/tests/squid/test_version.py
git commit -m "feat(history): record every acquisition's start, end and preview from the controller" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Folder import

**Files:**
- Create: `control/gallery_import.py`
- Create: `tests/control/gallery_fixtures.py` (move `write_acquisition` here; add `write_protocol_run(root, run_name, rounds)` writing `run_manifest.json` and `round_<n>_image/` acquisition folders whose `acquisition.yaml` has a `protocol` section)
- Modify: `control/widgets_gallery.py` (import the moved functions; nothing else yet)
- Modify: `control/acquisition_history_recorder.py` (`metadata_from_params` uses `metadata_from_folder` for yaml-only fields)
- Test: `tests/control/test_gallery_import.py`; update imports in `tests/control/test_widgets_gallery.py`, `tests/control/test_gallery_previews.py`, `tests/control/test_acquisition_history_recorder.py`, `tests/control/test_HighContentScreeningGui.py`

**Interfaces:**
- Consumes: `HistoryStore`, `entry_id_for`, `normalize_folder` (Task 1); `write_preview_png` (Task 2).
- Produces (moved verbatim from `widgets_gallery.py`): `METADATA_FILES`, `IMAGE_EXTENSIONS`, `NO_IMAGES`, `AcquisitionEntry`, `split_experiment_name`, `is_acquisition_folder`, `inspect_images`, `read_acquisition_entry`, `scan_acquisitions`.
- Produces (new):
  - `SNAP_FOLDER_PREFIX = "snapped images"`; `is_snap_folder(path) -> bool` (basename starts with it).
  - `RECORDED_METADATA_KEYS: tuple[str, ...]`: the Task 4 key set plus `image_format` and `protocol`, in display order; `metadata_from_folder` returns exactly these keys (None where unknown), and the recorder appends `machine`, `software_version`, `file_format`, `expected_images` after them.
  - `metadata_from_folder(path: str) -> dict`: those keys, read from `acquisition.yaml`, `acquisition parameters.json`, `acquisition_channels.yaml`, `coordinates.csv` (what `read_acquisition_entry` reads today, as a dict), plus `image_format` from `inspect_images` and `protocol` (the yaml's `protocol` section or None).
  - `protocol_rounds(folder: str) -> list[str]`: acquisition subfolders (one level) whose `acquisition.yaml` has a `protocol` mapping, sorted by `(round, attempt, name)`; empty when `folder` is not a protocol run dir.
  - `@dataclass ImportResult`: `added: list[str]` (entry ids), `skipped_existing: int`, `skipped_snaps: int`, `errors: list[str]`.
  - `import_folder(store: HistoryStore, root: str, *, include_snaps: bool, make_preview: Optional[Callable[[str, str], None]] = None) -> ImportResult`: for each acquisition folder directly under `root` (and `root` itself when it is one): skip snaps unless `include_snaps` (then `kind = "snap"`); skip ids already in `store.load()`; append `imported` with `started_at` from `metadata["start_time"]` or the folder-name timestamp, `outcome = "complete"` (a folder with no images → `"unknown"`); a subfolder that is a protocol run dir (`protocol_rounds` non-empty) gets `protocol_started`, one `imported` per round with `parent`, and `protocol_finished` in the same import, so an imported protocol run never sits Running: its outcome comes from `run_manifest.json`'s `status` through `map_protocol_outcome` when that file is readable and its status is one of the runner's end words (`finished`, `stopped`, `failed`; `reason` = the word), and is otherwise derived from the rounds (`complete` when every round is complete, else `aborted`; `reason = "derived"`; a manifest still saying `running`, `paused` or `held` is a run that never closed). When `make_preview` is given, call it `(folder, entry_id)` for every added entry (the caller runs it on a worker and appends the `preview` event).

- [ ] **Step 1: Write the failing tests**

```python
# tests/control/test_gallery_import.py
def test_import_adds_each_acquisition_once(saving_root):  # two runs → 2 added; second call → 0 added, skipped_existing 2
def test_import_skips_snaps_unless_asked(saving_root):  # "snapped images_2026-..." folder → skipped_snaps 1; include_snaps → kind "snap"
def test_import_dates_old_folders_from_their_name(saving_root):  # with_yaml=False → started_at == folder timestamp
def test_import_groups_a_protocol_run(saving_root):  # write_protocol_run(root, "run_7", rounds=3, status="finished") → one protocol_run entry named run_7 with 3 rounds, outcome "complete", not running
def test_imported_protocol_outcome_comes_from_the_manifest_or_the_rounds(saving_root):  # status "stopped" → aborted/"stopped"; status "running" (never closed) with all rounds complete → complete/"derived"; no manifest → derived
def test_import_calls_make_preview_for_added_entries(saving_root): ...
def test_protocol_rounds_orders_by_round_and_attempt(saving_root): ...
# gallery_fixtures.write_protocol_run(root, run_name, rounds, status="finished") writes run_manifest.json with that status
# (None → no manifest) and round_<n>_image/ acquisition folders whose acquisition.yaml carries a protocol section.
def test_metadata_from_folder_matches_the_recorder_key_set(saving_root):  # set(keys) == RECORDED_METADATA_KEYS (export the tuple from gallery_import; the recorder asserts the same in Task 4's test after this refactor)
def test_import_of_an_unreadable_root_reports_an_error_not_an_exception(tmp_path): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_gallery_import.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.gallery_import'`

- [ ] **Step 3: Implement `control/gallery_import.py`, move the fixtures, update imports, and point `metadata_from_params` at `metadata_from_folder`**

- [ ] **Step 4: Run every test touched**

Run: `pytest tests/control/test_gallery_import.py tests/control/test_widgets_gallery.py tests/control/test_gallery_previews.py tests/control/test_acquisition_history_recorder.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/gallery_import.py software/control/widgets_gallery.py software/control/acquisition_history_recorder.py software/tests/control/gallery_fixtures.py software/tests/control/test_gallery_import.py software/tests/control/test_widgets_gallery.py software/tests/control/test_gallery_previews.py software/tests/control/test_acquisition_history_recorder.py software/tests/control/test_HighContentScreeningGui.py
git commit -m "feat(gallery): import saved acquisition folders and protocol runs into the history" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Standalone ndviewer_light launcher

**Files:**
- Create: `control/ndviewer_launcher.py`
- Modify: `control/_def.py` (new flag beside `ENABLE_NDVIEWER`; `[VIEWS]` loader)
- Test: `tests/control/test_ndviewer_launcher.py`

**Interfaces:**
- Produces:
  - `control._def.NDVIEWER_LIGHT_COMMAND: str = ""`, loaded from `[VIEWS] ndviewer_light_command`.
  - `control._def.RECORD_SNAPS_IN_HISTORY: bool = False`, loaded from `[VIEWS] record_snaps_in_history` (defined here so Task 7's preferences and Task 4's recorder share one flag; Task 4 reads it through `getattr(control._def, "RECORD_SNAPS_IN_HISTORY", False)` until this task lands).
  - `@dataclass LauncherResolution`: `command: Optional[list[str]]`, `source: str` (`"preference"` | `"path"` | `"package"` | `"submodule"` | `"none"`), `reason: str` (human text for the disabled button, e.g. `"ndviewer_light is not installed: pip install it, or set its location in Preferences > Views"`), `env: Optional[dict]` (extra environment variables merged over `os.environ`; the preference-directory case sets `PYTHONPATH`), `cwd: Optional[str]` (the software folder in the submodule case, which runs `[python, "-m", "control.ndviewer_light"]`; None otherwise).
  - `resolve_ndviewer_light(preference: str = "", *, which: Callable[[str], Optional[str]] = shutil.which, package_importable: Callable[[], bool] = <importlib.util.find_spec("ndviewer_light") is not None>, submodule_main: str = <software>/control/ndviewer_light/__main__.py, python: str = sys.executable) -> LauncherResolution`. Order: a non-empty `preference` first (an existing file → `[preference]`; a directory holding `ndviewer_light/__main__.py` → `[python, "-m", "ndviewer_light"]` with `env["PYTHONPATH"]` = that directory; anything else → `shlex.split(preference)`); then `which("ndviewer-light")` → `[that]`; then `package_importable()` → `[python, "-m", "ndviewer_light"]`; then `os.path.isfile(submodule_main)` → `[python, "-m", "control.ndviewer_light"]` with `cwd` = software dir; else `command=None` with the reason above.
  - `launch_ndviewer_light(resolution: LauncherResolution, folder: str, *, popen: Callable = subprocess.Popen) -> subprocess.Popen`: `popen(command + [folder], cwd=resolution.cwd, env={**os.environ, **(resolution.env or {})}, close_fds=True, stdin/stdout/stderr=DEVNULL, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP on Windows, start_new_session=True elsewhere)`; raises `RuntimeError(resolution.reason)` when `command` is None, and lets `OSError` from `popen` propagate (the GUI shows it).

- [ ] **Step 1: Write the failing tests**

```python
def test_preference_executable_wins(tmp_path): ...  # file → command [file], source "preference"
def test_preference_directory_runs_the_module_from_it(tmp_path): ...  # dir/ndviewer_light/__main__.py → [python, "-m", "ndviewer_light"], env PYTHONPATH startswith dir
def test_path_command_then_package_then_submodule_then_none(tmp_path): ...  # four resolutions with injected which/package_importable/submodule_main
def test_launch_appends_the_folder_and_detaches(monkeypatch): ...  # fake popen records args/kwargs; on win32 creationflags has DETACHED_PROCESS
def test_launch_without_a_command_raises_the_reason(): ...
def test_def_flags_default(): assert control._def.NDVIEWER_LIGHT_COMMAND == "" and control._def.RECORD_SNAPS_IN_HISTORY is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_ndviewer_launcher.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the module and the two flags with their loader keys**

- [ ] **Step 4: Run the tests**

Run: `pytest tests/control/test_ndviewer_launcher.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/ndviewer_launcher.py software/control/_def.py software/tests/control/test_ndviewer_launcher.py
git commit -m "feat(gallery): find and launch a standalone ndviewer_light for a saved acquisition" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Preferences > Views additions

**Files:**
- Modify: `control/widgets.py` (`_create_views_tab` at 1953-2021; config writes at 2466-2490; runtime apply at 2620-2626; live-change rows at 3000-3030)
- Test: `tests/control/test_preferences_dialog.py` (`TestViewsTab` at line 739)

**Interfaces:**
- Consumes: `control._def.MOSAIC_Z_PROJECTION`, `ZProjectionMode` (Task 3); `RECORD_SNAPS_IN_HISTORY`, `NDVIEWER_LIGHT_COMMAND` (Task 6).
- Produces, on `PreferencesDialog`: `self.mosaic_z_projection_combo` (QComboBox, items "Max intensity projection" → data `"mip"`, "Last plane acquired" → data `"last_plane"`, in the Mosaic View group with the label "Z-stack projection:"), `self.record_snaps_checkbox` (label "Record snaps in the acquisition history:", in a new "Gallery" collapsible group), `self.ndviewer_light_command_edit` (QLineEdit, placeholder "auto-detect (PATH, installed package, submodule)", label "ndviewer_light location:", in the Gallery group with a tooltip naming the three accepted forms). Config keys under `[VIEWS]`: `mosaic_z_projection`, `record_snaps_in_history`, `ndviewer_light_command`. Runtime apply sets the three `control._def` values (`ZProjectionMode.convert_to_enum(data)`). Live-change rows: ("Z-stack projection", old, new, False), ("Record snaps in history", ..., False), ("ndviewer_light location", ..., False): none needs a restart.

- [ ] **Step 1: Write the failing tests**

```python
# in TestViewsTab
def test_gallery_controls_exist_and_mirror_runtime_defaults(self, preferences_dialog):
    d = preferences_dialog
    assert d.mosaic_z_projection_combo.currentData() == control._def.MOSAIC_Z_PROJECTION.value
    assert d.record_snaps_checkbox.isChecked() is control._def.RECORD_SNAPS_IN_HISTORY
    assert d.ndviewer_light_command_edit.text() == control._def.NDVIEWER_LIGHT_COMMAND

def test_gallery_settings_are_written_and_applied(self, preferences_dialog, ...):  # select last_plane, check snaps, type a path; save → config [VIEWS] keys; apply → control._def values (restore in a finally/monkeypatch)
def test_gallery_changes_do_not_require_restart(self, preferences_dialog): ...  # the three change rows have restart False
```
Follow the file's existing fixture (`preferences_dialog`) and the `_detect_changes`/save helpers the other Views tests use.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_preferences_dialog.py -q -k "gallery"`
Expected: FAIL with `AttributeError: ... has no attribute 'mosaic_z_projection_combo'`

- [ ] **Step 3: Add the controls, config writes, runtime apply and change rows**

- [ ] **Step 4: Run the preferences suite**

Run: `pytest tests/control/test_preferences_dialog.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/widgets.py software/tests/control/test_preferences_dialog.py
git commit -m "feat(preferences): z projection, snap recording and ndviewer_light location under Views" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Gallery widget on the history: model, grid and list views, filters

**Files:**
- Modify: `control/widgets_gallery.py` (rewrite the widget around `HistoryStore`; keep `make_placeholder_pixmap`, `square_tile`, the executor/generation pattern and `_escape`)
- Test: `tests/control/test_widgets_gallery.py` (rewrite the widget tests; the folder-reading tests moved in Task 5)

**Interfaces:**
- Consumes: `HistoryStore`, `HistoryEntry`, `folder_availability` (Task 1); `load_thumbnail_array`, `array_to_pixmap` (Task 2); `import_folder`, `write_preview_png` (Tasks 5, 2) in Task 10.
- Produces:
  - `class GalleryView(enum.Enum)`: `GRID = "grid"`, `LIST = "list"`; `LAST_GALLERY_VIEW_CACHE = "cache/last_gallery_view.yaml"`; `_load_last_gallery_view() -> GalleryView` / `_save_last_gallery_view(view)` (the `widgets_mosaic` pattern).
  - `STATUS_FILTERS = ("All", "Running", "Complete", "Aborted", "Failed", "Offline", "Snaps")` (Snaps shown only when `control._def.RECORD_SNAPS_IN_HISTORY`).
  - `@dataclass GalleryRow`: `entry: HistoryEntry`, `availability: str`, `status_label: str` (`"Running 38%"`, or `"Running · round 3"` for a protocol run; `"Complete"`, `"Aborted at 38%"`, `"Failed"`, `"Drive offline"`, `"Folder missing"`; `"Snap · "` prefix for snaps), `search_text: str` (lower-case join of name, sample, channels, objective, image format, protocol name).
  - `build_rows(entries: list[HistoryEntry], running_fraction: Optional[float] = None) -> list[GalleryRow]` (pure; availability through `folder_availability`; `running_fraction` feeds the running row's percentage); `status_label_for(entry, availability, running_fraction=None) -> str` (pure); `matches(row, needle: str, status_filter: str) -> bool` (pure); `filter_counts(rows) -> dict[str, int]` (pure, one count per `STATUS_FILTERS` name).
  - `class AcquisitionGalleryWidget(QWidget)`: `__init__(self, store: HistoryStore, *, import_root_provider: Callable[[], Optional[str]], parent=None)`; signals `signal_open_in_squid(str)`, `signal_open_in_window(str)`, `signal_load_settings(str)` (all carry the acquisition folder); `reload()` (loads the store on the scan executor, builds rows, repopulates both views, keeps the selection by entry id); `rows() -> list[GalleryRow]` in display order after filtering; `set_view(view: GalleryView)` / `view -> GalleryView`; `select_entry(entry_id) -> bool`; `selected_entry() -> Optional[HistoryEntry]`; `set_status_filter(name)`; `set_running_progress(fraction: Optional[float])` (relabels the running row without a reload); `filter_edit`, `filter_buttons: dict[str, QPushButton]` (one chip per `STATUS_FILTERS` name, text `f"{name} {count}"`), `view_toggle` (QPushButton or two-state control), `btn_refresh`, `count_label`, `status_label`, `list_widget` (grid, QListWidget IconMode as today), `table_widget` (QTableWidget with columns Name, Started, Sample, Objective, Channels, FOVs, Nz / Nt, Format, Status; a thumbnail icon in the Name cell; `setSortingEnabled(True)`); thumbnails come from `store.previews_dir / entry.preview` (cached by file mtime, decoded on the thumbnail executor), else the placeholder with the format text; a running entry's thumbnail is the "Running…" placeholder; a `QTimer` (2 s) reloads while the widget is visible when `store.path`'s mtime changed; `shutdown()` stops timer and executors.
  - Grid tile text: `f"{entry.name}\n{started:%Y-%m-%d %H:%M}"`; tooltip = status + the metadata summary lines (reuse the summary wording of the current build from the metadata dict). A protocol run's tile shows `f"{name} · {len(rounds)} rounds"` and the latest round's preview.
  - `signal_shown = Signal()` emitted from `showEvent` (the GUI re-checks the ndviewer_light launcher on it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/control/test_widgets_gallery.py (widget half; fixtures build a store in tmp_path with ah.HistoryStore and events
# for: complete run with preview PNG (write a 64x48 RGB png into previews/), aborted run (fraction 0.38), running run,
# offline run (folder "Q:\\gone\\run" with exists patched false → use a path whose anchor is missing), snap entry)
def test_build_rows_labels_statuses(store_with_entries): ...  # expected status_label per entry; snap label "Snap · Complete"; running row "Running 38%" with running_fraction 0.38
def test_matches_filters_by_status_and_text(): ...  # "Offline" matches only drive_offline/folder_missing; needle "488" by channel
def test_filter_counts_count_each_status(store_with_entries): ...  # {"All": 5, "Running": 1, "Complete": 2, "Aborted": 1, "Failed": 0, "Offline": 1, "Snaps": 1}
def test_set_running_progress_relabels_the_running_row(gallery): ...
def test_gallery_lists_the_history_newest_first(gallery): ...
def test_grid_and_list_show_the_same_rows_and_the_view_is_remembered(gallery, tmp_path, monkeypatch): ...  # set_view(LIST) → table_widget rowCount == len(rows); cache file holds "list"; a new widget starts in LIST
def test_status_filter_chips_narrow_the_list(gallery): ...
def test_snaps_filter_only_when_enabled(gallery, monkeypatch): ...
def test_thumbnails_load_from_the_preview_png(gallery, qtbot): ...  # cache keyed by entry id; icon not null; running entry keeps placeholder
def test_reload_keeps_the_selection_and_picks_up_new_events(gallery, qtbot): ...
def test_history_file_change_triggers_a_reload(gallery, qtbot): ...  # append an event; qtbot.waitUntil count grows within 5 s
def test_offline_entry_is_marked_and_listed(gallery): ...
def test_a_stale_load_result_is_ignored(gallery): ...; def test_shutdown_drops_late_results(gallery): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_widgets_gallery.py -q`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'import_root_provider'` and missing names)

- [ ] **Step 3: Rewrite the widget around the store with both views and the filters**

- [ ] **Step 4: Run the gallery tests**

Run: `pytest tests/control/test_widgets_gallery.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/widgets_gallery.py software/tests/control/test_widgets_gallery.py
git commit -m "feat(gallery): history-backed gallery with grid and list views and status filters" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Details pane, actions and context menu

**Files:**
- Modify: `control/widgets_gallery.py`
- Test: `tests/control/test_widgets_gallery.py`

**Interfaces:**
- Consumes: Task 8's widget; `load_channel_planes`, `find_preview_source`, `composite` (Task 2) for the large preview with channel chips.
- Produces, on `AcquisitionGalleryWidget`:
  - `set_open_in_squid(available: bool, reason: str = "", *, hidden: bool = False)` and `set_open_in_window(available: bool, reason: str = "")`: when unavailable the button is disabled with `reason` as tooltip and the status line shows it when the user tries; `hidden=True` (the NDViewer tab is switched off) removes the in-Squid button and menu entry altogether, as the spec says.
  - `metadata_label` also shows `bytes_on_disk` as a "Size" row (`f"{bytes/1e9:.1f} GB"`, MB under 1 GB) when known.
  - Details pane widgets: `preview_label` (QLabel showing the composite, longest side 360 px; from the online overview's planes when available, else the cached PNG, else the placeholder), `channel_chips` (one checkable QPushButton per channel in `preview_channels`/planes; toggling recomposites when planes are loaded, otherwise the chips are disabled), `metadata_label` (every key of `entry.metadata` as a two-column HTML table, in the order of `RECORDED_METADATA_KEYS`, then any extra keys), `details_toggle` + `raw_yaml_text` (QPlainTextEdit, read-only, filled with the run's `acquisition.yaml` text when online), buttons `btn_open_in_squid`, `btn_open_in_window`, `btn_show_folder`, `btn_load_settings` (enabled only when `acquisition.yaml` exists and the entry is online).
  - `open_selected()`: Open in Squid when available, else Open in window when available, else Show folder (double-click and Enter call it; the running entry only allows Show folder).
  - `show_selected_in_file_browser()`, `load_selected_settings()`, `remove_selected()` (appends `removed` and reloads), `locate_selected()` (`QFileDialog.getExistingDirectory`, refuses a folder whose basename differs from the entry's with a message, else appends `relocated` and reloads; for a `protocol_run` it also appends `relocated` for every round whose basename exists under the new folder, so the rounds' open actions follow the move, and names in the status line any round not found there), `copy_selected_path()` (`QApplication.clipboard()`).
  - Context menu entries, in order: Open in Squid, Open in window, Show folder, Load settings, separator, Copy path, Locate…, Remove from history.
  - For a `protocol_run` entry: `metadata_label` shows the protocol name and run folder, then a rounds table (round, step, attempt, when, outcome) where each row has its own Open in Squid / Open in window buttons emitting that round's folder; the entry-level open buttons act on the latest round.

- [ ] **Step 1: Write the failing tests**

```python
def test_selecting_an_entry_fills_the_details_pane(gallery): ...  # metadata keys rendered; status; chips == preview channels
def test_double_click_prefers_squid_then_window_then_folder(gallery, qtbot, monkeypatch): ...  # three configurations, one signal/opener each
def test_running_entry_only_shows_the_folder(gallery, qtbot, monkeypatch): ...
def test_load_settings_requires_an_online_yaml(gallery, qtbot): ...
def test_remove_appends_a_removed_event_and_drops_the_row(gallery, qtbot): ...
def test_locate_rejects_a_different_folder_name_and_accepts_the_same(gallery, qtbot, monkeypatch, tmp_path): ...  # getExistingDirectory patched; events; entry.folder updated
def test_locating_a_moved_protocol_run_moves_its_rounds_too(gallery, qtbot, monkeypatch, tmp_path):
    # protocol run recorded under old_root/run_7 with rounds round_1_image, round_2_image; copy the tree to new_root/run_7 minus round_2_image
    # locate → parent.folder == new_root/run_7; rounds[0].folder == new_root/run_7/round_1_image; rounds[1] unchanged and named in status_label;
    # the round-1 Open in window button now emits the new folder
def test_copy_path_puts_the_folder_on_the_clipboard(gallery): ...
def test_channel_chips_toggle_the_composite(gallery, qtbot, saving_root): ...  # online overview; untick one channel → preview pixel changes
def test_protocol_run_lists_its_rounds_with_their_own_open_buttons(gallery, qtbot): ...  # includes an imported, complete protocol run: its entry-level and per-round open buttons are enabled
def test_unavailable_viewer_disables_the_button_with_the_reason(gallery): ...
def test_hidden_viewer_removes_the_button_and_menu_entry(gallery): ...  # set_open_in_squid(False, hidden=True) → btn_open_in_squid.isHidden(); double-click goes to the window opener
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_widgets_gallery.py -q -k "details or double_click or running_entry or load_settings or remove or locate or copy_path or chips or protocol_run or unavailable"`
Expected: FAIL with missing attributes

- [ ] **Step 3: Implement the details pane, actions and context menu**

- [ ] **Step 4: Run the gallery tests**

Run: `pytest tests/control/test_widgets_gallery.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/widgets_gallery.py software/tests/control/test_widgets_gallery.py
git commit -m "feat(gallery): details pane with preview, channel chips, metadata and run actions" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Import from the gallery and the first-launch offer

**Files:**
- Modify: `control/widgets_gallery.py`
- Test: `tests/control/test_widgets_gallery.py`

**Interfaces:**
- Consumes: `import_folder`, `is_acquisition_folder` (Task 5); `write_preview_png` (Task 2); `control._def.RECORD_SNAPS_IN_HISTORY`.
- Produces, on `AcquisitionGalleryWidget`:
  - `btn_import` ("Add folder to history"): `QFileDialog.getExistingDirectory` starting at `import_root_provider()`; then `import_path(folder: str)`.
  - `import_path(folder: str) -> None`: runs `import_folder(store, folder, include_snaps=RECORD_SNAPS_IN_HISTORY, make_preview=self._make_preview)` on the scan executor; `_make_preview(folder, entry_id)` writes `previews_dir / f"{entry_id}.png"` and appends the `preview` event; when done, the status line reads `"Added N acquisitions from <folder> (M already in the history, K snaps skipped)"` and `reload()` runs; errors go to the status line and the log.
  - `offer_initial_import() -> None`: when the history is empty, `import_root_provider()` names an existing folder that holds at least one acquisition folder, and `cache/gallery_import_offered.txt` does not exist, ask once with `QMessageBox.question` ("Add the acquisitions already in <folder> to the history?"); write the marker file whatever the answer; on Yes call `import_path`. Called by the GUI after construction (Task 11).

- [ ] **Step 1: Write the failing tests**

```python
def test_import_button_adds_the_folders_and_previews(gallery, qtbot, saving_root, monkeypatch): ...  # getExistingDirectory patched → rows grow; previews/ has PNGs; status text
def test_initial_offer_asks_once_and_imports_on_yes(qtbot, tmp_path, saving_root, monkeypatch): ...  # empty store; question patched Yes → entries; marker written; second widget does not ask
def test_initial_offer_is_silent_when_the_history_is_not_empty_or_the_folder_has_no_runs(...): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/control/test_widgets_gallery.py -q -k "import_button or initial_offer"`
Expected: FAIL with missing attributes

- [ ] **Step 3: Implement import from the widget and the one-time offer**

- [ ] **Step 4: Run the gallery tests**

Run: `pytest tests/control/test_widgets_gallery.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/widgets_gallery.py software/tests/control/test_widgets_gallery.py
git commit -m "feat(gallery): add folders to the history from the tab, offered once on first launch" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: GUI wiring

**Files:**
- Modify: `control/gui_hcs.py` (`setupImageDisplayTabs` gallery block near line 1273; connections near line 1638; the Gallery handlers near line 3019; shutdown near line 3175)
- Modify: `control/widgets_mosaic.py` (`_dispatch_save` / `_write_save_snapshot` near lines 846-947: emit `signal_view_saved`)
- Modify: `control/widgets_fluidics/protocol_widget.py` (where `self.runner` is set near line 342, and `_finish` at line 514: emit the run lifecycle signals)
- Test: `tests/control/test_HighContentScreeningGui.py` (replace `test_gallery_tab_routes_open_and_load_settings_to_the_right_tabs`), `tests/control/test_unified_mosaic_widget.py` (the saved signal), the protocol widget's existing test module under `tests/control/widgets_fluidics/` (the two signals)

**Interfaces:**
- Consumes: `HistoryStore`, `history_dir` (Task 1); `AcquisitionHistoryRecorder` (Task 4); `AcquisitionGalleryWidget` (Tasks 8-10); `resolve_ndviewer_light`, `launch_ndviewer_light` (Task 6); `NDViewerTab.set_dataset_path`.
- Produces, on `UnifiedMosaicWidget`: `signal_view_saved = Signal(str)` carrying the target directory, emitted when `_write_save_snapshot` has finished writing (success or failure) for that directory; emitted from the save executor's thread, so the GUI-side receivers run queued on the GUI thread.
- Produces, on `FluidicsProtocolWidget`: `signal_run_started = Signal(str, str)` (run dir, run name) emitted when a runner is created and started, and `signal_run_finished = Signal(str, str)` (run dir, the `RunFinished.outcome` word: `finished` | `stopped` | `failed`) emitted from `_finish`.
- Produces, on `HighContentScreeningGui`:
  - `self.historyStore = HistoryStore(history_dir())`, `self.historyRecorder = AcquisitionHistoryRecorder(self.historyStore, preview_executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-preview"), software_version=software_version())`, and `self.multipointController.history_recorder = self.historyRecorder` right after the controller exists (in `load_objects`); then `self.historyRecorder.reconcile_orphans()` (logged at info level when it closes anything). In live-only mode the store and recorder still exist (runs from the TCP server get recorded), only the tab is absent.
  - Preview timing: on `signal_acquisition_start` the GUI remembers `self._history_run = (self.multipointController.history_entry_id, save_target)` (the id exists by then: `started` runs before the start callback), resets `self._history_last_timepoint = None` and `self._history_saved_paths = set()`; every `timepoint_finished(time_point)` updates `self._history_last_timepoint`, and every `signal_view_saved(path)` adds `normalize_folder(path)` to `self._history_saved_paths` from acquisition start onward (the final save often lands before `acquisition_finished`, which waits for job completion and hardware restoration). On `acquisition_finished`, when an overview save is expected for this run (`self.unifiedMosaicWidget is not None and self.unifiedMosaicWidget.layers_initialized and (SAVE_DOWNSAMPLED_OVERVIEW or SAVE_DOWNSAMPLED_WELL_IMAGES) and not self.performance_mode and self._history_last_timepoint is not None`), the GUI computes the one save it needs, `expected = normalize_folder(os.path.join(folder, f"{self._history_last_timepoint:0{FILE_ID_PADDING}}", "mosaic_view"))` (the same path `save_for_timepoint` builds): if `expected` is already in `self._history_saved_paths` it calls `historyRecorder.make_preview(entry_id, folder)` at once; otherwise only a later `signal_view_saved(path)` whose `normalize_folder(path) == expected` triggers it, and an earlier timepoint's save, which can finish after the run ends while the last one is still queued, is ignored. A `QTimer.singleShot(self.GALLERY_PREVIEW_WAIT_MS, ...)` (class attribute, 60 000) makes the preview anyway if that save never reports; if the awaited save arrives after the timeout already made one, the GUI calls `make_preview(entry_id, folder, replace=True)` so the final overview replaces the provisional picture. When no overview save is expected, `make_preview` runs right away. Either way the gallery reloads once the preview job's future completes (through a `QTimer`-polled future or the gallery's own mtime watcher).
  - Protocol lifecycle: when `self.fluidicsProtocolWidget` exists, `signal_run_started → historyRecorder.protocol_started(run_dir, run_name, protocol_name)` (protocol name from the widget's resolved protocol; `run_name` as given) and `signal_run_finished → historyRecorder.protocol_finished(run_dir, outcome)`; both in `_setup_fluidics_widgets`.
  - `self.acquisitionGalleryWidget = AcquisitionGalleryWidget(self.historyStore, import_root_provider=self._acquisition_saving_root)`; after the tabs are built: `set_open_in_squid(self.ndviewerTab is not None, "The NDViewer tab is off (Settings > Preferences > Views)", hidden=self.ndviewerTab is None)`, and `_refresh_ndviewer_launcher()` which does `resolution = resolve_ndviewer_light(control._def.NDVIEWER_LIGHT_COMMAND)` and `set_open_in_window(resolution.command is not None, resolution.reason)`, storing the resolution for `_open_acquisition_in_window`; the GUI calls `_refresh_ndviewer_launcher` again whenever the Preferences dialog applies (the existing settings-applied path the dialog already uses to update the GUI) and on each gallery `showEvent` (the widget emits `signal_shown` for it).
  - Connections: `signal_open_in_squid → _open_acquisition_in_ndviewer` (existing); `signal_open_in_window → _open_acquisition_in_window(folder)` (launches with the stored resolution; on `RuntimeError`/`OSError` shows `QMessageBox.warning(self, "Cannot open ndviewer_light", str(e))`); `signal_load_settings → _load_acquisition_settings_from_folder` (existing); `multipointController.signal_acquisition_start → gallery.set_open_in_squid(False, "Wait for the running acquisition to finish")` plus `gallery.reload()` so the running entry appears, and `acquisition_finished → set_open_in_squid(self.ndviewerTab is not None, ...)`; `signal_acquisition_progress` and `signal_region_progress` → `_on_gallery_progress`, which keeps the last values of both and calls `gallery.set_running_progress(fraction)` with the same arithmetic the multipoint widgets' progress bars use for these two signals (read `display_progress_bar`/its slots in `control/widgets.py` and mirror them; clip to [0, 1]); keep the delayed `reload` after `acquisition_finished` (rename `_refresh_gallery_after_acquisition` to call `reload`); `QTimer.singleShot(0, gallery.offer_initial_import)` after `makeConnections`.
  - Shutdown: `historyRecorder.shutdown()` and `acquisitionGalleryWidget.shutdown()` beside the NDViewer close.
  - The old `AcquisitionGalleryWidget(self._acquisition_saving_root)` construction and `_refresh_gallery_after_acquisition`'s `refresh` call are removed/renamed; `_acquisition_saving_root` stays as the import default.

- [ ] **Step 1: Write the failing test**

```python
def test_gallery_is_wired_to_the_history_viewer_and_launcher(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    ... build the simulated GUI ...
    assert win.multipointController.history_recorder is win.historyRecorder and win.historyStore.path.parent == tmp_path / "history"
    assert win.imageDisplayTabs.tabText(win.imageDisplayTabs.indexOf(win.acquisitionGalleryWidget)) == "Gallery"
    # open in Squid routes to a fake NDViewer tab and switches tabs (as the current test does)
    # open in window: monkeypatch control.gui_hcs.launch_ndviewer_light to record; emit signal_open_in_window → called with the folder
    # launcher unavailable: monkeypatch resolve to command None → btn_open_in_window disabled with the reason as tooltip
    # during an acquisition: emit signal_acquisition_start → btn_open_in_squid disabled; acquisition_finished → enabled again
    # progress: emit signal_acquisition_progress(1, 2, 0) then signal_region_progress(2, 4) → the running row's label carries a percentage
    # performance mode: win.performanceModeToggle.setChecked(True); win.togglePerformanceMode() → the Gallery tab is disabled like the other display tabs
    # load settings routing as the current test does

def test_preview_waits_for_the_final_overview_save(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    # historyRecorder.make_preview replaced by a recorder list of (entry_id, folder, replace); win.GALLERY_PREVIEW_WAIT_MS = 500
    # (a) overview expected (mosaic widget layers_initialized True, SAVE_DOWNSAMPLED_OVERVIEW True): emit signal_acquisition_start with
    #     history_entry_id patched to "e1" and save target tmp_path/"run"; emit timepoint_finished(0) then timepoint_finished(1);
    #     emit acquisition_finished → no call yet; emit signal_view_saved(str(tmp_path/"run"/"0"/"mosaic_view")) → still no call
    #     (an earlier timepoint); emit signal_view_saved(str(tmp_path/"run"/"1"/"mosaic_view")) → one call ("e1", folder, False);
    #     wait 700 ms → still one call
    # (b) overview expected but never saved: start, timepoint_finished(0), finished; qtbot.waitUntil one call within 1 s (timeout path)
    # (c) timeout first, save later: as (b); after the timeout call, emit the final save → a second call with replace=True
    # (d) save before completion: start, timepoint_finished(0), signal_view_saved(run/"0"/"mosaic_view"), then acquisition_finished
    #     → one call immediately, no wait, and no second call after 700 ms
    # (e) no overview expected (layers_initialized False): called immediately on acquisition_finished

# tests/control/test_unified_mosaic_widget.py
def test_write_save_snapshot_emits_view_saved(mosaic_widget, qtbot, tmp_path): ...  # one tile, _dispatch_save(tmp_path/"v") → waitSignal(signal_view_saved) args == [str(tmp_path/"v")]

# tests/control/widgets_fluidics/<the protocol widget's test module>
def test_run_lifecycle_signals(...):  # starting a run emits signal_run_started(run_dir, run_name); _finish("stopped") emits signal_run_finished(run_dir, "stopped")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/control/test_HighContentScreeningGui.py -q -k gallery`
Expected: FAIL with `AttributeError: 'HighContentScreeningGui' object has no attribute 'historyRecorder'`

- [ ] **Step 3: Add the two widget signals, then wire the store, recorder, orphan reconciliation, preview timing, protocol lifecycle, gallery, launcher and shutdown in `gui_hcs.py`**

- [ ] **Step 4: Run the GUI suite (minus the known-failing piezo test), the gallery, mosaic and fluidics widget tests**

Run: `pytest tests/control/test_HighContentScreeningGui.py -q -k "not with_or_without_piezo"` then `pytest tests/control/test_widgets_gallery.py tests/control/test_unified_mosaic_widget.py tests/control/widgets_fluidics -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add software/control/gui_hcs.py software/control/widgets_mosaic.py software/control/widgets_fluidics/protocol_widget.py software/tests/control/test_HighContentScreeningGui.py software/tests/control/test_unified_mosaic_widget.py software/tests/control/widgets_fluidics
git commit -m "feat(gallery): wire the history recorder, preview timing, protocol lifecycle, NDViewer and ndviewer_light launcher" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Documentation and whole-branch verification

**Files:**
- Modify: `docs/gallery-tab.md` (rewrite for the history, the two views, the two open actions, import, offline entries, the three preferences, the store location and event format)
- Modify: `docs/ndviewer-tab.md` (one paragraph: the Gallery tab opens saved runs here; MIP projection note belongs in a new short section of `docs/gallery-tab.md`)
- Modify: `README.md` under `software/` only if it lists the display tabs (check; otherwise leave it)

- [ ] **Step 1: Write the docs**

- [ ] **Step 2: Format and run the full relevant suites**

Run: `<python> -m black control squid tests --check` (fix anything it reports in files this branch touched), then
`pytest tests/squid tests/control/test_acquisition_history_recorder.py tests/control/test_gallery_import.py tests/control/test_gallery_previews.py tests/control/test_ndviewer_launcher.py tests/control/test_widgets_gallery.py tests/control/test_unified_mosaic_widget.py tests/control/test_preferences_dialog.py tests/control/test_MultiPointController.py -q`
Expected: all PASS (record the counts in the commit message body).

- [ ] **Step 3: PyQt6 check, when a binding is available**

PyQt6 is not installed on this machine. If `python -c "import PyQt6"` succeeds in a separate venv over the same site-packages, run `QT_API=pyqt6 pytest tests/control/test_widgets_gallery.py tests/control/test_gallery_previews.py tests/control/test_preferences_dialog.py -q`. Otherwise write in the final commit message: "Not run under PyQt6: no binding on this machine." Either way, grep the touched files for `PyQt5`/`PyQt6` imports: there must be none.

- [ ] **Step 4: Commit**

```bash
git add software/docs/gallery-tab.md software/docs/ndviewer-tab.md
git commit -m "docs(gallery): acquisition history, gallery views, opening in Squid or ndviewer_light" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand back**

Report the branch name, the test counts, the PyQt6 note, and the list of (call) decisions from the spec that the implementation settled further (the JSONL field names, the entry id scheme, the composite colouring).
