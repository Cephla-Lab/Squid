# PR 4: Camera Exposure in Simulation Mode

**Upstream Commit:** `d8e41e2` - Fix: Use camera exposure time in simulation mode (#381)
**Priority:** High
**Effort:** Small (+8 lines)

## Summary

Use actual camera exposure time in simulation mode instead of a fixed delay. This makes simulation behavior more realistic.

## Upstream Changes

**Files Modified:**
- `software/squid/camera/utils.py` (+8 lines)

## arch_v2 Target

**Location:** `software/src/squid/backend/drivers/cameras/`

Look for simulation camera implementation. Likely in:
- `simulation.py` or similar
- Camera utility functions

## Implementation Checklist

### Step 1: Locate Simulation Code
- [x] Find simulation camera implementation in arch_v2
- [x] Identify where frame capture delay is set
- [x] Check current implementation

### Step 2: Review Upstream Change
- [x] Read upstream diff for `squid/camera/utils.py`
- [x] Understand how exposure time is now used

### Step 3: Apply Fix
- [x] Map changes to arch_v2 location
- [x] Apply exposure time delay logic
- [x] Ensure exposure time is accessible in simulation mode

### Step 4: Testing
- [x] Run simulation mode with `--simulation` flag
- [x] Set different exposure times
- [x] Verify frame rate changes appropriately

## Expected Behavior

**Before:** Simulation mode uses fixed delay regardless of exposure setting
**After:** Simulation mode respects actual exposure time for more realistic timing

## Notes

- Good first PR to port - isolated change, easy to test
- Improves simulation accuracy for development/testing
