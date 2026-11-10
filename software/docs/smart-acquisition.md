# Smart Acquisition

Let the content of acquired images decide where the microscope goes next.

A normal Squid experiment is shaped by *geometry* — a well grid, a drawn polygon,
a flexible grid, a CSV someone typed. Smart Acquisition adds a second way: image
a sample at low magnification, analyse those images, and turn what the analysis
finds into a new set of real stage coordinates to image at high magnification.

The worked example is a 384-well plate whose wells each contain a 3x3 array of
ring structures: survey at 4x, detect all nine rings per well, then image each
ring individually at 20x. But nothing in the pipeline is specific to rings —
the detector is one implementation of an analyzer contract that any other
measurement (focus, SNR, confluence, a segmentation model) can implement.

> **On the `4dcell` branch.** This feature and the **Time at XY** tab were ported from the 4DCell
> portable build. The tab, its `acquisition.yaml` and its output folders are the same as in that
> build. What still differs:
>
> - Surveys must be saved as TIFF or OME-TIFF; Zarr surveys are not read.
> - dz on the Time at XY tab starts at 1 µm rather than at the objective's Nyquist step.
> - A z-stack software trigger that produces no frame aborts the run; it is not re-sent once.
> - An `acquisition.yaml` saved by the earlier Record + Z-Stack tab (a `recording:` block and a
>   `z_stack:` block with `z_min_um` / `z_max_um` / `z_step_um`) still loads: the recording becomes an
>   R row, the z-stack a Zf stack, and the z block is chosen to visit the same planes. The log lists
>   anything that could not be kept exactly (a stack that neither starts on nor is centred on the
>   reference plane has no equivalent, because the z block has no bottom offset).

## The pipeline

```
survey acquisition folder
        |
        v
   source.py          read images + the ACTUAL stage position of each FOV
        |
        v
   analyzers/         find things, in PIXEL coordinates
        |
        v
   geometry.py        pixel -> stage mm, then merge duplicates across FOVs
        |
        v
   emit.py            write acquisition.yaml + coordinates.csv
        |
        v
run it: Flexible Multipoint, the Workflow Runner, or the TCP server
```

Analysis runs **per FOV**, and results are merged in stage space afterwards.
That handles one FOV per well, several FOVs tiling one well, and several wells
inside one FOV with the same code — and it keeps stitching error out of the
coordinate path entirely.

## Command line

```bash
python scripts/analyze_to_targets.py \
    --input  /data/survey_4x_2026-09-03_10-00-00.000 \
    --analyzer ring_grid \
    --template configs/highmag_20x.yaml \
    --out    /data/targets \
    --overlay
```

Useful options:

