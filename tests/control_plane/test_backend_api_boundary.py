from __future__ import annotations

"""Focused coverage for backend API boundary contracts.

Last modified: 2026-03-31 Europe/Vienna
"""

import json
from pathlib import Path

from infra.controlplane import backend_api_boundary as boundary


def test_boundary_snapshot_declares_read_vs_mutating_surface_split() -> None:
    snapshot = boundary.boundary_snapshot()

    assert snapshot["uid"] == "PIS-20260313-100600Z-L8Q1"
    assert snapshot["depends_on"] == ["PIS-20260313-100601Z-M9R2", "PIS-20260313-100603Z-P2T4"]

    read_ids = {row["surface_id"] for row in snapshot["read_surfaces"]}
    assert read_ids == {"health", "status", "receipts"}
    assert all(row["mutating"] is False for row in snapshot["read_surfaces"])

    write_by_id = {row["command_id"]: row for row in snapshot["write_surfaces"]}
    assert set(write_by_id) == {"job.cancel", "job.resume", "job.suspend", "job.trigger", "send_turn"}
    assert all(row["mutating"] is True for row in write_by_id.values())

    assistant_by_id = {row["command_id"]: row for row in snapshot["assistant_bridge_commands"]}
    assert set(assistant_by_id) == {
        "assistant.execution_session_apply_diff",
        "assistant.execution_session_close",
        "assistant.execution_session_list_artifacts",
        "assistant.execution_session_open",
        "assistant.execution_session_read_artifact",
        "assistant.execution_session_validate",
        "assistant.file_read",
        "assistant.ledger_list_active",
        "assistant.ledger_read_item",
        "assistant.pis_improvement_trigger",
        "assistant.repo_search",
    }
    assert all(row.get("retry_owner") == "backend_boundary_with_idempotency_key" for row in assistant_by_id.values())


def test_command_request_semantics_cover_success_rejected_and_backend_unavailable() -> None:
    accepted = boundary.evaluate_command_request(
        {
            "request_id": "req_ok",
            "command_id": "send_turn",
            "payload": {
                "binding": {"type": "task_id", "id": "mbu"},
                "role": "builder",
                "message": "hello",
            },
        }
    )
    assert accepted["status"] == boundary.COMMAND_STATUS_ACCEPTED
    assert accepted["ok"] is True
    assert accepted["reason_code"] == ""

    rejected = boundary.evaluate_command_request(
        {"request_id": "req_bad", "command_id": "job.delete_all", "payload": {"job_id": "demo"}}
    )
    assert rejected["status"] == boundary.COMMAND_STATUS_REJECTED
    assert rejected["ok"] is False
    assert rejected["reason_code"] == boundary.REASON_UNSUPPORTED_COMMAND

    invalid = boundary.evaluate_command_request(
        {"request_id": "req_missing", "command_id": "job.trigger", "payload": {}}
    )
    assert invalid["status"] == boundary.COMMAND_STATUS_REJECTED
    assert invalid["reason_code"] == boundary.REASON_INVALID_REQUEST
    assert invalid["missing_fields"] == ["job_id"]

    unavailable = boundary.evaluate_command_request(
        {"request_id": "req_down", "command_id": "job.cancel", "payload": {"job_id": "demo"}},
        backend_reachable=False,
    )
    assert unavailable["status"] == boundary.COMMAND_STATUS_BACKEND_UNAVAILABLE
    assert unavailable["ok"] is False
    assert unavailable["reason_code"] == boundary.REASON_BACKEND_UNREACHABLE


def test_process_command_request_deduplicates_replays_for_send_turn(tmp_path: Path) -> None:
    req = {
        "request_id": "req_send_1",
        "idempotency_key": "idem_send_1",
        "command_id": "send_turn",
        "payload": {
            "binding": {"type": "task_id", "id": "mbu"},
            "role": "builder",
            "message": "hello",
        },
    }
    first = boundary.process_command_request(req, repo_root=tmp_path, writer="tests.send_turn.first")
    assert first["status"] == boundary.COMMAND_STATUS_ACCEPTED
    assert first["retry_owner"] == "backend_boundary"

    second = boundary.process_command_request(req, repo_root=tmp_path, writer="tests.send_turn.second")
    assert second["status"] == boundary.COMMAND_STATUS_DUPLICATE_SUPPRESSED
    assert second["reason_code"] == boundary.REASON_DUPLICATE_SUPPRESSED
    assert second["duplicate_of_request_id"] == "req_send_1"

    state_path = tmp_path / "runtime" / "state" / "controlplane" / "backend_api_boundary" / "command_idempotency_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    row = state["commands_by_key"]["idempotency_key:idem_send_1"]
    assert row["duplicate_hits"] == 1


def test_process_command_request_rejects_explicit_idempotency_conflict(tmp_path: Path) -> None:
    first = boundary.process_command_request(
        {
            "request_id": "req_job_1",
            "idempotency_key": "idem_job_shared",
            "command_id": "job.trigger",
            "payload": {"job_id": "overnight_1"},
        },
        repo_root=tmp_path,
        writer="tests.job.first",
    )
    assert first["status"] == boundary.COMMAND_STATUS_ACCEPTED

    conflict = boundary.process_command_request(
        {
            "request_id": "req_job_2",
            "idempotency_key": "idem_job_shared",
            "command_id": "job.cancel",
            "payload": {"job_id": "overnight_1"},
        },
        repo_root=tmp_path,
        writer="tests.job.conflict",
    )
    assert conflict["status"] == boundary.COMMAND_STATUS_REJECTED
    assert conflict["reason_code"] == boundary.REASON_IDEMPOTENCY_CONFLICT


