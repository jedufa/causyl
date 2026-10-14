from __future__ import annotations

from pathlib import Path

from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_execution_proof as proof
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_routing_policy as routing
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_writeback as writeback


_LEGACY_UID = "PIS-20260313-094306Z-X8Z2"


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)


def _seed_task(root: Path, *, task_id: str = "task_x8z2") -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="x8z2.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": f"Cross-workstream gate {task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": _LEGACY_UID,
            "next_expected_action": "prove integrated seam health",
        },
    )["task_register"]


def _classification(*, preferred_surface: str = "workflow") -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": "cross-workstream gate proof",
            "preferred_surface": preferred_surface,
            "estimated_duration_s": 240,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "multi_step": preferred_surface == "workflow",
        }
    )


def _mailbox_route_decision(*, task_id: str) -> dict:
    request = policy.build_typed_delegation_request(
        classification=_classification(preferred_surface="workflow"),
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": _LEGACY_UID},
        summary="Route delegated execution into workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"routing": {"multi_step": True, "cross_role": True, "workflow_step_count": 3, "code_changes_expected": True}},
        request_id=f"route_decision_{task_id}",
        idempotency_key=f"idem_route_{task_id}",
        correlation_id=f"corr_route_{task_id}",
        launch_turn_id=f"turn_route_{task_id}",
    )
    routed = routing.route_typed_delegation_request(request)
    assert routed["ok"] is True
    return dict(routed["decision"])


def _mailbox_request(*, task_id: str, request_id: str, correlation_id: str, launch_turn_id: str, idempotency_key: str, input_payload: dict | None = None) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification(),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": _LEGACY_UID},
        summary="Queue gate work",
        requested_action="enqueue durable mailbox request",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review detached result", "owner": "assistant"},
        request_id=request_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        launch_turn_id=launch_turn_id,
        launch_cause="scheduled_build",
        input_payload=dict(input_payload or {}),
    )


def _bind_request(root: Path, *, task_id: str, request: dict, writer: str, launch_result: dict) -> dict:
    current = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
    return bindings.bind_delegation_request(
        root,
        expected_generation=int(current["write_generation"]),
        writer=writer,
        request=request,
        launch_result=launch_result,
    )


def _claim_and_handoff(root: Path, *, request_id: str, detached_job_id: str, worker_id: str, claim_now_utc: str, handoff_writer: str) -> dict:
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


def _check_statuses(report: dict) -> dict[str, str]:
    return {row["check_id"]: row["status"] for row in report["checks"]}


def _find_task(register_obj: dict, task_id: str) -> dict:
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for task in register_obj.get(bucket_name, []):
            if task.get("task_id") == task_id:
                return task
    raise AssertionError(f"task not found: {task_id}")


