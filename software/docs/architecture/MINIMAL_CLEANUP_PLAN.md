# Squid Refactor: Minimal Cleanup Plan

## Goal
Clean up the existing implementation to be more modular, composable, and maintainable. **No new abstractions** - just split large files, remove cruft, and organize better.

---

## What Needs Cleanup

### Large Controllers (God Objects)
- `multi_point_worker.py` - 1,357 lines, mixes iteration/capture/storage/progress
- `live_controller.py` - streaming + mode switching + illumination coordination
- `auto_focus_controller.py` - multiple AF strategies mixed together

### Confused Organization
- `mcs/` vs `ops/` distinction is unclear
- Both have controllers at different abstraction levels
- Infrastructure code scattered

### Cruft
- Dead code paths
- Redundant methods
- Over-engineered abstractions already in the code

---

## Approach: Split, Don't Add

### 1. Split Large Files Into Focused Modules

**MultiPointWorker → multiple focused modules:**
```
ops/acquisition/
├── multi_point_controller.py  # orchestration only (~200 lines)
├── position_sequence.py       # generates position/z/channel sequences
├── fov_capture.py             # what happens at one FOV
├── progress_tracker.py        # timing, progress, ETA
└── acquisition_state.py       # state machine for start/pause/stop/resume
```

Each module does ONE thing. The controller just ties them together.

**LiveController → focused pieces:**
```
mcs/controllers/
├── live_controller.py         # streaming on/off, mode state
├── stream_config.py           # exposure, gain, triggering config
└── (illumination already separate in illumination_service.py)
```

**AutoFocusController → strategy separation:**
```
mcs/controllers/autofocus/
├── auto_focus_controller.py   # common interface
├── contrast_af.py             # software contrast-based AF
├── laser_af.py                # laser reflection AF
└── focus_map.py               # interpolated focus from previous measurements
```

### 2. Clarify mcs/ vs ops/

**mcs/ (Microscope Control Service)** - Hardware-level
- Services that wrap hardware (camera, stage, illumination, filter wheel)
- Controllers for single-device operations (live streaming, single AF sweep)
- Low-level, synchronous, immediate

**ops/ (Operations)** - Workflow-level
- Multi-step workflows (acquisition, timelapse, wellplate scanning)
- Coordinates multiple mcs controllers/services
- Higher-level, async, long-running

### 3. Operations Layer (if needed)

If extracting logic from controllers reveals reusable workflow chunks that are:
- More than a single service call
- Less than full controller orchestration
- Used by multiple controllers

These can become **Operations** - focused functions or small classes that coordinate a few services for a specific task:

```python
# Example: ops/acquisition/operations.py
def capture_z_stack(camera_service, piezo_service, config) -> List[Frame]:
    """Capture a z-stack at current position. Used by MultiPoint and Manual acquisition."""
    ...

def move_and_settle(stage_service, position, settle_time_ms) -> ActualPosition:
    """Move to position and wait for mechanical settling."""
    ...
```

Not a framework - just extracted, reusable functions.

### 4. Remove Cruft

- Identify and delete dead code
- Remove unused parameters and methods
- Simplify over-engineered bits
- Delete commented-out code blocks

---

## Constraints

- **No new abstractions** - use existing Service/Controller/Event patterns
- **No new frameworks** - no Execution Engine, no Action/Gate/Recorder
- **Preserve behavior** - existing tests must pass
- **Incremental** - can be done file by file

---

## Order of Operations

1. **MultiPointWorker** - biggest win, most tangled
2. **AutoFocus** - already partially split, finish the job
3. **LiveController** - moderate complexity
4. **Organization cleanup** - move files to proper locations
5. **Cruft removal** - delete dead code throughout

---

## Future Considerations (NOT part of this refactor)

The elaborate architecture (Execution Engine, Protocol DSL, Hardware-Fused Acquisition) from the previous plan may be useful someday for:
- Multi-day MERFISH experiments needing resume capability
- Hardware-triggered acquisition for 10x speed improvement
- Remote monitoring and alerting

But that's a separate effort. This refactor is just about cleaning up what exists.
