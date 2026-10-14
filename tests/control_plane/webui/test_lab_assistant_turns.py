from __future__ import annotations

from pathlib import Path

import pytest

from infra.webui import server_agents
from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_turns as turns


def _seed_task_register(tmp_path: Path) -> dict:
    current = register.load_or_init_task_register(tmp_path)
    return register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="test.turns",
        task={
            "task_id": "task_turns_01",
            "kind": "software",
            "title": "Direct turn checkpoint",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260327-132825Z-66E0",
            "next_expected_action": "run direct turn",
        },
    )["task_register"]


def test_turn_terminal_state_maps_cancellation_completion() -> None:
    assert server_agents._turn_terminal_state(ok=False, error="cancelled by operator") == "cancelled"
    assert server_agents._turn_terminal_state(ok=False, error="timeout waiting for worker") == "awaiting_resume"
    assert server_agents._turn_terminal_state(ok=False, error="unexpected runtime failure") == "failed"


def test_direct_turn_allows_only_legal_state_transitions(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="implement the change",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]

    running = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="executing",
    )["turn"]
    assert running["state"] == "running"

    completed = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="done",
    )["turn"]
    assert completed["state"] == "completed"

    with pytest.raises(turns.DirectTurnError, match="illegal turn transition"):
        turns.transition_turn(
            tmp_path,
            turn_id=turn_id,
            writer="test.turns",
            new_state="running",
            state_reason="terminal turns stay terminal",
        )

    preserved = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="failed",
        state_reason="late bookkeeping failure",
    )["turn"]
    assert preserved["state"] == "completed"


def test_direct_turn_records_checkpoint_and_resume_decision_in_task_register(tmp_path: Path) -> None:
    _seed_task_register(tmp_path)
    assert "direct_turn_job" in policy.ALLOWED_ENVELOPE_SURFACES
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "task_turns_01"},
        role="builder",
        message="checkpoint run",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == "task_turns_01")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == task.get("current_run_id"))
    checkpoint = dict(run_row.get("checkpoint") or {})
    assert run_row["surface"] == "codex_worker"
    assert checkpoint["checkpoint_id"] == f"direct_turn:{turn_id}"
    assert checkpoint["checkpoint_status"] == "recorded"

    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="connection lost",
    )
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == "task_turns_01")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == task.get("current_run_id"))
    decision = dict(run_row.get("resume_decision") or {})
    assert decision["decision"] == "blocked"
    assert decision["decision_reason"] == "direct_turn_state:awaiting_resume"


def test_direct_turn_duplicate_suppression_uses_durable_turn_identity(tmp_path: Path) -> None:
    first = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="same request",
        writer="test.turns",
        advisory_transport_id="socket-one",
    )
    turn_id = first["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )

    second = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="same request",
        writer="test.turns",
        advisory_transport_id="socket-two",
    )

    assert second["duplicate_suppressed"] is True
    assert second["turn"]["turn_id"] == turn_id
    assert second["turn"]["advisory_transport_ids"] == ["socket-one", "socket-two"]


def test_direct_turn_same_resend_key_with_different_message_creates_new_turn(tmp_path: Path) -> None:
    first = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="first message",
        writer="test.turns",
        resend_key="rk1",
    )
    second = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="DIFFERENT message",
        writer="test.turns",
        resend_key="rk1",
    )

    assert second["duplicate_suppressed"] is False
    assert second["created"] is True
    assert second["turn"]["turn_id"] != first["turn"]["turn_id"]
    assert second["turn"]["resend_key"] == "rk1"
    assert second["turn"]["request_fingerprint"] != first["turn"]["request_fingerprint"]


def test_direct_turn_resume_requires_proven_identity_or_creates_new_turn(tmp_path: Path) -> None:
    first = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="resume me",
        writer="test.turns",
    )
    turn_id = first["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="transport dropped",
    )
    replay = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)
    resume_cursor = replay["resume_cursor"]

    resumed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="resume me",
        writer="test.turns",
        resume_turn_id=turn_id,
        advisory_transport_id="new-socket",
        resume_after_event_seq=resume_cursor["after_event_seq"],
        resume_content_chars=resume_cursor["content_chars"],
    )
    assert resumed["resume_detected"] is True
    assert resumed["turn"]["turn_id"] == turn_id

    missing_cursor = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="resume me",
        writer="test.turns",
        resume_turn_id=turn_id,
        resend_key="force-new-turn-without-cursor",
    )
    assert missing_cursor["resume_detected"] is False
    assert missing_cursor["turn"]["turn_id"] != turn_id
    assert missing_cursor["turn"]["resume_unproven_reason"] == "resume_cursor_missing"

    mismatch = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="different payload",
        writer="test.turns",
        resume_turn_id=turn_id,
    )
    assert mismatch["resume_detected"] is False
    assert mismatch["turn"]["turn_id"] != turn_id
    assert mismatch["turn"]["resume_unproven_reason"] == "resume_identity_unproven"


