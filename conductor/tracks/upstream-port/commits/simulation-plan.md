# Simulation & Development Suite - Porting Plan

**STATUS: COMPLETED** (2026-01-12)

All commits ported:
- [x] 5ad9252a - SimulatedCamera binning fix → commit 6c9cb672
- [x] b91694f1 - Simulated disk I/O → commit a8121d30

See tracking files `19-6c9cb672-simulation-throttling-ui.md` and `25-a8121d30-simulated-disk-io.md` for details.

---

## Overview

Port 2 commits improving simulation mode for offline development and testing.

**Commits:**
1. `5ad9252a` - Regenerate SimulatedCamera frame when binning changes (bugfix)
2. `b91694f1` - Add simulated disk I/O mode for development (feature)

---

## File Mapping

### 5ad9252a - SimulatedCamera Binning Fix

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `squid/camera/utils.py` | `backend/drivers/cameras/simulated.py` | Modify |

**Change:** When binning changes, regenerate the simulated frame to match new dimensions.

### b91694f1 - Simulated Disk I/O

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/_def.py` | `core/config/_def.py` | Modify |
| `control/core/io_simulation.py` | `backend/io/simulated_writer.py` | **Create** |
| `control/core/job_processing.py` | `backend/controllers/multipoint/job_processing.py` | Modify |
| `control/core/multi_point_worker.py` | `backend/controllers/multipoint/multi_point_worker.py` | Modify |
| `control/gui_hcs.py` | `ui/main_window.py` | Modify |
| `control/widgets.py` (IOSimulationWidget) | `ui/widgets/display/io_simulation_widget.py` | **Create** |
| `main_hcs.py` | `main_hcs.py` | Modify |

---

## Implementation Phases

### Phase 1: SimulatedCamera Binning Fix (5ad9252a)

**Simple fix - 3 lines of code**

In `backend/drivers/cameras/simulated.py`, when `set_binning()` is called:

```python
def set_binning(self, binning_x: int, binning_y: int):
    self._binning_x = binning_x
    self._binning_y = binning_y
    # Regenerate frame with new dimensions
    self._generate_frame()
```

The key is that `_generate_frame()` (or equivalent) must be called to create a new simulated image with the correct binned dimensions.

**Test:** Set binning to 2x2, acquire frame, verify dimensions are half of sensor size.

### Phase 2: Simulated Disk I/O (b91694f1)

#### Step 1: Configuration

Add to `core/config/_def.py`:
```python
SIMULATE_DISK_IO = False
SIMULATED_WRITE_DELAY_MS = 10.0
SIMULATED_WRITE_RATE_MB_S = 500.0
```

#### Step 2: Create Simulated Writer

Create `backend/io/simulated_writer.py`:

```python
class SimulatedWriter:
    """Simulates disk I/O timing without writing data."""

    def __init__(self, rate_mb_s: float = 500.0, delay_ms: float = 10.0):
        self.rate_mb_s = rate_mb_s
        self.delay_ms = delay_ms
        self._bytes_written = 0
        self._start_time = None

    def write(self, data: np.ndarray) -> float:
        """Simulate writing data, returns simulated write time."""
        size_mb = data.nbytes / (1024 * 1024)
        write_time = size_mb / self.rate_mb_s
        delay = self.delay_ms / 1000.0

        # Simulate the time it would take
        time.sleep(write_time + delay)

        self._bytes_written += data.nbytes
        return write_time + delay

    def get_stats(self) -> dict:
        """Return statistics about simulated writes."""
        return {
            "bytes_written": self._bytes_written,
            "simulated_throughput_mb_s": self.rate_mb_s,
        }
```

#### Step 3: Integrate into Job Processing

Modify `backend/controllers/multipoint/job_processing.py`:

```python
def __init__(self, ..., simulate_io: bool = False):
    self._simulate_io = simulate_io
    if simulate_io:
        from squid.backend.io.simulated_writer import SimulatedWriter
        self._simulated_writer = SimulatedWriter()

def _save_image(self, frame, path):
    if self._simulate_io:
        self._simulated_writer.write(frame)
        return  # Don't actually write
    # Normal write path...
```

#### Step 4: CLI Flag

Add `--simulate-io` flag to `main_hcs.py`:

```python
parser.add_argument(
    "--simulate-io",
    action="store_true",
    help="Simulate disk I/O without writing files (for development)"
)
```

#### Step 5: Status Widget (Optional)

Create `ui/widgets/display/io_simulation_widget.py`:
- Show "SIMULATED I/O" indicator when active
- Display simulated write rate
- Show bytes "written" (not actually saved)

---

## Key Considerations

1. **SimulatedCamera Frame Generation:**
   - The simulated camera needs a method to regenerate its test pattern
   - Test patterns should be deterministic for reproducible tests
   - Consider caching frames at common resolutions

2. **Simulated I/O Use Cases:**
   - Testing acquisition timing without filling disk
   - Benchmarking acquisition speed vs. I/O bottlenecks
   - CI/CD testing without disk space concerns

3. **Thread Safety:**
   - SimulatedWriter may be called from multiple threads
   - Use proper locking if tracking global statistics

4. **Integration with Backpressure:**
   - Simulated I/O should still trigger backpressure signals
   - This allows testing the full pipeline without disk I/O

---

## Dependencies

```
5ad9252a (binning fix) - Independent, can port immediately
b91694f1 (simulated I/O) - Independent, but benefits from backpressure integration
```

---

## Critical Files
- `backend/drivers/cameras/simulated.py`
- `backend/io/simulated_writer.py` (new)
- `backend/controllers/multipoint/job_processing.py`
- `main_hcs.py`
