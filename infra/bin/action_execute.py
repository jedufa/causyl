#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate, route, or execute a phase-2 action request.")
    parser.add_argument("payload", nargs="?", help="Optional path to a JSON payload file. Reads stdin when omitted.")
    parser.add_argument("--execute", action="store_true", help="Execute the resolved playbook instead of returning only the routing plan.")
    args = parser.parse_args()
    raw = Path(args.payload).read_text(encoding="utf-8") if args.payload else sys.stdin.read()
    if not raw.strip():
        raise SystemExit("empty payload")
    payload = json.loads(raw)
    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from experiments_shared import dispatch_phase2_action_request, execute_phase2_action_request

    execute = bool(args.execute or payload.get("execute") or str(payload.get("dispatch_mode") or "").strip().lower() == "execute")
    if execute:
        result = execute_phase2_action_request(payload, repo_root=repo_root)
    else:
        result = dispatch_phase2_action_request(payload, repo_root=repo_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