def test_direct_turn_disconnect_reconnect_reattach_requires_durable_cursor_proof(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="stream and reconnect",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text="Alpha",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="transport dropped",
    )

    partial = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=2)
    partial_cursor = partial["resume_cursor"]
    reattach = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=turn_id,
        writer="test.turns",
        after_event_seq=partial_cursor["after_event_seq"],
        content_chars=partial_cursor["content_chars"],
    )
    assert reattach["reattachable"] is True
    assert reattach["resume_decision"] == "resume_existing_turn"
    assert reattach["continuity_proven"] is True
    assert reattach["cursor_proven"] is True

    ahead = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=turn_id,
        writer="test.turns",
        after_event_seq=partial_cursor["after_event_seq"] + 10,
        content_chars=partial_cursor["content_chars"],
    )
    assert ahead["reattachable"] is False
    assert ahead["resume_decision"] == "retry_new_turn"
    assert ahead["reason"] == "resume_cursor_ahead_of_durable"


def test_direct_turn_app_relaunch_resume_returns_replay_tail_from_durable_cursor(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="resume after relaunch",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text="Alpha",
    )
    before_relaunch = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)["resume_cursor"]
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text=" beta",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="app relaunched",
    )

    reattach = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=turn_id,
        writer="test.turns",
        after_event_seq=before_relaunch["after_event_seq"],
        content_chars=before_relaunch["content_chars"],
    )
    assert reattach["reattachable"] is True
    assert [row["event_type"] for row in reattach["replay"]["events"]] == ["output_delta", "turn_state_changed"]
    assert reattach["replay"]["content"] == "Alpha beta"


def test_direct_turn_reattach_allows_if_launch_turn_has_exact_detached_result_match(tmp_path: Path) -> None:
    turn = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "turn_resume_ok"},
        role="builder",
        message="detached result should not block reattach",
        writer="v7x9.reattach.ok",
    )["turn"]

    turns.transition_turn(
        tmp_path,
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ok",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ok",
        event_type="output_delta",
        text="checkpoint",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ok",
        new_state="awaiting_resume",
        state_reason="socket dropped",
    )

    correlation.record_delegation_binding(
        tmp_path,
        writer="v7x9.reattach.ok.binding",
        delegation_ref={
            "correlation_id": "corr_turn_resume_ok",
            "launch_turn_id": turn["turn_id"],
            "delegation_request_id": "req_turn_resume_ok",
            "surface": "mailbox",
            "summary": "Exact correlation for deterministic reattach",
            "requested_action": "continue detached work",
            "owner_ref": {"task_id": "task_resume_ok", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
        },
    )
    correlation.record_detached_result(
        tmp_path,
        writer="v7x9.reattach.ok.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_turn_resume_ok",
                "request_id": "req_turn_resume_ok",
                "surface": "mailbox",
                "status": "completed",
                "summary": "detached result attached to launch turn",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_turn_resume_ok.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Inspect result", "owner": "assistant"},
                "created_utc": "2026-03-13T10:00:00Z",
                "updated_utc": "2026-03-13T10:00:00Z",
                "correlation_id": "corr_turn_resume_ok",
                "launch_turn_id": turn["turn_id"],
                "detached_job_id": "job_turn_resume_ok",
                "worker_ref": "mailbox:job_turn_resume_ok",
                "producer": "test",
                "owner_ref": {"task_id": "task_resume_ok", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
                "result_correlation_basis": "request_id",
            }
        ),
    )

    cursor = turns.replay_turn_events(
        tmp_path,
        turn_id=turn["turn_id"],
        after_event_seq=0,
    )["resume_cursor"]
    reattach = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "turn_resume_ok"},
        role="builder",
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ok.evaluate",
        after_event_seq=cursor["after_event_seq"],
        content_chars=cursor["content_chars"],
    )

    assert reattach["correlation_gate"]["continuity_ok"] is True
    assert reattach["reattachable"] is True
    assert reattach["continuity_proven"] is True
    assert reattach["resume_decision"] == "resume_existing_turn"