| Option | Meaning |
|---|---|
| `--list-analyzers` | Print every analyzer and its parameters |
| `--param KEY=VALUE` | Set an analyzer parameter; repeatable |
| `--overlay` | Write annotated PNGs showing what was detected |
| `--dry-run` | Analyse and report without writing anything |
| `--carry-z` | Propagate the survey Z instead of leaving focus to autofocus |
| `--sweep-z` | Analyse every plane of each z-stack and keep the best-focus one (implies `--carry-z`) |
| `--no-interpolate-z` | With `--sweep-z`, snap to the winning plane instead of interpolating between planes |
| `--merge-um` | Merge targets closer together than this (default: 0.3 x grid pitch) |
| `--xy-offset-mm DX DY` | Shift every target by the offset between the two objectives (see below) |
| `--z-offset-mm` | Shift the carried z by the parfocal difference (needs `--carry-z`) |
| `--max-targets` | Keep only the N highest-scoring targets |
| `--channel` | Which channel to analyse (default: the acquisition's first) |

**Always look at the overlays the first time you run on a new sample.** Green
marks a measured detection, orange one inferred from the fitted lattice, and each
is labelled with the stage coordinate that will be emitted. It is much cheaper
than discovering a problem on the microscope.

## The template and output folder

Two things are needed to emit a runnable acquisition:

- **Template** -- an existing `acquisition.yaml`. Coordinates come from the
  analysis; *everything else* -- channels, exposure, Z-stack, autofocus -- comes
  from this file. Any previous acquisition's `acquisition.yaml` will do. To make
  one deliberately: set up the acquisition you want in a multipoint tab, run it
  once (a single well is fine), and use the `acquisition.yaml` it writes.
- **Output** -- a folder to write the generated `acquisition.yaml` and
  `coordinates.csv` into. Point a Workflow Runner acquisition step at that
  `acquisition.yaml` to run the targets unattended.

In the GUI both are filled in for you once a survey folder is chosen: the
template defaults to the survey's **own** `acquisition.yaml` and the output to a
`smart_targets` folder beside it. That is the right choice whenever you are
re-imaging the same sample with the same settings -- as when refining focus.
Change the template when the follow-up run needs different optics, which is the
usual case for survey-at-4x then image-at-20x.

Channels, Z-stack and autofocus for the *output* come from the template
`acquisition.yaml`, not from the survey — the survey is at low magnification with
different channels and exposures. The easiest way to make one: set up the
high-magnification acquisition you want in the GUI, run it once (a single well is
fine), and use the `acquisition.yaml` it writes.

Two fields are deliberately stripped from the template on the way out:
`objective.name` and `objective.camera_binning`. Squid's `validate_hardware`
compares those against the objective mounted *now*, and surveying at 4x then
imaging at 20x would otherwise be rejected as a hardware mismatch.

The template decides what kind of acquisition comes out:

- a **wellplate or flexible** template becomes a *Flexible Multipoint* acquisition
  with one position per target;
- a **Time at XY** template (`widget_type: record_zstack`) keeps its channel table
  (one row per channel with Zi / R / Zf, FPS and Dur), its z planes and Record row,
  its focus settings and its Nt/dt time series, and lists the targets for that
  tab's **Load Coordinates** mode. Every ring then gets: focus (laser AF, started
  from the carried survey z + dz), the Zi stack, the recording(s) on the chosen
  planes, the Zf stack, Nt times.

  One exception: a template that ticks **Use Focus Map** has that flag cleared
  when the targets carry a z. The carried z already plays the focus map's role,
  an unattended run has no fitted surface to start from, and the map would
  overwrite the carried z. The Analyze step logs the change as a `note:` line.

## The offset between two objectives

Two objectives on a turret are not parcentric or parfocal to within a field of
view: on the 4DCell unit the 20x sees a feature roughly 80 um in X and 940 um in
Y away from where the 4x reported it, 75 um higher. A target found at 4x is
therefore useless at 20x until it is shifted.

The convention everywhere (CLI, Analyze step, wizard) is

```
position under the follow-up objective = position under the survey objective + offset
```

To measure it: centre one distinctive feature under the survey objective and
note the stage X, Y (and Z); switch objectives, re-centre the same feature and
note again; the offset is the second reading minus the first. Offsets are in
millimetres and refused beyond +/-5 mm in XY and +/-2 mm in Z, which catches
microns typed into a millimetre field. The XY offset is applied before the
stage-limit filter, so a target the shift pushes off the plate is dropped and
reported rather than written. The Z offset only applies to targets that carry a
z (`--carry-z`), which you want here: laser AF at the follow-up objective needs
to start within its capture range, and survey z + parfocal offset is exactly that.
`tools/turret_offset_fit.py` on the 4DCell build measures the same quantity
automatically when tuning the turret.

## In the GUI

Enable it in the machine `.ini`:

```ini
[GENERAL]
enable_smart_acquisition = true
```

A **Smart Acquisition** tab appears next to the multipoint tabs. Pick the survey
folder (or press *Use Last*), choose an analyzer, press **Analyze**, and review
the target table. Then either **Save** the acquisition, or **Send to ...**: the
button names the tab the template belongs to (**Send to Time at XY** for a
Time at XY template, **Send to Flexible Multipoint** otherwise) and loads the
targets straight into it.

