from __future__ import annotations

from pathlib import Path

from infra.webui import server_agents


def test_context_rows_for_turn_skips_history_for_scheduled_run_scoped_mode(tmp_path: Path, monkeypatch) -> None:
    session_file = tmp_path / "session.jsonl"
    session_file.write_text('{"sender":"user","content":"old"}\n', encoding="utf-8")
    monkeypatch.setattr(server_agents, "_tail_rows", lambda path, limit=80: [{"sender": "user", "content": "old"}])
    rows = server_agents._context_rows_for_turn(
        session_file=session_file,
        options={"_scheduled_context_mode": "run_scoped_no_history"},
    )
    assert rows == []


def test_context_rows_for_turn_skips_history_for_explicit_scheduled_turn(tmp_path: Path, monkeypatch) -> None:
    session_file = tmp_path / "session.jsonl"
    session_file.write_text('{"sender":"user","content":"old"}\n', encoding="utf-8")
    monkeypatch.setattr(server_agents, "_tail_rows", lambda path, limit=80: [{"sender": "user", "content": "old"}])
    rows = server_agents._context_rows_for_turn(
        session_file=session_file,
        options={"_scheduled_turn": True},
    )
    assert rows == []


def test_context_rows_for_turn_uses_tail_by_default(tmp_path: Path, monkeypatch) -> None:
    session_file = tmp_path / "session.jsonl"
    session_file.write_text('{"sender":"user","content":"old"}\n', encoding="utf-8")
    monkeypatch.setattr(server_agents, "_tail_rows", lambda path, limit=80: [{"sender": "user", "content": "old"}])
    rows = server_agents._context_rows_for_turn(session_file=session_file, options={})
    assert rows == [{"sender": "user", "content": "old"}]
