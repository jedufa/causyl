#!/usr/bin/env python3
"""Promote a ready Idea Note into an Intent Sheet with durable provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger.idea_note_manager import IdeaNoteLedger


def promote_note(*, repo_root: Path, uid: str, note: str = "") -> dict:
    ledger = IdeaNoteLedger(repo_root=repo_root)
    result = ledger.promote_to_intent_sheet(uid, note=note)
    result["idea_note_path"] = str(ledger.get_note_dir(uid).relative_to(repo_root)).replace("\\", "/")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Promote a ready Idea Note into an Intent Sheet.")
    ap.add_argument("uid", help="IDEA-... uid to promote.")
    ap.add_argument("--note", default="", help="Optional short operator note for the promotion event.")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    args = ap.parse_args(argv)

    result = promote_note(repo_root=REPO_ROOT, uid=str(args.uid).strip(), note=str(args.note or "").strip())
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["intent_sheet_uid"])
        print(result["intent_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
