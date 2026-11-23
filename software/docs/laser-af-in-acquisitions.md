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
