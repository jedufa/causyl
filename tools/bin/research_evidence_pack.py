#!/usr/bin/env python3
"""Validate and author research evidence pack files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import research_evidence_pack_authoring as authoring


def _load_json(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and author research evidence pack files")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate an evidence pack JSON file")
    validate.add_argument("pack_path")
    validate.add_argument("--receipt-path", default="")

    create = sub.add_parser("create", help="Create a validated evidence pack JSON file from a source JSON object")
    create.add_argument("pack_path")
    create.add_argument("--from-json", required=True)
    create.add_argument("--receipt-path", default="")
    create.add_argument("--overwrite", action="store_true")

    append = sub.add_parser("append", help="Append validated records from a JSON object keyed by pack section")
    append.add_argument("pack_path")
    append.add_argument("--from-json", required=True)
    append.add_argument("--receipt-path", default="")

    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    receipt_path = Path(args.receipt_path) if str(getattr(args, "receipt_path", "") or "").strip() else None
    try:
        if args.command == "validate":
            result = authoring.validate_evidence_pack_file(
                Path(args.pack_path),
                receipt_path=receipt_path,
                repo_root=REPO_ROOT,
            )
        elif args.command == "create":
            result = authoring.create_evidence_pack_file(
                Path(args.pack_path),
                _load_json(Path(args.from_json)),
                receipt_path=receipt_path,
                repo_root=REPO_ROOT,
                overwrite=bool(args.overwrite),
            )
        elif args.command == "append":
            payload = _load_json(Path(args.from_json))
            result = authoring.append_evidence_pack_records(
                Path(args.pack_path),
                receipt_path=receipt_path,
                repo_root=REPO_ROOT,
                **payload,
            )
        else:
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True) + "\n")
        return 2

    stream = sys.stdout if result.get("ok") else sys.stderr
    stream.write(json.dumps(authoring.compact_result_for_output(result), indent=2, sort_keys=True) + "\n")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
