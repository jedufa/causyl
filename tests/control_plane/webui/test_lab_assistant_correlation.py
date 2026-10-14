from __future__ import annotations

from pathlib import Path

from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_task_register as register


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)


def _seed_task(root: Path, *, task_id: str, ledger_uid: str) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="f4a9.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": f"Track {task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "watch correlated delegated work",
        },
    )["task_register"]


def _classification() -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": "correlate detached work",
            "preferred_surface": "workflow",
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "mission_alignment": "mission_aligned",
            "mission_alignment_note": "Correlation of detached workflow evidence is in scope for the plane.",
        }
    )


def _mailbox_request(*, task_id: str, ledger_uid: str, request_id: str, correlation_id: str, launch_turn_id: str) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification(),
        surface="mailbox",
        owner_ref={"task_id": task_id, "ledger_uid": ledger_uid},
        summary=f"Mailbox request {request_id}",
        requested_action="queue mailbox work",
        expected_evidence=[{"kind": "job_record", "description": "detached job lifecycle", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review detached result", "owner": "assistant"},
        request_id=request_id,
        idempotency_key=request_id,
        correlation_id=correlation_id,
        launch_turn_id=launch_turn_id,
        launch_cause="scheduled_build",
    )


def test_detached_result_survives_restart_and_reattaches_by_launch_turn(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_f4a9_exact", ledger_uid="PIS-20260312-224201Z-F4A9")
    request = _mailbox_request(
        task_id="task_f4a9_exact",
        ledger_uid="PIS-20260312-224201Z-F4A9",
        request_id="mailbox_req_f4a9_exact",
        correlation_id="corr_f4a9_exact",
        launch_turn_id="turn_f4a9_launch",
    )
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="f4a9.bind",
        request=request,
        launch_result={},
    )

    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_f4a9",
        writer="f4a9.claim",
        lease_s=60,
        now_utc="2026-03-13T00:00:00Z",
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id="mailbox_req_f4a9_exact",
        worker_id="worker_f4a9",
        claim_id=claimed["active_claim"]["claim_id"],
        detached_job_id="job_f4a9_exact",
        job_surface="workflow",
        writer="f4a9.handoff",
    )
    result = jobs.record_job_result(
        tmp_path,
        detached_job_id="job_f4a9_exact",
        status="completed",
        summary="detached work completed while launch turn was gone",
        writer="f4a9.result",
        producer="test",
        result_id="result_f4a9_exact",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_f4a9_exact.json"],
        now_utc="2026-03-13T00:05:00Z",
    )["job_record"]

    persisted = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    assert persisted["delivery_counts"]["pending_reattach"] == 1
    assert persisted["records"][0]["latest_result_id"] == "result_f4a9_exact"
    assert result["terminal_result"]["result_id"] == "result_f4a9_exact"

    reattached = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_f4a9_launch",
        current_turn_id="turn_f4a9_resume",
        writer="f4a9.reattach",
        mark_delivered=True,
    )
    assert reattached["delivered_result_ids"] == ["result_f4a9_exact"]
    assert len(reattached["exact_results"]) == 1
    assert reattached["exact_results"][0]["delivery_status"] == "delivered"
    assert reattached["exact_results"][0]["detached_job_id"] == "job_f4a9_exact"

    record = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    assert record["delivery_counts"]["delivered"] == 1
    assert record["records"][0]["pending_result_ids"] == []

    assert bound["delegation_ref"]["correlation_id"] == "corr_f4a9_exact"