def test_direct_turn_reattach_blocks_if_launch_turn_has_ambiguous_detached_result_state(tmp_path: Path) -> None:
    turn = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "turn_resume_block"},
        role="builder",
        message="detached ambiguity should block reattach",
        writer="v7x9.reattach.ambiguous",
    )["turn"]

    turns.transition_turn(
        tmp_path,
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ambiguous",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ambiguous",
        event_type="output_delta",
        text="checkpoint",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ambiguous",
        new_state="awaiting_resume",
        state_reason="socket dropped",
    )

    correlation.record_delegation_binding(
        tmp_path,
        writer="v7x9.reattach.ambiguous.binding.a",
        delegation_ref={
            "correlation_id": "corr_turn_resume_ambig_a",
            "launch_turn_id": turn["turn_id"],
            "delegation_request_id": "req_turn_resume_ambig_a",
            "surface": "mailbox",
            "summary": "First ambiguous correlation",
            "requested_action": "resume with ambiguous",
            "owner_ref": {"task_id": "task_resume_ambig", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            "detached_job_id": "job_turn_resume_ambig",
        },
    )
    correlation.record_delegation_binding(
        tmp_path,
        writer="v7x9.reattach.ambiguous.binding.b",
        delegation_ref={
            "correlation_id": "corr_turn_resume_ambig_b",
            "launch_turn_id": turn["turn_id"],
            "delegation_request_id": "req_turn_resume_ambig_b",
            "surface": "mailbox",
            "summary": "Second ambiguous correlation",
            "requested_action": "also resume with shared job",
            "owner_ref": {"task_id": "task_resume_ambig", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            "detached_job_id": "job_turn_resume_ambig",
        },
    )
    correlation.record_detached_result(
        tmp_path,
        writer="v7x9.reattach.ambiguous.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_turn_resume_ambig",
                "request_id": "req_turn_resume_ambig",
                "surface": "workflow",
                "status": "completed",
                "summary": "shared job result causes ambiguity",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_turn_resume_ambig.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Inspect ambiguity", "owner": "assistant"},
                "created_utc": "2026-03-13T10:10:00Z",
                "updated_utc": "2026-03-13T10:10:00Z",
                "correlation_id": "corr_turn_resume_ambig_a",
                "launch_turn_id": turn["turn_id"],
                "detached_job_id": "job_turn_resume_ambig",
                "result_correlation_basis": "detached_job_id",
                "worker_ref": "workflow:job_turn_resume_ambig",
                "producer": "test",
                "owner_ref": {"task_id": "task_resume_ambig", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            }
        ),
    )

    cursor = turns.replay_turn_events(
        tmp_path,
        turn_id=turn["turn_id"],
        after_event_seq=0,
    )["resume_cursor"]
    reattach = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "turn_resume_block"},
        role="builder",
        turn_id=turn["turn_id"],
        writer="v7x9.reattach.ambiguous.evaluate",
        after_event_seq=cursor["after_event_seq"],
        content_chars=cursor["content_chars"],
    )

    assert reattach["correlation_gate"]["continuity_ok"] is False
    assert reattach["correlation_gate"]["ambiguous_count"] == 1
    assert reattach["correlation_gate"]["reason"] == "resume_blocked_by_correlation_ambiguous"
    assert reattach["reattachable"] is False
    assert reattach["continuity_proven"] is False
    assert reattach["resume_decision"] == "retry_new_turn"
    assert reattach["reason"] == "resume_blocked_by_correlation_ambiguous"


def test_direct_turn_stale_reattach_is_rejected_after_terminal_turn(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="stale reattach",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="done",
        terminal_state="completed",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="finished",
    )
    terminal_cursor = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)["resume_cursor"]

    stale = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=turn_id,
        writer="test.turns",
        after_event_seq=terminal_cursor["after_event_seq"],
        content_chars=terminal_cursor["content_chars"],
    )
    assert stale["reattachable"] is False
    assert stale["resume_decision"] == "retry_new_turn"
    assert stale["reason"] == "resume_turn_not_active"

    retried = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="stale reattach",
        writer="test.turns",
        resume_turn_id=turn_id,
        resume_after_event_seq=terminal_cursor["after_event_seq"],
        resume_content_chars=terminal_cursor["content_chars"],
    )
    assert retried["resume_detected"] is False
    assert retried["turn"]["turn_id"] != turn_id
    assert retried["turn"]["resume_unproven_reason"] == "resume_turn_not_active"