Only the parameters that describe the *sample* are shown up front (for
`ring_grid`: ring diameter, grid pitch, rows and columns). Detector tuning lives
under **Advanced**, collapsed by default. The control column is short, so the
panel scrolls rather than compressing its contents; everything below the fold is
reachable with the scrollbar.

The tab only generates coordinates; it never acquires. If a workflow starts an
acquisition while this tab is focused, Squid switches to a real acquisition tab
rather than failing.

## Closing the loop with the Workflow Runner

`Utils > Workflow Runner > New Smart Ring Workflow...` builds the whole unattended
run from six inputs: the 4x survey `acquisition.yaml` (which also names the wells
to survey), the survey objective, the follow-up `acquisition.yaml` (a Time at XY
run with the channel rows, Zi/R/Zf, FPS/Dur, z planes, Laser AF and Nt/dt you
want at each ring), the follow-up objective, the objective offset, and
(optionally) the recording folder the follow-up saves into. It produces this
workflow, which you can Save, edit step by step, and Run:

| # | Type | Setting |
|---|---|---|
| 1 | Objective | `4x`, laser-AF reference required |
| 2 | Acquisition | the 4x survey `acquisition.yaml` (its selected wells, e.g. `C5, C6, D5`) |
| 3 | Analyze | `ring_grid` with the ring diameter / pitch / rows / cols, the 20x template, the offset, carry z |
| 4 | Objective | `20x`, laser-AF reference required |
| 5 | Acquisition | `{last_targets}` (the file step 3 wrote), saving to the recording folder |

The same file as YAML:

```yaml
num_cycles: 1
stop_on_failure: true
sequences:
  - {name: Objective 4x, type: objective, objective: 4x}
  - {name: Survey 4x, type: acquisition, config_path: C:\data\templates\survey_4x\acquisition.yaml}
  - name: Find targets
    type: analyze
    analyzer: ring_grid
    analyzer_params: {ring_diameter_um: 320.0, grid_pitch_um: 1000.0, grid_rows: 3, grid_cols: 3}
    template_path: C:\data\templates\record_20x\acquisition.yaml
    xy_offset_mm: [0.08, -0.94]
    z_offset_mm: 0.075
    carry_z: true
  - {name: Objective 20x, type: objective, objective: 20x}
  - {name: Image targets 20x, type: acquisition, config_path: "{last_targets}", save_dir: D:\recordings}
```

What the operator has to have in place:

- a laser-AF reference taught for **each** objective (Laser-Based Focus tab; they
  are stored per objective and reloaded when the objective changes);
- the 4x survey file: on the Wellplate tab, set the 4x channels, scan size and
  overlap, select the wells to survey on the plate map (XY ticked, Select Wells),
  and click **Save Settings...**. Nothing is acquired. The wizard shows the wells
  the file selects under the survey row and refuses a file that selects none.
  The file records the Camera tab's binning, and the survey step refuses to
  run at any other binning. When the camera is at a different binning now, the
  wizard says so before it creates the workflow. The file also records the image
  size after the Camera tab's crop (`objective.image_size_px`). The survey runs
  at the current crop, with its tiles recomputed from that field of view, so
  the wizard warns when the crop has changed since the file was saved. It also
  warns when the field of view the survey will run at is smaller than one ring
  lattice, because `ring_grid` needs the whole lattice in each image. The 20x
  rows' own binning and crop (the **Cam** column) are free to differ from the
  survey's.
  When the file is loaded, its wells are checked against the plate in use, and a
  well that is not on that plate fails the step;
- the follow-up template: set up the Time at XY tab (channel rows, z planes,
  recording, Nt/dt) and click **Save Settings...**, which writes its
  `acquisition.yaml` without acquiring. The Flexible Multipoint tab has the same
  button, and a file written by any run works too;