def test_duplicate_detached_result_deliveries_are_idempotent_for_turn_reattach_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_f4a9_idempotent", ledger_uid="PIS-20260312-224201Z-F4A9")
    request = _mailbox_request(
        task_id="task_f4a9_idempotent",
        ledger_uid="PIS-20260312-224201Z-F4A9",
        request_id="mailbox_req_f4a9_idempotent",
        correlation_id="corr_f4a9_idempotent",
        launch_turn_id="turn_f4a9_idempotent",
    )
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="f4a9.idempotent.bind",
        request=request,
        launch_result={
            "workflow_id": "wf_f4a9_idempotent",
            "detached_job_id": "job_f4a9_idempotent",
        },
    )

    payload = policy.validate_typed_delegation_result(
        {
            "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
            "result_id": "result_f4a9_idempotent",
            "request_id": "mailbox_req_f4a9_idempotent",
            "surface": "workflow",
            "status": "completed",
            "summary": "duplicate delivery should be deduplicated",
            "native_status": "completed",
            "evidence_status": "present",
            "evidence_refs": ["governance/agents/runtime/orchestration/workflow__wf_f4a9_idempotent.json"],
            "artifact_refs": [],
            "next_decision": {"kind": "assistant_continue", "summary": "Review result", "owner": "assistant"},
            "created_utc": "2026-03-13T00:25:00Z",
            "updated_utc": "2026-03-13T00:25:00Z",
            "correlation_id": "corr_f4a9_idempotent",
            "launch_turn_id": "turn_f4a9_idempotent",
            "detached_job_id": "job_f4a9_idempotent",
            "result_correlation_basis": "request_id",
            "worker_ref": "workflow:job_f4a9_idempotent",
            "producer": "test",
            "owner_ref": {"task_id": "task_f4a9_idempotent", "ledger_uid": "PIS-20260312-224201Z-F4A9"},
        }
    )

    first = correlation.record_detached_result(tmp_path, writer="f4a9.idempotent.result", result=payload)
    second = correlation.record_detached_result(tmp_path, writer="f4a9.idempotent.result.dup", result=payload)

    assert first["result_record"]["result_id"] == "result_f4a9_idempotent"
    assert second["result_record"]["result_id"] == "result_f4a9_idempotent"

    snapshot = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    assert snapshot["record_count"] == 1
    assert snapshot["result_count"] == 1
    by_id = {row["correlation_id"]: row for row in snapshot["records"]}
    assert by_id["corr_f4a9_idempotent"]["pending_result_ids"] == ["result_f4a9_idempotent"]

    first_reattach = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_f4a9_idempotent",
        current_turn_id="turn_f4a9_idempotent_resume",
        writer="f4a9.idempotent.reattach",
        mark_delivered=True,
    )
    assert first_reattach["delivered_result_ids"] == ["result_f4a9_idempotent"]

    second_reattach = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_f4a9_idempotent",
        current_turn_id="turn_f4a9_idempotent_resume",
        writer="f4a9.idempotent.reattach",
        mark_delivered=True,
    )
    assert second_reattach["delivered_result_ids"] == []

    snapshot_after = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    result_records = {row["result_id"]: row for row in snapshot_after["results"]}
    assert result_records["result_f4a9_idempotent"]["delivery_status"] == "delivered"
    by_id_after = {row["correlation_id"]: row for row in snapshot_after["records"]}
    assert by_id_after["corr_f4a9_idempotent"]["pending_result_ids"] == []


