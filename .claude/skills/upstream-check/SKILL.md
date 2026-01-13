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

**Cutoff date: 2025-12-12** - Only commits on or after this date are tracked. Earlier commits are from before the arch_v2 divergence.

## Tracking System

This skill maintains canonical tracking in:
- `conductor/tracks/upstream-port/upstream-status.yaml` - canonical status file
- Git commit trailers (`Ports-Upstream:`, `Skips-Upstream:`) - audit trail

**The skill auto-maintains the YAML file. You never edit it manually.**

## Workflow

### Step 0: Auto-Discovery

Run the tracking tool to fetch upstream and discover new commits:

```bash
cd software && python tools/upstream_tracking.py add-pending --fetch
```

This will:
1. Fetch `upstream/master`
2. Auto-add any new upstream commits as "pending" to the status file
3. Show what was added

Then check the summary:

```bash
python tools/upstream_tracking.py summary
```

### Step 1: List Pending Commits

Show commits that need attention:

```bash
python tools/upstream_tracking.py list --status pending
```

### Step 2: Analyze Each Commit

For each pending commit, extract:
- **Hash and message**: What was done
- **Files changed**: Where it was done (old paths)
- **Diff content**: How it was done
- **Category**: Bug fix, feature, config, docs, or refactor

Use `git show <hash>` to see the full diff.

### Step 3: Map to New Architecture

Use the mapping in [MAPPING.md](MAPPING.md) to determine where changes should go in arch_v2.

### Step 4: Present Report

For each commit, present:

| Field | Value |
|-------|-------|
| **Commit** | `<hash>` - `<message>` |
| **Category** | Bug fix / Feature / Config / etc. |
| **Old files** | List of files in master |
| **New locations** | Mapped locations in arch_v2 |
| **Relevance** | HIGH / MEDIUM / LOW / SKIP |
| **Approach** | How to implement in new architecture |

### Step 5: Await Approval

Ask user which commits to port:
- **Approve** - implement the port
- **Skip** - not needed (requires justification)
- **Discuss** - need more info

### Step 6: Implement or Skip

#### For approved commits (porting):

1. Mark as in-progress by editing the YAML:
   ```yaml
   <hash>:
     status: in-progress
     started_date: YYYY-MM-DD
   ```

2. Implement equivalent change following arch_v2 patterns
3. Run tests
4. Create commit with proper format (see below)
5. Update YAML to mark as ported:
   ```yaml
   <hash>:
     status: ported
     ported_in: <our-commit-hash>
     ported_date: YYYY-MM-DD
   ```

#### For skipped commits:

1. Update YAML with skip information:
   ```yaml
   <hash>:
     status: skipped
     skip_reason: <not-applicable|superseded|already-fixed|deferred>
     skip_justification: |
       <detailed explanation of why this commit is being skipped>
     skip_date: YYYY-MM-DD
   ```

2. Create a skip documentation commit (see below)

### Step 7: Verify

After all changes, run verification:

```bash
python tools/upstream_tracking.py verify
```

## Commit Message Format

### Port Commits

```
<type>(<scope>): <description> (<upstream-hash1>, <upstream-hash2>)

<body describing what was ported and any arch_v2 adaptations>

Ports-Upstream: <hash1>
Ports-Upstream: <hash2>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

Example:
```
feat: Port Views tab to Preferences dialog (ee39b87)

Add Views tab to PreferencesDialog with plate/mosaic view settings.
Adapted for arch_v2 event-driven architecture.

Ports-Upstream: ee39b87
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

### Skip Documentation Commits

```
docs(upstream): Skip <hash> - <reason summary>

<justification>

Skips-Upstream: <hash>
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

Example:
```
docs(upstream): Skip 2a48f9b - Claude Code integration

Claude Code integration is repository-specific tooling, not
microscope functionality. arch_v2 has its own .claude/ configuration.

Skips-Upstream: 2a48f9b
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

## Architecture Rules

When porting, ensure:
- UI layer uses EventBus, never calls hardware directly
- Services use RLock for thread safety
- No circular dependencies between layers
- Extract pure functions where possible

## Skip Reasons

| Reason | When to Use |
|--------|-------------|
| `not-applicable` | Commit doesn't apply to arch_v2 (e.g., repo-specific tooling) |
| `superseded` | arch_v2 has a different/better implementation |
| `already-fixed` | arch_v2 architecture prevents the bug |
| `deferred` | Will port later, not priority now |

## Edge Cases

### Commit touches multiple areas
Break down the commit into logical parts and map each to its new location. User can approve parts individually.

### Functionality already exists differently
Compare the upstream approach with the arch_v2 implementation and recommend:
- Skip (arch_v2 already handles this better)
- Merge ideas (combine best of both)
- Replace (upstream is clearly better)

### Large refactoring commits
Skip these - arch_v2 has its own refactoring strategy. Only port the functional changes, not structural ones.

### Hardware-specific changes
For changes to specific hardware drivers:
1. Check if that hardware is supported in arch_v2
2. If yes, port to the new driver location
3. If no, note it but skip (can be added later if needed)

## Tracking Files

| File | Purpose |
|------|---------|
| `conductor/tracks/upstream-port/upstream-status.yaml` | Canonical status (auto-maintained) |
| `conductor/tracks/upstream-port/commits/` | Detailed analysis files for complex ports |
| `conductor/tracks/upstream-port/README.md` | Overview documentation |
