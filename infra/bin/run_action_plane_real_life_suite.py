from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_TESTS = [
    "tests/control_plane/e2e/test_action_plane_real_life_use_cases.py",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the action-plane real-life use-case regression suite through the e2e harness.")
    parser.add_argument("--verbose", action="store_true", help="Pass -vv to pytest.")
    parser.add_argument("tests", nargs="*", help="Optional explicit pytest test paths.")
    args = parser.parse_args()

    cmd = [sys.executable, "-m", "pytest", "-q"]
    if args.verbose:
        cmd.append("-vv")
    cmd.extend(args.tests or DEFAULT_TESTS)
    result = subprocess.run(cmd, cwd=str(repo_root()))
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