def test_ambiguous_detached_job_match_stays_ambiguous_instead_of_guessing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    first = _seed_task(tmp_path, task_id="task_f4a9_a", ledger_uid="PIS-20260312-224201Z-F4A9")
    request_a = policy.build_typed_delegation_request(
        classification=_classification(),
        surface="workflow",
        owner_ref={"task_id": "task_f4a9_a", "ledger_uid": "PIS-20260312-224201Z-F4A9"},
        summary="Workflow A",
        requested_action="launch A",
        expected_evidence=[{"kind": "workflow_digest", "description": "proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review A", "owner": "assistant"},
        request_id="delegate_f4a9_a",
        correlation_id="corr_f4a9_a",
        launch_turn_id="turn_f4a9_a",
        detached_job_id="job_f4a9_shared",
        result_correlation_basis="detached_job_id",
    )
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="f4a9.ambiguous.a",
        request=request_a,
        launch_result={"workflow_id": "wf_f4a9_a", "detached_job_id": "job_f4a9_shared"},
    )

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    second = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="f4a9.seed.second",
        task={
            "task_id": "task_f4a9_b",
            "kind": "software",
            "title": "Track task_f4a9_b",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-224201Z-F4A9",
            "next_expected_action": "watch ambiguous delegated work",
        },
    )["task_register"]
    request_b = policy.build_typed_delegation_request(
        classification=_classification(),
        surface="workflow",
        owner_ref={"task_id": "task_f4a9_b", "ledger_uid": "PIS-20260312-224201Z-F4A9"},
        summary="Workflow B",
        requested_action="launch B",
        expected_evidence=[{"kind": "workflow_digest", "description": "proof", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review B", "owner": "assistant"},
        request_id="delegate_f4a9_b",
        correlation_id="corr_f4a9_b",
        launch_turn_id="turn_f4a9_b",
        detached_job_id="job_f4a9_shared",
        result_correlation_basis="detached_job_id",
    )
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(second["write_generation"]),
        writer="f4a9.ambiguous.b",
        request=request_b,
        launch_result={"workflow_id": "wf_f4a9_b", "detached_job_id": "job_f4a9_shared"},
    )

    ambiguous = correlation.record_detached_result(
        tmp_path,
        writer="f4a9.ambiguous.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_f4a9_ambiguous",
                "request_id": "unknown_request",
                "surface": "workflow",
                "status": "completed",
                "summary": "shared detached job reported once",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["governance/agents/runtime/orchestration/workflow__wf_f4a9_a.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Inspect ambiguous match", "owner": "assistant"},
                "created_utc": "2026-03-13T00:10:00Z",
                "updated_utc": "2026-03-13T00:10:00Z",
                "correlation_id": "corr_f4a9_a",
                "launch_turn_id": "turn_f4a9_a",
                "detached_job_id": "job_f4a9_shared",
                "result_correlation_basis": "detached_job_id",
                "worker_ref": "workflow:job_f4a9_shared",
                "producer": "test",
                "owner_ref": {"task_id": "task_f4a9_a", "ledger_uid": "PIS-20260312-224201Z-F4A9"},
            }
        ),
    )
    assert ambiguous["result_record"]["delivery_status"] == "ambiguous"
    assert set(ambiguous["matched_correlation_ids"]) == {"corr_f4a9_a", "corr_f4a9_b"}

    reattach = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_f4a9_a",
        current_turn_id="turn_f4a9_resume",
        writer="f4a9.ambiguous.reattach",
        mark_delivered=True,
    )
    assert reattach["delivered_result_ids"] == []
    assert reattach["exact_results"] == []
    assert len(reattach["ambiguous_results"]) == 1
    assert reattach["ambiguous_results"][0]["result_id"] == "result_f4a9_ambiguous"


def test_unresolved_result_stays_detached_until_operator_has_truthful_linkage(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    unresolved = correlation.record_detached_result(
        tmp_path,
        writer="f4a9.unresolved.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_f4a9_unresolved",
                "request_id": "missing_request",
                "surface": "workflow",
                "status": "failed",
                "summary": "result arrived without a durable correlation record",
                "native_status": "failed",
                "evidence_status": "present",
                "evidence_refs": ["governance/agents/runtime/orchestration/workflow__missing.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Preserve unresolved result", "owner": "assistant"},
                "created_utc": "2026-03-13T00:15:00Z",
                "updated_utc": "2026-03-13T00:15:00Z",
                "correlation_id": "corr_missing",
                "launch_turn_id": "turn_missing",
                "detached_job_id": "job_missing",
                "result_correlation_basis": "detached_job_id",
                "worker_ref": "workflow:job_missing",
                "producer": "test",
                "owner_ref": {"task_id": "task_missing", "ledger_uid": "PIS-20260312-224201Z-F4A9"},
            }
        ),
    )

    assert unresolved["result_record"]["delivery_status"] == "unresolved"
    assert unresolved["result_record"]["unresolved_reason"] == "no_correlation_record_for_detached_job_id"

    after_restart = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id="turn_missing",
        current_turn_id="turn_after_restart",
        writer="f4a9.unresolved.reattach",
        mark_delivered=True,
    )
    assert after_restart["delivered_result_ids"] == []
    assert after_restart["exact_results"] == []
    assert len(after_restart["unresolved_results"]) == 1
    assert after_restart["unresolved_results"][0]["result_id"] == "result_f4a9_unresolved"


