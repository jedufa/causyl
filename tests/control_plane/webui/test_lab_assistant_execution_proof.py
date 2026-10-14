from __future__ import annotations

import json
from pathlib import Path

from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_execution_proof as proof
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_routing_policy as routing
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_writeback as writeback


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)


def _seed_task(root: Path, *, task_id: str = "task_g7b1") -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="g7b1.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "Prove delegated execution honestly",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-224201Z-G7B1",
            "next_expected_action": "collect delegated execution proof",
        },
    )["task_register"]


def _classification(*, preferred_surface: str = "workflow") -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": "delegate proof-producing work",
            "preferred_surface": preferred_surface,
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )


def _mailbox_request(*, task_id: str, input_payload: dict | None = None) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification(),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-224201Z-G7B1"},
        summary="Queue proof-bearing delegated work",
        requested_action="enqueue durable mailbox request",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review proof output", "owner": "assistant"},
        request_id=f"delegate_{task_id}",
        idempotency_key=f"idem_{task_id}",
        correlation_id=f"corr_{task_id}",
        launch_turn_id=f"turn_{task_id}",
        launch_cause="scheduled_build",
        input_payload=dict(input_payload or {}),
    )


def _workflow_route_decision(task_id: str) -> dict:
    request = policy.build_typed_delegation_request(
        classification=_classification(preferred_surface="workflow"),
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-224201Z-G7B1"},
        summary="Route to workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"routing": {"multi_step": True, "workflow_step_count": 3}},
    )
    routed = routing.route_typed_delegation_request(request)
    assert routed["ok"] is True
    return dict(routed["decision"])


def test_execution_proof_end_to_end_report_spans_launch_to_writeback(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    seeded = _seed_task(tmp_path)
    task_id = "task_g7b1"
    route_decision = _workflow_route_decision(task_id)
    request = _mailbox_request(task_id=task_id, input_payload={"routing_decision": route_decision})

    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="g7b1.bind",
        request=request,
        launch_result={},
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_g7b1",
        writer="g7b1.claim",
        lease_s=60,
        now_utc="2026-03-13T01:20:00Z",
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_g7b1",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_g7b1",
        job_surface="workflow",
        writer="g7b1.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_g7b1",
        status="completed",
        summary="workflow completed with durable proof",
        writer="g7b1.result",
        producer="test",
        result_id="result_g7b1",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_g7b1.json"],
        now_utc="2026-03-13T01:25:00Z",
    )
    outcome = writeback.reconcile_task(tmp_path, task_id=task_id, writer="g7b1.writeback")

    report = proof.read_latest_report(tmp_path, now_utc="2026-03-13T01:26:00Z")
    by_id = {row["check_id"]: row for row in report["checks"]}
    trace = report["traces"][0]

    assert outcome["proof_report_error"] == ""
    assert report["summary"]["overall_status"] == "ok"
    assert report["freshness"]["stale"] is False
    assert by_id["request_launch_and_correlation"]["status"] == "pass"
    assert by_id["route_selection_audit"]["status"] == "pass"
    assert by_id["writeback_integrity"]["status"] == "pass"
    assert by_id["operator_surface_truth"]["status"] == "pass"
    assert trace["stage_statuses"]["launch"]["status"] == "pass"
    assert trace["stage_statuses"]["claim"]["status"] == "pass"
    assert trace["stage_statuses"]["execution"]["status"] == "pass"
    assert trace["stage_statuses"]["result_delivery"]["status"] == "pass"
    assert trace["stage_statuses"]["writeback"]["status"] == "pass"
    assert trace["stage_statuses"]["operator_surface"]["status"] == "pass"
    assert Path(report["report_paths"]["report_path"]).exists()
    assert Path(report["report_paths"]["receipt_path"]).exists()


