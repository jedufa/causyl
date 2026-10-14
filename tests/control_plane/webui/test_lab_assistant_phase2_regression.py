from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_execution_proof as proof
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_recovery as recovery
from infra.webui import server_lab_assistant_routing_policy as routing
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_writeback as writeback


pytestmark = [pytest.mark.regression]


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)


def _seed_task(root: Path, *, task_id: str) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="phase2.regression.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": f"Track {task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-230103Z-8C2E",
            "next_expected_action": "prove the phase-2 delegation fabric",
        },
    )["task_register"]


def _find_task(register_obj: dict, task_id: str) -> dict:
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for task in list(register_obj.get(bucket_name) or []):
            if isinstance(task, dict) and task.get("task_id") == task_id:
                return task
    raise AssertionError(f"task not found: {task_id}")


def _classification(*, preferred_surface: str = "workflow") -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": "delegate durable regression work",
            "preferred_surface": preferred_surface,
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "multi_step": preferred_surface == "workflow",
        }
    )


def _workflow_route_decision(task_id: str) -> dict:
    request = policy.build_typed_delegation_request(
        classification=_classification(preferred_surface="workflow"),
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-230103Z-8C2E"},
        summary="route delegated work to workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"routing": {"multi_step": True, "workflow_step_count": 3}},
    )
    routed = routing.route_typed_delegation_request(request)
    assert routed["ok"] is True
    return dict(routed["decision"])


def _mailbox_request(
    *,
    task_id: str,
    request_id: str,
    correlation_id: str,
    launch_turn_id: str,
    idempotency_key: str | None = None,
) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification(),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-230103Z-8C2E"},
        summary="queue phase-2 delegated work",
        requested_action="enqueue durable mailbox request",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review durable delegated output", "owner": "assistant"},
        request_id=request_id,
        idempotency_key=idempotency_key or f"idem_{task_id}",
        correlation_id=correlation_id,
        launch_turn_id=launch_turn_id,
        launch_cause="scheduled_build",
        input_payload={"routing_decision": _workflow_route_decision(task_id)},
    )


def _bind_mailbox_request(root: Path, *, task_id: str, request: dict, writer: str) -> dict:
    current = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
    return bindings.bind_delegation_request(
        root,
        expected_generation=int(current["write_generation"]),
        writer=writer,
        request=request,
        launch_result={},
    )


def _claim_and_handoff(
    root: Path,
    *,
    request_id: str,
    detached_job_id: str,
    worker_id: str,
    claim_now_utc: str,
    handoff_writer: str,
) -> dict:
    claimed = mailbox.claim_next_request(
        root,
        worker_id=worker_id,
        writer=f"{handoff_writer}.claim",
        lease_s=60,
        now_utc=claim_now_utc,
    )["request_record"]
    return mailbox.accept_claim_handoff(
        root,
        request_id=request_id,
        worker_id=worker_id,
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id=detached_job_id,
        job_surface="workflow",
        writer=handoff_writer,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_phase2_regression_green_path_spans_request_to_proof_artifacts(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_task(tmp_path, task_id="task_phase2_green")
    request = _mailbox_request(
        task_id="task_phase2_green",
        request_id="delegate_phase2_green",
        correlation_id="corr_phase2_green",
        launch_turn_id="turn_phase2_green",
    )

    bound = _bind_mailbox_request(tmp_path, task_id="task_phase2_green", request=request, writer="phase2.green.bind")
    handoff = _claim_and_handoff(
        tmp_path,
        request_id="delegate_phase2_green",
        detached_job_id="job_phase2_green",
        worker_id="worker_phase2_green",
        claim_now_utc="2026-03-13T01:20:00Z",
        handoff_writer="phase2.green.handoff",
    )
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_phase2_green",
        writer="phase2.green.heartbeat",
        worker_id="worker_phase2_green",
        progress_message="workflow checkpoint reached",
        now_utc="2026-03-13T01:21:00Z",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_phase2_green",
        status="completed",
        summary="workflow completed with durable proof",
        writer="phase2.green.result",
        producer="test",
        result_id="result_phase2_green",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_phase2_green.json"],
        now_utc="2026-03-13T01:25:00Z",
    )
    reattached = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_phase2_green",
        current_turn_id="turn_phase2_green_review",
        writer="phase2.green.reattach",
        mark_delivered=True,
    )
    outcome = writeback.reconcile_task(tmp_path, task_id="task_phase2_green", writer="phase2.green.writeback")
    report = proof.record_execution_proof_report(tmp_path, writer="phase2.green.proof", now_utc="2026-03-13T01:27:00Z")

    mailbox_snapshot = mailbox.read_mailbox_snapshot(tmp_path, initialize_if_missing=False)
    correlation_snapshot = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, "task_phase2_green")
    trace = report["traces"][0]
    check_by_id = {row["check_id"]: row for row in report["checks"]}
    proof_events = _read_jsonl(tmp_path / "runtime" / "state" / "lab_assistant" / "delegation_proof" / "proof_events.jsonl")

    assert bool(bound.get("duplicate_suppressed")) is False
    assert handoff["job_lifecycle"]["job_record"]["state"] == "queued"
    assert reattached["delivered_result_ids"] == ["result_phase2_green"]
    assert mailbox_snapshot["request_count"] == 1
    assert mailbox_snapshot["counts"]["handed_off"] == 1
    assert correlation_snapshot["delivery_counts"]["delivered"] == 1
    assert correlation_snapshot["records"][0]["latest_delivery_status"] == "delivered"
    assert outcome["task_outcome"]["assimilation_status"] == "successful_assimilation"
    assert task["status"] == "completed"
    assert task["delegated_outcome_writeback"]["assimilation_status"] == "successful_assimilation"
    assert report["summary"]["overall_status"] == "ok"
    assert check_by_id["request_launch_and_correlation"]["status"] == "pass"
    assert check_by_id["route_selection_audit"]["status"] == "pass"
    assert check_by_id["duplicate_suppression_self_check"]["status"] == "pass"
    assert check_by_id["writeback_integrity"]["status"] == "pass"
    assert check_by_id["operator_surface_truth"]["status"] == "pass"
    assert trace["result_delivery_status"] == "delivered"
    assert trace["stage_statuses"]["launch"]["status"] == "pass"
    assert trace["stage_statuses"]["claim"]["status"] == "pass"
    assert trace["stage_statuses"]["execution"]["status"] == "pass"
    assert trace["stage_statuses"]["result_delivery"]["status"] == "pass"
    assert trace["stage_statuses"]["writeback"]["status"] == "pass"
    assert Path(report["report_paths"]["report_path"]).exists()
    assert Path(report["report_paths"]["receipt_path"]).exists()
    assert proof_events[-1]["event"] == "proof_report_recorded"


