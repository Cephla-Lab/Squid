# PR 3: PyVCAM Pip Installation

**Upstream Commit:** `412c81d` - Install PyVCAM using pip (#377)
**Priority:** Low
**Effort:** Small

## Summary

Simplify PyVCAM (Photometrics camera SDK) installation to use pip instead of manual installation.

## Upstream Changes

**Files Modified:**
- `software/drivers and libraries/photometrics/install_photometrics.sh` (-3, +1 lines)

## arch_v2 Target

**Location:** `software/drivers and libraries/photometrics/install_photometrics.sh`

## Implementation Checklist

### Step 1: Review Change
- [x] Read upstream diff
- [x] Understand the pip installation approach

### Step 2: Apply Change
- [x] Update install script to use pip
- [x] Remove manual installation steps

### Step 3: Verification
- [x] Test installation on a clean environment (if possible) - N/A, requires Photometrics hardware
- [x] Verify PyVCAM is importable after installation - N/A, requires Photometrics hardware

## Notes

- This is a simple driver installation improvement
- May not be testable without Photometrics hardware
- Low priority - can be done anytime
