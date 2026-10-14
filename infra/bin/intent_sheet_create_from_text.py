#!/usr/bin/env python3
"""Create a draft Intent Sheet from copied text."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger import ledger_schema
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger


def _clean_line(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _normalize_tags(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        for piece in str(value or "").split(","):
            token = piece.strip().lower()
            if not token or token in out:
                continue
            out.append(token)
    return out


def infer_title(text: str) -> str:
    lines = [_clean_line(line) for line in str(text or "").splitlines() if _clean_line(line)]
    if not lines:
        return ""
    return lines[0][:140].strip()


def build_intent_sheet_markdown(*, uid: str, title: str, raw_text: str) -> str:
    body = _clean_line(raw_text)
    return (
        "# Intent Sheet\n\n"
        f"**UID:** {uid}\n\n"
        "## 1. Title\n\n"
        f"{title}\n\n"
        "## 2. Goal / intended outcome\n\n"
        "Capture a durable intent reference that can derive one or more implementation PISes and remain the validation anchor later.\n\n"
        "## 3. Scope / boundaries\n\n"
        "- Preserve the operator's current intent in a stable ledger document.\n"
        "- Keep enough structure to derive multiple downstream PISes without re-mining chat.\n"
        "- Do not treat this sheet itself as implementation-complete work.\n\n"
        "## 4. Expected derived work\n\n"
        "- One or more downstream PISes may be created from this sheet.\n"
        "- Final implementation proof may cite this sheet as the original intent anchor.\n\n"
        "## 5. Validation anchor expectations\n\n"
        "- Downstream PISes should link back to this Intent Sheet.\n"
        "- Final implementation review may compare delivered behavior against this sheet.\n\n"
        "## 6. Source text\n\n"
        f"> {body}\n"
    )


def create_draft_sheet(
    *,
    repo_root: Path,
    raw_text: str,
    tags: Optional[List[str]] = None,
    uid: str = "",
    project_id: Optional[str] = None,
    workstream_type: Optional[str] = None,
    scope_boundary: Optional[str] = None,
    artifact_roots: Optional[List[str]] = None,
) -> dict:
    title = infer_title(raw_text) or "Untitled Intent Sheet"
    ledger = IntentSheetLedger(repo_root=repo_root)
    result = ledger.create_sheet(
        title=title,
        intent_markdown=build_intent_sheet_markdown(uid=uid or "PENDING", title=title, raw_text=raw_text),
        tags=tags,
        uid_override=uid or None,
        project_id=project_id,
        workstream_type=workstream_type,
        scope_boundary=scope_boundary,
        artifact_roots=artifact_roots,
    )
    created = ledger.read_sheet(result["uid"])
    final_markdown = build_intent_sheet_markdown(uid=result["uid"], title=title, raw_text=raw_text)
    ledger.update_text(result["uid"], final_markdown)
    return {
        "uid": result["uid"],
        "title": title,
        "project_id": created["meta"]["project_id"],
        "path": str(ledger.get_sheet_dir(result["uid"]).relative_to(repo_root)).replace("\\", "/"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Create a draft Intent Sheet from copied text.")
    ap.add_argument("--text", default="", help="Intent text. If omitted, use --text-file.")
    ap.add_argument("--text-file", default="", help="Path to a text/markdown file to import.")
    ap.add_argument("--uid", default="", help="Optional explicit IS-... uid override.")
    ap.add_argument("--tag", action="append", default=[], help="Repeatable topic tag.")
    ap.add_argument("--project-id", default="", help="Project id, e.g. causyl or causyl.")
    ap.add_argument("--workstream-type", default="", help="Workstream type.")
    ap.add_argument("--scope-boundary", default="", help="Scope boundary.")
    ap.add_argument("--artifact-root", action="append", default=[], help="Repeatable artifact root.")
    args = ap.parse_args(argv)

    raw_text = str(args.text or "").strip()
    if not raw_text and str(args.text_file or "").strip():
        raw_text = Path(str(args.text_file)).read_text(encoding="utf-8", errors="replace").strip()
    if not raw_text:
        raise SystemExit("intent text is required")

    result = create_draft_sheet(
        repo_root=REPO_ROOT,
        raw_text=raw_text,
        tags=ledger_schema.normalize_topic_tags(_normalize_tags(args.tag), max_tags=12),
        uid=str(args.uid or "").strip(),
        project_id=str(args.project_id or "").strip() or None,
        workstream_type=str(args.workstream_type or "").strip() or None,
        scope_boundary=str(args.scope_boundary or "").strip() or None,
        artifact_roots=[str(x).strip() for x in list(args.artifact_root or []) if str(x).strip()],
    )
    print(result["uid"])
    print(result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
