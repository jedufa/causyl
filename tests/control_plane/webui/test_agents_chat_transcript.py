from __future__ import annotations

import json
from pathlib import Path

from tools.bin import agents_chat_transcript as t


def _w(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_transcript_merges_roles_by_utc_and_includes_inbox(tmp_path: Path) -> None:
    chat = tmp_path / "agent_chat_TEST"
    _w(
        chat / "session__builder.jsonl",
        [
            {
                "utc": "2026-03-05T10:00:01Z",
                "role_panel": "builder",
                "sender": "assistant",
                "status": "ok",
                "content": "b reply",
            }
        ],
    )
    _w(
        chat / "session__generalist.jsonl",
        [
            {
                "utc": "2026-03-05T10:00:00Z",
                "role_panel": "generalist",
                "sender": "user",
                "status": "ok",
                "content": "g ask",
            }
        ],
    )
    _w(
        chat / "inbox__builder.jsonl",
        [
            {
                "utc": "2026-03-05T10:00:00Z",
                "from_role": "generalist",
                "to_role": "builder",
                "content": "hello\\nworld",
            }
        ],
    )

    rows = t._collect_rows(chat, include_inbox=True)
    out = t._render(rows, max_chars=500)
    assert "[2026-03-05T10:00:00Z] generalist/user" in out
    assert "[2026-03-05T10:00:00Z] inbox:generalist->builder" in out
    assert "[2026-03-05T10:00:01Z] builder/assistant" in out
    assert "hello\nworld" in out


def test_main_writes_output_file(tmp_path: Path) -> None:
    chat = tmp_path / "agent_chat"
    _w(
        chat / "session__builder.jsonl",
        [{"utc": "2026-03-05T10:00:00Z", "role_panel": "builder", "sender": "user", "content": "x"}],
    )
    out = tmp_path / "transcript.txt"
    rc = t.main([str(chat), "--output", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "builder/user" in text


def test_resolve_chat_dir_from_pis_uid(tmp_path: Path) -> None:
    uid = "PIS-20260305-130935Z-34B2"
    chat = (
        tmp_path
        / "governance"
        / "ledger"
        / "items"
        / f"{uid}__sample-slug"
        / "artifacts"
        / "agent_chat_TEST"
    )
    chat.mkdir(parents=True, exist_ok=True)
    got = t._resolve_chat_dir(chat_dir="", pis_uid=uid, chat_subdir="agent_chat_TEST", repo_root=str(tmp_path))
    assert got == chat.resolve()
