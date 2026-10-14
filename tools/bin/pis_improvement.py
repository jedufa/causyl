#!/usr/bin/env python3
"""List and launch shared PIS-improvement entrypoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import pis_improvement_entrypoints as entrypoints


def _print_human_list(payload: dict) -> None:
    for index, row in enumerate(payload.get("recent_candidates") or [], start=1):
        print(f"{index}. {row['uid']} [{row['status']}] {row['title']}")


def _print_human_launch(payload: dict) -> None:
    source = dict(payload.get("source_pis") or {})
    recommendation = dict(payload.get("recommendation") or {})
    print(payload.get("operator_summary") or "No operator summary returned.")
    if source:
        print(f"Source: {source.get('uid')} [{source.get('status')}] {source.get('title')}")
    if payload.get("session_id"):
        print(f"Session: {payload['session_id']}")
    if recommendation.get("outcome"):
        print(f"Recommendation: {recommendation['outcome']}")
    for name, ref in sorted(dict(payload.get("artifacts") or {}).items()):
        print(f"{name}: {ref}")


def main(argv: Optional[List[str]] = None, *, repo_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shared CLI for deterministic PIS improvement entrypoints.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list-recent", help="List recent PIS candidates.")
    list_parser.add_argument("--limit", type=int, default=entrypoints.DEFAULT_RECENT_LIMIT)
    list_parser.add_argument("--json", action="store_true")

    launch_parser = sub.add_parser("launch", help="Launch a shared PIS improvement session.")
    launch_parser.add_argument("--request-id", required=True)
    launch_parser.add_argument("--pis", required=True, dest="source_pis_uid")
    launch_parser.add_argument("--candidate", action="append", default=[], dest="selected_candidate_uids")
    launch_parser.add_argument("--limit", type=int, default=entrypoints.DEFAULT_RECENT_LIMIT)
    launch_parser.add_argument("--session-id", default="")
    launch_parser.add_argument("--title", default="")
    launch_parser.add_argument("--iteration-cap", type=int, default=entrypoints.DEFAULT_ITERATION_CAP)
    launch_parser.add_argument("--overwrite", action="store_true")
    launch_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(repo_root) if repo_root is not None else REPO_ROOT

    if args.command == "list-recent":
        request = {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "list_recent",
            "limit": int(args.limit),
        }
    else:
        request = {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": str(args.request_id),
            "source_pis_uid": str(args.source_pis_uid),
            "selected_candidate_uids": list(args.selected_candidate_uids or []),
            "limit": int(args.limit),
            "session_id": str(args.session_id or ""),
            "title": str(args.title or ""),
            "policy_overrides": {"iteration_cap": int(args.iteration_cap)},
            "overwrite": bool(args.overwrite),
        }

    try:
        payload = entrypoints.run_request(request, repo_root=root)
    except ValueError as exc:
        payload = entrypoints.error_result(
            action=str(request.get("action") or ""),
            request_id=str(request.get("request_id") or ""),
            error_code="invalid_request",
            message=str(exc),
        )
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["operator_summary"], file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    elif args.command == "list-recent":
        _print_human_list(payload)
    else:
        _print_human_launch(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
