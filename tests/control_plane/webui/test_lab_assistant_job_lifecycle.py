from __future__ import annotations

from pathlib import Path

import pytest

from infra.controlplane import continuation_packet_store as packet_store
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as register


def _request() -> dict:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "delegate lifecycle work",
            "preferred_surface": "workflow",
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )
    return policy.build_typed_delegation_request(
        classification=classification,
        surface="mailbox",
        owner_ref={"task_id": "task_c8d5", "ledger_uid": "PIS-20260312-224201Z-C8D5"},
        summary="Deliver detached job work",
        requested_action="queue detached lifecycle work",
        expected_evidence=[{"kind": "job_record", "description": "detached job lifecycle", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review detached job lifecycle"},
        request_id="mailbox_req_c8d5",
        idempotency_key="idem_c8d5",
        correlation_id="corr_c8d5",
        launch_turn_id="turn_c8d5",
        launch_cause="scheduled_build",
    )


def _handed_off(tmp_path: Path) -> dict:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="c8d5.enqueue")
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_c8d5",
        writer="c8d5.claim",
        lease_s=60,
        now_utc="2026-03-12T23:00:00Z",
    )["request_record"]
    return mailbox.accept_claim_handoff(
        tmp_path,
        request_id="mailbox_req_c8d5",
        worker_id="worker_c8d5",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_c8d5_01",
        job_surface="workflow",
        writer="c8d5.handoff",
    )


def _bootstrap_task_register(tmp_path: Path) -> None:
    (tmp_path / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    res = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="c8d5.task",
        task={
            "task_id": "task_c8d5",
            "kind": "software",
            "title": "Lifecycle checkpoint tracking",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-224201Z-C8D5",
            "next_expected_action": "monitor detached job",
        },
    )
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(res["task_register"]["write_generation"]),
        writer="c8d5.ownership",
        task_id="task_c8d5",
        selected_surface="pipeline",
        execution_id="job_c8d5_01",
        execution_ref="job_c8d5_01",
        binding_ref="mailbox_req_c8d5",
        job_uri="runtime/state/lab_assistant/detached_jobs/jobs/job_c8d5_01.json",
        trace_id="corr_c8d5",
        reference_status="execution_ref_active",
        reference_status_reason="mailbox_handoff",
    )


def test_handoff_creates_post_mailbox_lifecycle_record(tmp_path: Path) -> None:
    mailbox.enqueue_request(tmp_path, request=_request(), writer="c8d5.enqueue")
    assert jobs.read_snapshot(tmp_path, initialize_if_missing=False)["job_count"] == 0

    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_c8d5",
        writer="c8d5.claim",
        lease_s=60,
        now_utc="2026-03-12T23:00:00Z",
    )["request_record"]
    handed_off = mailbox.accept_claim_handoff(
        tmp_path,
        request_id="mailbox_req_c8d5",
        worker_id="worker_c8d5",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_c8d5_01",
        job_surface="workflow",
        writer="c8d5.handoff",
    )

    lifecycle = handed_off["job_lifecycle"]["job_record"]
    assert lifecycle["detached_job_id"] == "job_c8d5_01"
    assert lifecycle["state"] == "queued"
    assert lifecycle["mailbox_request_id"] == "mailbox_req_c8d5"
    assert jobs.read_snapshot(tmp_path, initialize_if_missing=False)["counts"]["queued"] == 1


def test_heartbeat_stale_timeout_and_late_result_are_durable(tmp_path: Path) -> None:
    _handed_off(tmp_path)
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="step 1",
        now_utc="2026-03-12T23:01:00Z",
    )

    stale = jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.stale",
        mode="steady_state",
        now_utc="2026-03-12T23:04:10Z",
    )
    assert stale["changed_job_ids"] == ["job_c8d5_01"]
    assert jobs.read_job(tmp_path, detached_job_id="job_c8d5_01")["state"] == "heartbeat_stale"

    timed = jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.timeout",
        mode="steady_state",
        now_utc="2026-03-13T00:10:00Z",
    )
    assert timed["changed_job_ids"] == ["job_c8d5_01"]
    assert jobs.read_job(tmp_path, detached_job_id="job_c8d5_01")["state"] == "timed_out"

    completed = jobs.record_job_result(
        tmp_path,
        detached_job_id="job_c8d5_01",
        status="completed",
        summary="late completion arrived",
        writer="c8d5.result",
        producer="test",
        result_id="result_late",
        now_utc="2026-03-13T00:11:00Z",
    )["job_record"]
    assert completed["state"] == "completed"
    assert completed["late_result"] is True
    assert completed["late_result_pending_reconciliation"] is True
    assert completed["late_result_basis_state"] == "timed_out"
    assert completed["terminal_result"]["result_id"] == "result_late"

    pending = jobs.read_job(tmp_path, detached_job_id="job_c8d5_01")
    assert pending["reconciliation"]["late_result_reconciled_utc"] == ""

    reconciled = jobs.reconcile_late_result(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.reconcile.late_result",
        final_state="completed",
        reason="operator_reviewed_late_result",
        now_utc="2026-03-13T00:11:30Z",
    )["job_record"]
    assert reconciled["state"] == "completed"
    assert reconciled["late_result_pending_reconciliation"] is False
    assert reconciled["reconciliation"]["late_result_reconciled_by"] == "c8d5.reconcile.late_result"

    superseded = jobs.record_job_result(
        tmp_path,
        detached_job_id="job_c8d5_01",
        status="failed",
        summary="straggler failure",
        writer="c8d5.result.superseded",
        producer="test",
        result_id="result_superseded",
        now_utc="2026-03-13T00:12:00Z",
    )["job_record"]
    assert superseded["state"] == "completed"
    assert superseded["superseded_result_count"] == 1
    assert superseded["superseded_results"][0]["result_id"] == "result_superseded"


