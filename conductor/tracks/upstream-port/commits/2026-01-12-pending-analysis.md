# Upstream Pending Commits Analysis

**Date:** 2026-01-12
**Commits analyzed:** 25 pending
**Source:** upstream/master

## Priority Tiers

### Tier 1: HIGH PRIORITY - Port Soon

#### Backpressure & RAM Management Suite
These form a cohesive feature set critical for production stability.

| Hash | Title | Category | Notes |
|------|-------|----------|-------|
| `081fd7e9` | Add acquisition backpressure to prevent RAM exhaustion (#436) | Feature | Core backpressure implementation |
| `c28b372b` | Add live RAM usage monitoring display in status bar (#434) | Feature | RAM monitoring UI |
| `97f85d1b` | Add backpressure status bar widget (#438) | Feature | Status bar integration |
| `c3322bb1` | Resolve backpressure deadlock with z-stack acquisitions (#446) | Bugfix | Critical deadlock fix |
| `e9c6249b` | Backpressure byte tracking and multiprocessing cleanup (#442) | Bugfix | Memory tracking improvements |
| `6bffd2d3` | Simplify backpressure code, fix saving path bug (#440) | Cleanup | Fixes and simplification |

**Port order:** 081fd7e9 → c28b372b → 97f85d1b → c3322bb1 → e9c6249b → 6bffd2d3

#### Configuration System Refactor
User requested full port of YAML/profiles system.

| Hash | Title | Category | Notes |
|------|-------|----------|-------|
| `13eff115` | New design for illumination channel configs (#417) | Refactor | 53 files, major refactor |
| `3866b183` | Remove legacy config managers, centralize (#441) | Refactor | Depends on 13eff115 |
| `98c50432` | Update stale channel_configuration_manager refs (#449) | Cleanup | Depends on 3866b183 |

**Port order:** 13eff115 → 3866b183 → 98c50432

---

### Tier 2: MEDIUM PRIORITY - Acquisition Features

| Hash | Title | Category | Notes |
|------|-------|----------|-------|
| `47e7aff7` | Add alignment button for sample registration (#448) | Feature | Useful for multi-session imaging |
| `88db4da8` | Save/load acquisition parameters via YAML (#421) | Feature | Automation support |
| `f8c05d0d` | run_acquisition_from_yaml TCP command (#422) | Feature | Remote automation |
| `57378358` | Acquisition throttling settings in Preferences (#444) | Feature | Ties into backpressure |
| `fc57e3da` | Persist last used base saving path (#430) | UX | Small quality of life |
| `98c7fbd6` | Save/restore camera settings on close/startup (#423) | Feature | Camera state persistence |

---

### Tier 3: MEDIUM PRIORITY - Simulation & Development

| Hash | Title | Category | Notes |
|------|-------|----------|-------|
| `5ad9252a` | Regenerate SimulatedCamera frame when binning changes (#429) | Bugfix | Important for simulation testing |
| `b91694f1` | Add simulated disk I/O mode for development (#431) | Feature | Testing without real disk |

---

### Tier 4: EVALUATE - May Not Apply

| Hash | Title | Category | Notes |
|------|-------|----------|-------|
| `4234d34b` | NDViewer tab with live viewing (#428) | Feature | Adds git submodule - evaluate need |
| `cc205460` | Fix NapariLiveWidget Qt signal connection (#443) | Bugfix | May not exist in arch_v2 |
| `afd71c97` | MCP commands for view settings control (#425) | Feature | arch_v2 may have different MCP |
| `2b1e2f6d` | Runtime control of view settings via MCP (#424) | Feature | Depends on afd71c97 |

---

### Tier 5: LIKELY SKIP - Low Value

| Hash | Title | Category | Notes |
|------|-------|----------|-------|
| `295afbb3` | Disable memory profiling in CI (#451) | CI | arch_v2 has own CI config |
| `a48fa4bc` | Simulated disk I/O documentation (#432) | Docs | Port only if b91694f1 ported |
| `0ce8d626` | Move doc to pending folder (#433) | Docs | File reorganization only |
| `aa817c0b` | Update napari version in setup script (#426) | Chore | Setup script specific |

---

## Recommended Port Order

1. **Backpressure Suite** (6 commits) - Critical for stability
2. **Config Refactor** (3 commits) - Major architectural change
3. **Acquisition Features** (6 commits) - Automation and UX
4. **Simulation** (2 commits) - Development support
5. **Evaluate** (4 commits) - Need arch_v2-specific review
6. **Skip** (4 commits) - Low value or not applicable

## Dependencies

```
Backpressure:
  081fd7e9 (base)
    └─→ c28b372b (RAM monitoring)
        └─→ 97f85d1b (status bar widget)
    └─→ c3322bb1 (deadlock fix)
    └─→ e9c6249b (byte tracking)
        └─→ 6bffd2d3 (cleanup)

Config:
  13eff115 (new design)
    └─→ 3866b183 (remove legacy)
        └─→ 98c50432 (cleanup refs)

Automation:
  88db4da8 (YAML params)
    └─→ f8c05d0d (TCP command)
```

## Next Steps

1. Start with backpressure suite (081fd7e9)
2. Plan config refactor migration strategy
3. Review "Evaluate" tier commits against arch_v2 codebase
