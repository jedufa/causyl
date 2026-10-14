#!/usr/bin/env python3
"""Render a merged chronological transcript from agent_chat JSONL files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from infra.webui.ledger import ledger_storage


def _parse_utc(value: str) -> float:
    s = str(value or "").strip()
    if not s:
        return 0.0
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    out: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _collect_rows(chat_dir: Path, *, include_inbox: bool) -> List[Tuple[float, int, Dict[str, Any], str]]:
    files = sorted(chat_dir.glob("session__*.jsonl"))
    if include_inbox:
        files += sorted(chat_dir.glob("inbox__*.jsonl"))
    rows: List[Tuple[float, int, Dict[str, Any], str]] = []
    seq = 0
    for p in files:
        for obj in _read_jsonl(p):
            seq += 1
            utc = str(obj.get("utc") or "")
            ts = _parse_utc(utc)
            rows.append((ts, seq, obj, p.name))
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows


def _role_label(row: Dict[str, Any], filename: str) -> str:
    if filename.startswith("inbox__"):
        to_role = str(row.get("to_role") or filename.replace("inbox__", "").replace(".jsonl", ""))
        from_role = str(row.get("from_role") or "unknown")
        return f"inbox:{from_role}->{to_role}"
    panel = str(row.get("role_panel") or filename.replace("session__", "").replace(".jsonl", ""))
    sender = str(row.get("sender") or "unknown")
    return f"{panel}/{sender}"


def _render(rows: Iterable[Tuple[float, int, Dict[str, Any], str]], *, max_chars: int) -> str:
    chunks: List[str] = []
    lim = max(200, int(max_chars))
    for _, _, obj, fname in rows:
        utc = str(obj.get("utc") or "-")
        label = _role_label(obj, fname)
        status = str(obj.get("status") or "")
        status_txt = f" status={status}" if status else ""
        content = str(obj.get("content") or "").strip()
        if "\\n" in content and "\n" not in content:
            content = content.replace("\\n", "\n")
        if not content:
            content = "(no content)"
        if len(content) > lim:
            content = content[:lim] + "\n...[truncated]"
        chunks.append(f"[{utc}] {label}{status_txt}\n{content}")
    return "\n\n".join(chunks)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Merge agent chat files into one chronological transcript")
    ap.add_argument("chat_dir", nargs="?", default="", help="Path to agent_chat or agent_chat_TEST directory")
    ap.add_argument("--pis-uid", default="", help="Ledger PIS UID (e.g. PIS-20260305-130935Z-34B2)")
    ap.add_argument(
        "--chat-subdir",
        default="agent_chat",
        help="Artifacts chat subdir when using --pis-uid (default: agent_chat; e.g. agent_chat_TEST)",
    )
    ap.add_argument(
        "--repo-root",
        default=".",
        help="Repo root used with --pis-uid (default: current directory)",
    )
    ap.add_argument("--include-inbox", action="store_true", help="Include inbox__*.jsonl messages")
    ap.add_argument("--max-chars", type=int, default=4000, help="Max content chars per message")
    ap.add_argument("--output", default="", help="Write transcript to file instead of stdout")
    return ap


def _resolve_chat_dir(*, chat_dir: str, pis_uid: str, chat_subdir: str, repo_root: str) -> Path:
    direct = str(chat_dir or "").strip()
    uid = str(pis_uid or "").strip()
    if direct and uid:
        raise SystemExit("Provide either chat_dir or --pis-uid, not both")
    if direct:
        return Path(direct).expanduser().resolve()
    if not uid:
        raise SystemExit("Provide chat_dir or --pis-uid")
    rr = Path(str(repo_root or ".")).expanduser().resolve()
    items_root = (rr / "governance" / "ledger" / "items").resolve()
    item_dir = ledger_storage.item_dir_for_uid(items_root, uid)
    if item_dir is None:
        raise SystemExit(f"No ledger item found for UID: {uid}")
    return (item_dir / "artifacts" / str(chat_subdir or "agent_chat")).resolve()


def main(argv: List[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    chat_dir = _resolve_chat_dir(
        chat_dir=str(args.chat_dir or ""),
        pis_uid=str(args.pis_uid or ""),
        chat_subdir=str(args.chat_subdir or "agent_chat"),
        repo_root=str(args.repo_root or "."),
    )
    if not chat_dir.exists() or not chat_dir.is_dir():
        sys.stderr.write(f"error: chat_dir not found: {chat_dir}\n")
        return 2
    rows = _collect_rows(chat_dir, include_inbox=bool(args.include_inbox))
    text = _render(rows, max_chars=int(args.max_chars))
    if str(args.output or "").strip():
        outp = Path(str(args.output)).expanduser().resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        sys.stdout.write(str(outp) + "\n")
    else:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
