# Live Laser AF Spot Diagnostics

The live spot overlay shows *that* a frame failed. This shows **why**, in terms of the settings
that decide it, and what value would let the spot back.

It appears on the **Laser-Based Focus** tab, as a live measured value beside each CC setting, and
needs nothing turned on beyond **Live** and **Live spot detection**. It writes no configuration and
moves nothing.

## Reading it

Each CC control carries the number it is being compared against, on the current frame:

```
  CC Threshold:                [    8]     176
  CC Min Area (pixels):        [  400]      61     <- red
  CC Max Area (pixels):        [ 5000]      61
  CC Row Tolerance (pixels):   [   20]      88     <- red
  CC Max Aspect Ratio:         [  2.5]     1.0
  Filter Sigma:                [  1.0]
  Spot Detection Mode:  [Exactly one spot]   0

  [ Apply Settings ]
```

While detection is working, every value reads plainly and the numbers are **headroom** — watch them
as you turn focus and you can see which setting will drop the detection next, before it does.

When detection drops out, the values that are out of range turn **red**, and the blob that was
rejected is drawn on the focus camera image as a red **x** with a short label. **Hover a red value**
for what to do about it:

> Blob measures 88 against CC Row Tolerance 20, so it is rejected. Set CC Row Tolerance to 88 to
> admit it. That would also admit 4 other blobs -- you would be loosening the detector, not
> finding the spot. Confirm with a Focus Sweep that the spot tracks z.

or, where no value of that setting could work:

> This is a streak, not a spot -- it is longer than the detector can be set to accept. Check for a
> specular reflection off the coverslip edge.

Every failing control carries its own suggestion, not just the first one — the detector stops at
the first filter a blob fails, so fixing them one at a time means applying, being told about the
next, and going round again. Nothing here changes a setting: you type the value, and **Apply
Settings** commits it. That split is deliberate; Apply writes the objective's YAML immediately.

## "Row offset" is a measurement, not a setting

There is no row-offset control and there should not be. The row offset is **how far the blob sits
from the vertical centre of the crop** — `expected_row` is `crop_height / 2` (`utils.py`). Two
things move it, neither of them a detection setting: the spot moving, and the crop moving
(**Crop Y Offset**). What you *can* set is how much of it is tolerated, and that is **CC Row
Tolerance (pixels)**.

That is why the measured values live beside their controls rather than in a table of their own: a
table has to invent a name for each row, and the name it invents is not the name of the control you
have to go and change.

**The tolerance ceiling follows the crop.** Because the deviation is measured from the middle of the
crop, it can never exceed half the crop height -- so a fixed ceiling means something different on
every crop. At the stock 256-tall crop, 200 px is already past anything measurable; on a full sensor
a blob can sit 1000 px off centre and a 200 px ceiling would refuse to admit a spot plainly visible
in the frame. The spinbox maximum is therefore recomputed from the crop on every resync
(`_update_row_tolerance_range`, called from `update_values`, which is the single resync point after
Apply Crop, Reset and Center on Last Detection).

Two floors keep that from ever losing a setting: the ceiling never drops below the static 200, and
never below what the objective already has saved -- otherwise narrowing the crop would clamp a
stored value on the way into the spinbox and the next Apply would write the clamped number back.

The same ceiling is what `_round_to_admit` checks before offering a suggestion, so the two cannot
disagree; a blob that is in the crop at all can now always be admitted by CC Row Tolerance. Whether
it *should* be is another matter -- at half the crop height the filter accepts anything and stops
discriminating, and moving the crop is usually the better fix. The tooltip says so.

`peak intensity` is likewise measured **after** the Gaussian filter, because that is what the
threshold is compared against. At `filter_sigma = 1` it reads lower than the raw pixel peak.

## When the answer is not a setting at all

Some frames get a sentence on the **status line under the Live spot detection checkbox**, and the
measured values blank out — because on those frames no number beside a control would be describing
anything:

- **"the spot is not in this crop"** — nothing spot-like is in frame even below the threshold. Use
  **Reset** and then widen the crop to find where it went, or check z. This is the failure that
  most misleads: before this tool it looked identical to a slightly tight threshold.
- **"Frame is uniform"** — no signal at all. Check the AF laser is on, then exposure and gain.
- **"the frame is noise"** — thousands of components above the threshold. Raise CC Threshold, or
  cut exposure and gain.
- **A merged blob** — when a blob fails CC Max Area because the spot has fused with a halo, the
  tooltip reports the threshold at which it separates. Raising the ceiling instead would only admit
  the halo.

## The hazard

**Relaxing settings until something is detected is how you lock onto a back-reflection.** The
intensity, area, aspect and row filters all pass a static blob sitting on the right row — see
`_confirm_spot_moves_with_z`.

So the suggestion says what it would cost before you take it: *"would also admit 4 other blobs"*
means that value admits four more things in this frame. No such sentence means the change is
surgical; a count in the dozens means you have loosened the detector rather than found the spot.

