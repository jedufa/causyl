#!/usr/bin/env python3
"""Create a lightweight draft Idea Note from copied text."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger import ledger_schema
from infra.webui.ledger.idea_note_manager import IdeaNoteLedger


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


def build_idea_note_markdown(*, uid: str, title: str, raw_text: str) -> str:
    body = _clean_line(raw_text)
    return (
        "# Idea Note\n\n"
        f"**UID:** {uid}\n\n"
        "## 1. Title\n\n"
        f"{title}\n\n"
        "## 2. Why keep this\n\n"
        "Preserve a rough idea without pretending it is ready to become an Intent Sheet or implementation work.\n\n"
        "## 3. Current shape\n\n"
        "- exploratory\n"
        "- not yet a contract\n"
        "- may later be promoted, merged, or discarded\n\n"
        "## 4. Raw note\n\n"
        f"> {body}\n"
    )


def create_draft_note(
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
    title = infer_title(raw_text) or "Untitled Idea Note"
    ledger = IdeaNoteLedger(repo_root=repo_root)
    result = ledger.create_note(
        title=title,
        idea_markdown=build_idea_note_markdown(uid=uid or "PENDING", title=title, raw_text=raw_text),
        tags=tags,
        uid_override=uid or None,
        project_id=project_id,
        workstream_type=workstream_type,
        scope_boundary=scope_boundary,
        artifact_roots=artifact_roots,
    )
    final_markdown = build_idea_note_markdown(uid=result["uid"], title=title, raw_text=raw_text)
    ledger.update_text(result["uid"], final_markdown)
    return {
        "uid": result["uid"],
        "title": title,
        "path": str(ledger.get_note_dir(result["uid"]).relative_to(repo_root)).replace("\\", "/"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Create a draft Idea Note from copied text.")
    ap.add_argument("--text", default="", help="Idea text. If omitted, use --text-file.")
    ap.add_argument("--text-file", default="", help="Path to a text/markdown file to import.")
    ap.add_argument("--uid", default="", help="Optional explicit IDEA-... uid override.")
    ap.add_argument("--tag", action="append", default=[], help="Repeatable topic tag.")
    ap.add_argument("--project-id", default="", help="Project id, e.g. causyl or causyl.")
    ap.add_argument("--workstream-type", default="shared_capability", help="Workstream type.")
    ap.add_argument("--scope-boundary", default="cross-project", help="Scope boundary.")
    ap.add_argument("--artifact-root", action="append", default=[], help="Repeatable artifact root.")
    args = ap.parse_args(argv)

    raw_text = str(args.text or "").strip()
    if not raw_text and str(args.text_file or "").strip():
        raw_text = Path(str(args.text_file)).read_text(encoding="utf-8", errors="replace").strip()
    if not raw_text:
        raise SystemExit("idea text is required")

    result = create_draft_note(
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