def test_cancellation_and_startup_reconcile_preserve_distinct_truth(tmp_path: Path) -> None:
    _handed_off(tmp_path)
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        now_utc="2026-03-12T23:01:00Z",
    )

    interrupted = jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.startup",
        mode="startup",
        now_utc="2026-03-12T23:04:10Z",
    )["job_snapshot"]
    assert interrupted["counts"]["interrupted"] == 1
    assert jobs.read_job(tmp_path, detached_job_id="job_c8d5_01")["state"] == "interrupted"

    cancelled = jobs.cancel_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.cancel",
        reason="operator_cancelled",
        now_utc="2026-03-12T23:05:00Z",
    )["job_record"]
    assert cancelled["state"] == "cancelled"
    assert cancelled["cancellation_requested"] is True

    ambiguous = jobs.record_job_result(
        tmp_path,
        detached_job_id="job_c8d5_01",
        status="completed",
        summary="worker finished after cancel",
        writer="c8d5.result.after_cancel",
        producer="test",
        result_id="result_after_cancel",
        now_utc="2026-03-12T23:06:00Z",
    )["job_record"]
    assert ambiguous["state"] == "ambiguous"
    assert ambiguous["late_result"] is True
    assert ambiguous["late_result_pending_reconciliation"] is True


def test_startup_reconcile_marks_unproven_detached_job_interrupted_after_restart(tmp_path: Path) -> None:
    _handed_off(tmp_path)
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="mid-flight checkpoint",
        now_utc="2026-03-12T23:01:00Z",
    )

    reconciled = jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.startup",
        mode="startup",
        now_utc="2026-03-12T23:04:10Z",
    )

    record = jobs.read_job(tmp_path, detached_job_id="job_c8d5_01")
    assert reconciled["changed_job_ids"] == ["job_c8d5_01"]
    assert record["state"] == "interrupted"
    assert record["reconciliation"]["last_reconcile_mode"] == "startup"


def test_detached_job_heartbeat_records_checkpoint_in_task_register(tmp_path: Path) -> None:
    _bootstrap_task_register(tmp_path)
    _handed_off(tmp_path)

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="step 1",
        now_utc="2026-03-12T23:01:00Z",
    )

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == "task_c8d5")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == task.get("current_run_id"))
    checkpoint = dict(run_row.get("checkpoint") or {})
    assert checkpoint["checkpoint_id"] == "job:job_c8d5_01"
    assert checkpoint["checkpoint_status"] == "recorded"
    assert checkpoint["progress_summary"] == "step 1"
    assert checkpoint["checkpoint_source"] == "c8d5.heartbeat"
    assert run_row.get("resume_decision") == {}


def test_detached_job_interrupt_records_resume_decision(tmp_path: Path) -> None:
    _bootstrap_task_register(tmp_path)
    _handed_off(tmp_path)

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="mid-flight checkpoint",
        now_utc="2026-03-12T23:01:00Z",
    )

    jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.startup",
        mode="startup",
        now_utc="2026-03-12T23:04:10Z",
    )

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == "task_c8d5")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == task.get("current_run_id"))
    checkpoint = dict(run_row.get("checkpoint") or {})
    assert checkpoint["checkpoint_id"] == "job:job_c8d5_01"
    assert checkpoint["checkpoint_status"] == "stale"
    assert checkpoint["progress_summary"] == "mid-flight checkpoint"
    decision = dict(run_row.get("resume_decision") or {})
    assert decision["decision"] == "resume"
    assert decision["decision_reason"] == "job_state:interrupted"
    assert decision["checkpoint_id"] == "job:job_c8d5_01"


def test_detached_job_interrupt_without_checkpoint_records_retry(tmp_path: Path) -> None:
    _bootstrap_task_register(tmp_path)
    _handed_off(tmp_path)

    jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.startup",
        mode="startup",
        now_utc="2026-03-12T23:04:10Z",
    )

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == "task_c8d5")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == task.get("current_run_id"))
    decision = dict(run_row.get("resume_decision") or {})
    assert decision["decision"] == "retry"
    assert decision["decision_reason"] == "job_state:interrupted"