def test_phase2_regression_duplicate_restart_truth_stays_honest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_task(tmp_path, task_id="task_phase2_restart")
    request = _mailbox_request(
        task_id="task_phase2_restart",
        request_id="delegate_phase2_restart",
        correlation_id="corr_phase2_restart",
        launch_turn_id="turn_phase2_restart",
        idempotency_key="idem_phase2_restart",
    )

    first = _bind_mailbox_request(tmp_path, task_id="task_phase2_restart", request=request, writer="phase2.restart.bind")
    second = _bind_mailbox_request(tmp_path, task_id="task_phase2_restart", request=request, writer="phase2.restart.bind_again")
    _claim_and_handoff(
        tmp_path,
        request_id="delegate_phase2_restart",
        detached_job_id="job_phase2_restart",
        worker_id="worker_phase2_restart",
        claim_now_utc="2026-03-13T02:00:00Z",
        handoff_writer="phase2.restart.handoff",
    )
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_phase2_restart",
        writer="phase2.restart.heartbeat",
        worker_id="worker_phase2_restart",
        progress_message="work in flight before restart",
        now_utc="2026-03-13T02:01:00Z",
    )
    jobs.reconcile_jobs(
        tmp_path,
        writer="phase2.restart.reconcile",
        mode="startup",
        now_utc="2026-03-13T02:05:10Z",
    )
    outcome = writeback.reconcile_task(tmp_path, task_id="task_phase2_restart", writer="phase2.restart.writeback")
    report = proof.record_execution_proof_report(tmp_path, writer="phase2.restart.proof", now_utc="2026-03-13T02:06:00Z")

    mailbox_snapshot = mailbox.read_mailbox_snapshot(tmp_path, initialize_if_missing=False)
    job_record = jobs.read_job(tmp_path, detached_job_id="job_phase2_restart")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, "task_phase2_restart")
    snapshot = operator_snapshot.build_operator_snapshot(tmp_path)
    trace = report["traces"][0]
    check_by_id = {row["check_id"]: row for row in report["checks"]}

    assert bool(first.get("duplicate_suppressed")) is False
    assert bool(second.get("duplicate_suppressed")) is True
    assert second["delegation_ref"]["delegation_request_id"] == first["delegation_ref"]["delegation_request_id"]
    assert mailbox_snapshot["request_count"] == 1
    assert mailbox_snapshot["requests"][0]["idempotency_keys"] == recovery.delegation_idempotency_keys(request)
    assert job_record["state"] == "interrupted"
    assert outcome["task_outcome"]["assimilation_status"] == "ambiguous_assimilation"
    assert task["status"] == "interrupted_recovering"
    assert task["delegated_outcome_writeback"]["decision_reason"] == "delegated_truth_ambiguous"
    assert snapshot["delegated_work"][0]["status"] == "interrupted"
    assert report["summary"]["overall_status"] == "ok"
    assert check_by_id["duplicate_suppression_self_check"]["status"] == "pass"
    assert check_by_id["duplicate_suppression_self_check"]["details"]["duplicate_keys"] == {}
    assert check_by_id["restart_truth_and_reconciliation"]["status"] == "pass"
    assert check_by_id["writeback_integrity"]["status"] == "pass"
    assert trace["job_state"] == "interrupted"
    assert trace["stage_statuses"]["result_delivery"]["status"] == "warn"
    assert trace["stage_statuses"]["operator_surface"]["status"] == "pass"


