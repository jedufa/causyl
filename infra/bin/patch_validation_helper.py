#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="phase-1 patch validation helper")
    parser.add_argument("--patch-path", default="")
    args = parser.parse_args(argv)
    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from experiments_shared import normalize_patch_for_phase1

    patch_path = str(args.patch_path or "").strip()
    if not patch_path:
        print(json.dumps({"ok": True, "mode": "self_check"}))
        return 0
    payload = normalize_patch_for_phase1((repo_root / patch_path).read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, **payload}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
