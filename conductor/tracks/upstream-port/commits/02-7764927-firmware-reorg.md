# PR 2: Firmware Directory Reorganization

**Upstream Commit:** `7764927` - refactor: Reorganize firmware directory structure
**Priority:** CRITICAL (applies after a4db687)
**Effort:** Small

## Summary

Reorganize firmware directory structure and update README.

## Upstream Changes

**Files Modified:**
- `firmware/README.md` (+45 lines)
- Various file moves/renames within `firmware/`

## arch_v2 Target

**Location:** `firmware/`

## Implementation Checklist

### Step 1: Apply After a4db687
- [x] Ensure PR 1 (a4db687) is completed first
- [x] This commit reorganizes the structure created in PR 1

### Step 2: Apply Reorganization
- [x] Review upstream diff for exact file moves
- [x] Apply directory structure changes
- [x] Update `firmware/README.md` with new documentation

### Step 3: Verification
- [x] Verify all files are in correct locations
- [x] Verify firmware still compiles (N/A - no build environment)
- [x] Check README accuracy

## Dependencies

- **Requires:** PR 1 (a4db687) completed first

## Notes

- This is a follow-up reorganization commit
- Should be applied immediately after PR 1
- Consider cherry-picking both commits together as a single PR

## Implementation Notes (2025-12-29)

Changes applied:
1. Moved `octopi_firmware_v3/main_controller_teensy41` to `controller/`
2. Created `joystick/` from `octopi_firmware_v2/control_panel_teensyLC`
3. Renamed `archived/` to `legacy/`
4. Moved `octopi_firmware_v1_030`, `octopi_firmware_v2`, and `trigger_and_DAC_controller_teensy40` to `legacy/`
5. Updated `README.md` with new structure and build instructions