def test_x8z2_gate_green_path_passes_all_cross_workstream_checks(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    first_boot = host.bootstrap_host_baseline(tmp_path)
    _seed_task(tmp_path)
    second_boot = host.bootstrap_host_baseline(tmp_path)

    route_decision = _mailbox_route_decision(task_id="task_x8z2")
    request = _mailbox_request(
        task_id="task_x8z2",
        request_id="delegate_x8z2_green",
        correlation_id="corr_x8z2_green",
        launch_turn_id="turn_x8z2_green",
        idempotency_key="idem_x8z2_green",
        input_payload={"routing_decision": route_decision},
    )
    bound = _bind_request(
        tmp_path,
        task_id="task_x8z2",
        request=request,
        writer="x8z2.green.bind",
        launch_result={"workflow_id": "wf_x8z2_green"},
    )

    _claim_and_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        detached_job_id="job_x8z2_green",
        worker_id="worker_x8z2_green",
        claim_now_utc="2026-03-13T01:00:00Z",
        handoff_writer="x8z2.green.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_x8z2_green",
        status="completed",
        summary="cross-workstream seam proof",
        writer="x8z2.green.result",
        producer="test",
        result_id="result_x8z2_green",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_x8z2_green.json"],
        now_utc="2026-03-13T01:05:00Z",
    )
    reattached = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_x8z2_green",
        current_turn_id="turn_x8z2_green_review",
        writer="x8z2.green.reattach",
        mark_delivered=True,
    )
    outcome = writeback.reconcile_task(tmp_path, task_id="task_x8z2", writer="x8z2.green.writeback")
    report = proof.record_execution_proof_report(tmp_path, writer="x8z2.green.proof", now_utc="2026-03-13T01:06:00Z")

    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, "task_x8z2")
    mailbox_snapshot = mailbox.read_mailbox_snapshot(tmp_path, initialize_if_missing=False)
    correlation_snapshot = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    report_checks = _check_statuses(report)

    assert first_boot["status"] == "ok"
    assert first_boot["mode"] in {"clean_boot", "interrupted_restart"}
    assert second_boot["mode"] == "interrupted_restart"
    assert second_boot["status"] == "ok"
    assert task["status"] == "completed"
    assert task["delegated_outcome_writeback"]["assimilation_status"] == "successful_assimilation"
    assert reattached["delivered_result_ids"] == ["result_x8z2_green"]
    assert mailbox_snapshot["request_count"] == 1
    assert correlation_snapshot["delivery_counts"]["delivered"] == 1
    assert correlation_snapshot["records"][0]["latest_delivery_status"] == "delivered"

    operator = operator_snapshot.build_operator_snapshot(tmp_path)
    operator_task = next(
        row
        for row in list(operator["active_tasks"]) + list(operator["completed_tasks"])
        if row["task_id"] == "task_x8z2"
    )
    operator_work = next(work for work in operator["delegated_work"] if work["task_id"] == "task_x8z2")
    assert operator_task["status"] == operator_work["status"]
    assert operator_task["status"] == "completed"

    trace = report["traces"][0]
    assert report["summary"]["overall_status"] == "ok"
    assert report_checks["request_launch_and_correlation"] == "pass"
    assert report_checks["route_selection_audit"] == "pass"
    assert report_checks["duplicate_suppression_self_check"] == "pass"
    assert report_checks["writeback_integrity"] == "pass"
    assert report_checks["operator_surface_truth"] == "pass"
    assert report_checks["restart_truth_and_reconciliation"] == "pass"
    assert trace["stage_statuses"]["launch"]["status"] == "pass"
    assert trace["stage_statuses"]["claim"]["status"] == "pass"
    assert trace["stage_statuses"]["execution"]["status"] == "pass"
    assert trace["stage_statuses"]["result_delivery"]["status"] == "pass"
    assert trace["stage_statuses"]["writeback"]["status"] == "pass"
    assert trace["result_id"] == "result_x8z2_green"
    assert report["report_paths"]["report_path"] and report["report_paths"]["receipt_path"]
    assert outcome["proof_report_error"] == ""

    # bootstrap/task-register seam must remain reconstructible after restart
    reloaded_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    assert _find_task(reloaded_register, "task_x8z2")["status"] == "completed"


