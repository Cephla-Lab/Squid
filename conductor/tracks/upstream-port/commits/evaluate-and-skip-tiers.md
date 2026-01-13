# Evaluate & Skip Tier Assessment

## Tier 4: EVALUATE - Need arch_v2-specific Review

### `4234d34b` - NDViewer Tab with Live Viewing

**What it does:** Adds a new tab using NDViewer (napari-based) for live viewing with plate navigation.

**Files changed:**
- Adds `ndviewer_light` as git submodule
- `gui_hcs.py` - add new tab
- `widgets.py` - NDViewerWidget implementation

**Assessment:**
| Factor | Status |
|--------|--------|
| Adds git submodule | ⚠️ Needs evaluation |
| arch_v2 has napari integration | Need to check |
| 680 lines of new code | Significant effort |

**Recommendation:** DEFER until napari integration strategy is decided for arch_v2. The submodule dependency adds complexity.

---

### `cc205460` - Fix NapariLiveWidget Signal Connection

**What it does:** Fixes Qt signal connection in NapariLiveWidget (2 lines changed).

**Assessment:**
| Factor | Status |
|--------|--------|
| NapariLiveWidget in arch_v2? | Need to verify |
| Minimal change | Easy to port if applicable |

**Recommendation:** Check if `NapariLiveWidget` exists in arch_v2. If yes, port. If no, SKIP.

---

### `afd71c97` - MCP Commands for View Settings

**What it does:** Adds MCP (Model Context Protocol) commands for controlling view settings, primarily for RAM debugging.

**Files changed:**
- `microscope_control_server.py` - new MCP handlers
- New documentation
- Tests

**Assessment:**
| Factor | Status |
|--------|--------|
| arch_v2 MCP implementation | May differ |
| RAM debugging utility | Useful for development |
| 550 lines | Moderate effort |

**Recommendation:** DEFER - Review arch_v2's control server implementation first. May need adaptation.

---

### `2b1e2f6d` - Runtime View Settings via MCP

**What it does:** Companion to afd71c97. Adds runtime control of view settings.

**Assessment:**
| Factor | Status |
|--------|--------|
| Depends on afd71c97 | Must port together |
| PreferencesDialog changes | Need to verify arch_v2 structure |

**Recommendation:** DEFER - Same as afd71c97.

---

## Tier 5: LIKELY SKIP

### `295afbb3` - Disable Memory Profiling in CI

**What it does:** Adds environment variable to disable memory profiling in GitHub Actions.

**Reason to skip:** arch_v2 has its own CI configuration. This is upstream-specific.

**Recommendation:** SKIP
- **Reason:** `not-applicable`
- **Justification:** CI workflow configuration is repository-specific. arch_v2 maintains its own `.github/workflows/`.

---

### `a48fa4bc` - Simulated Disk I/O Documentation

**What it does:** Adds documentation for simulated disk I/O feature.

**Reason to skip:** Only relevant if `b91694f1` (simulated I/O feature) is ported.

**Recommendation:** CONDITIONAL
- If porting `b91694f1`: Create arch_v2-specific documentation
- If not porting `b91694f1`: SKIP

---

### `0ce8d626` - Move Documentation File

**What it does:** Moves `dynamic-widget-visibility.md` from `docs/architecture/` to `software/docs/pending/`.

**Reason to skip:** File reorganization in upstream repo structure.

**Recommendation:** SKIP
- **Reason:** `not-applicable`
- **Justification:** Documentation file movement within upstream structure. arch_v2 has different documentation organization.

---

### `aa817c0b` - Update Napari Version in Setup Script

**What it does:** Updates napari version and app name in setup/desktop shortcut scripts.

**Files changed:**
- `main_hcs.py` - app window title
- `setup_22.04.sh` - napari version
- `script_create_desktop_shortcut.py` - app name

**Assessment:**
| Change | arch_v2 Impact |
|--------|----------------|
| Napari version | May need separate evaluation |
| App window title | Could port |
| Setup script | arch_v2-specific |
| Desktop shortcut | arch_v2-specific |

**Recommendation:** PARTIAL SKIP
- App window title change: Consider if relevant
- Setup scripts: SKIP (arch_v2 has own scripts)

---

## Summary Table

| Hash | Title | Recommendation | Action |
|------|-------|----------------|--------|
| `4234d34b` | NDViewer tab | DEFER | Evaluate napari strategy |
| `cc205460` | NapariLiveWidget fix | CONDITIONAL | Check if widget exists |
| `afd71c97` | MCP view settings | DEFER | Review control server |
| `2b1e2f6d` | Runtime view settings | DEFER | Same as above |
| `295afbb3` | CI memory profiling | SKIP | Not applicable |
| `a48fa4bc` | Simulated I/O docs | CONDITIONAL | Depends on b91694f1 |
| `0ce8d626` | Move doc file | SKIP | Not applicable |
| `aa817c0b` | Napari version update | PARTIAL SKIP | Evaluate app title only |
