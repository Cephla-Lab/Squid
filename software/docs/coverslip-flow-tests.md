# Coverslip and flow tests for a flow-cell rig

These command-line tools measure how a flow cell's coverslip moves when the syringe pump draws liquid through it, and
whether each reagent line lets the flow cell relax afterwards. They use the laser autofocus (AF) spot as a height
gauge: the focus is not moved (except by the piezo check), the AF camera frames are saved, and the bright reflection is
re-detected in every frame afterwards. Everything runs from a terminal while the Squid GUI stays open and keeps the
hardware.

The most useful test is the **port hold check** (test 4). It finds lines that leave the flow cell under suction after
a draw. If the line used right before imaging does this, every imaging round starts defocused, with the coverslip under
load.

## Contents

The tools are in `software/tools/coverslip_flow/`.

| Script | What it does |
|---|---|
| `piezo_check.py` | Moves the objective piezo ±10 µm and checks the AF spot follows |
| `flow_displacement_experiment.py` | One pull (optionally after a pre-fill), recording coverslip and flow at ~30 Hz |
| `port_hold_check.py` | A small pull on each port, then a valve move to a free-flowing port; reports held suction (`--sensor-only`: from the flow sensor alone, no laser AF) |
| `valve_sequence.py` | Valve moves only, no pumping, recording the coverslip |
| `af_map_flow.py` | Maps coverslip height over a scan grid before and after a pull |
| `af_map_analyze.py` | Maps, differences and a bowl fit from `af_map_flow.py` output |

Run them from the `software/` directory with the Python that runs Squid (they need numpy, tifffile and matplotlib).
Each prints its own `--help`. Ctrl-C stops any of them cleanly: the pump is halted and what was recorded is saved.

### How they reach the GUI

The recordings run **inside the GUI process**, sent as code to the control server's `python_exec` command
(`127.0.0.1:5050`, `--server-port` to change it). That is what lets them sample the AF camera at the camera's rate and
time the pump exactly, but it means:

- **Settings → Enable MCP Control Server** and **Settings → Enable MCP Python Exec** must both be on. `python_exec` runs
  arbitrary code in the GUI with the microscope's objects; turn it off again when you are done.
- They use GUI internals (`gui.fluidics`, `gui.laserAutofocusController` and its `_get_laser_spot_centroid()`), so a
  Squid change to those can break them. They set `gui._flow_displacement_abort` / `gui._af_map_abort` to stop a
  recording.
- The `python_exec` sandbox has no `open`/`eval`/`exec` and allows imports of numpy, math, time, datetime, json and
  os.path only; functions defined in the sent code can't see its local names. The scripts already work within this.

## Safety

- **Pulls only. Never push liquid back through the flow cell.** A push bows the coverslip outward about as far as a
  pull draws it in, and leaves it past where it started, loading the seal outward. The tools have no push option.
- **Every pull draws liquid through the sample.** Decide first which ports hold what, which may be drawn from and how
  much. Each test below lists the liquid it uses. Valve-only moves and the piezo check use no liquid but still move
  hardware.
- **Keep the coverslip in the AF camera's view.** A 500 µL/min pull can move it 70–90 µm, 1000 µL/min about 150 µm;
  faster rates may lose the spot. Set the laser AF range to at least 400 µm (below).
- `af_map_flow.py` moves the stage in X and Y over the scan grid at the current focus.
- Don't change laser AF settings during the tests, and don't run them while a fluidics protocol or an acquisition runs.

## Before you start

1. **A flow sensor in the fluidics config.** The fluidics config the GUI loads needs a `flow_sensors` block, e.g.

   ```yaml
   flow_sensors:
   - index: 1
     name: flow
     monitor: 'off'
   ```

   Restart the GUI after adding it. `flow_displacement_experiment.py --allow-no-flow-sensor` runs without one but then
   reports no flow volumes.
2. **Control server and python exec enabled** (above).
3. **Fluidics initialized** in the GUI (Fluidics tab → Initialize), and nothing running.
4. **Laser AF initialized and a reference set**, in focus on the coverslip, with the objective you will use. Leave the
   focus-camera live view off. In the Laser AF settings:
   - *Laser AF range* ≥ 400 µm. With 100 µm the GUI's own reading fails during a pull (the tools track the spot
     themselves, but the range also gates the GUI's laser AF during scans).
   - Leave the *spot detection mode* as it is. The tools re-detect the right-hand (brighter) of the two reflections in
     post-processing, because the fainter one fades at large deflection and the finder then jumps to the other one.
