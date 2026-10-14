#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from experiments_shared import validate_tool_capability_registry

    result = validate_tool_capability_registry(repo_root=repo_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