def test_evaluate_reattach_allows_durable_exact_detached_correlation(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="reattach exact result",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="transport interrupted",
    )

    correlation.record_delegation_binding(
        tmp_path,
        delegation_ref={
            "correlation_id": "corr_turn_exact",
            "launch_turn_id": turn_id,
            "delegation_request_id": "req_turn_exact",
            "request_id": "req_turn_exact",
            "surface": "mailbox",
            "summary": "Exact correlation target",
            "requested_action": "resume test",
            "owner_ref": {"task_id": "a7k", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
        },
        writer="test.turns.exact.binding",
    )
    correlation.record_detached_result(
        tmp_path,
        writer="test.turns.exact.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_turn_exact",
                "request_id": "req_turn_exact",
                "surface": "mailbox",
                "status": "completed",
                "summary": "exact result is in",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/turns/result_turn_exact.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Review exact correlation", "owner": "assistant"},
                "created_utc": "2026-03-13T04:00:00Z",
                "updated_utc": "2026-03-13T04:00:00Z",
                "correlation_id": "corr_turn_exact",
                "launch_turn_id": turn_id,
                "detached_job_id": "job_turn_exact",
                "result_correlation_basis": "launch_turn_id",
                "worker_ref": "mailbox:job_turn_exact",
                "producer": "test",
                "owner_ref": {"task_id": "a7k", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            }
        ),
    )

    resume_cursor = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)["resume_cursor"]
    reattach = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=turn_id,
        writer="test.turns",
        after_event_seq=resume_cursor["after_event_seq"],
        content_chars=resume_cursor["content_chars"],
    )

    assert reattach["reattachable"] is True
    assert reattach["continuity_proven"] is True
    assert reattach["correlation_reattach"]["status"] == "clear"


def test_evaluate_reattach_blocks_when_detached_result_correlation_is_ambiguous(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="reattach ambiguous result",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="transport interrupted",
    )

    correlation.record_delegation_binding(
        tmp_path,
        delegation_ref={
            "correlation_id": "corr_turn_ambig_a",
            "launch_turn_id": turn_id,
            "delegation_request_id": "req_turn_ambig_a",
            "request_id": "req_turn_ambig_a",
            "surface": "mailbox",
            "summary": "Ambiguous correlation target A",
            "requested_action": "run ambiguous test",
            "owner_ref": {"task_id": "a7k", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            "result_correlation_basis": "detached_job_id",
            "detached_job_id": "job_turn_ambig_shared",
        },
        writer="test.turns.ambig.binding_a",
    )
    correlation.record_delegation_binding(
        tmp_path,
        delegation_ref={
            "correlation_id": "corr_turn_ambig_b",
            "launch_turn_id": turn_id,
            "delegation_request_id": "req_turn_ambig_b",
            "request_id": "req_turn_ambig_b",
            "surface": "mailbox",
            "summary": "Ambiguous correlation target B",
            "requested_action": "run ambiguous test",
            "owner_ref": {"task_id": "a7k", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            "result_correlation_basis": "detached_job_id",
            "detached_job_id": "job_turn_ambig_shared",
        },
        writer="test.turns.ambig.binding_b",
    )
    correlation.record_detached_result(
        tmp_path,
        writer="test.turns.ambig.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_turn_ambiguous",
                "request_id": "req_turn_ambig_a",
                "surface": "mailbox",
                "status": "completed",
                "summary": "ambiguous result is in",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/turns/result_turn_ambiguous.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Review ambiguous result", "owner": "assistant"},
                "created_utc": "2026-03-13T04:10:00Z",
                "updated_utc": "2026-03-13T04:10:00Z",
                "correlation_id": "corr_turn_ambig_a",
                "launch_turn_id": turn_id,
                "detached_job_id": "job_turn_ambig_shared",
                "result_correlation_basis": "detached_job_id",
                "worker_ref": "mailbox:job_turn_ambig_shared",
                "producer": "test",
                "owner_ref": {"task_id": "a7k", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            }
        ),
    )

    resume_cursor = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)["resume_cursor"]
    reattach = turns.evaluate_reattach(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        turn_id=turn_id,
        writer="test.turns",
        after_event_seq=resume_cursor["after_event_seq"],
        content_chars=resume_cursor["content_chars"],
    )

    assert reattach["reattachable"] is False
    assert reattach["correlation_reattach"]["status"] == "ambiguous"
    assert reattach["reason"] == "resume_blocked_by_correlation_ambiguous"