def test_continuation_packet_emits_partial_then_interrupted_revision(tmp_path: Path) -> None:
    _handed_off(tmp_path)

    no_packet = jobs.emit_continuation_packet(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.packet.queued",
        now_utc="2026-03-12T23:00:30Z",
    )
    assert no_packet["emitted"] is False
    assert no_packet["reason"] == "insufficient_lifecycle_truth"
    assert packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path) is None

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="mid-flight checkpoint",
        now_utc="2026-03-12T23:01:00Z",
    )
    partial = packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path)
    assert partial is not None
    assert partial["packet_revision"] == 1
    assert partial["packet"]["result_summary"]["outcome"] == "partial"
    assert partial["packet"]["failed_paths"][0]["path_id"] == "fp_terminal_not_proven"

    jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.startup",
        mode="startup",
        now_utc="2026-03-12T23:04:10Z",
    )
    interrupted = packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path)
    assert interrupted is not None
    assert interrupted["packet_revision"] == 2
    assert interrupted["packet"]["result_summary"]["outcome"] == "interrupted"
    assert interrupted["packet"]["recommended_next_steps"][0]["step_id"] == "step_resume_or_retry"


def test_continuation_packet_blocks_running_state_without_checkpoint_summary(tmp_path: Path) -> None:
    _handed_off(tmp_path)

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        now_utc="2026-03-12T23:01:00Z",
    )

    blocked = jobs.emit_continuation_packet(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.packet.running",
        now_utc="2026-03-12T23:01:30Z",
    )
    assert blocked["emitted"] is False
    assert blocked["reason"] == "ambiguous_checkpoint_state"
    assert packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path) is None


def test_continuation_packet_repeated_emit_is_idempotent_for_unchanged_checkpoint(tmp_path: Path) -> None:
    _handed_off(tmp_path)

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="mid-flight checkpoint",
        now_utc="2026-03-12T23:01:00Z",
    )

    first = packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path)
    assert first is not None

    repeated = jobs.emit_continuation_packet(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.packet.repeat",
        now_utc="2026-03-12T23:01:30Z",
    )
    assert repeated["emitted"] is False
    assert repeated["reason"] == "idempotent_existing_packet"

    latest = packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path)
    rows = packet_store.read_store_records(repo_root=tmp_path)
    assert latest is not None
    assert latest["packet_revision"] == first["packet_revision"]
    assert [row["record_kind"] for row in rows] == ["packet_revision"]


def test_continuation_packet_blocks_clean_success_until_late_result_is_reconciled(tmp_path: Path) -> None:
    _handed_off(tmp_path)
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.heartbeat",
        worker_id="worker_c8d5",
        progress_message="checkpoint before timeout",
        now_utc="2026-03-12T23:01:00Z",
    )
    jobs.reconcile_jobs(
        tmp_path,
        writer="c8d5.reconcile.timeout",
        mode="steady_state",
        now_utc="2026-03-13T00:10:00Z",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_c8d5_01",
        status="completed",
        summary="late completion arrived",
        writer="c8d5.result",
        producer="test",
        result_id="result_late",
        evidence_refs=["artifacts/late_result.json"],
        now_utc="2026-03-13T00:11:00Z",
    )

    blocked = packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path)
    assert blocked is not None
    assert blocked["packet"]["result_summary"]["outcome"] == "blocked"
    assert blocked["packet"]["confidence"]["level"] == "low"
    assert blocked["packet"]["artifact_index"][-1]["uri"] == "artifacts/late_result.json"

    jobs.reconcile_late_result(
        tmp_path,
        detached_job_id="job_c8d5_01",
        writer="c8d5.reconcile.late_result",
        final_state="completed",
        reason="operator_reviewed_late_result",
        now_utc="2026-03-13T00:11:30Z",
    )
    succeeded = packet_store.latest_valid_packet(lookup_key="job_id", lookup_value="job_c8d5_01", repo_root=tmp_path)
    assert succeeded is not None
    assert succeeded["packet_revision"] == blocked["packet_revision"] + 1
    assert succeeded["packet"]["result_summary"]["outcome"] == "succeeded"
    assert succeeded["packet"]["confidence"]["level"] == "high"


def test_accept_mailbox_handoff_rejects_detached_job_identity_collision(tmp_path: Path) -> None:
    first = _handed_off(tmp_path)["request_record"]

    collision = dict(first)
    collision["request_id"] = "mailbox_req_collision"
    collision["request"] = dict(collision.get("request") or {})
    collision["request"]["request_id"] = "mailbox_req_collision"
    collision["request"]["correlation_id"] = "corr_collision"
    collision["request"]["launch_turn_id"] = "turn_collision"
    collision["handoff"] = dict(collision.get("handoff") or {})
    collision["handoff"]["claim_id"] = "claim_collision"

    with pytest.raises(jobs.DetachedJobLifecycleError, match="detached job id collision"):
        jobs.accept_mailbox_handoff(
            tmp_path,
            request_record=collision,
            writer="c8d5.handoff.collision",
            now_utc=collision["handoff"]["accepted_utc"],
        )