5. **Know the syringe.** The rates the pump can run depend on the syringe (a 5 mL syringe runs 500, 600 … 1000,
   1500 µL/min, nothing below 500). `flow_displacement_experiment.py` refuses a rate the syringe can't run and lists
   the ones it can.
6. **Know the ports.** Ports are numbered across a cascade of selector valves: valve 0 (closest to the flow cell)
   carries ports 1 … N0−1 directly, and its last position is plumbed to valve 1's outlet, so valve 1's positions become
   ports N0, N0+1, … (`reagent_selection.selector_valves` in the fluidics config: `number_of_ports` per valve). Ports on
   valve 1 go through an extra valve and connecting line.
7. **Pick a release port**: a port whose line passes liquid freely and whose liquid may enter the shared line (a few µL
   per move). Confirm it in test 4 (it should hold < 2 µm itself), and **re-check the release port in every survey**
   against a second free port: a release port that starts holding stops relieving the other ports, and their holds are
   then missed.
8. **Know how z is actuated** (`Z_MOTOR_CONFIG`, `HAS_OBJECTIVE_PIEZO` in the machine `.ini`) before diagnosing a
   focus problem; on a piezo-only rig stage z always reads 0.0 mm.

## Repeats and order

- **Two repeats per condition.** Add a third only if the first two differ by more than about 2 µm, or a port is
  flagged.
- **Shuffle or alternate the order** (`--order shuffle --seed N`, or interleave conditions), so a slow drift can't look
  like a difference between conditions.
- **Empty the syringe before every pull** (`--empty-first`), so the fill level is the same each time. The empty goes to
  waste and does not disturb the coverslip.
- Stop when the answer is clear.

## The tests, in order

Replace the port numbers with your own. Outputs go wherever `--out` / `--out-dir` points.

### 0. Piezo check (10 s, no liquid)

```bash
python3 tools/coverslip_flow/piezo_check.py
```

PASS when the spot moves about 10 µm ÷ (µm per pixel) each way and comes back within a pixel. On FAIL the piezo driver
is not following its input: every laser AF correction in a scan then fails its cross-correlation check and the whole
scan stays at one z. Restart the piezo driver, refocus, run it again. Tests 1–6 don't move focus and don't need it, but
run it before any imaging check. It leaves the piezo where it started.

### 1. Dry run (30 s, no liquid)

```bash
python3 tools/coverslip_flow/flow_displacement_experiment.py --dry-run 30 --out ~/flowtest/dry30
```

Checks the setup: expect ≥ 5 Hz displacement samples (typically 30 Hz) and a steady reading. Below 5 Hz, lower the laser
AF averaging or the focus-camera exposure.

### 2. Baseline pulls (2 × 3 min, 2 × 500 µL of wash buffer)

```bash
python3 tools/coverslip_flow/flow_displacement_experiment.py --empty-first --port <wash> --volume 500 --rate 500 \
    --baseline 30 --post 60 --out ~/flowtest/base_1
```

Run it twice (`base_2`). Each run writes `<out>.csv` (time, GUI displacement, AF frame, flow, events),
`<out>_bright.csv` (the bright-spot track, absolute and relative to the baseline), `<out>_meta.json`, `<out>.png` and
`<out>_af.tif` (the AF frames; `--no-af-images` to skip). `--reprocess --out <out>` redoes the tracking and plot of a
finished run.

### 3. Prime each line and check for air (≈ 1.5 min and 250 µL per port)

Draw a bit more than the line's tubing volume (`tubing_fluid_amount_ul` in the fluidics config) from every port you
will test, so the line is full of liquid from the bottle:

```bash
for P in 1 2 3; do
  python3 tools/coverslip_flow/flow_displacement_experiment.py --empty-first --port $P --volume 250 --rate 500 \
      --baseline 10 --post 20 --no-plot --out ~/flowtest/prime/port$P
done
```

Look at each run's flow trace (the `flow_…` column of the `.csv`). **Air** shows as the flow dropping to zero or below
and then spiking to thousands of µL/min, usually on a port's first draw only. If a port still does it on a second
draw, check that its tubing end is under the liquid and its fittings are tight. The 50 µL draws of the next test are
smaller than the tubing volume and can miss air at the bottle end; that is what this step is for.

