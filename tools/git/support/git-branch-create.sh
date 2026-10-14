#!/usr/bin/env bash
set -euo pipefail

# git-branch-create.sh
# Create a patch work branch from main, with guardrails.
#
# Usage:
#   ./git-branch-create.sh --branchname patch/foo
#
# Notes:
# - Refuses to proceed if the working tree is dirty.
# - Always branches from main.

die() { echo "ERROR: $*" >&2; exit 1; }

BRANCHNAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branchname) BRANCHNAME="${2:-}"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  git-branch-create.sh --branchname patch/foo

Creates and checks out a new branch from main. Refuses if working tree is dirty.
EOF
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ -n "$BRANCHNAME" ]] || die "--branchname is required"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not inside a git repository"

echo "== Current status =="
git branch --show-current
git status -sb

# must be clean
[[ -z "$(git status --porcelain)" ]] || die "Working tree is dirty. Commit/stash/reset first."

# make sure main exists locally
git show-ref --verify --quiet refs/heads/main || die "Local branch 'main' not found."

echo "== Switching to main =="
git checkout main

echo "== Creating branch: $BRANCHNAME =="
git show-ref --verify --quiet "refs/heads/$BRANCHNAME" && die "Branch already exists: $BRANCHNAME"

git checkout -b "$BRANCHNAME"

echo "== Done. You are now on: =="
git branch --show-current
git log --oneline --decorate -5
