#!/usr/bin/env bash
# File: tools/bootstrap_worktree.sh
# Purpose:
#   Deterministically bootstrap a git worktree folder for Causyl baselines:
#     - create/verify a Python venv inside the worktree
#     - install runtime requirements from engine/requirements.txt
#     - write a small machine-readable bootstrap record for forensics
#
# Design rules:
#   - No engine/harness changes.
#   - No Phase-0 contract changes.
#   - Avoid ad-hoc installs: installs are always derived from the repo requirements file.
#   - Safe defaults, explicit overrides via env vars.
#
# Usage:
#   tools/bootstrap_worktree.sh --worktree_dir <path>
#
# Environment overrides:
#   CAUSYL_REQUIREMENTS_PATH   Relative path (inside worktree) to requirements.
#                               Default: engine/requirements.txt
#   CAUSYL_VENV_DIRNAME        Venv folder name inside worktree.
#                               Default: .venv
#   CAUSYL_PIP_CACHE_DIR       Shared pip cache (speeds up baseline creation).
#                               Default: /mnt/d/WSL/pip_cache (created if missing)
#   CAUSYL_BOOTSTRAP_FORCE     If '1', reinstall even if requirements hash matches.
#                               Default: 0
#   CAUSYL_BOOTSTRAP_PYTHON    Python executable to create venv.
#                               Default: python3 found on PATH
#   CAUSYL_REQUIRED_PYTHON_PREFIX  If set (e.g. '3.12.'), bootstrap fails if
#                                    the created venv's python version does not
#                                    start with this prefix.
#                                    Default: 3.12.
#   CAUSYL_REQUIRED_PYTHON_MM  Required major.minor (e.g. 3.12). If set and
#                               the created venv is not that major.minor, fail.
#                               Default: 3.12
#
# Output:
#   <worktree>/.bootstrap/worktree_bootstrap.json
#
set -euo pipefail

_die() { echo "Error: $*" >&2; exit 1; }

_usage() {
  cat >&2 <<'USAGE'
Usage:
  tools/bootstrap_worktree.sh --worktree_dir <path>
USAGE
}

_abspath() {
  python3 - <<'PY' "$1"
import os,sys
print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
}

_sha256_file() {
  local f="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$f" | awk '{print $1}'
  else
    python3 - <<'PY' "$f"
import hashlib,sys
p=sys.argv[1]
h=hashlib.sha256()
with open(p,'rb') as fp:
  for chunk in iter(lambda: fp.read(1024*1024), b''):
    h.update(chunk)
print(h.hexdigest())
PY
  fi
}

_json_escape() {
  python3 - <<'PY' "$1"
import json,sys
print(json.dumps(sys.argv[1]))
PY
}

WORKTREE_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worktree_dir)
      WORKTREE_DIR="${2:-}"; shift 2 ;;
    -h|--help)
      _usage; exit 0 ;;
    *)
      _die "Unknown arg: $1" ;;
  esac
done

[[ -n "$WORKTREE_DIR" ]] || { _usage; _die "--worktree_dir is required"; }
WORKTREE_DIR="$(_abspath "$WORKTREE_DIR")"
[[ -d "$WORKTREE_DIR" ]] || _die "worktree_dir not found: $WORKTREE_DIR"

# ------------------------------------------------------------------------------
# Shared pip cache default
# ------------------------------------------------------------------------------
# Reproducibility note:
# - This only caches downloaded wheels/sdists.
# - It does NOT share installed packages between worktrees.
# - Venv isolation remains intact.
if [[ -z "${CAUSYL_PIP_CACHE_DIR:-}" ]]; then
  CAUSYL_PIP_CACHE_DIR="/mnt/d/WSL/pip_cache"
fi

export PIP_CACHE_DIR="$CAUSYL_PIP_CACHE_DIR"
mkdir -p "$PIP_CACHE_DIR"

REQ_REL="${CAUSYL_REQUIREMENTS_PATH:-engine/requirements.txt}"
REQ_FILE="$WORKTREE_DIR/$REQ_REL"
[[ -f "$REQ_FILE" ]] || _die "Requirements not found: $REQ_FILE"

VENV_NAME="${CAUSYL_VENV_DIRNAME:-.venv}"
VENV_DIR="$WORKTREE_DIR/$VENV_NAME"
PY_CREATE="${CAUSYL_BOOTSTRAP_PYTHON:-python3}"
FORCE="${CAUSYL_BOOTSTRAP_FORCE:-0}"

