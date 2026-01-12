# Upstream Commit Porting

Port commits from `upstream/master` (Cephla-Lab/Squid) to the current architecture branch.

## CRITICAL: Semantic Ports Only

**NEVER merge or cherry-pick directly from upstream.** The arch_v2 architecture has diverged significantly:

- Different directory structure (3-layer: core/backend/ui)
- Different patterns (EventBus, Services, RLock threading)
- Different file locations and module organization

**All ports must be semantic reimplementations** - understand what the upstream commit does, then implement the equivalent functionality following arch_v2 patterns.

## Tracking System

**Canonical status is tracked in `upstream-status.yaml`** - a machine-parseable YAML file that is automatically maintained by the `/upstream-check` skill.

### Quick Commands

```bash
cd software

# Check current status
python tools/upstream_tracking.py summary

# Fetch upstream and discover new commits
python tools/upstream_tracking.py add-pending --fetch

# List pending commits
python tools/upstream_tracking.py list --status pending

# Verify consistency
python tools/upstream_tracking.py verify
```

### Status Types

| Status | Meaning |
|--------|---------|
| `pending` | Needs to be ported or explicitly skipped |
| `in-progress` | Currently being ported |
| `ported` | Successfully ported to arch_v2 |
| `skipped` | Intentionally not porting (with documented justification) |

## Workflow

Use the `/upstream-check` skill to manage upstream ports. The skill:

1. **Auto-discovers** new upstream commits and adds them as pending
2. **Tracks decisions** - every commit must be ported or skipped with justification
3. **Enforces conventions** - proper commit message format with trailers
4. **Verifies consistency** - checks that YAML and git history match

See `.claude/skills/upstream-check/SKILL.md` for detailed workflow.

## Git Commit Convention

### Port Commits

```
feat: Port <feature> from upstream (<hash>)

<description>

Ports-Upstream: <hash>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

### Skip Documentation

```
docs(upstream): Skip <hash> - <reason>

<justification>

Skips-Upstream: <hash>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

## Files

| File | Purpose |
|------|---------|
| `upstream-status.yaml` | **Canonical status** - auto-maintained by skill |
| `commits/` | Detailed analysis files for complex ports |
| `port-log.md` | **DEPRECATED** - legacy tracking (kept for history) |

## Key File Mappings

When porting, use these mappings to find the correct arch_v2 location:

| Upstream Path | arch_v2 Path |
|---------------|--------------|
| `control/widgets.py` | `src/squid/ui/widgets/<domain>/` |
| `control/gui_hcs.py` | `src/squid/ui/main_window.py` |
| `control/core/multi_point_controller.py` | `src/squid/backend/controllers/multipoint/` |
| `control/core/multi_point_worker.py` | `src/squid/backend/controllers/multipoint/` |
| `squid/logging.py` | `src/squid/core/logging.py` |
| `squid/camera/utils.py` | `src/squid/backend/drivers/cameras/` |
| `firmware/` | `firmware/` (direct copy) |

See `.claude/skills/upstream-check/MAPPING.md` for complete mapping reference.
