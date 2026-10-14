#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import stat
import subprocess
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git_path(name: str) -> Path:
    cp = subprocess.run(
        ["git", "rev-parse", "--git-path", name],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    path = str(cp.stdout or "").strip()
    if not path:
        raise SystemExit(f"failed to resolve git path for {name}")
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = (REPO_ROOT / resolved).resolve()
    return resolved


def _hook_body(mode: str) -> str:
    args = '"$@"' if mode == "commit-msg" else ""
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'repo_root="$(git rev-parse --show-toplevel)"\n'
        'cd "$repo_root"\n'
        f'exec python tools/bin/plane_critical_guard.py --mode {mode} {args}\n'
    )


def install_hooks() -> Dict[str, str]:
    hooks_dir = _git_path("hooks")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, str] = {}
    for mode in ("pre-commit", "commit-msg", "pre-push"):
        path = hooks_dir / mode
        path.write_text(_hook_body(mode), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        out[mode] = str(path)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the plane critical-path git hooks.")
    ap.parse_args(argv)
    installed = install_hooks()
    for mode, path in installed.items():
        print(f"{mode}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