# Default to a shared Windows-drive pip cache in WSL. This is a speed optimization
# only: it does not change what gets installed, it only avoids re-downloading.
if [[ -z "${CAUSYL_PIP_CACHE_DIR:-}" ]]; then
  CAUSYL_PIP_CACHE_DIR="/mnt/d/WSL/pip_cache"
fi

# Default interpreter policy: we want deterministic farms, not "whatever python3
# became this month". You can override these if you really want chaos.
REQ_PY_MM="${CAUSYL_REQUIRED_PYTHON_MM:-3.12}"
REQ_PY_PREFIX="${CAUSYL_REQUIRED_PYTHON_PREFIX:-${REQ_PY_MM}.}"
export PIP_CACHE_DIR="$CAUSYL_PIP_CACHE_DIR"
mkdir -p "$PIP_CACHE_DIR"

BOOT_DIR="$WORKTREE_DIR/.bootstrap"
BOOT_JSON="$BOOT_DIR/worktree_bootstrap.json"
mkdir -p "$BOOT_DIR"

REQ_SHA="$(_sha256_file "$REQ_FILE")"

NEED_INSTALL="1"
if [[ -f "$BOOT_JSON" && "$FORCE" != "1" ]]; then
  # If the recorded requirements hash matches, we can skip reinstall.
  REC_SHA="$(python3 - <<'PY' "$BOOT_JSON"
import json,sys
p=sys.argv[1]
try:
  d=json.load(open(p,'r',encoding='utf-8'))
  print(d.get('requirements_sha256',''))
except Exception:
  print('')
PY
)"
  if [[ -n "$REC_SHA" && "$REC_SHA" == "$REQ_SHA" && -x "$VENV_DIR/bin/python" ]]; then
    NEED_INSTALL="0"
  fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "== Creating venv =="
  "$PY_CREATE" -m venv "$VENV_DIR"
fi

PY_EXE="$VENV_DIR/bin/python"
[[ -x "$PY_EXE" ]] || _die "Venv python not found/executable: $PY_EXE"

# Enforce interpreter policy (farm-safe determinism).
PY_VSTR="$($PY_EXE -c "import sys; print(sys.version.split()[0])" | tr -d '\n' || true)"
PY_MM="$($PY_EXE -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" | tr -d '\n' || true)"
if [[ -z "$PY_VSTR" || -z "$PY_MM" ]]; then
  _die "Could not determine venv python version (python executable: $PY_EXE)"
fi
if [[ "$PY_MM" != "$REQ_PY_MM" ]]; then
  _die "Python major.minor mismatch: required $REQ_PY_MM, got $PY_MM (executable: $PY_EXE). Set CAUSYL_BOOTSTRAP_PYTHON to a $REQ_PY_MM interpreter."
fi
if [[ "$PY_VSTR" != ${REQ_PY_PREFIX}* ]]; then
  _die "Python version prefix mismatch: required prefix '${REQ_PY_PREFIX}', got '${PY_VSTR}' (executable: $PY_EXE)."
fi

echo "== Using pip cache =="
echo "$PIP_CACHE_DIR"

if [[ "$NEED_INSTALL" == "1" ]]; then
  echo "== Installing requirements =="
  "$PY_EXE" -m pip install -U pip
  "$PY_EXE" -m pip install -r "$REQ_FILE"
else
  echo "== Requirements already satisfied (hash match), skipping install =="
fi

PIP_V="$($PY_EXE -m pip -V | tr -d '\n' || true)"
PY_V="$($PY_EXE -c "import sys; print(sys.version.replace('\\n',' '))" | tr -d '\n' || true)"

# Verify PyYAML (harness runtime dependency)
YAML_V="$($PY_EXE -c "import yaml; print(getattr(yaml,'__version__','unknown'))" 2>/dev/null || true)"
if [[ -z "$YAML_V" ]]; then
  _die "PyYAML not importable in venv. This usually means requirements did not install correctly from $REQ_REL."
fi

NOW_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

cat > "$BOOT_JSON" <<JSON
{
  "schema_version": "v1",
  "created_at_utc": "${NOW_UTC}",
  "worktree_dir": "${WORKTREE_DIR}",
  "venv_dir": "${VENV_DIR}",
  "python_executable": "${PY_EXE}",
  "python_version": ${PY_V@Q},
  "python_required_prefix": ${REQ_PY_PREFIX@Q},
  "pip_version": ${PIP_V@Q},
  "yaml_version": ${YAML_V@Q},
  "requirements_path": "${REQ_REL}",
  "requirements_sha256": "${REQ_SHA}",
  "pip_cache_dir": "${PIP_CACHE_DIR}",
  "install_performed": ${NEED_INSTALL}
}
JSON

echo "== Bootstrap record =="
echo "$BOOT_JSON"
