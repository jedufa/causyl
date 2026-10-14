from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(str(text or ""), encoding="utf-8")
    os.replace(tmp, path)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = str(line or "").strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def new_session_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dt%H%M%SZ").lower()
    return f"lab_{stamp}_{uuid.uuid4().hex[:8]}"


def sessions_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "governance" / "agents" / "sessions" / "lab_front_door").resolve()


def bubbles_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "governance" / "agents" / "lab_bubbles").resolve()


def session_jsonl_path(repo_root: Path, session_id: str) -> Path:
    return (sessions_root(repo_root) / session_id / "session__lab.jsonl").resolve()


def bubble_markdown_path(repo_root: Path, session_id: str) -> Path:
    return (bubbles_root(repo_root) / f"{session_id}.md").resolve()


def recent_session_context(repo_root: Path, session_id: str, *, limit_messages: int = 6, max_chars: int = 2400) -> str:
    rows = _read_jsonl(session_jsonl_path(repo_root, session_id))
    messages = [row for row in rows if str(row.get("kind") or "").strip().lower() == "message"]
    tail = messages[-max(1, int(limit_messages)) :]
    lines: List[str] = []
    for row in tail:
        sender = str(row.get("sender") or "").strip().lower()
        if sender not in {"user", "assistant"}:
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        phase = str(row.get("phase") or "").strip().lower()
        phase_suffix = f" [{phase}]" if sender == "assistant" and phase and phase != "final" else ""
        lines.append(f"{sender}{phase_suffix}: {content}")
    if not lines:
        return ""
    text = "\n".join(lines).strip()
    if len(text) > int(max_chars):
        text = text[-int(max_chars) :]
    return text


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n")


def _render_lab_bubble_markdown(session_id: str, rows: List[Dict[str, Any]]) -> str:
    lines: List[str] = [
        f"# Lab / {session_id}",
        "",
        f"- updated_utc: {_utc_now_iso()}",
        "",
    ]
    session_seen = False
    for row in rows:
        kind = str(row.get("kind") or "").strip().lower()
        if kind == "session_start":
            session_seen = True
            lines.append(f"## Session {session_id}")
            lines.append("")
            lines.append(f"- started_utc: {str(row.get('utc') or '-').strip() or '-'}")
            mode = str(row.get("mode") or "").strip()
            if mode:
                lines.append(f"- mode: `{mode}`")
            cwd = str(row.get("cwd") or "").strip()
            if cwd:
                lines.append(f"- cwd: `{cwd}`")
            lines.append("")
            continue
        if kind != "message":
            continue
        sender = str(row.get("sender") or "").strip().lower()
        content = str(row.get("content") or "").strip()
        if sender not in {"user", "assistant"} or not content:
            continue
        utc = str(row.get("utc") or "-").strip() or "-"
        phase = str(row.get("phase") or "").strip().lower()
        phase_suffix = f" [{phase}]" if sender == "assistant" and phase and phase != "final" else ""
        lines.append(f"[{utc}] {sender}{phase_suffix}")
        lines.append(content)
        lines.append("")
    if not session_seen:
        lines.append("(no lab transcript yet)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def refresh_projection(repo_root: Path, session_id: str) -> None:
    rows = _read_jsonl(session_jsonl_path(repo_root, session_id))
    _write_text_atomic(bubble_markdown_path(repo_root, session_id), _render_lab_bubble_markdown(session_id, rows))


def ensure_session_started(repo_root: Path, session_id: str, *, mode: str, cwd: Path) -> None:
    path = session_jsonl_path(repo_root, session_id)
    rows = _read_jsonl(path)
    if rows:
        return
    _append_jsonl(
        path,
        {
            "schema_version": "lab_chat_message_v1",
            "kind": "session_start",
            "session_id": session_id,
            "mode": str(mode or "").strip() or "oneshot",
            "cwd": str(Path(cwd).resolve()),
            "utc": _utc_now_iso(),
        },
    )
    refresh_projection(repo_root, session_id)


def append_turn(
    repo_root: Path,
    session_id: str,
    *,
    request_text: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    rendered_text: str,
) -> None:
    path = session_jsonl_path(repo_root, session_id)
    utc = _utc_now_iso()
    _append_jsonl(
        path,
        {
            "schema_version": "lab_chat_message_v1",
            "kind": "message",
            "session_id": session_id,
            "sender": "user",
            "phase": "final",
            "content": str(request_text or "").strip(),
            "utc": utc,
            "payload": dict(payload),
        },
    )
    _append_jsonl(
        path,
        {
            "schema_version": "lab_chat_message_v1",
            "kind": "message",
            "session_id": session_id,
            "sender": "assistant",
            "phase": "final",
            "content": str(rendered_text or "").strip(),
            "utc": _utc_now_iso(),
            "selection": dict(result.get("selection") or {}),
            "summary": str(result.get("summary") or "").strip(),
            "supported": bool(result.get("supported")),
            "terminal_class": str(result.get("terminal_class") or "").strip(),
            "codex_used": bool(dict(result.get("operator_surface") or {}).get("codex_transparency", {}).get("codex_used")),
        },
    )
    refresh_projection(repo_root, session_id)


def append_system_note(repo_root: Path, session_id: str, *, note: str, phase: str = "commentary") -> None:
    path = session_jsonl_path(repo_root, session_id)
    _append_jsonl(
        path,
        {
            "schema_version": "lab_chat_message_v1",
            "kind": "message",
            "session_id": session_id,
            "sender": "assistant",
            "phase": str(phase or "commentary").strip(),
            "content": str(note or "").strip(),
            "utc": _utc_now_iso(),
        },
    )
    refresh_projection(repo_root, session_id)


def end_session(repo_root: Path, session_id: str, *, reason: str) -> None:
    path = session_jsonl_path(repo_root, session_id)
    _append_jsonl(
        path,
        {
            "schema_version": "lab_chat_message_v1",
            "kind": "session_end",
            "session_id": session_id,
            "reason": str(reason or "").strip() or "completed",
            "utc": _utc_now_iso(),
        },
    )
    refresh_projection(repo_root, session_id)