### 4. Port hold check (≈ 2.5 min and 50 µL per port)

```bash
python3 tools/coverslip_flow/port_hold_check.py --ports 1 2 3 4 --release-port 5 \
    --order shuffle --seed 1 --out-dir ~/flowtest/port_hold
```

Per port: empty the syringe, turn the valve to the port, 30 s baseline, pull 50 µL at 500 µL/min, record 45 s; then
turn the valve to the release port and hold 30 s. The release port can't be in `--ports`: check it in a separate run
with another release port. Re-analyse a folder with `--analyze-only --out-dir …`.

The summary (`port_hold_summary.csv` and the printed table):

| Column | Meaning |
|---|---|
| `held_um` | Coverslip at the end of the post-pull recording, relative to just before the pull (negative = still pulled in). **A free line comes back within ±2 µm. Flagged HOLDS PRESSURE beyond ±5 µm.** |
| `release_rise_um`, `release_inflow_ul` | Rise and sensor volume when the valve moved to the release port: the suction the port was holding, released |
| `dip_um` | Deepest point during the pull, relative to before it. Deeper than other ports = more resistance in that line |
| `sensor_volume_ul` | Sensor volume during and 30 s after the pull. Clearly below the other ports = the line did not deliver |
| `valve_move_um`, `valve_move_inflow_ul` | Shift when the valve turned onto this port, with no pumping (bottle height or route differences) |
| `sensor_verdict` | The sensor-only verdict (4b) of the same run |

A holding line typically also dips deeper during the pull and passes a few µL less: more resistance while flowing and
a cut-off near rest, i.e. a partial blockage (a pinned bubble, debris, an inline filter). Holding can come and go on
the same line within a day, so repeat the check on any port that matters.

**Repeat** with a different seed and a new `--out-dir`. Run a third time only on ports where the two runs disagree by
more than about 2 µm, and always on the ports used just before imaging (imaging buffer, last wash).

**What to do with a holding port:** inspect its line (kinks, debris, an inline filter, air at the valve fittings, the
tubing end out of the liquid), flush or reseat it, and re-run the check on that port. Until it is fixed, a valve move
to a free-flowing port after the draw releases the suction within seconds, but lets a few µL of that port's liquid
into the shared line.

**Clearing a holding line with a fast pull** (try before taking the line apart; pulls only):

```bash
python3 tools/coverslip_flow/flow_displacement_experiment.py --empty-first --port 3 --volume 250 \
    --rate 1000 --baseline 15 --post 45 --no-plot --out ~/flowtest/unclog/fast_port3
python3 tools/coverslip_flow/valve_sequence.py --pre 5 --steps 5:30 --out ~/flowtest/unclog/fast_port3_release
python3 tools/coverslip_flow/port_hold_check.py --sensor-only --ports 3 3 --release-port 5 \
    --out-dir ~/flowtest/unclog/checks_port3
```

Release to the free port right after the fast pull (the line may still hold), then judge the line from two 50 µL
sensor-only checks, not from the fast pull itself. A fast pull dips the coverslip about twice as far as a 500 µL/min
pull. Step up only while the checks still say HOLDS: 250 µL at 1000 µL/min, then 500 µL at 1000 µL/min, then 2500 µL at
1500 µL/min. If that doesn't clear it, back-flush the line by hand syringe from the valve end toward the bottle, with
the line disconnected from the valve (never through the flow cell); recut or replace the tubing end and ferrule; check
for a filter; then re-run the sensor-only check. Recheck cleared lines later: holding can come back.

#### 4b. Sensor-only hold check (≈ 1.5 min and 50 µL per port, no laser AF)

```bash
python3 tools/coverslip_flow/port_hold_check.py --sensor-only --ports 1 2 3 --release-port 5 \
    --order shuffle --seed 1 --out-dir ~/flowtest/sensor_hold
```

The same pull, 45 s wait and move to the release port, judged from the flow sensor alone: a held line leaves the flow
cell short of liquid, and when the valve moves that liquid flows back in. Use it on rigs without laser AF, or to
re-check ports quickly. Re-analyse with `--analyze-only --sensor-only`. Writes `NN_sensor_port<P>_to<R>_flow.csv` /
`_meta.json` and `port_hold_sensor_summary.csv`.

