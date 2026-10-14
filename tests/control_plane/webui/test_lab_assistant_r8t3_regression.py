from __future__ import annotations

from pathlib import Path

import pytest

from infra.webui import server_agents
from infra.webui import server_lab_assistant_turns as turns


pytestmark = [pytest.mark.regression]


class _FakeCodexManager:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_worker(self, *, binding, role, bootstrap_text):
        return {"ok": True, "worker_id": "worker-r8t3"}

    def send_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
        self.calls += 1
        return {
            "ok": True,
            "worker_id": "worker-r8t3",
            "protocol": "oneshot",
            "exit_code": 0,
            "content": "Stable durable answer",
        }


def _patch_send_message_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager: _FakeCodexManager) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        server_agents,
        "load_prompt_bundle",
        lambda role, binding, prompt_file=None, workflow=False: {
            "instructions": "builder prompt",
            "base": {"sha256": "base"},
            "role": {"sha256": "role"},
        },
    )
    monkeypatch.setattr(server_agents, "_build_repo_context", lambda message, context_rows: {"text": "", "files": []})
    monkeypatch.setattr(server_agents, "_refresh_mobile_bubble_projection", lambda **kwargs: None)
    monkeypatch.setattr(server_agents, "_maybe_dispatch_direct_host_verification", lambda **kwargs: {})
    monkeypatch.setattr(server_agents, "_codex_manager", lambda: manager)


def test_r8t3_regression_reconnect_restart_and_stale_turn_truth(tmp_path: Path) -> None:
    reconnect_claim = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="reconnect and stale coverage",
        writer="z7x1.reconnect",
    )
    reconnect_turn_id = reconnect_claim["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=reconnect_turn_id,
        writer="z7x1.reconnect",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=reconnect_turn_id,
        writer="z7x1.reconnect",
        event_type="output_delta",
        text="Alpha",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=reconnect_turn_id,
        writer="z7x1.reconnect",
        new_state="awaiting_resume",
        state_reason="socket dropped",
    )

    reconnect_cursor = turns.replay_turn_events(
        tmp_path,
        turn_id=reconnect_turn_id,
        after_event_seq=0,
    )["resume_cursor"]
    reconnect = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=reconnect_turn_id,
        writer="z7x1.reconnect",
        after_event_seq=reconnect_cursor["after_event_seq"],
        content_chars=reconnect_cursor["content_chars"],
    )

    restart_claim = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="restart coverage",
        writer="z7x1.restart",
    )
    restart_turn_id = restart_claim["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=restart_turn_id,
        writer="z7x1.restart",
        new_state="running",
        state_reason="in flight",
    )
    boot = turns.reconcile_turns_on_boot(tmp_path, writer="z7x1.restart")
    restart_projection = turns.recent_turn_projections(
        tmp_path,
        limit=10,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
    )

    stale_claim = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="stale coverage",
        writer="z7x1.stale",
    )
    stale_turn_id = stale_claim["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=stale_turn_id,
        writer="z7x1.stale",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=stale_turn_id,
        writer="z7x1.stale",
        event_type="terminal",
        text="done",
        terminal_state="completed",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=stale_turn_id,
        writer="z7x1.stale",
        new_state="completed",
        state_reason="done",
    )
    terminal_cursor = turns.replay_turn_events(tmp_path, turn_id=stale_turn_id, after_event_seq=0)["resume_cursor"]
    stale = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=stale_turn_id,
        writer="z7x1.reconnect.stale",
        after_event_seq=terminal_cursor["after_event_seq"],
        content_chars=terminal_cursor["content_chars"],
    )

    by_id = {row["turn_id"]: row for row in restart_projection["turns"]}
    assert reconnect["reattachable"] is True
    assert reconnect["resume_decision"] == "resume_existing_turn"
    assert boot["changed_turn_ids"] == [restart_turn_id]
    assert by_id[restart_turn_id]["classification"]["status"] == "interrupted"
    assert by_id[restart_turn_id]["state"] == "awaiting_resume"
    assert stale["reattachable"] is False
    assert stale["reason"] == "resume_turn_not_active"


def test_r8t3_regression_duplicate_send_idempotent_commit_and_pending_projection_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeCodexManager()
    _patch_send_message_runtime(tmp_path, monkeypatch, manager)

    first = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="ship it",
        options={"resend_key": "z7x1-send"},
    )
    second = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="ship it",
        options={"resend_key": "z7x1-send"},
    )

    turn_id = first["turn"]["turn_id"]
    classified = turns.classify_turn(tmp_path, turn_id=turn_id)
    session_file = tmp_path / "governance" / "agents" / "sessions" / "a7k" / "session__builder.jsonl"
    first_row = server_agents._latest_assistant_row_for_turn(session_file, turn_id=turn_id)
    assert first_row is not None
    first_row = dict(first_row)

    first_commit = server_agents._commit_turn_transcript_row(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        session_file=session_file,
        turn_id=turn_id,
        writer="z7x1.commit",
        assistant_row=first_row,
        turn_event_seq=int(first["turn"].get("terminal_event_seq") or 0),
    )
    second_commit = server_agents._commit_turn_transcript_row(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        session_file=session_file,
        turn_id=turn_id,
        writer="z7x1.commit",
        assistant_row={**first_row, "content": "SHOULD_NOT_APPEND"},
        turn_event_seq=int(first["turn"].get("terminal_event_seq") or 0),
    )

    pending_claim = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="pending commit truth",
        writer="z7x1.pending",
        resend_key="z7x1-pending",
    )
    pending_turn_id = pending_claim["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=pending_turn_id,
        writer="z7x1.pending",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=pending_turn_id,
        writer="z7x1.pending",
        event_type="terminal",
        text="pending output",
        terminal_state="completed",
    )
    pending_terminal = turns.transition_turn(
        tmp_path,
        turn_id=pending_turn_id,
        writer="z7x1.pending",
        new_state="completed",
        state_reason="done",
    )["turn"]
    turns.mark_turn_final_commit_pending(
        tmp_path,
        turn_id=pending_turn_id,
        writer="z7x1.pending",
        turn_event_seq=int(pending_terminal.get("terminal_event_seq") or 0),
        error="interrupted_after_output",
    )
    projection = turns.recent_turn_projections(
        tmp_path,
        limit=10,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
    )

    assert manager.calls == 1
    assert first["ok"] is True
    assert second["duplicate_suppressed"] is True
    assert second["turn"]["turn_id"] == turn_id
    assert classified["classification"]["status"] == "finished"
    assert classified["classification"]["terminal"] is True
    assert first_commit["wrote"] is False
    assert second_commit["wrote"] is False
    rows = server_agents._read_jsonl(session_file)
    assert len(rows) == 2
    assert rows[-1]["content"] != "SHOULD_NOT_APPEND"

    projected = {row["turn_id"]: row for row in projection["turns"]}
    assert projected[turn_id]["classification"]["status"] == "finished"
    assert projected[pending_turn_id]["classification"]["status"] == "pending_commit"
    assert projected[pending_turn_id]["classification"]["pending_commit"] is True
    assert projected[pending_turn_id]["final_commit"]["state"] == "pending"
