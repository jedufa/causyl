#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"

# Serialize write-side git operations across shared worktrees.
source "${SCRIPT_DIR}/git-write-lock.sh"

# git-branch-merge.sh
# Commit current changes on a patch branch, merge into main, delete patch branch.
#
# Usage:
#   ./git-branch-merge.sh --branchname patch/foo [--msg "Apply patch foo (final)"]
#   ./git-branch-merge.sh            # interactive branch picker
#
# Notes:
# - Refuses to run if you're currently on main (to avoid committing directly on main by accident).
# - Requires a clean main branch before merge.
# - Warns if patch branch is not based on main HEAD.

die() { echo "ERROR: $*" >&2; exit 1; }

BRANCHNAME=""
MSG=""

pick_branch() {
  echo "No --branchname provided. Existing patch/* branches:"
  mapfile -t branches < <(git for-each-ref --format='%(refname:short)' refs/heads/patch | sort)
  [[ ${#branches[@]} -gt 0 ]] || die "No local branches under 'patch/*'"

  local i=1
  for b in "${branches[@]}"; do
    echo "  [$i] $b"
    i=$((i+1))
  done

  echo -n "Select branch number: "
  read -r choice
  [[ "$choice" =~ ^[0-9]+$ ]] || die "Not a number"
  (( choice >= 1 && choice <= ${#branches[@]} )) || die "Out of range"
  BRANCHNAME="${branches[$((choice-1))]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branchname) BRANCHNAME="${2:-}"; shift 2 ;;
    --msg) MSG="${2:-}"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  git-branch-merge.sh --branchname patch/foo [--msg "Commit message"]
  git-branch-merge.sh    # interactive patch/* branch picker

Commits current changes on the patch branch, merges into main, deletes the patch branch.
EOF
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not inside a git repository"

[[ -n "$BRANCHNAME" ]] || pick_branch

reexec_with_git_write_lock "${SCRIPT_PATH}" "$@"

git show-ref --verify --quiet "refs/heads/$BRANCHNAME" || die "Branch not found: $BRANCHNAME"

CURRENT="$(git branch --show-current)"
[[ "$CURRENT" != "main" ]] || die "You are on main. Checkout the patch branch first (safety)."

echo "== Current branch =="
git branch --show-current
git status -sb
git log --oneline --decorate -5

# Ensure we are on the requested branch
if [[ "$CURRENT" != "$BRANCHNAME" ]]; then
  echo "== Checking out $BRANCHNAME =="
  git checkout "$BRANCHNAME"
fi

# Commit if there are changes
if [[ -n "$(git status --porcelain)" ]]; then
  if [[ -z "$MSG" ]]; then
    MSG="Apply ${BRANCHNAME} (final)"
  fi
  echo "== Committing changes =="
  git add -A
  git commit -m "$MSG"
else
  echo "== No uncommitted changes. Skipping commit. =="
fi

# Switch to main and ensure it's clean
echo "== Switching to main =="
git checkout main
[[ -z "$(git status --porcelain)" ]] || die "Main has local changes. Clean it before merging."

# Warn if branch isn't based on main HEAD (not fatal, but useful)
MAIN_HEAD="$(git rev-parse main)"
MERGE_BASE="$(git merge-base main "$BRANCHNAME")"
if [[ "$MERGE_BASE" != "$MAIN_HEAD" ]]; then
  echo "WARNING: $BRANCHNAME is not based on current main HEAD."
  echo "  main HEAD:    $MAIN_HEAD"
  echo "  merge-base:   $MERGE_BASE"
  echo "This is fine if intentional (dependent patches), otherwise you probably branched from the wrong place."
fi

echo "== Merging $BRANCHNAME into main =="
git merge "$BRANCHNAME"

echo "== Deleting branch $BRANCHNAME =="
git branch -d "$BRANCHNAME"

echo "== Done. main status =="
git status -sb
git log --oneline --decorate -5
