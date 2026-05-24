#!/usr/bin/env bash
# Restore machine-specific config files that were untracked in PR #546.
#
# Those files (filter_wheels.yaml, illumination_channel_config.yaml,
# Xeryon_settings.txt) are now gitignored. A `git pull` crossing the
# untrack commit deletes any unmodified local copy. Run this once after
# such a pull to restore the pre-removal versions from git history.
# Subsequent pulls leave the files alone because they are gitignored.

set -euo pipefail

REMOVED_AT=af71d2ef2ccb2ba30a4b5e6549556b8d272a9cad
REPO_ROOT="$(git rev-parse --show-toplevel)"

FILES=(
  "software/control/Xeryon_settings.txt"
  "software/machine_configs/filter_wheels.yaml"
  "software/machine_configs/illumination_channel_config.yaml"
)

cd "$REPO_ROOT"

for f in "${FILES[@]}"; do
  if [[ -e "$f" ]]; then
    echo "skip    $f (already exists)"
    continue
  fi
  if ! git cat-file -e "${REMOVED_AT}^:${f}" 2>/dev/null; then
    echo "missing $f (not in git history; copy the .example template manually)"
    continue
  fi
  git show "${REMOVED_AT}^:${f}" > "$f"
  echo "restore $f"
done
