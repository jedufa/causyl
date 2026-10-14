#!/usr/bin/env bash
# tools/diagnostics/verify_p0a_determinism_gate.sh
#
# Purpose:
#   Quick “did I reintroduce unauthorized randomness?” check for P0-A.
#
# Usage:
#   bash tools/diagnostics/verify_p0a_determinism_gate.sh
#   bash tools/diagnostics/verify_p0a_determinism_gate.sh --with-tests
#   bash tools/diagnostics/verify_p0a_determinism_gate.sh --with-canary
#   bash tools/diagnostics/verify_p0a_determinism_gate.sh --with-tests --with-canary
#
# Exit codes:
#   0  OK
#   2  governance missing
#   3  scanner failed (lock mismatch / violations / runtime error)
#   4  pytest failed
#   5  canary failed (scanner didn't catch it)

set -euo pipefail

WITH_TESTS=0
WITH_CANARY=0

for arg in "$@"; do
  case "$arg" in
    --with-tests) WITH_TESTS=1 ;;
    --with-canary) WITH_CANARY=1 ;;
    -h|--help)
      sed -n '1,80p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg"
      exit 1
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  echo "ERROR: Not in a git repo. Run from inside the Emergence repo."
  exit 1
fi
cd "$REPO_ROOT"

echo "======================================================"
echo "P0-A Determinism Gate Check"
echo "repo: $REPO_ROOT"
echo "======================================================"

echo
echo "1) Governance presence"
if [[ ! -f governance/determinism_allowlist.json || ! -f governance/determinism_allowlist.lock.json ]]; then
  echo "ERROR: Missing governance files:"
  [[ ! -f governance/determinism_allowlist.json ]] && echo " - governance/determinism_allowlist.json"
  [[ ! -f governance/determinism_allowlist.lock.json ]] && echo " - governance/determinism_allowlist.lock.json"
  exit 2
fi
echo "OK - governance files present"

echo
echo "2) Static RNG scan (must be lock_ok=True and violations_total=0)"
python - <<'PY'
from tools.diagnostics.static_rng_scan import scan_repo
from pathlib import Path
import json

hits, summary = scan_repo(Path("."))
print(json.dumps(summary, indent=2, sort_keys=True))

lock_ok = summary.get("lock_ok") is True
violations_total = int(summary.get("violations_total") or 0)

if not lock_ok:
    raise SystemExit("FAIL: lock_ok is False (allowlist lock missing/mismatch)")
if violations_total != 0:
    raise SystemExit(f"FAIL: violations_total={violations_total} (non-allowlisted RNG found)")

print("OK - determinism gate satisfied (no non-allowlisted RNG; lock ok)")
PY

if [[ "$WITH_TESTS" -eq 1 ]]; then
  echo
  echo "3) CI test (pytest)"
  pytest -q tests/determinism/test_static_rng_scan.py
  echo "OK - pytest gate passed"
fi

if [[ "$WITH_CANARY" -eq 1 ]]; then
  echo
  echo "4) Canary (prove scanner catches new RNG usage)"
  # Safety: refuse canary if working tree is dirty (so we don't leave junk behind)
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Working tree is dirty. Commit/stash first before running canary."
    echo "       (Canary creates a temp file under engine/ and then deletes it.)"
    exit 1
  fi

  CANARY="engine/__det_canary_forbidden_rng__.py"
  trap 'rm -f "$CANARY"' EXIT

  cat > "$CANARY" <<'PY'
import uuid
def _canary():
    return uuid.uuid4()
PY

  python - <<'PY'
from tools.diagnostics.static_rng_scan import scan_repo
from pathlib import Path

_, summary = scan_repo(Path("."))
viol = int(summary.get("violations_total") or 0)
assert viol > 0, f"FAIL: Canary did not trigger violations_total>0; summary={summary}"
print("OK - canary triggered forbidden RNG detection")
PY

  # trap handles cleanup
  echo "OK - canary passed and cleaned up"
fi

echo
echo "======================================================"
echo "PASS: P0-A determinism gate is healthy"
echo "======================================================"