That is not proof either way. The only real test of whether a reflection is the sample is whether it
*moves with z*, which is what **Focus Sweep** shows — the sample reflection traces a sloped line, a
back-reflection a flat one. The tool tells you what to change; it does not claim the change is right.

## Notes for developers

- The detector records rejections rather than a separate pass re-deriving them:
  `utils._collect_valid_spots(collect_rejects=True)` returns what every component measured and how
  many filters turned it away. One function defines what a rejection is, so the live diagnosis
  cannot drift out of agreement with the detector it is explaining.
- `utils.analyze_frame` is the single entry point for a live caller. Detection and diagnosis share
  one Gaussian-filtered frame and one connected-components pass; building either twice roughly
  doubles the cost of the full-sensor case, which is the case someone is diagnosing in.
- The filters are evaluated as whole-array numpy predicates. `labels == i` is a full-frame boolean
  comparison and runs **only for survivors** — running it across the tens of thousands of
  components a noisy full-sensor frame produces would take minutes on the GUI thread. Rejects are
  measured from `stats`/`centroids` and, where pixels are needed at all, from the blob's own
  bounding box.
- Two tiers, and only one needs to look below the configured threshold. If the frame peak clears
  `cc_threshold`, components exist and every number is already exact. If it does not, the threshold
  is the sole cause and the blob is located against a noise-derived floor
  (`median + k·MAD`, clamped below `cc_threshold`) — never a peak-derived one, which a single
  saturated pixel would push above the real spot.
- Suggestions round **in the admitting direction** onto the spinbox's decimal grid, and are refused
  outright rather than clamped when no reachable value would work. `cc_threshold` is a strict
  comparison, so its suggestion must be strictly below the peak. The round-trip test in
  `test_laser_af_spot_diagnostics.py` is what keeps all of this honest: feed a suggestion back into
  the detector and a candidate must appear.
- Every reject carries a verdict for **all five** criteria, never short-circuited. The detector
  stops at the first failing filter; a diagnosis that did the same would send the operator round
  the loop once per filter.
- `SpotCriterion.name` is the config field name, so the readouts are a direct lookup into
  `self.measured_labels` with no intermediate structure. Adding a criterion means adding a
  `measured=True` spinbox and an entry in `_MEASURED_FORMATS`.
- The readouts are blanked in `update_values` and `update_threshold_settings` — a crop change moves
  the pixels they describe, and an Apply moves the limits they were measured against.

### Panel width

Every CC row is laid out label / stretch / spinbox / value column, and the value column is reserved
on **every** such row even where nothing is shown in it, so the controls share one right edge. The
spinbox width is capped (`_SPINBOX_WIDTH_PX`) and the value column fixed (`_MEASURED_WIDTH_PX`) so
a value going from `9` to `88` does not shift the column under the eye of someone watching it move.

The camera row, the four crop fields and the two sweep fields use `_add_column_spinbox` instead —
title above, spinbox below, several across one line. Those rows carry no measured value, so they
give up the shared right edge in exchange for the vertical space, which is the scarce dimension
here. Their cap is `_COMPACT_SPINBOX_WIDTH_PX`, which is *not* smaller than `_SPINBOX_WIDTH_PX`
despite the name: the arrows take a fixed bite out of a spinbox, so below about 90 px the value
itself starts being elided, and four columns showing `1|` instead of `1536` is worse than a wider
panel. It exists as its own constant so the two can be tuned independently.

This matters more than it sounds. The first version of this feature used a monospace table with
`setWordWrap(False)`, which gave it a `minimumSizeHint` of 367 px that **could not shrink** — every
other wide element in the panel word-wraps and yields when the dock narrows, so that one block set
the floor for the whole tab and forced horizontal scrolling.

Measured `minimumSizeHint().width()`, same machine and font:

| | width |
| --- | --- |
| before the feature | 398 |
| with the margin table | 405 (hard floor, unwrappable) |
| value column, before shortening the spot-mode label | 417 |
| after the feature | 398 |
| **after the panel was compacted around the sweep** | **373** |

Those numbers are from one machine and one font; what matters is the direction. Re-measured on a
higher-DPI machine, compacting the panel took it from **740 x 1114** to **416 x 914** — the width
came off the one-control-per-row labels, the height off those plus the controls that went with the
two-point calibration.

The floor is the three crop buttons sharing a row, which predates all of this. If the panel ever
needs to be narrower than that, that row is the next lever — not the readouts.

The panel does not grow when a failure appears: the advice lives in tooltips rather than in a
paragraph that would add several lines exactly when the panel was already busy. The Relax button
that used to sit under these controls is gone — what it knew is now on the suggestion itself.

### Formatting caveat

`black` was not available on the build machine, so these changes were written to the repo's style
by hand rather than formatted. No added line exceeds 120 characters. Per
`software/docs/laser-af-map.md`, `widgets.py` and `gui_hcs.py` already contain pre-existing
unformatted code — check before attributing any complaint to this work.
