---
name: upstream-check
description: Analyzes upstream commits on master that need porting to arch_v2. Performs semantic analysis of each commit, maps changes to new architecture locations, and presents a structured report for approval before implementation.
allowed-tools: Bash(git fetch:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(python*upstream_tracking*), Read, Write, Edit, Grep, Glob, AskUserQuestion
---

# Upstream Check Skill

Analyzes commits on `upstream/master` not in the current branch and helps port them semantically to the new 3-layer architecture.

## CRITICAL: No Direct Merges

**NEVER merge or cherry-pick directly from upstream.** The arch_v2 architecture has diverged significantly from upstream/master:

- Different directory structure (3-layer: core/backend/ui)
- Different patterns (EventBus, Services with RLock, etc.)
- Different file locations and module organization

**All ports must be SEMANTIC** - understand what the upstream commit does, then reimplement it following arch_v2 patterns. Direct patches will not apply cleanly and will break the architecture.

**Cutoff date: 2025-12-12** - Only commits on or after this date are tracked.

---

## MANDATORY Documentation Requirements

**EVERY port MUST have standardized documentation. This is NON-NEGOTIABLE.**

### Required Files

| File | When Created | Purpose |
|------|--------------|---------|
| `plans/<suite-name>.md` | BEFORE implementation | Plan with checklists |
| `commits/<number>-<our-hash>-<slug>.md` | AFTER implementation | Detailed record |
| `upstream-status.yaml` | Auto-updated | Machine-parseable status |

### Naming Conventions (Machine-Readable)

**Plan files:** `plans/<suite-name>.md`
- Use lowercase, hyphenated names
- Group related upstream commits under one plan
- Examples: `backpressure-suite.md`, `simulation-suite.md`, `config-refactor.md`

**Commit tracking files:** `commits/<NN>-<our-hash>-<slug>.md`
- `<NN>`: Sequential number (2 digits, zero-padded)
- `<our-hash>`: First 8 chars of OUR commit hash (not upstream)
- `<slug>`: Lowercase hyphenated description
- Example: `18-f5130544-backpressure-suite.md`

**YAML status entries:** Keyed by upstream commit hash
- Each upstream commit gets its own entry
- Links to tracking file via `analysis_file` field

### File Locations

```
conductor/tracks/upstream-port/
├── README.md                    # Overview and current status
├── upstream-status.yaml         # Canonical status (auto-maintained)
├── plans/                       # Implementation plans with checklists
│   ├── backpressure-suite.md
│   ├── simulation-suite.md
│   └── ...
└── commits/                     # Per-commit tracking files
    ├── 18-f5130544-backpressure-suite.md
    ├── 19-6c9cb672-simulation-throttling-ui.md
    └── ...
```

---

## Plan File Format (REQUIRED)

Create in `plans/<suite-name>.md` BEFORE starting implementation:

```markdown
# <Suite Name>

**Status:** IN-PROGRESS
**Started:** YYYY-MM-DD

## Upstream Commits

- [ ] `<hash1>` - <title>
- [ ] `<hash2>` - <title>

## Implementation Checklist

### Phase 1: <Name>
- [ ] Step 1
- [ ] Step 2

### Phase 2: <Name>
- [ ] Step 1
- [ ] Step 2

### Tests
- [ ] Unit tests for X
- [ ] Integration tests for Y

## Notes

<Any important notes or decisions>
```

**Update checkboxes as work progresses.** When complete, update status to COMPLETED.

---

## Commit Tracking File Format (REQUIRED)

Create in `commits/<number>-<our-hash>-<name>.md` AFTER implementation:

```markdown
# <Feature Name>

**Our Commit:** <hash>
**Date:** YYYY-MM-DD
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| <hash1> | <title> |
| <hash2> | <title> |

## Summary

<1-2 paragraph description of what was ported>

## Files Created/Modified

### Created
- `path/to/new/file.py` (<lines> lines) - <description>

### Modified
- `path/to/existing.py` - <what changed>

## Architecture Adaptations

<How the implementation was adapted for arch_v2>

## Tests

**File:** `tests/path/to/test_file.py`
**Count:** N tests

Covers:
- Test case 1
- Test case 2

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Tests added
- [ ] <any incomplete items>
```

---

## Workflow

### Step 0: Auto-Discovery

```bash
cd software && python tools/upstream_tracking.py add-pending --fetch
python tools/upstream_tracking.py summary
```

### Step 1: Analyze Pending Commits

```bash
python tools/upstream_tracking.py list --status pending
```

For each commit:
```bash
git show <hash> --stat
git show <hash>
```

### Step 2: Group Related Commits

Group commits into logical suites (e.g., "backpressure", "acquisition-features").

### Step 3: Create Plan File

**BEFORE any implementation**, create `plans/<suite-name>.md` with:
- All upstream commits listed with checkboxes
- Implementation phases with checkboxes
- Test requirements

### Step 4: Get Approval

Present the plan to the user. Wait for approval before proceeding.

### Step 5: Implement

1. Mark commits as in-progress in YAML
2. Implement following arch_v2 patterns
3. **Check off items in the plan file as you complete them**
4. Run tests
5. Create commit with proper format (see below)

### Step 6: Create Tracking File

**AFTER implementation**, create `commits/<N>-<hash>-<name>.md` with:
- All upstream commits ported
- Files created/modified
- Architecture adaptations
- Test information
- Audit checklist

### Step 7: Update Status

1. Update plan file status to COMPLETED
2. Update YAML via tracking tool
3. Verify with `python tools/upstream_tracking.py verify`

---

## Commit Message Format

### Port Commits

```
<type>(<scope>): <description> (<upstream-hash>)

<body describing what was ported>

Ports-Upstream: <hash1>
Ports-Upstream: <hash2>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

### Skip Commits

```
docs(upstream): Skip <hash> - <reason>

<justification>

Skips-Upstream: <hash>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

---

## Architecture Rules

When porting, ensure:
- UI layer uses EventBus, never calls hardware directly
- Services use RLock for thread safety
- No circular dependencies between layers
- Extract pure functions where possible

## Skip Reasons

| Reason | When to Use |
|--------|-------------|
| `not-applicable` | Commit doesn't apply to arch_v2 |
| `superseded` | arch_v2 has a different/better implementation |
| `already-fixed` | arch_v2 architecture prevents the bug |
| `deferred` | Will port later, not priority now |

---

## Edge Cases

### Commit touches multiple areas
Break down the commit into logical parts and map each to its new location. User can approve parts individually.

### Functionality already exists differently
Compare the upstream approach with the arch_v2 implementation and recommend:
- Skip (arch_v2 already handles this better)
- Merge ideas (combine best of both)
- Replace (upstream is clearly better)

### Large refactoring commits
Analyze carefully - arch_v2 has its own architecture, so structural changes may not apply directly. Consider:
- Extract functional/behavioral changes and port those
- Evaluate if the refactoring pattern improves arch_v2
- May need to be broken into smaller logical pieces

### Hardware-specific changes
For changes to specific hardware drivers:
1. Check if that hardware is supported in arch_v2
2. If yes, port to the new driver location
3. If no, note it but skip (can be added later if needed)

---

## Numbering Convention

Commit tracking files are numbered sequentially:
- Files 01-17: Legacy ports (pre-tracking system)
- Files 18+: Jan 2026 onwards

Check the highest number in `commits/` and increment.

---

## Quick Reference

```bash
# Check status
cd software && python tools/upstream_tracking.py summary

# Fetch and discover new
python tools/upstream_tracking.py add-pending --fetch

# List pending
python tools/upstream_tracking.py list --status pending

# Verify consistency
python tools/upstream_tracking.py verify
```

---

## Automation Enforcement

**The skill MUST maintain tracking automatically. Manual tracking leads to drift.**

### After EVERY port commit:
1. Update `upstream-status.yaml` to mark upstream commits as `ported`
2. Create commit tracking file in `commits/`
3. Update plan file checkboxes
4. Run `python tools/upstream_tracking.py verify`

### After EVERY skip decision:
1. Update `upstream-status.yaml` with `skipped` status, reason, and justification
2. Create skip documentation commit with `Skips-Upstream:` trailer

### Verification checks:
- All upstream commits accounted for (ported/skipped/pending)
- All "ported" entries have matching `Ports-Upstream:` trailers in git
- All "skipped" entries have justifications
- Plan file checklists match implementation status
