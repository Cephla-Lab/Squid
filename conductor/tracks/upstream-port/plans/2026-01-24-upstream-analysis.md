# Upstream Port Analysis - January 24, 2026

**Status:** PLANNING
**Started:** 2026-01-24
**Total Pending:** 23 commits

## Overview

23 new commits on `upstream/master` need evaluation. They fall into these categories:

1. **Preferences/Settings Refactors** - 4 commits
2. **NDViewer Enhancements** - 3 commits
3. **Bug Fixes** - 8 commits
4. **New Features** - 5 commits
5. **Hardware Drivers** - 2 commits
6. **Documentation** - 1 commit

---

## Suite 1: Preferences & UI Polish

These commits form a related set around preferences dialog improvements:

| Hash | Title | Recommendation |
|------|-------|----------------|
| `3d5b7922` | Skip hardware init on restart after settings change | **PORT** - useful for fast restart |
| `364d2cf1` | Per-component hardware simulation controls | **PORT** - valuable for debugging |
| `5b10bca4` | Reorganize Preferences dialog | **SKIP** - superseded by arch_v2 PreferencesDialog |
| `5a22a08b` | UI improvements - maximize window | **PORT** - simple, useful |

### Recommendation
- Port `3d5b7922`, `364d2cf1`, `5a22a08b` as a suite
- Skip `5b10bca4` - arch_v2 has its own PreferencesDialog structure

---

## Suite 2: NDViewer Tab Enhancements

These build on the NDViewer tab already ported:

| Hash | Title | Status |
|------|-------|--------|
| `c58983c0` | NDViewer push-based API for live acquisition | **PORTED** ✓ |
| `ddd5a549` | Settings option to enable/disable NDViewer | **PORTED** ✓ |
| `44264e3c` | QMetaObject.invokeMethod for TCP GUI state | **NOT APPLICABLE** - arch_v2 uses EventBus |
| `a1dfc4ad` | Terminate GUI after --wait acquisition | **NOT APPLICABLE** - different run_acquisition.py |

### Recommendation
- Suite complete. 2 ported, 2 not applicable due to arch_v2's different architecture

---

## Suite 3: Slack Notifications (New Feature)

| Hash | Title | Recommendation |
|------|-------|----------------|
| `0346d508` | Add Slack notifications for acquisition events | **PORT** - valuable feature |
| `17ed8c7b` | Status bar widget for warnings/errors | **PORT** - useful UX improvement |

### Recommendation
- Port both - the status bar widget is foundational, Slack notifications build on it

---

## Suite 4: Bug Fixes (Critical)

| Hash | Title | Recommendation |
|------|-------|----------------|
| `0dcbcef2` | Live illumination switching w/ hardware trigger | **PORT** - critical bug fix |
| `5f4b158c` | Race condition in NapariLiveWidget alignment | **PORT** - fixes MCU timeouts |
| `606e4175` | Initialize Optospin filter wheel positions | **PORT** - 1-line fix |
| `fbd9eda5` | MCU cmd timeout and toupcam strobe delay | **PORT** - hardware timing fix |
| `f270061a` | Preserve manual region drawing order | **PORT** - user intent fix |
| `b352f0a7` | Suppress stale read warnings in simulation | **PORT** - quality of life |
| `82dafa6f` | Laser AF spot detection mode fixes | **EVALUATE** - may be superseded |
| `e5cd025c` | Sync contrast limits / alignment feedback | **PORT** - UX improvement |

### Recommendation
- Port most - these are important bug fixes
- Evaluate `82dafa6f` carefully - arch_v2 may have different LaserAF implementation

---

## Suite 5: MCU Logging & Debugging

| Hash | Title | Recommendation |
|------|-------|----------------|
| `0f4cbb8d` | Detailed MCU command logging | **PORT** - valuable for debugging |

### Recommendation
- Port - improves debuggability of hardware communication

---

## Suite 6: Hardware Drivers

| Hash | Title | Recommendation |
|------|-------|----------------|
| `03f4d11c` | FLIR camera driver update | **PORT** - driver improvement |
| `9a015e0f` | Tucsen Libra25 support | **PORT** - new hardware support |

