#!/usr/bin/env python3
"""tools/verify/phase4_multi_seed_determinism_matrix.py

Phase 4 determinism hardening: multi-seed determinism matrix harness.

Runs a small CI-friendly determinism experiment for multiple seeds by invoking:
  phase3_resume_determinism_e2e.sh --ci_profile

Asserts (hard fail per seed):
  - E2E script returns 0 (meaning: metrics/behaviors/fingerprint/state-dumps/membership all passed)

Defaults are tuned for <10s CPU on typical dev machines PER SEED. In CI you can:
  - run a single seed in PR checks (e.g., 42)
  - run the full matrix (1,7,42,1234) in nightly

Usage:
  python -m tools.verify.phase4_multi_seed_determinism_matrix \
    --seeds 1,7,42,1234 --gen 1 --steps 30 --pop 32 --runs_dir runs

Exit codes:
  0 = PASS
  1 = FAIL (first seed mismatch)
  2 = INFRA/usage
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Iterable, List


def _run(cmd: List[str], env: dict) -> None:
    """Run a command, raising on non-zero."""
    subprocess.run(cmd, env=env, check=True)


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=str, default="1,7,42,1234", help="Comma-separated seeds")
    p.add_argument("--gen", type=int, default=1)
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--pop", type=int, default=32)
    p.add_argument("--runs_dir", type=str, default="runs")
    args = p.parse_args(list(argv) if argv is not None else None)

    seeds = [int(s.strip()) for s in str(args.seeds).split(",") if s.strip()]
    if not seeds:
        print("[MATRIX] no seeds provided", file=sys.stderr)
        return 2

    repo_root = os.getcwd()
    script = os.path.join(repo_root, "phase3_resume_determinism_e2e.sh")
    if not os.path.exists(script):
        print(f"[MATRIX] missing script: {script}", file=sys.stderr)
        return 2

    base_env = dict(os.environ)
    # Force strict determinism in the stack.
    base_env["DEBUG_DETERMINISM"] = "1"
    base_env["PYTHONHASHSEED"] = "0"

    for seed in seeds:
        cmd = [
            script,
            "--ci_profile",
            "--seed",
            str(seed),
            "--gen",
            str(int(args.gen)),
            "--steps",
            str(int(args.steps)),
            "--pop",
            str(int(args.pop)),
            "--runs_dir",
            str(args.runs_dir),
        ]
        try:
            _run(cmd, env=base_env)
        except subprocess.CalledProcessError as e:
            print(f"[MATRIX] FAIL seed={seed} rc={e.returncode}", file=sys.stderr)
            return 1

    print(f"[MATRIX] PASS seeds={seeds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
