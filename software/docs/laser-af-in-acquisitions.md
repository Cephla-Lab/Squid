# Laser AF in acquisitions

## When laser AF fails at FOVs

By default an acquisition with laser AF (Reflection AF) images a FOV where it is when laser AF fails there (no spot,
a displacement out of range, or a failed alignment check), logs the failure and counts it in the Slack time point
summary. A setting in the machine's `.ini`, under `[GENERAL]`, makes it stop instead once laser AF has failed at a
number of FOVs in a row:

```ini
[GENERAL]
laser_af_stop_after_consecutive_failures = 3
```

| Setting | Default | What it does |
|---|---|---|
| `laser_af_stop_after_consecutive_failures` | `0` (off) | When laser AF has failed at this many FOVs in a row, end the acquisition instead of imaging that FOV out of focus. That FOV is not imaged; the FOVs before it, where laser AF had also failed, were imaged where they were. A FOV where laser AF succeeds resets the count. |

An acquisition stopped this way ends as an `error`: the log and the watchdog's `run.json` (`stats.detail`) name the
region, FOV and time point where it stopped, the watchdog alerts, and a fluidics protocol run holds at that imaging
step.

With a count of 1, a single FOV where laser AF keeps failing stops every retry of the acquisition there; a count of 2
or more lets a retry image that FOV and carry on, and stops only when laser AF loses the surface over several FOVs.

The count applies to the laser AF at each FOV. The laser AF before each later channel's stack (Z First, "AF per
stack") keeps its own behaviour: on a failure the previous anchor stands, and it does not change the count.