- the objective offset (previous section). The wizard remembers it per
  objective pair (4x -> 20x) in `cache/smart_ring_wizard.yaml` when you create a
  workflow, and fills it in again next time; a pair with nothing saved starts
  at 0.

To survey other wells, select them on the plate map and save the survey file
again. A hand-written `wells: "C5, C6"` key on an acquisition step still
replaces the file's wells, for a workflow that reuses one survey file across
well sets.

The **recording folder** sets where the Image targets step saves: the step's
`save_dir` replaces the Time at XY tab's saving folder for that run (the folder
is created if needed), and each run still gets its own timestamped experiment
folder inside it. Left empty, the step saves wherever the tab points, as
before. The wizard remembers the last folder in `cache/smart_ring_wizard.yaml`.
Any acquisition step can carry a `save_dir`; the step dialog has a Save folder
field for it. The survey keeps saving to the Wellplate tab's folder.

Each step is a gate: a survey that fails to start, an analysis that finds no
rings, a missing laser-AF reference or a turret that will not move stops the run
with the reason in the workflow log. Nothing pops up a dialog while a workflow
runs. Look at `<survey>/smart_targets/overlays/` after the first run on a new
sample before trusting it unattended.

### Making the Time at XY template

The follow-up template is an ordinary Time at XY run, done once by hand on one
ring; the run's own `acquisition.yaml` is the template. Set the tab up like this:

- **XY** = Current Position (the positions are replaced anyway);
- one **channel row** per channel, with **Zi** (initial z-stack), **R**
  (time-lapse recording, with its FPS and Dur) and/or **Zf** (final z-stack)
  ticked as wanted at every ring;
- **Z planes**: Relative or From Bottom with the dz and Nz (or range) you want;
  the **Record** row picks which of those planes the recordings visit;
- **Focus** row: **Laser AF** ticked, **Use Focus Map** unticked (the recipe
  clears it anyway when z is carried); avoid **Set Range**, which fixes absolute
  planes and switches autofocus off for every ring;
- **Time**: Nt and dt as wanted at each ring.

The wizard warns when a template ticks Use Focus Map, uses Set Range planes or
has Laser AF unticked. Each ring is then written under the recording folder as

```
<saving path>/Image_targets_20x_<timestamp>/
    zstack_initial/zarr/<ring>/fov_0.ome.zarr                 (Zi)
    recording/t<N>/<ring>/<channel>/fov_0_z<i>.ome.zarr       (R, one store per plane)
    zstack_final/zarr/<ring>/fov_0.ome.zarr                   (Zf)
```

#### Per-channel camera settings