def test_direct_turn_output_events_advance_monotonic_cursor_and_replay_tail(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="stream this",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="progress",
        status_message="streaming_started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text="Hello",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text=" world",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="Hello world",
        terminal_state="completed",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="finished",
    )

    replay = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)
    tail = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=3)

    assert [row["event_seq"] for row in replay["events"]] == [1, 2, 3, 4, 5, 6, 7]
    assert replay["replay_status"] == "completed"
    assert replay["continuity_proven"] is True
    assert replay["terminal_proven"] is True
    assert replay["content"] == "Hello world"
    assert replay["resume_cursor"] == {"after_event_seq": 7, "content_chars": 11}
    assert [row["event_type"] for row in tail["events"]] == ["output_delta", "output_delta", "terminal", "turn_state_changed"]


def test_direct_turn_replay_recovers_partial_stream_without_terminal_guessing(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="partial stream",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="progress",
        status_message="streaming_started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text="Alpha",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text=" beta",
    )

    replay = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=2)

    assert replay["replay_status"] == "running"
    assert replay["continuity_proven"] is True
    assert replay["terminal_proven"] is False
    assert replay["classification"]["status"] == "running"
    assert replay["content"] == "Alpha beta"
    assert replay["resume_cursor"] == {"after_event_seq": 5, "content_chars": 10}
    assert [row["event_seq"] for row in replay["events"]] == [3, 4, 5]


def test_direct_turn_replay_surfaces_interrupted_truth_when_event_continuity_breaks(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="break continuity",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="output_delta",
        text="Alpha",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="Alpha",
        terminal_state="completed",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="finished",
    )

    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "direct_turns" / "turn_events.jsonl"
    rows = events_path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in rows if '"event_seq":3' not in line]
    events_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    replay = turns.replay_turn_events(tmp_path, turn_id=turn_id, after_event_seq=0)

    assert replay["replay_status"] == "interrupted"
    assert replay["continuity_proven"] is False
    assert replay["terminal_proven"] is False
    assert replay["interruption_reason"] == "event_sequence_gap"
    assert replay["classification"]["status"] == "ambiguous_terminal"
    assert replay["classification"]["terminal"] is False
    assert replay["classification"]["terminal_ambiguous"] is True
    assert replay["classification"]["output_present"] is True


def test_direct_turn_classifies_cancel_intent_without_claiming_completion(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="cancel requested but still running",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="cancel requested by operator",
    )

    classified = turns.classify_turn(tmp_path, turn_id=turn_id)
    assert classified["classification"]["status"] == "cancel"
    assert classified["classification"]["terminal"] is False


def test_direct_turn_classifies_cancel_completion_separately_from_intent(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="cancelled turn",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="cancelled by operator",
        terminal_state="cancelled",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="cancelled",
        state_reason="cancelled by operator",
    )

    classified = turns.classify_turn(tmp_path, turn_id=turn_id)
    assert classified["classification"]["status"] == "cancelled"
    assert classified["classification"]["terminal"] is True
    assert classified["classification"]["terminal_ambiguous"] is False


def test_direct_turn_classifies_timeout_distinct_from_interruption(tmp_path: Path) -> None:
    timeout_turn = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="timeout run",
        writer="test.turns",
    )["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=timeout_turn,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=timeout_turn,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="timeout while waiting for runtime reply",
    )
    timeout_classified = turns.classify_turn(tmp_path, turn_id=timeout_turn)
    assert timeout_classified["classification"]["status"] == "timed_out"
    assert timeout_classified["classification"]["terminal"] is False

    interrupted_turn = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="interrupted run",
        writer="test.turns",
    )["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=interrupted_turn,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=interrupted_turn,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="transport dropped",
    )
    interrupted_classified = turns.classify_turn(tmp_path, turn_id=interrupted_turn)
    assert interrupted_classified["classification"]["status"] == "interrupted"
    assert interrupted_classified["classification"]["terminal"] is False


