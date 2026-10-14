from __future__ import annotations

import json
from pathlib import Path

from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_writeback as writeback


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _bootstrap(root: Path) -> None:
    host.bootstrap_host_baseline(root)


def _seed_task(root: Path, *, task_id: str, ledger_uid: str = "PIS-20260313-094302Z-P4R6") -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="p4r6.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "Mailbox lifecycle canonical writeback",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "wait for delegated result",
        },
    )["task_register"]


def _find_task(register_obj: dict, task_id: str) -> dict:
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for task in list(register_obj.get(bucket_name) or []):
            if isinstance(task, dict) and task.get("task_id") == task_id:
                return task
    raise AssertionError(f"task not found: {task_id}")


def _classification(surface: str) -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": f"delegate via {surface}",
            "preferred_surface": surface,
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )


def _mailbox_request(task_id: str) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification("workflow"),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260313-094302Z-P4R6"},
        summary="Queue mailbox lifecycle work",
        requested_action="enqueue mailbox request",
        expected_evidence=[{"kind": "mailbox_record", "description": "mailbox lifecycle state", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review delegated result", "owner": "assistant"},
        request_id=f"delegate_{task_id}",
        idempotency_key=f"idem_{task_id}",
        correlation_id=f"corr_{task_id}",
        launch_turn_id=f"turn_{task_id}",
        launch_cause="scheduled_build",
    )


def _workflow_request(task_id: str) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification("workflow"),
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260313-094302Z-P4R6"},
        summary="Run workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "workflow digest", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )


def _deliver_mailbox_result(root: Path, *, task_id: str) -> dict:
    return correlation.reattach_results_for_turn(
        root,
        launch_turn_id=f"turn_{task_id}",
        current_turn_id=f"turn_{task_id}_review",
        writer="p4r6.reattach",
        mark_delivered=True,
    )


def test_mailbox_handoff_result_writeback_reconciles_once(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_p4r6_green")
    task_id = "task_p4r6_green"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="p4r6.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_p4r6", writer="p4r6.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_p4r6",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_p4r6_green",
        job_surface="workflow",
        writer="p4r6.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_p4r6_green",
        status="completed",
        summary="mailbox lifecycle completed",
        writer="p4r6.result",
        producer="test",
        result_id="job_result_p4r6_green",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_p4r6_green.json"],
        now_utc="2026-03-13T04:00:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="p4r6.writeback")
    generation_after_first = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="p4r6.writeback")
    generation_after_second = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    job = jobs.read_job(tmp_path, detached_job_id="job_p4r6_green")
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert first["task_outcome"]["reconciled_status"] == "completed"
    assert second["writeback_record"]["applied"] is False
    assert generation_after_first == generation_after_second
    assert task["status"] == "completed"
    assert task["delegated_outcome_writeback"]["assimilation_status"] == "successful_assimilation"
    assert record["assimilation_status"] == "successful_assimilation"
    assert job["state"] == "completed"


def test_duplicate_terminal_delivery_does_not_duplicate_canonical_mutation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_p4r6_duplicate")
    task_id = "task_p4r6_duplicate"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="p4r6.duplicate.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(tmp_path, worker_id="worker_p4r6_duplicate", writer="p4r6.duplicate.claim", lease_s=60)["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_p4r6_duplicate",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_p4r6_duplicate",
        job_surface="workflow",
        writer="p4r6.duplicate.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_p4r6_duplicate",
        status="completed",
        summary="first terminal result",
        writer="p4r6.duplicate.result",
        producer="test",
        result_id="job_result_p4r6_duplicate_v1",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_p4r6_duplicate.json"],
        now_utc="2026-03-13T04:10:00Z",
    )
    _deliver_mailbox_result(tmp_path, task_id=task_id)

    first = writeback.reconcile_task(tmp_path, task_id=task_id, writer="p4r6.duplicate.writeback")
    generation_after_first = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])

    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_p4r6_duplicate",
        status="completed",
        summary="duplicate terminal replay",
        writer="p4r6.duplicate.result.replay",
        producer="test",
        result_id="job_result_p4r6_duplicate_v2",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_p4r6_duplicate.json"],
        now_utc="2026-03-13T04:10:30Z",
    )
    second = writeback.reconcile_task(tmp_path, task_id=task_id, writer="p4r6.duplicate.writeback")
    generation_after_second = int(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]["write_generation"])
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert first["writeback_record"]["applied"] is True
    assert second["writeback_record"]["applied"] is False
    assert first["task_outcome"]["reconciled_status"] == "completed"
    assert second["task_outcome"]["reconciled_status"] == "completed"
    assert generation_after_first == generation_after_second
    assert task["status"] == "completed"
    assert record["assimilation_status"] == "successful_assimilation"
    assert record["attempt_count"] == 2


def test_terminal_mailbox_result_without_delivery_proof_stays_non_green(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_p4r6_pending_delivery")
    task_id = "task_p4r6_pending_delivery"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="p4r6.pending_delivery.bind",
        request=_mailbox_request(task_id),
        launch_result={},
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_p4r6_pending_delivery",
        writer="p4r6.pending_delivery.claim",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="worker_p4r6_pending_delivery",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_p4r6_pending_delivery",
        job_surface="workflow",
        writer="p4r6.pending_delivery.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_p4r6_pending_delivery",
        status="completed",
        summary="worker finished but result not yet delivered",
        writer="p4r6.pending_delivery.result",
        producer="test",
        result_id="job_result_p4r6_pending_delivery",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_p4r6_pending_delivery.json"],
        now_utc="2026-03-13T04:20:00Z",
    )

    outcome = writeback.reconcile_task(tmp_path, task_id=task_id, writer="p4r6.pending_delivery.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    correlation_snapshot = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert correlation_snapshot["records"][0]["latest_delivery_status"] == "pending_reattach"
    assert outcome["task_outcome"]["assimilation_status"] == "ambiguous_assimilation"
    assert task["status"] == "interrupted_recovering"
    assert "delivery" in task["blocked_reason"].lower()
    assert record["assimilation_status"] == "ambiguous_assimilation"


def test_missing_writeback_evidence_stays_non_green_and_opens_blocked_loop(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_p4r6_ambiguous")
    task_id = "task_p4r6_ambiguous"
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="p4r6.workflow.bind",
        request=_workflow_request(task_id),
        launch_result={"workflow_id": "wf_p4r6_ambiguous"},
    )
    ref = bound["delegation_ref"]
    state_path = Path(ref["status_refs"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"workflow_id": "wf_p4r6_ambiguous", "status": "completed"}), encoding="utf-8")

    outcome = writeback.reconcile_task(tmp_path, task_id=task_id, writer="p4r6.workflow.writeback")
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = _find_task(task_register, task_id)
    blocked_loops = [loop for loop in task_register["open_loops"] if loop["linked_task_id"] == task_id and loop["status"] == "open"]
    record = writeback.read_task_record(tmp_path, task_id=task_id)

    assert outcome["task_outcome"]["assimilation_status"] == "ambiguous_assimilation"
    assert task["status"] == "interrupted_recovering"
    assert task["blocked_reason"]
    assert task["status"] != "completed"
    assert record["assimilation_status"] == "ambiguous_assimilation"
    assert any(loop["kind"] == "blocked_question" for loop in blocked_loops)