| Column | Meaning |
|---|---|
| `release_inflow_ul` | Sensor volume in the 15 s after the move to the release port, above the sensor's rest reading. **HOLDS ≥ 1.0 µL**, borderline 0.6–1.0 µL, ok below |
| `release_peak_ul_min` | Peak of that refill above the rest reading |
| `pull_volume_ul`, `tail_ul` | Sensor volume during and 30 s after the pull, and after it stopped |
| `verdict` | HOLDS / borderline / ok, or **SENSOR GLITCH - repeat**: a reading above 1000 µL/min, or flow still 20 µL/min above rest 12–15 s after the move (air or a bad reading, not a refill) |

The 1.0 µL threshold was set against the coverslip check, where a holding line takes in roughly 1–4 µL on release and
a free line under 1 µL (about 4 µm of coverslip per µL). Limits: always release to the same free port, at a similar or
lower bottle height (moving into a higher bottle also lets liquid in); absolute volumes depend on the sensor's
calibration for the liquid; and it does not see rest-position shifts that aren't suction, which the coverslip check
does.

### 5. Valve-only route check (≈ 3 min, no liquid drawn)

```bash
python3 tools/coverslip_flow/valve_sequence.py --pre 10 --steps 1:30 5:60 1:60 --out ~/flowtest/valve_1_5_1
```

`PORT:SECONDS` steps; the valve moves, nothing is pumped. `<out>_samples.csv` has `bright_spot_um` (absolute, from the
AF reference), `<out>_flow.csv` the sensor readings. Moves between ports of the same valve typically shift the
coverslip by well under 1 µm; moves between valves, or into a bottle at a different height, can shift it several µm
with a few µL of flow; a move from a port holding suction to a free port releases it.

### 6. Optional: before/after height map (≈ 10 min per cycle, one 500 µL pull each)

Needs a scan grid: run a normal scan in the GUI first and use its `0/coordinates.csv`. The stage moves over the grid at
the current focus (no AF corrections).

```bash
python3 tools/coverslip_flow/af_map_flow.py --positions /path/to/acquisition/0/coordinates.csv \
    --port <wash> --volume 500 --rate 500 --settle 60 --n-before 2 --post 30 --out ~/flowtest/map/m1
python3 tools/coverslip_flow/af_map_analyze.py ~/flowtest/map/m1
```

Maps twice before the pull (their difference is the map noise), pulls at the centre, waits `--post` s after the pump
finishes, maps again. `af_map_analyze.py` reports each map's range, the difference maps and a paraboloid fit (bowl
centre, height at the centre, depth at `--radius` mm), and saves `<base>_maps.png`, `<base>_centre.png` and
`<base>_map_summary.json`. `--map-only` maps without emptying, moving the valve or pulling.

Run one more cycle with `--n-before 1` (the noise is already known). If it agrees with the first within about 1 µm,
stop.

### Not in the standard suite

For investigating flow-rate or syringe effects only: a **rate series** (the same pull at several rates, shuffled, once
per rate) and a **syringe-fill series** (`flow_displacement_experiment.py --prefill UL --empty-first`, one run per fill
level). The pre-fill draws through the flow cell too and can move the rest position.

## Typical values

From a 20x air objective, SSC buffers and a 5 mL syringe; use them to spot a rig that behaves differently.

- **Pull-in during a 500 µL/min pull:** −70 to −90 µm at the centre (deeper on lines with more resistance). The flow
  reaches 90% of its plateau about 7 s after the pump starts.
- **Recovery after the pump stops:** 90% in 2–6 s; within 2 µm of rest in under 20 s.
- **Free-flowing port:** back within ±2 µm after a pull. Repeating the same pull returns the coverslip to the same shape
  within about 0.5 µm; map-to-map noise is under 1 µm.
- **Flow sensor:** a sensor calibrated for water reads low on salt buffers (about half the commanded rate on SSC), and
  lower still at high rates. Read its flow as relative. At rest it reads a few µL/min off zero; the tools measure from
  that rest reading.

## Known pitfalls

- **The Laser AF settings panel can show stale values** after settings are changed through the control server. Don't
  press Apply/Update there unless the panel shows what you want written; reselecting the objective reloads it.
- **The piezo driver can stop following its input** with no error in the log. Run `piezo_check.py` before any scan whose
  z matters.
- **Valve moves between ports on different valves can shift the coverslip** by several µm without pumping; keep that in
  mind when comparing ports.