def test_boot_reconcile_marks_nonterminal_turns_as_awaiting_resume(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="boot reconcile this turn",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="stream in flight",
    )

    reconciled = turns.reconcile_turns_on_boot(tmp_path, writer="test.turns.boot")
    turn = turns.read_turn(tmp_path, turn_id=turn_id)
    projection = turns.recent_turn_projections(tmp_path, limit=5)
    by_id = {row["turn_id"]: row for row in projection["turns"]}

    assert turn["state"] == "awaiting_resume"
    assert "startup reconciliation marked nonterminal turn" in turn["state_reason"]
    assert turn_id in reconciled["changed_turn_ids"]
    assert by_id[turn_id]["classification"]["status"] == "interrupted"
    assert by_id[turn_id]["continuity_proven"] is True


def test_recent_turn_projection_surfaces_pending_commit_truth(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="pending commit projection",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="Final text",
        terminal_state="completed",
    )
    terminal = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="done",
    )["turn"]
    turns.mark_turn_final_commit_pending(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        turn_event_seq=int(terminal.get("terminal_event_seq") or 0),
        error="session append interrupted",
    )

    projection = turns.recent_turn_projections(
        tmp_path,
        limit=10,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
    )
    assert projection["turn_count"] == 1
    row = projection["turns"][0]
    assert row["turn_id"] == turn_id
    assert row["classification"]["status"] == "pending_commit"
    assert row["classification"]["pending_commit"] is True
    assert row["final_commit"]["state"] == "pending"


def test_recent_turn_projection_preserves_ambiguous_terminal_truth(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="ambiguous projection",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="answer",
        terminal_state="completed",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="finished",
    )

    events_file = tmp_path / "runtime" / "state" / "lab_assistant" / "direct_turns" / "turn_events.jsonl"
    raw_lines = events_file.read_text(encoding="utf-8").splitlines()
    kept = [line for line in raw_lines if '"event_seq":3' not in line]
    events_file.write_text("\n".join(kept) + "\n", encoding="utf-8")

    projection = turns.recent_turn_projections(tmp_path, limit=5)
    by_id = {row["turn_id"]: row for row in projection["turns"]}
    row = by_id[turn_id]
    assert row["classification"]["status"] == "ambiguous_terminal"
    assert row["classification"]["terminal"] is False
    assert row["classification"]["terminal_ambiguous"] is True
    assert row["continuity_proven"] is False
    assert row["replay_status"] == "interrupted"


def test_recent_turn_projections_preloads_event_rows_once_for_selected_turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="first projection",
        writer="test.turns",
    )["turn"]["turn_id"]
    second = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="second projection",
        writer="test.turns",
    )["turn"]["turn_id"]

    calls: list[list[str]] = []
    real_loader = turns._load_replay_rows_by_turn_id

    def _wrapped_loader(repo_root, *, wanted_turn_ids=None):
        calls.append(list(wanted_turn_ids or []))
        return real_loader(repo_root, wanted_turn_ids=wanted_turn_ids)

    monkeypatch.setattr(turns, "_load_replay_rows_by_turn_id", _wrapped_loader)

    projection = turns.recent_turn_projections(tmp_path, limit=5)

    assert projection["turn_count"] == 2
    assert len(calls) == 1
    assert set(calls[0]) == {first, second}


def test_agent_send_message_persists_turn_id_and_suppresses_duplicate_stream_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    class _FakeManager:
        def __init__(self) -> None:
            self.calls = 0

        def ensure_worker(self, *, binding, role, bootstrap_text):
            return {"ok": True, "worker_id": "worker-1"}

        def send_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            self.calls += 1
            return {
                "ok": True,
                "worker_id": "worker-1",
                "protocol": "oneshot",
                "exit_code": 0,
                "content": "Final answer",
            }

    fake_mgr = _FakeManager()
    monkeypatch.setattr(server_agents, "_codex_manager", lambda: fake_mgr)

    first = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="hello",
        options={"resend_key": "send-1"},
    )
    second = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="hello",
        options={"resend_key": "send-1"},
    )

    assert fake_mgr.calls == 1
    assert first["turn"]["state"] == "completed"
    assert second["duplicate_suppressed"] is True
    assert second["turn"]["turn_id"] == first["turn"]["turn_id"]
    assert second["assistant"]["meta"]["turn_id"] == first["turn"]["turn_id"]