The **Cam** column in the channel table gives a row its own binning, a
centred crop (W × H, in pixels at that row's binning) and a pixel format such
as MONO8. A field left at **Current** uses whatever the Camera tab is set to
when the run starts, so a row with nothing set (shown as `—`) behaves as before.
The worker sets each row's values while the camera is stopped. At the end of
the run it restores the Camera tab's binning, crop and format, whether the run
finished, was aborted or failed. The next survey therefore runs at the user's
own settings. **Test FR** measures at the row's settings too.

Channels with different settings produce images of a different size, bit
depth and pixel size, so they are never combined:

- **Recordings** are already one store per channel, and each is sized at its
  own row's settings.
- **Z-stacks** are split by camera setting. The Zi (or Zf) channels that share
  the same settings form a group. Each group is acquired in turn (z outer,
  channel inner within the group) and written to its own store, one level
  deeper:

  ```
  zstack_initial/<group>/zarr/<ring>/fov_0.ome.zarr
  ```

  `<group>` names the settings, for example `bin1_roi_mono12` (the Camera
  tab's ROI) or `bin2_1024x1024_mono8`. When every stack channel shares one
  setting, there is no group level and the layout is exactly as shown above.
  The confirm dialog lists the groups before the run starts.

In `acquisition.yaml`, a row that sets anything carries a `camera:` block
(`binning`, `crop_width`, `crop_height`, `pixel_format`, and `pixel_size_um`
when it sets binning). The emit step copies it into the follow-up untouched.
It ignores only the template's global `objective.camera_binning`. The viewer
(CONTRACT v1.1) does not read the group level yet.

The older script route still works, and is the way to use an analyzer that
lives in another conda environment: a Script sequence running
`scripts/analyze_to_targets.py --analyzer ring_grid --template <template> --out
C:\data\targets --xy-offset-mm 0.08 -0.94` picks the survey up from
`SQUID_LAST_ACQUISITION_PATH` (passed through the environment because
arguments go through `shlex.split`, which mangles Windows paths), and the next
Acquisition sequence points at `C:\data\targets\acquisition.yaml`.

## Writing a new analyzer

An analyzer answers one question about one field of view, in pixel coordinates.
It never converts to stage coordinates — `geometry.py` does that in one place, so
analyzers need no coordinate fixtures and are easy to test.

```python
from control.core.smart_acquisition.analyzers.base import BaseAnalyzer, register
from control.core.smart_acquisition.targets import PixelDetection

class MyAnalyzer(BaseAnalyzer):
    name = "my_analyzer"
    defaults = {"threshold": 0.5}          # also the accepted-parameter whitelist

    # Optional: the parameters the GUI shows up front; the rest go under
    # "Advanced". Omit it and every parameter is shown.
    primary = ("threshold",)

    def analyze_fov(self, image, fov):
        # fov.pixel_size_um lets you express thresholds in microns, which is
        # what makes an analyzer work at any magnification.
        return [PixelDetection(px=100.0, py=200.0, score=1.0, label="thing0")]

register(MyAnalyzer)
```

Import it from `analyzers/__init__.py` and it shows up in both the CLI and the
GUI dropdown automatically.

Two are shipped:

- **`ring_grid`** — finds a `grid_rows` x `grid_cols` lattice of rings. It
  flat-fields the image, pools `cv2.HoughCircles` over several thresholds, then
  fits `p(i,j) = o + i*u + j*v`. The lattice fit both rejects debris and
  *recovers* structures that are missing or unreadable, so a well always yields a
  complete grid; recovered nodes are marked `measured=False`.
- **`focus_score`** — reports focus measure and SNR per FOV. It exists to keep
  the contract honest: a different kind of question through the same interface.
  Paired with `--sweep-z` it becomes a best-focus finder.

## Finding best focus in a z-stack

By default one plane per FOV is analysed -- the middle of the stack -- so a
focus analyzer can report a number but cannot *find* focus. Pass `--sweep-z`
(or tick **Find best Z in stack** in the GUI) and the analyzer runs on every
plane instead; only the best-scoring plane survives, and its Z is carried into
the emitted coordinates.

```bash
python scripts/analyze_to_targets.py --input /data/zstack_survey     --analyzer focus_score --sweep-z     --template configs/highmag_20x.yaml --out /data/targets --verbose
```

```
  36 FOV(s) -> 4 target(s) (4 measured)
    smart-A1-fov0   x=  12.0517  y=   9.0517  z=3.0030  best_z_level=1
    smart-A2-fov0   x=  16.5517  y=   9.0517  z=3.0090  best_z_level=3
    smart-A3-fov0   x=  21.0517  y=   9.0517  z=3.0150  best_z_level=5
    smart-A4-fov0   x=  25.5517  y=   9.0517  z=3.0210  best_z_level=7
```

That output is a focus map: a coordinate list whose Z follows the sample. On a
tilted or uneven plate it is a way to measure the surface once and then image
from it.

A plane wins on the analyzer's own score, with overall image sharpness breaking
ties. The tie-break is not cosmetic: `ring_grid` scores 1.0 per readable ring
and saturates at 9 on every plane where the lattice fits, so without it a
defocused plane would win just by coming first.

### Sub-plane interpolation

Picking a plane quantises the answer to the z-step -- with a 3 um step you can
be 1.5 um out no matter how good the focus metric is. So the Z is refined by
fitting a parabola through the winning plane and its two neighbours and taking
the vertex, which is on by default (`--no-interpolate-z` to disable). Measured
against a synthetic stack on a 2 um step, mean error drops from 0.56 um to
0.32 um and worst case from 1.00 um to 0.67 um.

The curve interpolated is whichever quantity decided the winner: the analyzer's
score when it varies across the peak, otherwise the sharpness that broke the tie
(`ring_grid`'s own score carries no focus information once the lattice fits).

Interpolation is skipped, and the plane's own Z used, when it would not be
trustworthy: the peak sits at either end of the stack (no bracket, and true
focus may lie outside the stack entirely), the curve is flat, the three points
are not concave, or the vertex escapes the bracketing planes. The reason is
recorded in `z_interpolation_note`.

Each surviving target records `best_z_level`, `planes_examined`,
`plane_sharpness`, `z_interpolated` and `z_plane_mm` in its metrics; the GUI
marks interpolated rows with `*`.

`--sweep-z` reads every plane of every stack, so it costs roughly NZ times a
flat analysis.

### Photobleaching, and why the default metric is Tenengrad

Validated against a real 33-plane 405 nm stack on the microscope. Mean intensity
fell **33% across the stack** as the fluorophore bleached, and because LAPE (the
`calculate_focus_measure` default) goes as intensity *squared*, it simply
tracked the decay and named plane 0 best focus in **all seven** usable stacks.

`focus_score` therefore defaults to `TENENGRAD` divided by mean intensity
(`normalize_intensity`, on by default), which found the true peak. The same
normalisation is used for the sharpness tie-break. If you change
`focus_method`, keep normalisation on.

On that dataset, six FOVs spanning a 4.7 mm grid gave best-focus Z values that
fit a plane to **0.41 um RMS** -- about a quarter of the 1.594 um z-step -- and
the two rows independently measured the plate tilt as -0.491 and -0.527 um/mm.
Snapping to planes instead of interpolating raised the residual to 0.68 um and
collapsed six distinct Z values into three.

Blank planes are skipped rather than scored. An OME-TIFF is pre-allocated
zero-filled, so an aborted or still-running acquisition leaves whole planes as
zeros -- and a blank plane would otherwise be free to win a focus sweep.

## Things to know

- **Positions come from `<timepoint>/coordinates.csv`**, never from image
  metadata. In OME-TIFF mode the plane positions are staged in a temp-directory
  sidecar and only written into the file once the stack is complete.
- **Analysis refuses to run on an unfinished acquisition** (no `.done` sentinel).
  In OME-TIFF mode the stack file is pre-allocated zero-filled, so reading it
  mid-run would silently return blank frames. `--allow-incomplete` overrides this
  if you know what you are doing.
- **Zarr surveys are not supported yet** and raise rather than half-working.
- **Z defaults to being omitted**, leaving focus to laser AF. The 4x focus is not
  the 20x focus. Use `--carry-z` only if you mean it.
- **Z is all-or-nothing per region.** Squid drops Z for a whole region if any one
  FOV lacks it, so `emit.py` makes that decision explicitly and tells you.
- **Targets outside the software stage limits are dropped and reported**, not
  fatal — a fitted lattice can legitimately extrapolate a node off the plate, and
  that must not discard the whole region.
- **The pixel-to-stage mapping assumes an untransformed camera image.** If
  `CAMERA_CONFIG.FLIP_IMAGE` or `ROTATE_IMAGE_ANGLE` is set, analysis refuses to
  run rather than silently emitting mirrored coordinates.

## See also

- [Workflow Runner](workflow-runner.md) — sequencing acquisitions and scripts
- [Automation](automation.md) — running acquisitions from scripts
- [Configuration System](configuration-system.md)