def test_process_command_request_timeout_stays_ambiguous_and_retry_is_suppressed(tmp_path: Path) -> None:
    req = {
        "request_id": "req_timeout_1",
        "idempotency_key": "idem_timeout_1",
        "command_id": "job.cancel",
        "payload": {"job_id": "nightly_timeout"},
    }
    ambiguous = boundary.process_command_request(
        req,
        repo_root=tmp_path,
        transport_timed_out=True,
        writer="tests.timeout.first",
    )
    assert ambiguous["status"] == boundary.COMMAND_STATUS_AMBIGUOUS_TIMEOUT
    assert ambiguous["reason_code"] == boundary.REASON_AMBIGUOUS_TIMEOUT
    assert ambiguous["retry_owner"] == "caller_retry_same_idempotency_key"

    replay = boundary.process_command_request(req, repo_root=tmp_path, writer="tests.timeout.retry")
    assert replay["status"] == boundary.COMMAND_STATUS_DUPLICATE_SUPPRESSED
    assert replay["original_status"] == boundary.COMMAND_STATUS_AMBIGUOUS_TIMEOUT


def test_assistant_bridge_boundary_enforces_explicit_idempotency_scope_and_intent() -> None:
    missing_idempotency = boundary.evaluate_command_request(
        {
            "request_id": "assistant_repo_search_missing_idem",
            "command_id": "assistant.repo_search",
            "intent_classification": "read_only",
            "token_scopes": ["repo.read"],
            "payload": {"query": "assistant bridge"},
        }
    )
    assert missing_idempotency["status"] == boundary.COMMAND_STATUS_REJECTED
    assert missing_idempotency["reason_code"] == boundary.REASON_MISSING_IDEMPOTENCY_KEY

    forbidden_scope = boundary.evaluate_command_request(
        {
            "request_id": "assistant_live_diff_scope_check",
            "command_id": "assistant.execution_session_apply_diff",
            "idempotency_key": "idem_live_diff_scope_check",
            "intent_classification": "isolated_trial",
            "token_scopes": ["diff.apply"],
            "payload": {
                "session_id": "assistant_session_demo",
                "execution_mode": "live",
                "base_commit_sha": "abc123",
                "diff_text": "diff --git a/pkg/demo.py b/pkg/demo.py",
                "diff_hash": "deadbeef",
                "expected_changed_files": ["pkg/demo.py"],
            },
        }
    )
    assert forbidden_scope["status"] == boundary.COMMAND_STATUS_REJECTED
    assert forbidden_scope["reason_code"] == boundary.REASON_FORBIDDEN_SCOPE
    assert forbidden_scope["missing_scopes"] == ["repo.write.live"]
    assert boundary.http_status_for_result(forbidden_scope) == 403

    prohibited_intent = boundary.evaluate_command_request(
        {
            "request_id": "assistant_repo_search_bad_intent",
            "command_id": "assistant.repo_search",
            "idempotency_key": "idem_repo_search_bad_intent",
            "intent_classification": "isolated_trial",
            "token_scopes": ["repo.read"],
            "payload": {"query": "assistant bridge"},
        }
    )
    assert prohibited_intent["status"] == boundary.COMMAND_STATUS_REJECTED
    assert prohibited_intent["reason_code"] == boundary.REASON_PROHIBITED_INTENT
    assert boundary.http_status_for_result(prohibited_intent) == 403


def test_pis_improvement_trigger_requires_request_payload_and_session_scope() -> None:
    missing_request = boundary.evaluate_command_request(
        {
            "request_id": "assistant_pis_improvement_missing_request",
            "command_id": "assistant.pis_improvement_trigger",
            "idempotency_key": "idem_missing_request",
            "intent_classification": "isolated_trial",
            "token_scopes": ["ledger.read", "session.open"],
            "payload": {},
        }
    )
    assert missing_request["status"] == boundary.COMMAND_STATUS_REJECTED
    assert missing_request["reason_code"] == boundary.REASON_INVALID_REQUEST
    assert missing_request["missing_fields"] == ["request"]

    missing_scope = boundary.evaluate_command_request(
        {
            "request_id": "assistant_pis_improvement_missing_scope",
            "command_id": "assistant.pis_improvement_trigger",
            "idempotency_key": "idem_missing_scope",
            "intent_classification": "isolated_trial",
            "token_scopes": ["ledger.read"],
            "payload": {"request": {"schema_version": "pis_improvement_entrypoint_request_v1", "action": "launch"}},
        }
    )
    assert missing_scope["status"] == boundary.COMMAND_STATUS_REJECTED
    assert missing_scope["reason_code"] == boundary.REASON_FORBIDDEN_SCOPE
    assert missing_scope["missing_scopes"] == ["session.open"]