def test_phase2_regression_timeout_then_late_result_reattaches_and_completes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_task(tmp_path, task_id="task_phase2_late")
    request = _mailbox_request(
        task_id="task_phase2_late",
        request_id="delegate_phase2_late",
        correlation_id="corr_phase2_late",
        launch_turn_id="turn_phase2_late",
    )

    _bind_mailbox_request(tmp_path, task_id="task_phase2_late", request=request, writer="phase2.late.bind")
    _claim_and_handoff(
        tmp_path,
        request_id="delegate_phase2_late",
        detached_job_id="job_phase2_late",
        worker_id="worker_phase2_late",
        claim_now_utc="2026-03-13T03:00:00Z",
        handoff_writer="phase2.late.handoff",
    )
    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="job_phase2_late",
        writer="phase2.late.heartbeat",
        worker_id="worker_phase2_late",
        progress_message="started long job",
        now_utc="2026-03-13T03:01:00Z",
    )
    jobs.reconcile_jobs(
        tmp_path,
        writer="phase2.late.timeout",
        mode="steady_state",
        now_utc="2026-03-13T04:10:00Z",
    )
    timed_out = jobs.read_job(tmp_path, detached_job_id="job_phase2_late")
    first_outcome = writeback.reconcile_task(tmp_path, task_id="task_phase2_late", writer="phase2.late.writeback.before_result")

    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_phase2_late",
        status="completed",
        summary="late result arrived after timeout",
        writer="phase2.late.result",
        producer="test",
        result_id="result_phase2_late",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_phase2_late.json"],
        now_utc="2026-03-13T04:11:00Z",
    )
    pending_late = writeback.reconcile_task(tmp_path, task_id="task_phase2_late", writer="phase2.late.writeback.pending_late_result")
    jobs.reconcile_late_result(
        tmp_path,
        detached_job_id="job_phase2_late",
        writer="phase2.late.reconcile_result",
        final_state="completed",
        reason="operator_reviewed_late_result",
        now_utc="2026-03-13T04:11:30Z",
    )
    reattached = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_phase2_late",
        current_turn_id="turn_phase2_late_resume",
        writer="phase2.late.reattach",
        mark_delivered=True,
    )
    final_outcome = writeback.reconcile_task(tmp_path, task_id="task_phase2_late", writer="phase2.late.writeback.after_result")
    report = proof.record_execution_proof_report(tmp_path, writer="phase2.late.proof", now_utc="2026-03-13T04:12:00Z")

    job_record = jobs.read_job(tmp_path, detached_job_id="job_phase2_late")
    correlation_snapshot = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, "task_phase2_late")
    trace = report["traces"][0]
    reattach_events = _read_jsonl(tmp_path / "runtime" / "state" / "lab_assistant" / "correlation" / "correlation_events.jsonl")

    assert timed_out["state"] == "timed_out"
    assert first_outcome["task_outcome"]["assimilation_status"] == "ambiguous_assimilation"
    assert pending_late["task_outcome"]["assimilation_status"] == "ambiguous_assimilation"
    assert job_record["state"] == "completed"
    assert job_record["late_result"] is True
    assert job_record["late_result_pending_reconciliation"] is False
    assert reattached["delivered_result_ids"] == ["result_phase2_late"]
    assert correlation_snapshot["delivery_counts"]["delivered"] == 1
    assert final_outcome["task_outcome"]["assimilation_status"] == "successful_assimilation"
    assert task["status"] == "completed"
    assert report["summary"]["overall_status"] == "ok"
    assert trace["job_state"] == "completed"
    assert trace["result_delivery_status"] == "delivered"
    assert trace["stage_statuses"]["execution"]["status"] == "pass"
    assert trace["stage_statuses"]["result_delivery"]["status"] == "pass"
    assert any(row["event"] == "turn_reattached" for row in reattach_events)
