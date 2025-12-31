# Upstream Commit Porting Plan

Port commits from `upstream/master` (Cephla-Lab/Squid) to `arch_v2` branch.

**Source:** `upstream/master` (Cephla-Lab/Squid)
**Target:** `arch_v2` branch
**Created:** 2025-12-29

## Summary

| Status | Count | Description |
|--------|-------|-------------|
| To Port | 17 | Active commits to port |
| Deferred | 2 | 1536-well plate features (low priority) |
| Total | 19 | All upstream commits |

## Implementation Order

### Phase 0: Firmware (CRITICAL)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 1 | [a4db687](commits/01-a4db687-firmware-v3.md) | Firmware v3 (+8900 lines) | Large |
| 2 | [7764927](commits/02-7764927-firmware-reorg.md) | Firmware directory reorganization | Small |
| 3 | [412c81d](commits/03-412c81d-pyvcam-pip.md) | PyVCAM pip install | Small |

### Phase 1: Quick Bug Fixes (1-2 hours)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 4 | [d8e41e2](commits/04-d8e41e2-camera-simulation.md) | Camera exposure in simulation | Small |
| 5 | [4bfa2a0](commits/05-4bfa2a0-wellplate-switch.md) | 1536 well plate switch fix | Small |
| 6 | [7b9d0e3](commits/06-7b9d0e3-laser-af-ui.md) | Laser AF exposure UI fix | Small |

### Phase 2: Bug Fixes (2-3 hours)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 7 | [2fd9816](commits/07-2fd9816-mosaic-ram.md) | Free RAM on mosaic clear | Small |
| 8 | [67dfbf5](commits/08-67dfbf5-test-fixes.md) | Test segfaults + Qt compat | Medium |

### Phase 3: Small Features (3-4 hours)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 9 | [c7bf416](commits/09-c7bf416-napari-icon.md) | Napari icon replacement | Small |
| 10 | [1241941](commits/10-1241941-fov-dialog.md) | FOV dialog for fluidics | Small |
| 11 | [1b71973](commits/11-1b71973-skip-saving.md) | Skip Saving checkbox | Small |

### Phase 4: Medium Features (1-2 days)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 12 | [9995e5c](commits/12-9995e5c-acquisition-log.md) | Save log in acquisition folder | Medium |
| 13 | [e3e1730](commits/13-e3e1730-scan-size.md) | Scan size consistency | Medium |
| 14 | [f416d58](commits/14-f416d58-ram-check.md) | RAM usage check for mosaic | Medium |

### Phase 5: Large Features (3-5 days)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 15 | [decdcc7](commits/15-decdcc7-config-dialog.md) | Configuration dialog (+826 lines) | Large |
| 16 | [ad9479d](commits/16-ad9479d-downsampled-view.md) | Downsampled plate view (+4000 lines) | Large |

### Phase 6: Major Refactor (2-3 days)
| # | Commit | Description | Effort |
|---|--------|-------------|--------|
| 17 | [b385904](commits/17-b385904-channel-config.md) | Channel configuration refactor (+2900 lines) | Large |

### Deferred (1536-well specific)
| Commit | Description | Reason |
|--------|-------------|--------|
| 6eb3427 | 1536 well plate mouse selection | Not using 1536-well plates |
| 4e940f7 | Bug fix for #372/#373 | Depends on 6eb3427 |

## Tests to Port

Several commits include significant test suites that MUST be ported:

| Commit | Test Files | Lines | Priority |
|--------|-----------|-------|----------|
| `ad9479d` | 4 test files for downsampled views | +1901 | HIGH |
| `b385904` | test_channel_configuration.py | +788 | HIGH |
| `decdcc7` | test_preferences_dialog.py | +351 | MEDIUM |
| `f416d58` | test_MultiPointController.py, test_widgets.py | +239 | MEDIUM |
| `e3e1730` | test_scan_size_consistency.py | +118 | MEDIUM |
| `67dfbf5` | conftest.py + test updates | +63 | CRITICAL (infrastructure) |

**Total: ~3460 lines of tests**

Each per-commit plan has detailed test porting instructions with arch_v2 target locations.

---

## Key File Mappings

| Upstream Path | arch_v2 Path |
|---------------|--------------|
| `control/widgets.py` | `src/squid/ui/widgets/<domain>/` |
| `control/gui_hcs.py` | `src/squid/ui/main_window.py` |
| `control/core/multi_point_controller.py` | `src/squid/backend/controllers/multipoint/multi_point_controller.py` |
| `control/core/multi_point_worker.py` | `src/squid/backend/controllers/multipoint/multi_point_worker.py` |
| `squid/logging.py` | `src/squid/core/logging.py` |
| `squid/camera/utils.py` | `src/squid/backend/drivers/cameras/` |
| `firmware/` | `firmware/` (direct copy) |

## Tracking

Track progress in [port-log.md](port-log.md).