### Recommendation
- Port both - hardware support is straightforward to map

---

## Suite 7: User Profile Migration

| Hash | Title | Recommendation |
|------|-------|----------------|
| `cf4f1784` | User profile migration and spot detection fixes | **SKIP** - superseded |

### Recommendation
- Skip - arch_v2 uses different config system (JSON/Pydantic vs YAML/ConfigRepository)

---

## Suite 8: Documentation

| Hash | Title | Recommendation |
|------|-------|----------------|
| `fe927b0f` | Comprehensive config system documentation | **SKIP** - not applicable |

### Recommendation
- Skip - documents upstream's YAML config system, not arch_v2's

---

## Implementation Priority

### Phase 1: Critical Bug Fixes (Highest Priority)
- [x] `0dcbcef2` - Live illumination switching fix ✓ (ported 2026-01-24)
- [x] `5f4b158c` - NapariLiveWidget race condition ✓ (ported 2026-01-24)
- [x] `fbd9eda5` - MCU timeout fixes ✓ (ported 2026-01-24)
- [x] `606e4175` - Optospin init fix ✓ (ported 2026-01-24)

### Phase 2: NDViewer Suite
- [x] `c58983c0` - Push-based API ✓ (ported 2026-01-24)
- [x] `ddd5a549` - Enable/disable toggle ✓ (ported 2026-01-24)
- [x] `44264e3c` - TCP GUI state fix ✓ (not applicable - arch_v2 uses EventBus pattern)
- [x] `a1dfc4ad` - Wait termination fix ✓ (not applicable - arch_v2 has different run_acquisition.py)

### Phase 3: Preferences & Simulation
- [x] `5a22a08b` - Window maximize ✓ (ported 2026-01-24)
- [x] `364d2cf1` - Per-component simulation ✓ (ported 2026-01-24)
- [x] `3d5b7922` - Skip init on restart ✓ (ported 2026-01-24)

### Phase 4: Features
- [x] `17ed8c7b` - Warning/error status bar ✓ (ported 2026-01-24)
- [x] `0346d508` - Slack notifications ✓ (SKIPPED - user request)
- [x] `0f4cbb8d` - MCU logging ✓ (ported 2026-01-24)

### Phase 5: Hardware & Misc
- [x] `03f4d11c` - FLIR driver ✓ (ported 2026-01-24 - added FLIRCameraModel enum, full refactor deferred)
- [x] `9a015e0f` - Tucsen Libra25 ✓ (ported 2026-01-24 - added ModeLibra, model properties, config)
- [x] `f270061a` - Manual region order ✓ (ported 2026-01-24)
- [x] `b352f0a7` - Simulation warnings ✓ (ported 2026-01-24)
- [x] `e5cd025c` - Contrast limits sync ✓ (ported 2026-01-24)

---

## Skip Summary

| Hash | Reason | Justification |
|------|--------|---------------|
| `5b10bca4` | superseded | arch_v2 has own PreferencesDialog structure |
| `cf4f1784` | superseded | arch_v2 uses JSON/Pydantic config, not YAML/ConfigRepository |
| `fe927b0f` | not-applicable | Documents upstream's config system |
| `82dafa6f` | superseded | arch_v2 uses Pydantic type-safe config with validators |
| `bf30b5c9` | ported | XLight fixes ported 2026-01-24 |
| `44264e3c` | not-applicable | arch_v2 uses EventBus for thread-safe GUI updates, not QMetaObject.invokeMethod |
| `a1dfc4ad` | not-applicable | arch_v2's run_acquisition.py is a TCP client only, no GUI launching |
| `0346d508` | user-request | Slack notifications skipped per user request |

---

## Effort Estimate

| Phase | Commits | Complexity |
|-------|---------|------------|
| Phase 1 | 4 | Low (bug fixes, mostly direct) |
| Phase 2 | 4 | Medium (integration with existing NDViewer port) |
| Phase 3 | 3 | Medium (preferences architecture differs) |
| Phase 4 | 3 | High (new features, significant code) |
| Phase 5 | 5 | Low-Medium (drivers, small fixes) |

**Total: 19 ports, 3-4 skips**
