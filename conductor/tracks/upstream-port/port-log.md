# Upstream Port Log

Track progress of porting commits from `upstream/master` to `arch_v2`.

## Progress

| # | Upstream Commit | Description | Status | Date | arch_v2 Commit |
|---|-----------------|-------------|--------|------|----------------|
| 1 | a4db687 | Firmware v3 | completed | 2025-12-29 | (pending commit) |
| 2 | 7764927 | Firmware reorg | completed | 2025-12-29 | (pending commit) |
| 3 | 412c81d | PyVCAM pip | completed | 2025-12-29 | (pending commit) |
| 4 | d8e41e2 | Camera simulation | completed | 2025-12-29 | (pending commit) |
| 5 | 4bfa2a0 | Wellplate switch | completed | 2025-12-29 | N/A (already fixed) |
| 6 | 7b9d0e3 | Laser AF UI | completed | 2025-12-29 | (pending commit) |
| 7 | 2fd9816 | Mosaic RAM | completed | 2025-12-29 | (pending commit) |
| 8 | 67dfbf5 | Test fixes | completed | 2025-12-29 | (pending commit) |
| 9 | c7bf416 | Napari icon | completed | 2025-12-29 | (pending commit) |
| 10 | 1241941 | FOV dialog | completed | 2025-12-29 | (pending commit) |
| 11 | 1b71973 | Skip saving | completed | 2025-12-29 | (pending commit) |
| 12 | 9995e5c | Acquisition log | completed | 2025-12-29 | (pending commit) |
| 13 | e3e1730 | Scan size | completed | 2025-12-29 | (pending commit) |
| 14 | f416d58 | RAM check | completed | 2025-12-29 | (pending commit) |
| 15 | decdcc7 | Config dialog | completed | 2025-12-29 | (pending commit) |
| 16 | ad9479d | Downsampled view | completed | 2025-12-29 | (pending commit) |
| 17 | b385904 | Channel config | completed | 2025-12-29 | (pending commit) |

## Deferred

| Commit | Description | Reason |
|--------|-------------|--------|
| 6eb3427 | 1536 well plate mouse selection | Not using 1536-well plates |
| 4e940f7 | Bug fix for #372/#373 | Depends on 6eb3427 |

## Notes

- Update status to `in_progress`, `completed`, or `skipped` as work progresses
- Record the arch_v2 commit hash after porting
- Add notes about any issues or deviations from the plan