def test_agent_send_message_same_resend_key_different_message_creates_new_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    class _FakeManager:
        def __init__(self) -> None:
            self.calls = 0

        def ensure_worker(self, *, binding, role, bootstrap_text):
            return {"ok": True, "worker_id": "worker-1"}

        def send_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            self.calls += 1
            return {
                "ok": True,
                "worker_id": "worker-1",
                "protocol": "oneshot",
                "exit_code": 0,
                "content": f"Final answer {self.calls}",
            }

    fake_mgr = _FakeManager()
    monkeypatch.setattr(server_agents, "_codex_manager", lambda: fake_mgr)

    first = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="hello",
        options={"resend_key": "send-1"},
    )
    second = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="different hello",
        options={"resend_key": "send-1"},
    )

    assert fake_mgr.calls == 2
    assert first["duplicate_suppressed"] is False
    assert second["duplicate_suppressed"] is False
    assert second["turn"]["turn_id"] != first["turn"]["turn_id"]
    assert second["assistant"]["meta"]["turn_id"] == second["turn"]["turn_id"]


def test_agent_send_message_preserves_completed_turn_when_post_turn_hook_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(server_agents, "_launch_codex_surface_run", lambda **kwargs: {"ok": False, "launched": False, "task_id": "", "run_id": ""})

    class _FakeManager:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return {"ok": True, "worker_id": "worker-1"}

        def send_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            return {
                "ok": True,
                "worker_id": "worker-1",
                "protocol": "oneshot",
                "exit_code": 0,
                "content": "Final answer",
            }

    monkeypatch.setattr(server_agents, "_codex_manager", lambda: _FakeManager())

    def _boom(**kwargs):
        raise RuntimeError("late host dispatch failure")

    monkeypatch.setattr(server_agents, "_maybe_dispatch_direct_host_verification", _boom)

    result = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="hello",
        options={"resend_key": "send-late-error"},
    )

    assert result["ok"] is True
    assert result["assistant"]["content"] == "Final answer"
    assert str(result.get("late_error") or "") == "late host dispatch failure"
    assert result["turn"]["state"] == "completed"
    session_file = tmp_path / "governance" / "agents" / "sessions" / "a7k" / "session__builder.jsonl"
    rows = server_agents._read_jsonl(session_file)
    assert len(rows) == 2
    assert rows[-1]["content"] == "Final answer"
    assert rows[-1]["status"] == "ok"


def test_append_turn_terminal_event_accepts_awaiting_resume(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "resumeable"},
        role="builder",
        message="resume later",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="executing",
    )
    event = turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="resume needed",
        terminal_state="awaiting_resume",
    )["turn"]
    assert event["state"] == "running"
    assert int(event["terminal_event_seq"]) > 0


def test_direct_turn_can_complete_after_awaiting_resume(tmp_path: Path) -> None:
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "resume-complete"},
        role="builder",
        message="finish after resume",
        writer="test.turns",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="executing",
    )
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="awaiting_resume",
        state_reason="worker disconnected",
    )

    completed = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="resumed turn finalized cleanly",
    )["turn"]

    assert completed["state"] == "completed"
    assert completed["terminal_utc"]


def test_agent_stream_persists_durable_turn_output_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EIEL_AGENTS_BACKEND", raising=False)
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
    monkeypatch.setattr(
        server_agents,
        "stream_response_events",
        lambda **kwargs: iter(
            [
                {"type": "response.output_text.delta", "delta": "Hello"},
                {"type": "response.output_text.delta", "delta": " world"},
                {"type": "response.completed", "response": {"output_text": "Hello world"}},
            ]
        ),
    )
    monkeypatch.setattr(
        server_agents,
        "extract_output_text",
        lambda response: str(response.get("output_text") or ""),
    )

    events = list(
        server_agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="hello",
        )
    )

    done = events[-1]
    replay = turns.replay_turn_events(tmp_path, turn_id=done["turn"]["turn_id"], after_event_seq=0)

    assert done["type"] == "done"
    assert done["ok"] is True
    assert replay["replay_status"] == "completed"
    assert replay["content"] == "Hello world"
    assert [row["event_type"] for row in replay["events"]] == [
        "turn_created",
        "turn_state_changed",
        "progress",
        "output_delta",
        "output_delta",
        "terminal",
        "turn_final_commit_pending",
        "turn_final_commit_committed",
        "turn_state_changed",
    ]


