#!/usr/bin/env bash
set -euo pipefail

# git-branch-revert.sh
# Cancel a patch branch by switching to main and deleting the branch.
#
# Usage:
#   ./git-branch-revert.sh --branchname patch/foo
#   ./git-branch-revert.sh    # interactive patch/* branch picker

die() { echo "ERROR: $*" >&2; exit 1; }

BRANCHNAME=""

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
    -h|--help)
      cat <<'EOF'
Usage:
  git-branch-revert.sh --branchname patch/foo
  git-branch-revert.sh    # interactive patch/* branch picker

Switches to main and deletes the patch branch (hard delete).
EOF
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not inside a git repository"

[[ -n "$BRANCHNAME" ]] || pick_branch
git show-ref --verify --quiet "refs/heads/$BRANCHNAME" || die "Branch not found: $BRANCHNAME"

echo "== Current branch/status =="
git branch --show-current
git status -sb

# If you're on the branch being deleted, get off it first
CURRENT="$(git branch --show-current)"
if [[ "$CURRENT" == "$BRANCHNAME" ]]; then
  [[ -z "$(git status --porcelain)" ]] || die "Working tree is dirty. Clean it before cancelling the branch."
  git checkout main
else
  git checkout main
fi

echo "== Deleting branch (cancel): $BRANCHNAME =="
git branch -D "$BRANCHNAME"

echo "== Done. =="
git status -sb
git log --oneline --decorate -5