def test_unresolved_result_does_not_smear_across_records_sharing_launch_turn(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    first = _seed_task(tmp_path, task_id="task_f4a9_shared_turn_a", ledger_uid="PIS-20260312-224201Z-F4A9")
    shared_turn_id = "turn_f4a9_shared_launch"
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(first["write_generation"]),
        writer="f4a9.shared-turn.a",
        request=_mailbox_request(
            task_id="task_f4a9_shared_turn_a",
            ledger_uid="PIS-20260312-224201Z-F4A9",
            request_id="mailbox_req_f4a9_shared_turn_a",
            correlation_id="corr_f4a9_shared_turn_a",
            launch_turn_id=shared_turn_id,
        ),
        launch_result={},
    )

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    second = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="f4a9.seed.shared-turn.second",
        task={
            "task_id": "task_f4a9_shared_turn_b",
            "kind": "software",
            "title": "Track task_f4a9_shared_turn_b",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-224201Z-F4A9",
            "next_expected_action": "watch shared launch-turn correlation",
        },
    )["task_register"]
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(second["write_generation"]),
        writer="f4a9.shared-turn.b",
        request=_mailbox_request(
            task_id="task_f4a9_shared_turn_b",
            ledger_uid="PIS-20260312-224201Z-F4A9",
            request_id="mailbox_req_f4a9_shared_turn_b",
            correlation_id="corr_f4a9_shared_turn_b",
            launch_turn_id=shared_turn_id,
        ),
        launch_result={},
    )

    unresolved = correlation.record_detached_result(
        tmp_path,
        writer="f4a9.shared-turn.unresolved.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_f4a9_shared_turn_unresolved",
                "request_id": "mailbox_req_missing_for_shared_turn",
                "surface": "mailbox",
                "status": "failed",
                "summary": "result arrived for shared turn without truthful request match",
                "native_status": "failed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_missing_for_shared_turn.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Preserve shared-turn unresolved result", "owner": "assistant"},
                "created_utc": "2026-03-13T00:20:00Z",
                "updated_utc": "2026-03-13T00:20:00Z",
                "correlation_id": "corr_missing_for_shared_turn",
                "launch_turn_id": shared_turn_id,
                "detached_job_id": "job_missing_for_shared_turn",
                "result_correlation_basis": "request_id",
                "worker_ref": "mailbox:job_missing_for_shared_turn",
                "producer": "test",
                "owner_ref": {"task_id": "task_f4a9_shared_turn_a", "ledger_uid": "PIS-20260312-224201Z-F4A9"},
            }
        ),
    )

    assert unresolved["result_record"]["delivery_status"] == "unresolved"
    snapshot = correlation.read_snapshot(tmp_path, initialize_if_missing=False)
    assert snapshot["delivery_counts"]["unresolved"] == 1
    by_id = {row["correlation_id"]: row for row in snapshot["records"]}
    assert by_id["corr_f4a9_shared_turn_a"]["unresolved_result_ids"] == []
    assert by_id["corr_f4a9_shared_turn_b"]["unresolved_result_ids"] == []
    assert by_id["corr_f4a9_shared_turn_a"]["latest_result_id"] == ""
    assert by_id["corr_f4a9_shared_turn_b"]["latest_result_id"] == ""
    assert by_id["corr_f4a9_shared_turn_a"]["latest_delivery_status"] == ""
    assert by_id["corr_f4a9_shared_turn_b"]["latest_delivery_status"] == ""

    after_restart = correlation.reattach_results_for_turn(
        tmp_path,
        launch_turn_id=shared_turn_id,
        current_turn_id="turn_f4a9_resume_shared_turn",
        writer="f4a9.shared-turn.unresolved.reattach",
        mark_delivered=True,
    )
    assert after_restart["delivered_result_ids"] == []
    assert after_restart["exact_results"] == []
    assert len(after_restart["unresolved_results"]) == 1
    assert after_restart["unresolved_results"][0]["result_id"] == "result_f4a9_shared_turn_unresolved"
