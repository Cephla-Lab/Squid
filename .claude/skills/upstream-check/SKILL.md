---
name: upstream-check
description: Analyzes upstream commits on master that need porting to arch_v2. Performs semantic analysis of each commit, maps changes to new architecture locations, and presents a structured report for approval before implementation.
allowed-tools: Bash(git fetch:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Read, Grep, Glob, AskUserQuestion
---

# Upstream Check Skill

Analyzes commits on a source branch not in `arch_v2` and helps port them semantically to the new 3-layer architecture.

## When to Use

- Periodically check what upstream changes exist
- Before merging or rebasing
- When bugs are reported that might have upstream fixes

## Workflow

### Step 0: Select Source

Ask the user which source branch to check for commits:

- **upstream/master** - Cephla-Lab/Squid main repository (recommended for pulling in official changes)
- **origin/master** - User's fork master branch

Use the AskUserQuestion tool to prompt for this choice.

### Step 1: Fetch and List

Based on the user's selection, fetch and list commits:

```bash
# For upstream/master:
git fetch upstream master
git log upstream/master --not arch_v2 --oneline

# For origin/master:
git fetch origin master
git log origin/master --not arch_v2 --oneline
```

### Step 2: Analyze Each Commit

For each commit, extract:
- **Hash and message**: What was done
- **Files changed**: Where it was done (old paths)
- **Diff content**: How it was done
- **Category**: Bug fix, feature, config, docs, or refactor

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
- **Skip** - not needed
- **Discuss** - need more info

### Step 6: Implement (if approved)

For approved commits:
1. Implement equivalent change in arch_v2
2. Follow 3-layer architecture patterns
3. Run tests
4. Create commit referencing original

## Architecture Rules

When porting, ensure:
- UI layer uses EventBus, never calls hardware directly
- Services use RLock for thread safety
- No circular dependencies between layers
- Extract pure functions where possible

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

## Tracking

Maintain a log of reviewed/ported commits in `conductor/tracks/upstream-port-log.md` to prevent re-reviewing.

| Upstream Commit | Date Reviewed | Action | arch_v2 Commit |
|-----------------|---------------|--------|----------------|
| abc1234 | 2025-01-15 | Ported | xyz7890 |
| def5678 | 2025-01-15 | Skipped | - |
