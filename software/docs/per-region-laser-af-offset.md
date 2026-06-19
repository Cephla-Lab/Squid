# Per-region laser-AF offset

Focus each well/region at its **own height relative to the laser-AF reference plane**,
while laser autofocus keeps that height stable against drift over a long acquisition.

Normally, when laser reflection autofocus (laser AF) is on, every field of view is driven
back to the **single** laser-AF reference plane. This feature instead lets each region
keep the focus offset you recorded for it — useful when different wells need slightly
different focus relative to the reference (e.g. sample-height differences across a plate).

## When it applies

The feature is active only when **all** of these are true:

- Laser AF (**Reflection AF**) is enabled for the acquisition.
- A **focus map** is used, set to **`constant`** method with **`Fit by Region`** checked
  (i.e. one focus point per well/region — the "constant-z" case).
- The **`Laser AF Offset`** checkbox (in the Focus Map tab) is checked.

If any of these is off, behavior is unchanged: laser AF drives every FOV to the single
reference plane.

## How to use it

1. **Set the laser-AF reference** once (the usual "Set Reference" step), with the sample at
   a representative focus. All per-region offsets are measured relative to this plane.
2. Open the **Focus Map** tab. Set **Fitting Method** to `constant` and check **Fit by
   Region**. The **`Laser AF Offset`** checkbox now becomes available — check it.
3. For **each well/region**, add one focus point:
   - Navigate to the region and bring it into the focus you want.
   - Click **Add** (or select the region's point and click **Update Z**).
   - The current laser-AF displacement is recorded as that region's offset and shown on the
     status line (e.g. `Region A1: Laser AF offset +2.30 µm`). The live view briefly pauses
     while the reading is taken, then resumes. Problems are reported on the status line too:
     no reference set, spot not detected, or an offset larger than the laser-AF range (that
     region may fail AF during the run).
4. In the multipoint panel, enable **Reflection AF** and **Use Focus Map**.
5. Start the acquisition. At each FOV, laser AF drives the stage to that region's recorded
   offset instead of to the shared reference plane.

## Good to know

- **Re-setting the laser-AF reference clears all captured offsets** — they were measured
  against the old reference and are no longer valid. Re-record them after changing the
  reference.
- **Changing focus points keeps offsets in sync**: removing a region's point drops its
  offset; regenerating the focus grid clears them.
- **Save / reuse**: Export the focus points to CSV — the file includes an `Offset_um`
  column. Importing restores both the points and their offsets. Older CSV files without
  that column still import (with no offsets).
- **Per-channel Z-offsets still apply** on top of the per-region offset, exactly as before.
- The offset value is the laser-AF displacement (µm) from the reference plane at the focus
  point, so focus tracks the reference as the sample drifts — even across time-lapse
  timepoints.

## Troubleshooting

- **The `Laser AF Offset` checkbox is greyed out.** It only enables for a
  `constant` + `Fit by Region` focus map. Pick those in the Focus Map tab.
- **Offsets don't seem to apply during the run.** Confirm **Reflection AF** is enabled in
  the multipoint panel you started the run from, and that the checkbox is still checked.
- **A region keeps failing autofocus.** Its offset may exceed the laser-AF range; re-record
  it closer to the reference plane, or move the reference.