def test_execution_proof_read_latest_labels_stale_without_reusing_old_green(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    proof.record_execution_proof_report(tmp_path, writer="g7b1.proof", now_utc="2026-03-13T00:00:00Z")

    stale = proof.read_latest_report(tmp_path, now_utc="2026-03-13T02:00:01Z", max_age_s=3600)

    assert stale["freshness"]["stale"] is True
    assert stale["summary"]["overall_status"] == "stale"


def test_execution_proof_warns_when_route_selection_lacks_durable_breadcrumb(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_g7b1_gap")
    request = policy.build_typed_delegation_request(
        classification=_classification(preferred_surface="workflow"),
        surface="workflow",
        owner_ref={"task_id": "task_g7b1_gap", "ledger_uid": "PIS-20260312-224201Z-G7B1"},
        summary="Launch direct workflow without persisted route breadcrumb",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow proof", "owner": "assistant"},
        request_id="delegate_task_g7b1_gap",
        correlation_id="corr_task_g7b1_gap",
        launch_turn_id="turn_task_g7b1_gap",
    )
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="g7b1.gap.bind",
        request=request,
        launch_result={"workflow_id": "wf_g7b1_gap"},
    )
    ref = bound["delegation_ref"]
    state_path = Path(ref["status_refs"][0])
    digest_path = Path(ref["evidence_candidates"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"workflow_id": "wf_g7b1_gap", "status": "completed"}), encoding="utf-8")
    digest_path.write_text(json.dumps({"summary": "proof"}), encoding="utf-8")

    report = proof.build_execution_proof_report(tmp_path, writer="g7b1.gap.report", now_utc="2026-03-13T01:00:00Z")
    by_id = {row["check_id"]: row for row in report["checks"]}

    assert by_id["route_selection_audit"]["status"] == "warn"
    assert report["summary"]["overall_status"] == "proof_gap"


def test_execution_proof_fails_duplicate_self_check_when_snapshot_has_shared_key(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_task(tmp_path, task_id="task_g7b1_dup")
    request_one = _mailbox_request(task_id="task_g7b1_dup", input_payload={"routing_decision": _workflow_route_decision("task_g7b1_dup")})
    request_two = policy.build_typed_delegation_request(
        classification=_classification(),
        surface="mailbox",
        owner_ref={"task_id": "task_g7b1_dup", "ledger_uid": "PIS-20260312-224201Z-G7B1"},
        summary="Queue proof-bearing delegated work duplicate",
        requested_action="enqueue durable mailbox request",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review proof output", "owner": "assistant"},
        request_id="delegate_task_g7b1_dup_second",
        idempotency_key=request_one["idempotency_key"],
        correlation_id="corr_task_g7b1_dup_second",
        launch_turn_id="turn_task_g7b1_dup_second",
        launch_cause="scheduled_build",
        input_payload={"routing_decision": _workflow_route_decision("task_g7b1_dup")},
    )

    mailbox._store_record(  # type: ignore[attr-defined]
        tmp_path,
        {
            "request_id": request_one["request_id"],
            "state": "queued",
            "writer": "g7b1.dup.seed1",
            "request": request_one,
        },
    )
    mailbox._store_record(  # type: ignore[attr-defined]
        tmp_path,
        {
            "request_id": request_two["request_id"],
            "state": "queued",
            "writer": "g7b1.dup.seed2",
            "request": request_two,
        },
    )
    mailbox._write_snapshot(tmp_path, writer="g7b1.dup.snapshot")  # type: ignore[attr-defined]

    report = proof.build_execution_proof_report(tmp_path, writer="g7b1.dup.report", now_utc="2026-03-13T01:00:00Z")
    by_id = {row["check_id"]: row for row in report["checks"]}
    duplicate_check = by_id["duplicate_suppression_self_check"]

    assert duplicate_check["status"] == "fail"
    assert duplicate_check["details"]["duplicate_keys"] == {
        "idempotency_key:idem_task_g7b1_dup": [
            "delegate_task_g7b1_dup",
            "delegate_task_g7b1_dup_second",
        ]
    }
