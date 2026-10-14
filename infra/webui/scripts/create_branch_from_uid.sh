#!/usr/bin/env bash
set -euo pipefail

# create_branch_from_uid.sh
# Usage:
#   create_branch_from_uid.sh --uid <PIS-...> --slug <slug>

UID=""
SLUG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uid)
      UID="${2:-}"
      shift 2
      ;;
    --slug)
      SLUG="${2:-}"
      shift 2
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$UID" ]]; then
  echo "missing --uid" >&2
  exit 2
fi

if [[ ! "$UID" =~ ^PIS-[0-9]{8}-[0-9]{6}Z-[A-Z0-9]{4}$ ]]; then
  echo "invalid uid format" >&2
  exit 2
fi

if [[ -z "$SLUG" ]]; then
  SLUG="change"
fi

SAFE_SLUG="$(echo "$SLUG" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g; s/-+/-/g')"
if [[ -z "$SAFE_SLUG" ]]; then
  SAFE_SLUG="change"
fi

BRANCH="pis/${UID}/${SAFE_SLUG}"
git checkout -b "$BRANCH"
echo "$BRANCH"