def test_x8z2_gate_missing_routing_breadcrumb_is_a_gate_gap(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_task(tmp_path, task_id="task_x8z2_route_gap")

    request = _mailbox_request(
        task_id="task_x8z2_route_gap",
        request_id="delegate_x8z2_route_gap",
        correlation_id="corr_x8z2_route_gap",
        launch_turn_id="turn_x8z2_route_gap",
        idempotency_key="idem_x8z2_route_gap",
        input_payload={},
    )
    bound = _bind_request(
        tmp_path,
        task_id="task_x8z2_route_gap",
        request=request,
        writer="x8z2.route_gap.bind",
        launch_result={"workflow_id": "wf_x8z2_route_gap"},
    )
    _claim_and_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        detached_job_id="job_x8z2_route_gap",
        worker_id="worker_x8z2_route_gap",
        claim_now_utc="2026-03-13T02:00:00Z",
        handoff_writer="x8z2.route_gap.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_x8z2_route_gap",
        status="completed",
        summary="route breadcrumb intentionally omitted",
        writer="x8z2.route_gap.result",
        producer="test",
        result_id="result_x8z2_route_gap",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_x8z2_route_gap.json"],
        now_utc="2026-03-13T02:05:00Z",
    )
    writeback.reconcile_task(tmp_path, task_id="task_x8z2_route_gap", writer="x8z2.route_gap.writeback")
    report = proof.record_execution_proof_report(tmp_path, writer="x8z2.route_gap.proof", now_utc="2026-03-13T02:06:00Z")

    report_checks = _check_statuses(report)

    assert report_checks["route_selection_audit"] == "warn"
    assert report_checks["request_launch_and_correlation"] == "pass"
    assert report_checks["writeback_integrity"] == "pass"
    assert report_checks["operator_surface_truth"] == "pass"
    assert report["summary"]["overall_status"] == "proof_gap"

    route_gaps = report["checks_by_id"]["route_selection_audit"]["details"]["missing_route_breadcrumbs"] if "checks_by_id" in report else None
    if route_gaps is None:
        # tolerate report implementations that preserve only `checks`.
        route_gaps = dict(
            (row["check_id"], row["details"]) for row in report["checks"]
        )["route_selection_audit"]["missing_route_breadcrumbs"]
    assert route_gaps


def test_x8z2_gate_duplicate_idempotency_collision_fails_the_gate(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_task(tmp_path, task_id="task_x8z2_idem")

    route_decision = _mailbox_route_decision(task_id="task_x8z2_idem")
    request = _mailbox_request(
        task_id="task_x8z2_idem",
        request_id="delegate_x8z2_idem_primary",
        correlation_id="corr_x8z2_idem",
        launch_turn_id="turn_x8z2_idem",
        idempotency_key="idem_x8z2_collision",
        input_payload={"routing_decision": route_decision},
    )
    bound = _bind_request(
        tmp_path,
        task_id="task_x8z2_idem",
        request=request,
        writer="x8z2.idem.bind",
        launch_result={"workflow_id": "wf_x8z2_idem"},
    )

    _claim_and_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        detached_job_id="job_x8z2_idem",
        worker_id="worker_x8z2_idem",
        claim_now_utc="2026-03-13T03:00:00Z",
        handoff_writer="x8z2.idem.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_x8z2_idem",
        status="completed",
        summary="collision seeded execution",
        writer="x8z2.idem.result",
        producer="test",
        result_id="result_x8z2_idem",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_x8z2_idem.json"],
        now_utc="2026-03-13T03:05:00Z",
    )
    writeback.reconcile_task(tmp_path, task_id="task_x8z2_idem", writer="x8z2.idem.writeback")

    duplicate_request = dict(request)
    duplicate_request["request_id"] = "delegate_x8z2_idem_duplicate"
    mailbox._store_record(  # type: ignore[attr-defined]
        tmp_path,
        {
            "request_id": duplicate_request["request_id"],
            "state": "queued",
            "writer": "x8z2.idem.seed_duplicate",
            "request": duplicate_request,
        },
    )
    mailbox._write_snapshot(tmp_path, writer="x8z2.idem.snapshot")  # type: ignore[attr-defined]

    report = proof.record_execution_proof_report(tmp_path, writer="x8z2.idem.proof", now_utc="2026-03-13T03:06:00Z")
    report_checks = _check_statuses(report)

    duplicates = report_checks and report["checks_by_id"]["duplicate_suppression_self_check"]["details"]["duplicate_keys"] if "checks_by_id" in report else None
    if duplicates is None:
        duplicates = dict((row["check_id"], row["details"]) for row in report["checks"])["duplicate_suppression_self_check"]["duplicate_keys"]

    assert report_checks["duplicate_suppression_self_check"] == "fail"
    assert report["summary"]["overall_status"] == "failed"
    assert "idempotency_key:idem_x8z2_collision" in duplicates
    assert set(duplicates["idempotency_key:idem_x8z2_collision"]) == {"delegate_x8z2_idem_primary", "delegate_x8z2_idem_duplicate"}
