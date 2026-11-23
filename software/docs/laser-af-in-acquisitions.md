# Laser AF in acquisitions

## When laser AF finds no spot at a FOV

By default an acquisition with laser AF (Reflection AF) images a FOV where it is when laser AF fails there (no spot,
a displacement out of range, or a failed alignment check), logs the failure and counts it in the Slack time point
summary. Two settings in the machine's `.ini`, under `[GENERAL]`, change this:

```ini
[GENERAL]
laser_af_spot_search = true
laser_af_spot_search_offsets_um = [5, -5, 10, -10, 15, -15, 20, -20]
laser_af_stop_on_spot_loss = true
```

| Setting | Default | What it does |
|---|---|---|
| `laser_af_spot_search` | `false` | After a failed laser AF, move the piezo to each offset in turn (µm from where the AF left it) and measure. At the first offset where a spot is seen within Laser AF Range, run laser AF again from there. If no offset has a spot, or that AF fails too, the piezo goes back to where the search started. Offsets past the piezo's travel are skipped. Needs a piezo; without one there is no search. |
| `laser_af_spot_search_offsets_um` | `[5, -5, 10, -10, 15, -15, 20, -20]` | The offsets, in the order they are tried. |
| `laser_af_stop_on_spot_loss` | `false` | If laser AF still failed (after the search, when it is on), end the acquisition instead of imaging the FOV out of focus. The FOV is not imaged. |

An acquisition stopped this way ends as an `error`: the log and the watchdog's `run.json` (`stats.detail`) name the
region, FOV and time point, the watchdog alerts, and a fluidics protocol run holds at that imaging step.

Both apply to the laser AF at each FOV. The laser AF before each later channel's stack (Z First, "AF per stack")
keeps its own behaviour: on a failure the previous anchor stands.

## The laser AF record of an acquisition

An acquisition with laser AF writes `laser_af.csv` in its experiment folder: one row per laser AF call, written as it
happens, so a run that ends early keeps its rows. The per-FOV laser AF has an empty `channel`; a Z First "AF per stack"
row names the channel.

| Column | |
|---|---|
| `time` | Unix time (s) the call started |
| `time_point`, `region`, `fov`, `channel` | Where |
| `x_mm`, `y_mm` | Stage position |
| `z_before_um`, `z_after_um` | Focus height before and after: stage z, plus the piezo when laser AF moves the piezo (µm) |
| `displacement_um` | The call's first measurement (`nan`: no spot) |
| `correlation` | The alignment check of the last move (`nan`: not reached) |
| `ok` | Laser AF succeeded |
| `target_um` | The region's laser AF offset |
| `af_time_s` | How long the call took, the spot search included |
| `searched` | The spot search ran |

At the end it writes `laser_af_summary.json` next to it, and the same in the log, one entry per time point of the
per-FOV rows: `n`, `ok`, `searched`, `abs_displacement_um_p95` / `_max`, and `focus_surface`, a quadratic fit
z(x, y) of the reference plane (`z_after_um - target_um` of the FOVs where laser AF succeeded):

| `focus_surface` | |
|---|---|
| `centre_z_um` | The fit at the centre of the FOVs' bounding box (`centre_x_mm`, `centre_y_mm`) |
| `dome_um` | Peak-to-peak of the fit over the FOVs |
| `residual_rms_um` | RMS of the FOVs about the fit |
| `model`, `n_points` | `quadratic`; `plane` or `constant` when the FOVs cannot hold a quadratic (fewer than 6, or in a line) |

Comparing `centre_z_um` and `dome_um` between the rounds of a fluidics protocol shows how the coverslip moved.

```ini
[GENERAL]
laser_af_save_record = true    # laser_af.csv and laser_af_summary.json (default on)
laser_af_save_frames = false   # also every call's focus-camera frame (default off)
```

With `laser_af_save_frames` each call also saves the focus-camera frame it ended on (the alignment check's frame
after a successful move, the frame where the spot was lost otherwise) to `laser_af_frames/<time point>/`, as an 8-bit
BMP: 0.4 MB for a 1536 × 256 crop, about 350 MB for 900 FOVs. Saving one takes about a millisecond, on the acquisition
thread, as the focus-camera image of a failed laser AF already is.