def test_turn_final_transcript_commit_is_at_most_once_for_same_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="commit once",
        writer="test.turns",
        resend_key="x5v8-once",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="Final answer",
        terminal_state="completed",
    )
    terminal = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="done",
    )["turn"]
    session_file = tmp_path / "governance" / "agents" / "sessions" / "a7k" / "session__builder.jsonl"
    row = {
        "schema_version": "agent_chat_message_v1",
        "utc": "2026-03-13T11:40:00Z",
        "role_panel": "builder",
        "sender": "assistant",
        "content": "Final answer",
        "status": "ok",
        "binding": {"type": "task_id", "id": "a7k"},
        "meta": {"turn_id": turn_id, "source": "test"},
    }
    first = server_agents._commit_turn_transcript_row(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        session_file=session_file,
        turn_id=turn_id,
        writer="test.x5v8",
        assistant_row=row,
        turn_event_seq=int(terminal.get("terminal_event_seq") or 0),
    )
    second = server_agents._commit_turn_transcript_row(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        session_file=session_file,
        turn_id=turn_id,
        writer="test.x5v8",
        assistant_row={**row, "content": "SHOULD NOT WRITE"},
        turn_event_seq=int(terminal.get("terminal_event_seq") or 0),
    )
    assert first["wrote"] is True
    assert second["wrote"] is False
    rows = server_agents._read_jsonl(session_file)
    assert len(rows) == 1
    assert rows[0]["content"] == "Final answer"
    final_turn = turns.read_turn(tmp_path, turn_id=turn_id)
    assert final_turn["final_commit_state"] == "committed"


def test_duplicate_retry_reconciles_missing_session_row_from_turn_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="reconcile me",
        writer="test.turns",
        resend_key="x5v8-reconcile",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="Recovered answer",
        terminal_state="completed",
    )
    terminal = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="done",
    )["turn"]
    turns.mark_turn_final_commit_pending(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        turn_event_seq=int(terminal.get("terminal_event_seq") or 0),
        error="interrupted_after_output",
    )

    result = server_agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="reconcile me",
        options={"resend_key": "x5v8-reconcile"},
    )

    assert result["duplicate_suppressed"] is True
    assert result["assistant"] is not None
    assert result["assistant"]["content"] == "Recovered answer"
    assert result["assistant"]["meta"]["source"] == "turn_event_reconcile"
    session_file = tmp_path / "governance" / "agents" / "sessions" / "a7k" / "session__builder.jsonl"
    rows = server_agents._read_jsonl(session_file)
    assert len(rows) == 1
    reconciled_turn = turns.read_turn(tmp_path, turn_id=turn_id)
    assert reconciled_turn["final_commit_state"] == "committed"


def test_turn_commit_failure_leaves_pending_commit_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    claimed = turns.begin_or_resume_turn(
        tmp_path,
        binding={"type": "task_id", "id": "a7k"},
        role="builder",
        message="pending on failure",
        writer="test.turns",
        resend_key="x5v8-pending",
    )
    turn_id = claimed["turn"]["turn_id"]
    turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="running",
        state_reason="started",
    )
    turns.append_turn_output_event(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        event_type="terminal",
        text="Will fail commit",
        terminal_state="completed",
    )
    terminal = turns.transition_turn(
        tmp_path,
        turn_id=turn_id,
        writer="test.turns",
        new_state="completed",
        state_reason="done",
    )["turn"]

    def _fail_open(*args, **kwargs):
        raise OSError("disk_full")

    monkeypatch.setattr(server_agents, "open", _fail_open, raising=False)
    session_file = tmp_path / "governance" / "agents" / "sessions" / "a7k" / "session__builder.jsonl"
    with pytest.raises(OSError, match="disk_full"):
        server_agents._commit_turn_transcript_row(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            session_file=session_file,
            turn_id=turn_id,
            writer="test.x5v8",
            assistant_row={
                "schema_version": "agent_chat_message_v1",
                "utc": "2026-03-13T11:41:00Z",
                "role_panel": "builder",
                "sender": "assistant",
                "content": "Will fail commit",
                "status": "ok",
                "binding": {"type": "task_id", "id": "a7k"},
                "meta": {"turn_id": turn_id},
            },
            turn_event_seq=int(terminal.get("terminal_event_seq") or 0),
        )
    turn_after = turns.read_turn(tmp_path, turn_id=turn_id)
    assert turn_after["final_commit_state"] == "pending"
    assert "disk_full" in turn_after["final_commit_last_error"]
