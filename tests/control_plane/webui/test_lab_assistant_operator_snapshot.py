from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments_shared import job_family_resource_key
from infra.controlplane import continuation_packet_store as packet_store
from infra.controlplane import job_budget_cost_ledger_store as budget_store
from infra.controlplane import plane_mission_contract as mission_contract
from infra.controlplane import shared_resource_claim_ledger as resource_claim_ledger
from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_recovery as recovery
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_status_diagnostics as status_diag
from infra.webui import server_lab_assistant_self_check as self_check
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_writeback as writeback
from infra.webui import server_routes_and_main as routes


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _write_mission_contract(root: Path) -> None:
    payload = json.loads(Path("governance/plane_mission_contract_v1.json").read_text(encoding="utf-8"))
    target = root / mission_contract.CONTRACT_ARTIFACT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _seed_task(
    root: Path,
    *,
    task_id: str = "task_2db7",
    status: str = "active",
    ledger_uid: str = "PIS-20260312-161622Z-2DB7",
    title: str = "Operator-visible task",
    next_expected_action: str = "show truthful snapshot",
) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": title,
            "status": status,
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": next_expected_action,
        },
    )["task_register"]


def _request(*, task_id: str = "task_2db7") -> dict:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "truthful operator snapshot",
            "preferred_surface": "workflow",
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
            "mission_alignment": "mission_aligned",
            "mission_alignment_note": "Operator snapshot truth and evidence review are in scope for the plane.",
        }
    )
    req = policy.build_typed_delegation_request(
        classification=classification,
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": "PIS-20260312-161622Z-2DB7"},
        summary="Collect operator-visible evidence",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "durable workflow digest", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review durable proof", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )
    req["request_id"] = "delegate_2db7"
    return req


def _mailbox_request(*, task_id: str, request_id: str, correlation_id: str, launch_turn_id: str, idempotency_key: str) -> dict:
    request = _request(task_id=task_id)
    request["request_id"] = request_id
    request["summary"] = "Collect operator-visible evidence"
    request["requested_action"] = "enqueue mailbox request"
    request["surface"] = "mailbox"
    request["expected_evidence"] = [{"kind": "status_artifact", "description": "mailbox result", "required": True}]
    request["next_decision"] = {"kind": "review_evidence", "summary": "Review durable proof", "owner": "assistant"}
    request["idempotency_key"] = idempotency_key
    request["correlation_id"] = correlation_id
    request["launch_turn_id"] = launch_turn_id
    request["launch_cause"] = "scheduled_build"
    return request


def _bind_mailbox_request(tmp_path: Path, *, task_id: str, writer: str, request_id: str, correlation_id: str, launch_turn_id: str) -> dict:
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    req = _mailbox_request(
        task_id=task_id,
        request_id=request_id,
        correlation_id=correlation_id,
        launch_turn_id=launch_turn_id,
        idempotency_key=f"idem_{task_id}",
    )
    return bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer=writer,
        request=req,
        launch_result={},
    )


def test_snapshot_downgrades_completed_without_evidence(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(tmp_path)
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="snapshot.bind",
        request=_request(),
        launch_result={"workflow_id": "wf_2db7"},
    )
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_2db7", "status": "completed"})

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["schema_version"] == snapshot.SNAPSHOT_SCHEMA_VERSION
    assert payload["operator_state"] == "blocked"
    assert payload["active_tasks"][0]["status"] == "evidence_missing"
    assert payload["active_tasks"][0]["evidence_backed_completion"] is False
    assert payload["delegated_work"][0]["status"] == "evidence_missing"


def test_snapshot_reads_completed_and_residue_tasks_from_canonical_register_buckets(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(tmp_path, task_id="task_complete_bucket", status="completed", title="Completed bucket task")
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.bucket_test",
        task={
            "task_id": "task_residue_bucket",
            "kind": "software",
            "title": "Residue bucket task",
            "status": "interrupted_recovering",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-161622Z-RESI",
            "next_expected_action": "inspect interrupted task",
        },
    )

    register_snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    assert register_snapshot["active_tasks"] == []
    assert [task["task_id"] for task in register_snapshot["completed_tasks"]] == ["task_complete_bucket"]
    assert [task["task_id"] for task in register_snapshot["residue_tasks"]] == ["task_residue_bucket"]

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert [task["task_id"] for task in payload["completed_tasks"]] == ["task_complete_bucket"]
    assert [task["task_id"] for task in payload["active_tasks"]] == ["task_residue_bucket"]


def test_snapshot_prefers_nonterminal_task_over_completed_row_when_ledger_uid_collides(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_completed_collision",
        status="completed",
        ledger_uid="PIS-20260312-161622Z-COLLIDE",
        title="Older completed row",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.collision",
        task={
            "task_id": "task_blocked_collision",
            "kind": "software",
            "title": "Current blocked row",
            "status": "evidence_missing",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-161622Z-COLLIDE",
            "next_expected_action": "collect missing evidence",
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert [task["task_id"] for task in payload["active_tasks"]] == ["task_blocked_collision"]
    assert payload["completed_tasks"] == []


def test_snapshot_reports_corrupt_summary_cache_explicitly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(tmp_path)
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    summary_path.write_text('{"register_generation": 1', encoding="utf-8")

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["task_register"]["integrity"]["summary_cache"]["status"] == "malformed"
    assert payload["task_register"]["integrity"]["summary_cache"]["partial_write_suspected"] is True
    assert payload["freshness"]["stale"] is True
    assert any(row["kind"] == "task_register_summary_cache_corruption" for row in payload["operator_summary"]["truth_conflicts"])
    assert payload["operator_summary"]["status_language"]["success_claim_allowed"] is False


def test_snapshot_reports_canonical_task_register_corruption_explicitly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path)
    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    register_path.write_text("{bad\n", encoding="utf-8")

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["task_register"]["integrity"]["canonical_register"]["status"] == "malformed"
    assert payload["freshness"]["stale"] is True
    assert any(row["kind"] == "task_register_corruption" for row in payload["operator_summary"]["truth_conflicts"])
    assert payload["operator_summary"]["status_language"]["success_claim_allowed"] is False


def test_snapshot_includes_diagnostic_boundary_contract(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    boundary = payload["diagnostic_boundary"]
    assert boundary["schema_version"] == "lab_assistant_status_diagnostic_boundary_v1"
    assert boundary["canonical_snapshot"]["api_path"] == "/api/admin/lab_assistant/snapshot"
    legacy_paths = {row["api_path"] for row in boundary["legacy_endpoints"]}
    assert "/api/admin/lab_assistant/task_register" in legacy_paths


def test_snapshot_surfaces_missing_mission_contract_explicitly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["mission_contract"]["ok"] is False
    assert payload["mission_contract"]["failure_class"] == "mission_contract_missing"
    assert payload["mission_contract"]["summary"] == "Canonical plane mission contract is missing."
    assert payload["mission_surface"]["status"] == "degraded"
    assert payload["mission_surface"]["headline"] == "Mission contract unavailable"
    assert "plane mission contract" in payload["mission_surface"]["summary"].lower()
    assert payload["operator_summary"]["mission_surface"]["status"] == "degraded"


def test_snapshot_surfaces_shared_resource_contention_from_durable_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    resource = job_family_resource_key("cpu_light").as_dict()
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "claim_a:acquired",
            "claim_uid": "claim_a",
            "claimant_uid": "worker.alpha",
            "resource": dict(resource),
            "recorded_at_utc": "2026-04-07T05:10:00Z",
            "status": "acquired",
            "wait_reason_code": "",
            "detail": "",
        },
        repo_root=tmp_path,
    )
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "claim_b:waiting",
            "claim_uid": "claim_b",
            "claimant_uid": "worker.beta",
            "resource": dict(resource),
            "recorded_at_utc": "2026-04-07T05:10:01Z",
            "status": "waiting",
            "wait_reason_code": "awaiting_capacity",
            "detail": "shared resource capacity busy for job_family:scheduler:cpu_light; active=1/1",
        },
        repo_root=tmp_path,
    )
    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["resource_concurrency"]["status"] == "ok"
    assert payload["resource_concurrency"]["waiting_claim_count"] == 1
    notification = next(row for row in payload["operator_summary"]["notifications"] if row["kind"] == "shared_resource_contention")
    assert notification["severity"] == "warning"
    assert "job_family:scheduler:cpu_light is contended" in notification["message"]
    assert "awaiting_capacity" in notification["message"]


def test_snapshot_surfaces_continuation_packet_and_memory_projection(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(
        tmp_path,
        task_id="task_continuation_projection",
        title="Continuation projection task",
        next_expected_action="resume from durable continuation state",
    )
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="snapshot.continuation",
        task_id="task_continuation_projection",
        selected_surface="workflow",
        execution_id="job_continuation_projection",
        execution_ref="workflow:job_continuation_projection",
        binding_ref="binding:continuation_projection",
        job_uri="runtime/state/lab_assistant/detached_jobs/jobs/job_continuation_projection.json",
        trace_id="trace:job_continuation_projection",
    )["task_register"]
    register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="snapshot.continuation",
        task_id="task_continuation_projection",
        run_id="job_continuation_projection",
        checkpoint={
            "checkpoint_id": "cp_continuation_projection",
            "checkpoint_status": "recorded",
            "checkpoint_note": "safe continuation point",
            "progress_summary": "bounded checkpoint captured",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_continuation_projection.json"],
        },
    )
    packet_store.append_packet_revision_once(
        {
            "schema_version": "continuation_packet_v1",
            "packet_id": "detached_job:job_continuation_projection",
            "packet_revision": 1,
            "created_utc": "2026-04-07T09:00:00Z",
            "source_run": {
                "job_id": "job_continuation_projection",
                "run_id": "req_continuation_projection",
                "attempt_id": "turn_continuation_projection",
                "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_continuation_projection.json"],
            },
            "handoff_brief": "Resume from the durable checkpoint without transcript archaeology.",
            "result_summary": {"outcome": "interrupted", "summary": "Checkpointed continuation is available."},
            "evidence_created": [{"evidence_id": "ev_projection", "kind": "unit_test", "summary": "operator snapshot continuation projection", "refs": ["tests/control_plane/webui/test_lab_assistant_operator_snapshot.py"]}],
            "artifact_index": [{"artifact_id": "art_projection", "kind": "json", "uri": "runtime/state/lab_assistant/detached_jobs/jobs/job_continuation_projection.json", "summary": "job record"}],
            "open_questions": [{"question_id": "q_projection", "question": "Resume immediately?"}],
            "failed_paths": [{"path_id": "fp_projection", "summary": "Terminal success is not yet proven."}],
            "recommended_next_steps": [{"step_id": "step_projection", "priority": "now", "step": "Resume from the durable checkpoint."}],
            "confidence": {"level": "medium", "rationale": "Checkpoint truth is durable."},
            "applicability": {"scope": "immediate_successor", "limitations": ["Lifecycle truth remains authoritative for raw state."]},
        },
        repo_root=tmp_path,
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-07T12:00:00Z")

    inspection = next(row for row in payload["continuation_inspection"] if row["task_ref"]["task_id"] == "task_continuation_projection")
    assert inspection["packet"]["status"] == "ready"
    assert inspection["packet"]["packet_id"] == "detached_job:job_continuation_projection"
    assert inspection["memory"]["status"] == "ready"
    assert inspection["memory"]["activity_counts"]["active"] >= 1
    assert inspection["handoff"]["authoritative_source"] == "continuation_packet"
    assert "Terminal success is not yet proven." in inspection["handoff"]["summary_text"]


def test_snapshot_marks_expired_derived_memory_without_hiding_packet_caveats(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(
        tmp_path,
        task_id="task_expired_memory",
        title="Expired memory task",
        next_expected_action="inspect durable continuation state",
    )
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="snapshot.expired_memory",
        task_id="task_expired_memory",
        selected_surface="workflow",
        execution_id="job_expired_memory",
        execution_ref="workflow:job_expired_memory",
        binding_ref="binding:expired_memory",
        job_uri="runtime/state/lab_assistant/detached_jobs/jobs/job_expired_memory.json",
        trace_id="trace:job_expired_memory",
    )["task_register"]
    register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="snapshot.expired_memory",
        task_id="task_expired_memory",
        run_id="job_expired_memory",
        checkpoint={
            "checkpoint_id": "cp_expired_memory",
            "checkpoint_status": "recorded",
            "checkpoint_note": "old checkpoint",
            "progress_summary": "checkpoint captured",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_expired_memory.json"],
        },
    )
    packet_store.append_packet_revision_once(
        {
            "schema_version": "continuation_packet_v1",
            "packet_id": "detached_job:job_expired_memory",
            "packet_revision": 1,
                "created_utc": "2026-03-01T09:00:00Z",
            "source_run": {
                "job_id": "job_expired_memory",
                "run_id": "req_expired_memory",
                "attempt_id": "turn_expired_memory",
                "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_expired_memory.json"],
            },
            "handoff_brief": "The packet exists, but time has moved on.",
            "result_summary": {"outcome": "partial", "summary": "Only bounded follow-up guidance was captured."},
            "evidence_created": [{"evidence_id": "ev_expired_memory", "kind": "unit_test", "summary": "expired memory projection", "refs": ["tests/control_plane/webui/test_lab_assistant_operator_snapshot.py"]}],
            "artifact_index": [{"artifact_id": "art_expired_memory", "kind": "json", "uri": "runtime/state/lab_assistant/detached_jobs/jobs/job_expired_memory.json", "summary": "job record"}],
                "open_questions": [{"question_id": "q_expired_memory", "question": "Does this next step still apply?"}],
                "failed_paths": [{"path_id": "fp_expired_memory", "summary": "Earlier follow-up guidance may now be stale."}],
                "recommended_next_steps": [{"step_id": "step_expired_memory", "priority": "later", "step": "Resume the previously suggested follow-up."}],
            "confidence": {"level": "low", "rationale": "This guidance may go stale quickly."},
            "applicability": {"scope": "same_family", "limitations": ["Short-lived guidance only."]},
        },
        repo_root=tmp_path,
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-20T12:00:00Z")

    inspection = next(row for row in payload["continuation_inspection"] if row["task_ref"]["task_id"] == "task_expired_memory")
    assert inspection["packet"]["status"] == "ready"
    assert inspection["memory"]["status"] == "degraded"
    assert inspection["handoff"]["status"] == "degraded"
    assert inspection["memory"]["activity_counts"]["expired"] >= 1
    assert inspection["handoff"]["authoritative_source"] == "continuation_packet"
    assert "Does this next step still apply?" in inspection["handoff"]["summary_text"]
    assert any("selected continuation packet is stale" in item for item in inspection["handoff"]["visible_caveats"])


def test_diagnostic_mismatch_marks_degraded_for_fresh_snapshot(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    recovery.reconcile_startup(tmp_path, writer="snapshot.test")

    snapshot_payload = routes._lab_assistant_operator_snapshot_payload(repo_root=tmp_path)
    diagnostic_payload = routes._lab_assistant_task_register_payload(repo_root=tmp_path)
    diagnostic_payload = dict(diagnostic_payload)
    diagnostic_payload["task_register"] = dict(diagnostic_payload.get("task_register") or {})
    diagnostic_payload["task_register"]["write_generation"] = int(snapshot_payload["task_register"]["write_generation"]) + 1

    mismatch = status_diag.diagnostic_mismatch(
        "task_register",
        diagnostic_payload=diagnostic_payload,
        snapshot_payload=snapshot_payload,
        now_utc="2026-03-27T00:00:00Z",
    )

    assert snapshot_payload["freshness"]["stale"] is False
    assert mismatch["state"] == "degraded_mismatch"
    assert mismatch["mismatch"] is True
    assert mismatch["canonical_snapshot"]["api_path"] == "/api/admin/lab_assistant/snapshot"


def test_snapshot_route_self_heals_stale_open_work_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    stale_snapshot = {
        "schema_version": "lab_assistant_operator_snapshot_v1",
        "truth_conflicts": [
            {
                "kind": "stale_task_register_with_open_work",
                "summary": "Task register freshness is stale while open work exists; treat completion claims as unproven.",
            }
        ],
    }
    healed_snapshot = {
        "schema_version": "lab_assistant_operator_snapshot_v1",
        "truth_conflicts": [],
        "freshness": {"stale": False},
    }
    snapshots = [stale_snapshot, healed_snapshot]
    seen: dict[str, list[str]] = {"calls": []}

    def _build_snapshot(_root: Path) -> dict:
        seen["calls"].append("build")
        return snapshots.pop(0)

    def _reconcile(_root: Path, *, writer: str, bindings_module=None) -> dict:
        assert bindings_module is None
        seen["calls"].append(f"reconcile:{writer}")
        return {"status": "waiting_on_job"}

    def _rebuild(_root: Path, *, writer: str) -> dict:
        seen["calls"].append(f"summary:{writer}")
        return {"ok": True}

    monkeypatch.setattr(routes._lab_operator_snapshot, "build_operator_snapshot", _build_snapshot)
    monkeypatch.setattr(routes._lab_recovery, "reconcile_startup", _reconcile)
    monkeypatch.setattr(routes._lab_task_register, "rebuild_summary_cache", _rebuild)

    payload = routes._lab_assistant_operator_snapshot_payload(repo_root=tmp_path)

    assert payload == healed_snapshot
    assert seen["calls"] == [
        "build",
        "reconcile:operator_snapshot.self_heal.recovery",
        "summary:operator_snapshot.self_heal.summary_cache",
        "build",
    ]


def test_snapshot_surfaces_budget_block_notifications_from_durable_scheduler_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "budget_alert_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Budget blocked job.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600, "require_budget_admission": True},
        },
        repo_root=tmp_path,
    )
    budget_store.append_allocation_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "allocation_uid": "budget_alert_001:alloc:001",
            "job_uid": "budget_alert_001",
            "recorded_at_utc": "2026-04-06T06:00:00Z",
            "amount_minor_units": 100,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
        repo_root=tmp_path,
    )
    budget_store.append_cost_event_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": "budget_alert_001:cost:001",
            "job_uid": "budget_alert_001",
            "recorded_at_utc": "2026-04-06T06:05:00Z",
            "amount_minor_units": 125,
            "unit": "usd_cents",
            "direction": "debit",
            "cost_reason_code": "usage",
        },
        repo_root=tmp_path,
    )
    budget_store.ensure_recovery_outcome_recorded(
        "budget_alert_001",
        recorded_at_utc="2026-04-06T06:10:00Z",
        repo_root=tmp_path,
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-06T07:00:00Z")

    notification = next(row for row in payload["operator_summary"]["notifications"] if row["kind"] == "job_budget_attention")
    assert notification["severity"] == "danger"
    assert "budget_alert_001" in notification["message"]
    assert notification["jobs"][0]["job_id"] == "budget_alert_001"


def test_snapshot_does_not_invent_budget_notifications_for_jobs_without_budget_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "budget_alert_none_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Plain job.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=tmp_path,
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-06T07:00:00Z")

    assert not any(row["kind"] == "job_budget_attention" for row in payload["operator_summary"]["notifications"])


def test_snapshot_route_does_not_self_heal_without_stale_open_work_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    payload = {
        "schema_version": "lab_assistant_operator_snapshot_v1",
        "truth_conflicts": [{"kind": "host_unreachable_with_open_work"}],
    }

    monkeypatch.setattr(routes._lab_operator_snapshot, "build_operator_snapshot", lambda _root: payload)

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("self-heal should not run")

    monkeypatch.setattr(routes._lab_recovery, "reconcile_startup", _unexpected)
    monkeypatch.setattr(routes._lab_task_register, "rebuild_summary_cache", _unexpected)

    result = routes._lab_assistant_operator_snapshot_payload(repo_root=tmp_path)

    assert result == payload


def test_snapshot_keeps_register_completed_task_out_of_idle_without_evidence(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(tmp_path, status="completed", title="Looks done until you ask for proof")
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="snapshot.bind",
        request=_request(),
        launch_result={"workflow_id": "wf_2db7_done"},
    )
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_2db7_done", "status": "completed"})

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["operator_state"] == "blocked"
    assert not payload["completed_tasks"]
    assert payload["active_tasks"][0]["register_status"] == "completed"
    assert payload["active_tasks"][0]["status"] == "evidence_missing"
    assert payload["delegated_work"][0]["status"] == "evidence_missing"


def test_snapshot_marks_stale_sources_without_claiming_activity(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-13T12:00:00Z")

    assert payload["operator_state"] == "idle"
    assert payload["freshness"]["stale"] is True
    assert payload["backend_projection"]["state"] == "stale"
    assert payload["operator_summary"]["status_language"]["state"] == "stale"
    assert any(row["kind"] == "stale_data" for row in payload["operator_summary"]["notifications"])
    stale_sources = set(payload["freshness"]["stale_sources"])
    assert "task_register" not in stale_sources
    assert "recovery" in stale_sources
    assert payload["freshness"]["generated_at"] == "2026-03-13T12:00:00Z"
    assert payload["freshness"]["confidence"] == "STALE"
    assert isinstance(payload["freshness"]["freshness_seconds"], int)


def test_snapshot_does_not_mark_quiescent_register_stale_when_other_sources_are_fresh(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    recovery.reconcile_startup(tmp_path, writer="snapshot.test")

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    assistant_state_path = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_state.json"
    recovery_path = tmp_path / "runtime" / "state" / "lab_assistant" / "recovery" / "recovery_snapshot.json"
    register_obj = json.loads(register_path.read_text(encoding="utf-8"))
    summary_obj = json.loads(summary_path.read_text(encoding="utf-8"))
    assistant_state_obj = json.loads(assistant_state_path.read_text(encoding="utf-8"))
    recovery_obj = json.loads(recovery_path.read_text(encoding="utf-8"))
    register_obj["last_written_utc"] = "2026-03-30T05:00:00Z"
    summary_obj["generated_utc"] = "2026-03-30T05:00:00Z"
    assistant_state_obj["last_written_utc"] = "2026-03-30T05:00:00Z"
    recovery_obj["reconciled_utc"] = "2026-03-30T05:00:00Z"
    _write_json(register_path, register_obj)
    _write_json(summary_path, summary_obj)
    _write_json(assistant_state_path, assistant_state_obj)
    _write_json(recovery_path, recovery_obj)

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-30T05:20:00Z")

    assert payload["operator_state"] == "idle"
    assert payload["freshness"]["stale"] is False
    assert payload["backend_projection"]["state"] == "reachable"
    assert payload["freshness"]["stale_sources"] == []
    register_marker = next(row for row in payload["freshness"]["markers"] if row["source"] == "task_register")
    assistant_marker = next(row for row in payload["freshness"]["markers"] if row["source"] == "assistant_state")
    recovery_marker = next(row for row in payload["freshness"]["markers"] if row["source"] == "recovery")
    assert register_marker["stale"] is False
    assert register_marker["reason"] == "quiescent_register"
    assert assistant_marker["stale"] is False
    assert assistant_marker["reason"] == "quiescent_idle_assistant"
    assert recovery_marker["stale"] is False
    assert recovery_marker["reason"] == "quiescent_idle_recovery"


def test_snapshot_ignores_internal_synthetic_helper_tasks_for_operator_freshness(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(register.load_or_init_task_register(tmp_path)["write_generation"]),
        writer="snapshot.synthetic.seed",
        task={
            "task_id": "task_internal_helper",
            "kind": "software",
            "title": "[Codex Scheduled Controller Contract] - You are the Codex executor of record for a scheduled write-capable job.",
            "status": "waiting_on_job",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260313-094301Z-N3P5",
            "next_expected_action": "Wait for healer to produce durable evidence.",
        },
    )["task_register"]
    recovery.reconcile_startup(tmp_path, writer="snapshot.synthetic.recovery")

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    register_obj = json.loads(register_path.read_text(encoding="utf-8"))
    summary_obj = json.loads(summary_path.read_text(encoding="utf-8"))
    register_obj["last_written_utc"] = "2026-03-30T05:00:00Z"
    summary_obj["generated_utc"] = "2026-03-30T05:00:00Z"
    _write_json(register_path, register_obj)
    _write_json(summary_path, summary_obj)

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-30T05:20:00Z")

    assert payload["operator_state"] == "idle"
    assert payload["freshness"]["stale"] is False
    assert payload["backend_projection"]["state"] == "reachable"
    assert payload["active_tasks"] == []


def test_snapshot_treats_evidence_backed_completed_residue_as_quiescent_for_freshness(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_completed_residue", status="active", title="Evidence-backed completion residue")
    bound = _bind_mailbox_request(
        tmp_path,
        task_id="task_completed_residue",
        writer="snapshot.completed_residue.bind",
        request_id="delegate_completed_residue",
        correlation_id="corr_completed_residue",
        launch_turn_id="turn_completed_residue",
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="snapshot_completed_residue_worker",
        writer="snapshot.completed_residue.claim",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="snapshot_completed_residue_worker",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_completed_residue",
        job_surface="workflow",
        writer="snapshot.completed_residue.handoff",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_completed_residue",
        status="completed",
        summary="Evidence-backed completion residue",
        writer="snapshot.completed_residue.result",
        producer="test",
        result_id="result_completed_residue",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_completed_residue.json"],
        now_utc="2026-03-30T05:00:00Z",
    )
    writeback.reconcile_task(tmp_path, task_id="task_completed_residue", writer="snapshot.completed_residue.writeback")
    recovery.reconcile_startup(tmp_path, writer="snapshot.completed_residue.recovery")

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    summary_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
    assistant_state_path = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_state.json"
    recovery_path = tmp_path / "runtime" / "state" / "lab_assistant" / "recovery" / "recovery_snapshot.json"
    register_obj = json.loads(register_path.read_text(encoding="utf-8"))
    summary_obj = json.loads(summary_path.read_text(encoding="utf-8"))
    assistant_state_obj = json.loads(assistant_state_path.read_text(encoding="utf-8"))
    recovery_obj = json.loads(recovery_path.read_text(encoding="utf-8"))
    register_obj["last_written_utc"] = "2026-03-30T05:00:00Z"
    summary_obj["generated_utc"] = "2026-03-30T05:00:00Z"
    assistant_state_obj["last_written_utc"] = "2026-03-30T05:00:00Z"
    assistant_state_obj["current_state"] = "waiting_on_job"
    assistant_state_obj["state_reason"] = "Stale completion residue should not poison quiescent freshness."
    recovery_obj["reconciled_utc"] = "2026-03-30T05:00:00Z"
    recovery_obj["status"] = "waiting_on_job"
    recovery_obj["summary"] = "Stale recovery residue should not keep Home waiting forever."
    _write_json(register_path, register_obj)
    _write_json(summary_path, summary_obj)
    _write_json(assistant_state_path, assistant_state_obj)
    _write_json(recovery_path, recovery_obj)

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-30T05:20:00Z")

    assert payload["operator_state"] == "idle"
    assert payload["freshness"]["stale"] is False
    assert payload["backend_projection"]["state"] == "reachable"
    assert payload["assistant_state"]["current_state"] == "idle"
    assert payload["recovery"]["status"] == "ready"
    assert payload["operator_summary"]["status_language"]["state"] == "successful"
    register_marker = next(row for row in payload["freshness"]["markers"] if row["source"] == "task_register")
    assistant_marker = next(row for row in payload["freshness"]["markers"] if row["source"] == "assistant_state")
    recovery_marker = next(row for row in payload["freshness"]["markers"] if row["source"] == "recovery")
    assert register_marker["stale"] is False
    assert register_marker["reason"] == "quiescent_register"
    assert assistant_marker["stale"] is False
    assert assistant_marker["reason"] == "quiescent_idle_assistant"
    assert recovery_marker["stale"] is False
    assert recovery_marker["reason"] == "quiescent_idle_recovery"


def test_snapshot_long_running_reassurance_never_claims_completion(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_waiting",
        status="waiting_on_job",
        title="Long running item",
        next_expected_action="wait for worker completion",
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    notifications = payload["operator_summary"]["notifications"]

    long_running = next(row for row in notifications if row["kind"] == "long_running_reassurance")
    assert long_running["completion_claim_allowed"] is False
    assert "no completion is claimed yet" in long_running["message"].lower()


def test_snapshot_operator_summary_orders_sections_active_blocked_next_actions_then_evidence(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_active",
        status="waiting_on_job",
        title="Active item",
        next_expected_action="wait for worker",
    )
    _seed_task(
        tmp_path,
        task_id="task_blocked",
        status="evidence_missing",
        title="Blocked item",
        ledger_uid="PIS-20260312-161622Z-BLK1",
        next_expected_action="collect missing proof",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.bind",
        request=_request(task_id="task_active"),
        launch_result={"workflow_id": "wf_2db7_order"},
    )
    ref = bound["delegation_ref"]
    _write_json(
        Path(ref["status_refs"][0]),
        {
            "workflow_id": "wf_2db7_order",
            "status": "running",
            "evidence_refs": ["runtime/state/lab_assistant/delegation_bindings/status/wf_2db7_order.json"],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    sections = payload["operator_summary"]["sections"]

    assert [section["section_id"] for section in sections] == ["mission_contract", "active_work", "blocked_work", "next_actions", "evidence"]
    assert sections[1]["count"] >= 1
    assert sections[2]["count"] >= 1
    assert sections[3]["count"] >= 1
    assert sections[4]["count"] >= 1


def test_snapshot_status_language_marks_conflicted_on_upstream_completion_conflict(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(tmp_path, status="completed", title="Looks done until you ask for proof")
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="snapshot.bind",
        request=_request(),
        launch_result={"workflow_id": "wf_2db7_conflict"},
    )
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_2db7_conflict", "status": "completed"})

    payload = snapshot.build_operator_snapshot(tmp_path)
    conflicts = payload["operator_summary"]["truth_conflicts"]
    status_language = payload["operator_summary"]["status_language"]
    notifications = payload["operator_summary"]["notifications"]

    assert conflicts
    conflict = next(row for row in conflicts if row["kind"] == "register_completed_but_projection_incomplete")
    assert conflict["field"] == "status"
    assert conflict["canonical_value"] == "completed"
    assert conflict["projected_value"] in {"evidence_missing", "interrupted", "interrupted_recovering", "ambiguous_artifact"}
    assert status_language["state"] == "conflicted"
    assert status_language["success_claim_allowed"] is False
    assert any(row["kind"] == "blocked_or_failed_work" for row in notifications)


def test_snapshot_surfaces_interrupted_recovery_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, title="Recover interrupted task")
    host.bootstrap_host_baseline(tmp_path)
    recovery.reconcile_startup(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["operator_state"] == "interrupted_recovering"
    assert payload["backend_projection"]["state"] == "reconnecting"
    assert payload["operator_summary"]["status_language"]["state"] == "reconnecting"
    assert any(row["kind"] == "resumed_recovery" for row in payload["operator_summary"]["notifications"])
    assert payload["recovery"]["status"] == "interrupted_recovering"
    assert payload["active_tasks"][0]["status"] == "interrupted_recovering"
    assert "no durable delegation bindings" in payload["recovery"]["summary"]


def test_snapshot_reports_host_down_when_host_truth_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    monkeypatch.setattr(snapshot._host, "host_health_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("host offline")))

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["operator_state"] == "host_down_unreachable"
    assert payload["backend_projection"]["state"] == "unreachable"
    assert payload["operator_summary"]["status_language"]["state"] == "unreachable"
    assert any(row["kind"] == "unreachable_server" for row in payload["operator_summary"]["notifications"])
    assert payload["host_health"]["mode"] == "host_down_unreachable"
    assert payload["freshness"]["stale"] is True


def test_snapshot_surfaces_degraded_backend_truth_when_host_reports_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    monkeypatch.setattr(
        snapshot._host,
        "host_health_snapshot",
        lambda *_args, **_kwargs: {
            "schema_version": "lab_assistant_host_health_v1",
            "utc": "2026-03-13T12:00:00Z",
            "status": "degraded",
            "mode": "partial_restore_degraded",
            "blocked": False,
            "summary": "restore is missing required surfaces",
            "reasons": ["partial_restore_missing:task_register"],
            "last_boot_id": "boot_demo",
            "last_boot_utc": "2026-03-13T11:59:00Z",
            "last_shutdown_clean": False,
            "surfaces": [],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-13T12:00:10Z")

    assert payload["backend_projection"]["state"] == "degraded"
    assert payload["operator_summary"]["status_language"]["state"] == "degraded"


def test_snapshot_surfaces_reachable_backend_truth_when_projection_is_fresh_and_settled(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    recovery.reconcile_startup(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["backend_projection"]["state"] == "reachable"
    assert payload["freshness"]["stale"] is False
    assert payload["freshness"]["confidence"] == "HIGH"


def test_snapshot_does_not_report_reconnecting_after_recovery_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    monkeypatch.setattr(
        snapshot._recovery,
        "read_recovery_snapshot",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "reconciled_utc": "2026-03-30T05:47:44Z",
            "summary": "Recovery found no unresolved delegated work.",
        },
    )
    monkeypatch.setattr(
        snapshot._host,
        "host_health_snapshot",
        lambda *_args, **_kwargs: {
            "schema_version": "lab_assistant_host_health_v1",
            "utc": "2026-03-30T05:47:32Z",
            "status": "ok",
            "mode": "interrupted_restart",
            "blocked": False,
            "summary": "Previous boot did not end cleanly.",
            "reasons": ["previous_boot_unclean"],
            "last_boot_id": "boot_demo",
            "last_boot_utc": "2026-03-30T05:13:37Z",
            "last_shutdown_clean": True,
            "surfaces": [],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-30T05:48:00Z")

    assert payload["backend_projection"]["state"] == "reachable"
    assert payload["operator_summary"]["status_language"]["state"] == "idle"
    assert not any(row["kind"] == "resumed_recovery" for row in payload["operator_summary"]["notifications"])


def test_snapshot_exposes_command_center_projection_with_operator_facing_sections(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_attention",
        status="evidence_missing",
        title="Proof gap",
        ledger_uid="PIS-20260312-161622Z-ATTN",
        next_expected_action="Collect focused proof",
    )
    _seed_task(
        tmp_path,
        task_id="task_running",
        status="waiting_on_job",
        title="Builder task",
        ledger_uid="PIS-20260312-161622Z-RUNN",
        next_expected_action="Wait for builder output",
    )
    _seed_task(
        tmp_path,
        task_id="task_scheduled",
        status="scheduled",
        title="Morning follow-up",
        ledger_uid="PIS-20260312-161622Z-SCHD",
        next_expected_action="Wait for scheduled launch",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.command_center",
        request=_request(task_id="task_running"),
        launch_result={"workflow_id": "wf_command_center"},
    )
    ref = bound["delegation_ref"]
    _write_json(
        Path(ref["status_refs"][0]),
        {
            "workflow_id": "wf_command_center",
            "status": "running",
            "evidence_refs": ["runtime/state/lab_assistant/delegation_bindings/status/wf_command_center.json"],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    command_center = payload["command_center"]

    assert command_center["schema_version"] == snapshot.COMMAND_CENTER_SCHEMA_VERSION
    assert command_center["intake"]["prompt"] == "Send a new request through the plane."
    assert command_center["snapshot_meta"]["confidence"] in {"HIGH", "DEGRADED", "STALE"}
    assert [row["task_id"] for row in command_center["needs_attention"]["items"]] == ["task_attention"]
    assert [row["task_id"] for row in command_center["running"]["items"]] == ["task_running"]
    assert [row["task_id"] for row in command_center["scheduled"]["items"]] == ["task_scheduled"]
    assert command_center["running"]["items"][0]["execution_surface"] == "Running (Pipeline)"
    assert "Confidence LOW" not in command_center["running"]["items"][0]["metadata"]
    assert command_center["needs_attention"]["items"][0]["recommended_action"]["recommended"] is True


def test_command_center_does_not_count_queued_delegated_work_as_running(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_queued",
        status="waiting_on_job",
        title="Queued scheduler work",
        next_expected_action="Wait for queued launch",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.command_center_queued",
        request=_request(task_id="task_queued"),
        launch_result={"workflow_id": "wf_command_center_queued"},
    )
    ref = bound["delegation_ref"]
    _write_json(
        Path(ref["status_refs"][0]),
        {
            "workflow_id": "wf_command_center_queued",
            "status": "queued",
            "evidence_refs": [],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    command_center = payload["command_center"]

    assert command_center["running"]["count"] == 0
    assert [row["task_id"] for row in command_center["scheduled"]["items"]] == ["task_queued"]
    assert command_center["scheduled"]["items"][0]["execution_surface"] == "Scheduled (Pipeline)"


def test_snapshot_treats_blocked_question_open_loop_as_blocked_operator_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    current = _seed_task(
        tmp_path,
        task_id="task_blocked_question",
        status="active",
        title="Need acceptance target decision",
        next_expected_action="Wait for blocked question resolution",
    )
    register.upsert_open_loop(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.blocked_question",
        open_loop={
            "loop_id": "loop_blocked_question",
            "kind": "blocked_question",
            "status": "open",
            "linked_task_id": "task_blocked_question",
            "note": "Need operator decision about the acceptance target before continuing.",
        },
    )
    recovery_path = tmp_path / "runtime" / "state" / "lab_assistant" / "recovery" / "recovery_snapshot.json"
    _write_json(
        recovery_path,
        {
            "schema_version": "lab_assistant_recovery_snapshot_v1",
            "status": "ready",
            "summary": "",
            "reconciled_utc": "2026-03-30T20:00:00Z",
            "continuity_proven": True,
            "degraded_reasons": [],
            "surface_gaps": [],
            "task_outcomes": [],
            "recovery_note_path": str(recovery_path),
            "idempotency_journal_path": str(
                tmp_path / "runtime" / "state" / "lab_assistant" / "recovery" / "idempotency_journal.jsonl"
            ),
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-30T20:05:00Z")
    blocked_section = next(
        section for section in payload["operator_summary"]["sections"] if section["section_id"] == "blocked_work"
    )

    assert payload["operator_state"] == "blocked"
    assert payload["operator_summary"]["status_language"]["state"] == "blocked"
    assert blocked_section["count"] == 1
    assert blocked_section["items"][0]["status"] == "blocked_question"
    assert any(row["kind"] == "blocked_or_failed_work" for row in payload["operator_summary"]["notifications"])


def test_snapshot_exposes_work_item_contract_with_next_action_and_allowed_actions(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    current = _seed_task(
        tmp_path,
        task_id="task_input_needed",
        status="waiting_on_operator",
        title="Need operator input",
        next_expected_action="Clarify the acceptance target",
    )
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.work_item",
        task={
            "task_id": "task_input_needed",
            "kind": "software",
            "title": "Need operator input",
            "status": "waiting_on_operator",
            "owner": "assistant",
                "source": "operator",
                "ledger_uid": "PIS-20260312-161622Z-2DB7",
                "next_expected_action": "Clarify the acceptance target",
                "intake": {
                    "schema_version": register.TASK_INTAKE_SCHEMA_VERSION,
                    "raw_request_text": "Build the mobile front door slice.",
                    "inferred_intent": "build mobile front door slice",
                    "work_class": "software",
                    "work_state": "active_work",
                    "readiness": "clarification_needed",
                    "summary": "User asked for a mobile front door update.",
                    "confidence": 0.5,
                    "missing_information": [
                        {
                            "field": "acceptance_target",
                        "question": "What exact acceptance target should this slice prove?",
                        "reason": "The current request is missing a precise proof target.",
                        "required": True,
                    }
                ],
            },
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    work_item = next(row for row in payload["work_items"] if row["task_id"] == "task_input_needed")

    assert work_item["schema_version"] == snapshot.WORK_ITEM_SCHEMA_VERSION
    assert work_item["state"] == "NEEDS_INPUT"
    assert work_item["next_action"]["type"] == "provide_input"
    assert work_item["next_action"]["blocking"] is True
    assert work_item["next_action"]["input_schema"]["required_fields"][0]["field"] == "acceptance_target"
    assert work_item["recommended_action"]["recommended"] is True
    assert work_item["recommended_action"]["risk_level"] == "low"
    assert work_item["confidence"]["label"] == "MEDIUM"
    assert work_item["retry"]["label"] == ""
    allowed = {row["type"]: row for row in work_item["allowed_actions"]}
    assert allowed["provide_input"]["allowed"] is True
    assert allowed["retry"]["allowed"] is False


def test_snapshot_keeps_projection_and_provenance_in_sync_after_successful_writeback(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_2db7_writeback_success", status="active")
    bound = _bind_mailbox_request(
        tmp_path,
        task_id="task_2db7_writeback_success",
        writer="snapshot.bind_mailbox_success",
        request_id="delegate_2db7_writeback_success",
        correlation_id="corr_2db7_writeback_success",
        launch_turn_id="turn_2db7_writeback_success",
    )
    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="snapshot_worker_success",
        writer="snapshot.claim_success",
        lease_s=60,
    )["request_record"]
    mailbox.accept_claim_handoff(
        tmp_path,
        request_id=bound["delegation_ref"]["delegation_request_id"],
        worker_id="snapshot_worker_success",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="job_2db7_writeback_success",
        job_surface="workflow",
        writer="snapshot.handoff_success",
    )
    jobs.record_job_result(
        tmp_path,
        detached_job_id="job_2db7_writeback_success",
        status="completed",
        summary="Operator projection success proof",
        writer="snapshot.success_result",
        producer="test",
        result_id="result_2db7_writeback_success",
        evidence_refs=["runtime/state/lab_assistant/detached_jobs/jobs/job_2db7_writeback_success.json"],
        now_utc="2026-03-13T12:20:00Z",
    )
    writeback.reconcile_task(tmp_path, task_id="task_2db7_writeback_success", writer="snapshot.writeback_success")

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["operator_state"] in {"idle", "successful"}
    assert len(payload["completed_tasks"]) == 1
    task = payload["completed_tasks"][0]
    delegated_outcome = task["delegated_outcome_writeback"]
    assert task["status"] == "completed"
    assert task["evidence_backed_completion"] is True
    assert delegated_outcome["assimilation_status"] == "successful_assimilation"
    assert delegated_outcome["canonical_task_status"] == "completed"
    assert delegated_outcome["decision_reason"] == "terminal_worker_evidence"
    assert payload["delegated_work"][0]["delegated_outcome_writeback"]["assimilation_status"] == "successful_assimilation"
    assert payload["operator_summary"]["status_language"]["success_claim_allowed"] is False


def test_snapshot_projects_canonical_mission_contract_when_present(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    _write_mission_contract(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["mission_contract"]["ok"] is True
    assert payload["mission_contract"]["operator_projection"]["headline"]
    assert payload["mission_contract"]["operator_projection"]["operator_success_model"]
    assert payload["mission_contract"]["operator_projection"]["source_fingerprint"]
    assert payload["mission_surface"]["status"] == "ok"
    assert payload["mission_surface"]["success_model"] == payload["mission_contract"]["operator_projection"]["operator_success_model"]
    assert payload["mission_surface"]["non_goals"] == payload["mission_contract"]["operator_projection"]["explicit_non_goals"]
    assert payload["mission_surface"]["source_fingerprint"] == payload["mission_contract"]["operator_projection"]["source_fingerprint"]
    assert payload["operator_summary"]["mission_surface"]["source_fingerprint"] == payload["mission_contract"]["runtime_projection"]["source_fingerprint"]


def test_operator_snapshot_refreshes_missing_self_check_coverage(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["self_check"]["status"] == "ok"
    assert payload["self_check"]["mode"] == "self_check_ok"
    assert payload["self_check"]["checks"]["storage_write_probe"]["status"] == "pass"
    latest_snapshot_path = tmp_path / "runtime" / "state" / "lab_assistant" / "self_check" / "latest_snapshot.json"
    assert latest_snapshot_path.exists() is True


def test_snapshot_rejects_unproven_completion_without_writeback_evidence(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_2db7_writeback_missing_evidence",
        status="completed",
        title="Writeback proof is missing evidence",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.bind_writeback_missing",
        request=_request(task_id="task_2db7_writeback_missing_evidence"),
        launch_result={"workflow_id": "wf_2db7_writeback_missing"},
    )
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_2db7_writeback_missing", "status": "completed"})
    writeback.reconcile_task(tmp_path, task_id="task_2db7_writeback_missing_evidence", writer="snapshot.writeback_missing")

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["operator_state"] == "blocked"
    assert len(payload["active_tasks"]) == 1
    task = payload["active_tasks"][0]
    delegated_outcome = task["delegated_outcome_writeback"]
    assert task["status"] != "completed"
    assert task["evidence_backed_completion"] is False
    assert delegated_outcome["assimilation_status"] != "successful_assimilation"
    assert payload["operator_summary"]["truth_conflicts"] == []
    assert payload["operator_summary"]["status_language"]["state"] in {"blocked", "conflicted", "stale"}
    assert payload["operator_summary"]["status_language"]["success_claim_allowed"] is False


def test_snapshot_status_contract_reports_unknown_when_task_register_source_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)

    def _missing_task_register_snapshot(*_args, **_kwargs):
        raise RuntimeError("task_register_snapshot_missing")

    monkeypatch.setattr(snapshot._register, "read_task_register_snapshot", _missing_task_register_snapshot)

    payload = snapshot.build_operator_snapshot(tmp_path)

    contract = payload["status_contract"]
    assert contract["state"] == "unknown"
    assert contract["schema_version"] == snapshot.STATUS_CONTRACT_SCHEMA_VERSION
    assert contract["evaluated_utc"]
    assert "task_register_snapshot_missing" in contract["missing_sources"]


def test_snapshot_includes_execution_surface_projection_and_status_contract_fields(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_exec_surface",
        title="Needs execution surface trace",
        status="active",
    )

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.execution_surface",
        task_id="task_exec_surface",
        selected_surface="pipeline",
        execution_id="run_001",
        execution_ref="subsystem_pipeline:run_001",
        binding_ref="binding_pipeline_001",
        job_uri="runtime/state/lab_assistant/pipeline/run_001/state.json",
        trace_id="trace_pipeline_001",
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    surface_projection = payload["execution_surface"]
    assert surface_projection["schema_version"] == snapshot.EXECUTION_SURFACE_SCHEMA_VERSION
    assert surface_projection["state"] == "fresh"
    row = next(task for task in surface_projection["tasks"] if task["task_id"] == "task_exec_surface")
    assert row["selected_surface"] == "pipeline"
    assert row["provenance"]["source"] == "BD05.task_register.task_runs[current_run_id]"
    assert row["reference_status"] in {"execution_ref_active", "execution_ref_unresolved"}

    status_contract = payload["status_contract"]
    assert status_contract["schema_version"] == snapshot.STATUS_CONTRACT_SCHEMA_VERSION
    assert any(field["field"] == "task_status_projection" for field in status_contract["fields"])
    assert any(field["field"] == "execution_surface_projection" for field in status_contract["fields"])


def test_snapshot_task_status_contract_exposes_canonical_task_run_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_run_truth",
        title="Canonical run truth should be traceable",
        status="active",
    )

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.run_truth",
        task_id="task_run_truth",
        selected_surface="pipeline",
        execution_id="run_truth_001",
        execution_ref="subsystem_pipeline:run_truth_001",
        binding_ref="binding_run_truth_001",
        job_uri="runtime/state/lab_assistant/pipeline/run_truth_001/state.json",
        trace_id="trace_run_truth_001",
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    task = next(row for row in payload["active_tasks"] if row["task_id"] == "task_run_truth")
    contract = task["status_contract"]
    run_source = next(source for source in contract["sources"] if source["source"] == "BD05.task_runs")

    assert task["current_run_id"] == "run_truth_001"
    assert task["current_run_state"] == "running"
    assert contract["source_precedence"] == ["canonical_task", "canonical_task_run", "delegation_bindings", "reconciliation"]
    assert contract["current_run_id"] == "run_truth_001"
    assert contract["current_run_state"] == "running"
    assert run_source["value"] == "running"
    assert run_source["provenance"]["run_id"] == "run_truth_001"


def test_snapshot_surfaces_conflict_when_canonical_run_truth_is_blocked(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_run_conflict",
        title="Canonical run conflict should surface",
        status="active",
    )

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.run_conflict",
        task_id="task_run_conflict",
        selected_surface="pipeline",
        execution_id="run_conflict_001",
        execution_ref="subsystem_pipeline:run_conflict_001",
        binding_ref="binding_run_conflict_001",
        job_uri="runtime/state/lab_assistant/pipeline/run_conflict_001/state.json",
        trace_id="trace_run_conflict_001",
        reference_status="execution_ref_conflict",
        reference_status_reason="worker heartbeat disagrees with durable binding",
        allow_conflict_overwrite=True,
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    conflict = next(
        row for row in payload["operator_summary"]["truth_conflicts"] if row["kind"] == "canonical_run_blocked_but_projection_active"
    )
    status_language = payload["operator_summary"]["status_language"]
    task = next(row for row in payload["active_tasks"] if row["task_id"] == "task_run_conflict")

    assert conflict["field"] == "task_run_state"
    assert conflict["task_id"] == "task_run_conflict"
    assert conflict["canonical_value"] == "conflict"
    assert conflict["projected_value"] == "active"
    assert status_language["state"] == "conflicted"
    assert status_language["success_claim_allowed"] is False
    assert task["current_run_state"] == "conflict"


def test_snapshot_task_register_freshness_uses_snapshot_projection_staleness(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    current["write_generation"] = 3
    current["last_written_utc"] = "2026-04-01T00:00:00Z"
    current["summary_stale"] = False
    current["snapshot_projection"] = {
        "schema_version": "lab_assistant_task_register_snapshot_refresh_v1",
        "refresh_generation": 2,
        "refresh_writer": "stale.writer",
        "refresh_utc": "2026-03-30T05:00:00Z",
        "refresh_mode": "eager",
        "refresh_reason": "eager_refresh",
    }
    register._write_json_atomic(register._register_path(tmp_path), current)

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert "task_register" in payload["freshness"]["stale_sources"]
    assert payload["task_register"]["snapshot_projection"]["stale"] is True


def test_snapshot_truth_conflicts_include_completed_tasks_with_blocked_run_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_done_conflict",
        title="Completed task still has blocked canonical run",
        status="completed",
    )

    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.done_conflict",
        task_id="task_done_conflict",
        selected_surface="pipeline",
        execution_id="run_done_conflict_001",
        execution_ref="subsystem_pipeline:run_done_conflict_001",
        binding_ref="binding_done_conflict_001",
        job_uri="runtime/state/lab_assistant/pipeline/run_done_conflict_001/state.json",
        trace_id="trace_done_conflict_001",
        reference_status="execution_ref_conflict",
        reference_status_reason="blocked after completion",
        allow_conflict_overwrite=True,
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    conflict = next(
        row for row in payload["operator_summary"]["truth_conflicts"] if row["kind"] == "canonical_run_blocked_but_projection_active"
    )
    assert conflict["task_id"] == "task_done_conflict"
    assert conflict["canonical_value"] == "conflict"


def test_snapshot_task_status_contract_marks_register_projection_mismatch_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_status_mismatch",
        title="Mismatch should degrade contract",
        status="blocked",
    )
    seeded = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="snapshot.status_mismatch.bind",
        request=_request(task_id="task_status_mismatch"),
        launch_result={"workflow_id": "wf_status_mismatch"},
    )
    monkeypatch.setattr(
        snapshot._bindings,
        "synthesize_task_status",
        lambda repo_root, task_id, task_row, persist_reconciliation=False: {
            "latest": {
                "status": "active",
                "summary": "Delegated surface still reports active.",
                "evidence_refs": [],
            },
            "delegated": True,
            "delegation_count": 1,
            "delegation_refs": [{}],
            "delegation_summaries": [],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    task = next(row for row in payload["active_tasks"] if row["task_id"] == "task_status_mismatch")
    contract = task["status_contract"]

    assert contract["canonical_status"] == "blocked"
    assert contract["effective_status"] == "active"
    assert contract["mismatch"] is True
    assert contract["contract_state"] == "degraded"


def test_snapshot_includes_run_checkpoint_and_resume_decision(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_checkpoint_snapshot",
        title="Checkpoint projection",
        status="active",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.checkpoint",
        task_id="task_checkpoint_snapshot",
        selected_surface="pipeline",
        execution_id="run_snap_001",
        execution_ref="subsystem_pipeline:run_snap_001",
        binding_ref="binding_snap_001",
        job_uri="runtime/state/lab_assistant/pipeline/run_snap_001/state.json",
        trace_id="trace_snap_001",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.checkpoint",
        task_id="task_checkpoint_snapshot",
        run_id="run_snap_001",
        checkpoint={
            "checkpoint_id": "cp_snap_001",
            "checkpoint_status": "recorded",
            "checkpoint_note": "Checkpoint written.",
            "progress_percent": 62.0,
            "progress_summary": "Halfway through work.",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_snap_001.json"],
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    task = next(row for row in payload["active_tasks"] if row["task_id"] == "task_checkpoint_snapshot")
    assert task["run_checkpoint"]["checkpoint_id"] == "cp_snap_001"
    assert task["run_resume_decision"]["decision"] == "resume"
    surface_row = next(row for row in payload["execution_surface"]["tasks"] if row["task_id"] == "task_checkpoint_snapshot")
    assert surface_row["run_checkpoint"]["checkpoint_id"] == "cp_snap_001"
    assert surface_row["run_resume_decision"]["decision"] == "resume"


def test_snapshot_surfaces_latest_run_intervention_and_conflict_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_intervention_snapshot",
        title="Intervention projection",
        status="active",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.intervention",
        task_id="task_intervention_snapshot",
        selected_surface="pipeline",
        execution_id="run_intervention_snapshot_001",
        execution_ref="subsystem_pipeline:run_intervention_snapshot_001",
        binding_ref="binding_intervention_snapshot_001",
        job_uri="runtime/state/lab_assistant/pipeline/run_intervention_snapshot_001/state.json",
        trace_id="trace_intervention_snapshot_001",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_task_run_intervention(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.intervention",
        task_id="task_intervention_snapshot",
        run_id="run_intervention_snapshot_001",
        action="pause",
        reason="Pause requested.",
        requested_by="operator",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_task_run_intervention(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.intervention",
        task_id="task_intervention_snapshot",
        run_id="run_intervention_snapshot_001",
        action="resume",
        reason="Conflicting resume requested.",
        requested_by="operator",
    )

    payload = snapshot.build_operator_snapshot(tmp_path)
    task = next(row for row in payload["active_tasks"] if row["task_id"] == "task_intervention_snapshot")
    assert task["run_intervention"]["action"] == "resume"
    assert task["run_intervention"]["outcome_state"] == "conflict"
    surface_row = next(row for row in payload["execution_surface"]["tasks"] if row["task_id"] == "task_intervention_snapshot")
    assert surface_row["run_intervention"]["outcome_state"] == "conflict"
    assert "intervention_conflict" in surface_row["unknown_reasons"]


def test_snapshot_prefers_task_run_record_over_execution_surface_ownership(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(
        tmp_path,
        task_id="task_exec_surface_preferred",
        title="Run canonical run should win",
        status="active",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in current["active_tasks"] if task["task_id"] == "task_exec_surface_preferred")
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.execowner",
        task_id="task_exec_surface_preferred",
        selected_surface="pipeline",
        execution_id="run_001",
        execution_ref="subsystem_pipeline:run_001",
        binding_ref="binding_preferred_001",
        job_uri="runtime/state/pipeline/run_001/state.json",
        trace_id="trace_pipeline_001",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    stale_ownership_task = next(task for task in current["active_tasks"] if task["task_id"] == "task_exec_surface_preferred")
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.execowner",
        task={
            "task_id": "task_exec_surface_preferred",
            "kind": "software",
            "title": "Run canonical run should win",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-161622Z-2DB7",
            "next_expected_action": "demonstrate precedence",
            "execution_surface_ownership": {
                "schema_version": register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                "selected_surface": "scheduler",
                "execution_id": "intruder_001",
                "execution_ref": "scheduler:intruder_001",
                "binding_ref": "binding_intruder",
                "job_uri": "runtime/state/scheduler/jobs/intruder_001.json",
                "trace_id": "trace_scheduler_intruder",
                "reference_status": "execution_ref_active",
                "reference_status_reason": "legacy overwrite attempt",
                "reference_revision": 99,
                "owner_uid": register._EXECUTION_OWNER_UID,
            },
            "task_runs": stale_ownership_task.get("task_runs") or [],
            "current_run_id": stale_ownership_task.get("current_run_id") or "",
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    surface_projection = payload["execution_surface"]
    row = next(task for task in surface_projection["tasks"] if task["task_id"] == "task_exec_surface_preferred")
    assert row["current_run_id"] == stale_ownership_task.get("current_run_id")
    assert row["run_state"] == "running"
    assert row["selected_surface"] == "pipeline"
    assert row["execution_id"] == "run_001"
    assert row["provenance"]["source"] == "BD05.task_register.task_runs[current_run_id]"


def test_snapshot_marks_execution_surface_degraded_when_run_and_ownership_disagree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_exec_surface_disagreement",
        title="Mixed stale ownership should not look fresh",
        status="active",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.execowner.disagreement",
        task_id="task_exec_surface_disagreement",
        selected_surface="pipeline",
        execution_id="run_live_001",
        execution_ref="subsystem_pipeline:run_live_001",
        binding_ref="binding_live_001",
        job_uri="runtime/state/pipeline/run_live_001/state.json",
        trace_id="trace_live_001",
    )
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = next(task for task in current["active_tasks"] if task["task_id"] == "task_exec_surface_disagreement")
    register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="snapshot.execowner.disagreement",
        task={
            **task,
            "execution_surface_ownership": {
                "schema_version": register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                "selected_surface": "scheduler",
                "execution_id": "remote_shadow_001",
                "execution_ref": "scheduler:remote_shadow_001",
                "binding_ref": "binding_remote_shadow_001",
                "job_uri": "runtime/state/scheduler/jobs/remote_shadow_001.json",
                "trace_id": "trace_remote_shadow_001",
                "reference_status": "execution_ref_active",
                "reference_status_reason": "stale_remote_restore_claim",
                "reference_revision": 2,
                "owner_uid": register._EXECUTION_OWNER_UID,
            },
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path)

    row = next(task for task in payload["execution_surface"]["tasks"] if task["task_id"] == "task_exec_surface_disagreement")
    assert row["contract_state"] == "degraded"
    assert "ownership_run_disagreement" in row["unknown_reasons"]


def test_snapshot_status_contract_operator_state_sources_are_traceable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(
        tmp_path,
        task_id="task_operator_state_trace",
        status="waiting_on_job",
        title="Needs operator-state traceability",
    )

    monkeypatch.setattr(
        snapshot._state,
        "read_assistant_state_snapshot",
        lambda *_args, **_kwargs: {
            "assistant_state": {
                "current_state": "assistant_idle",
                "last_written_utc": "2026-03-13T11:58:00Z",
            },
        },
    )
    monkeypatch.setattr(
        snapshot._recovery,
        "read_recovery_snapshot",
        lambda *_args, **_kwargs: {
            "status": "interrupted_recovering",
            "reconciled_utc": "2026-03-13T11:57:00Z",
            "summary": "Patched recovery status for traceability test",
        },
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-13T12:00:10Z")
    contract = payload["status_contract"]
    operator_field = next(field for field in contract["fields"] if field["field"] == "operator_state")

    assert operator_field["source_order"] == [
        "backend_projection.state",
        "assistant_state.current_state",
        "recovery_snapshot.status",
    ]
    assert [source["source"] for source in operator_field["sources"]] == [
        "backend_projection.state",
        "assistant_state.current_state",
        "recovery_snapshot.status",
    ]
    assert operator_field["sources"][0]["value"] == "reconnecting"
    assert operator_field["sources"][1]["value"] == "assistant_idle"
    assert operator_field["sources"][2]["value"] == "interrupted_recovering"


def test_snapshot_status_contract_degraded_state_records_reason_and_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    def _stale_register_snapshot(*_args, **_kwargs):
        return {
            "task_register": {
                "active_tasks": [
                    {
                        "task_id": "task_stale_projection",
                        "title": "Old active task",
                        "status": "waiting_on_job",
                        "updated_utc": "2026-03-13T08:00:00Z",
                        "execution_surface_ownership": {
                            "selected_surface": "pipeline",
                            "reference_status": "execution_ref_unresolved",
                            "owner_uid": "PIS-20260312-223543Z-E4F1",
                        },
                    },
                ],
                "completed_tasks": [],
                "open_loops": [],
                "last_written_utc": "2026-03-13T08:00:00Z",
                "summary_cache": {
                    "generated_utc": "2026-03-13T08:00:00Z",
                    "generated_task_count": 0,
                    "open_task_count": 0,
                    "completed_task_count": 0,
                },
            },
            "summary_cache": {
                "generated_utc": "2026-03-13T08:00:00Z",
                "generated_task_count": 0,
                "open_task_count": 0,
                "completed_task_count": 0,
            },
        }

    def _assistant_state_snapshot(*_args, **_kwargs):
        return {
            "assistant_state": {
                "current_state": "assistant_idle",
                "last_written_utc": "2026-03-13T11:59:30Z",
            },
        }

    def _recovery_snapshot(*_args, **_kwargs):
        return {
            "status": "interrupted_recovering",
            "reconciled_utc": "2026-03-13T11:59:50Z",
            "summary": "Patched recovery status for degraded contract test",
        }

    monkeypatch.setattr(snapshot._register, "read_task_register_snapshot", _stale_register_snapshot)
    monkeypatch.setattr(snapshot._state, "read_assistant_state_snapshot", _assistant_state_snapshot)
    monkeypatch.setattr(snapshot._recovery, "read_recovery_snapshot", _recovery_snapshot)

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-03-13T12:00:00Z")
    contract = payload["status_contract"]
    operator_field = next(field for field in contract["fields"] if field["field"] == "operator_state")

    assert contract["state"] == "degraded"
    assert contract["evaluated_utc"] == "2026-03-13T12:00:00Z"
    assert contract["missing_sources"] == []
    assert "task_register" in contract["stale_sources"]
    assert "backend:task_register" in contract["degraded_reasons"]
    assert operator_field["sources"][0]["value"] == "reconnecting"
    assert operator_field["sources"][1]["value"] == "assistant_idle"
    assert operator_field["sources"][2]["value"] == "interrupted_recovering"


def test_snapshot_projection_contract_marks_snapshot_as_default_operator_projection(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)

    payload = snapshot.build_operator_snapshot(tmp_path)

    contract = payload["projection_contract"]
    assert contract["schema_version"] == snapshot.PROJECTION_CONTRACT_SCHEMA_VERSION
    assert contract["default_operator_projection"]["api_path"] == "/api/admin/lab_assistant/snapshot"
    assert contract["default_operator_projection"]["canonical_for_operator_use"] is True
    assert contract["ownership"]["canonical_truth_owner"] == "BD05.task_register"
    assert any(row["field"] == "operator_state" for row in contract["field_contracts"])
    assert any(row["field"] == "execution_surface_projection" for row in contract["field_contracts"])


def test_snapshot_projection_contract_marks_cached_truth_when_backend_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)

    def _host_down(*_args, **_kwargs):
        raise RuntimeError("host unreachable")

    monkeypatch.setattr(snapshot._host, "host_health_snapshot", _host_down)

    payload = snapshot.build_operator_snapshot(tmp_path)

    projection_state = payload["projection_contract"]["projection_state"]
    assert payload["backend_projection"]["state"] == "unreachable"
    assert projection_state["backend_state"] == "unreachable"
    assert projection_state["cached_truth_must_be_marked"] is True

def test_snapshot_status_language_exposes_copy_for_waiting_on_operator(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    recovery.reconcile_startup(tmp_path, writer="snapshot.language_waiting")
    _seed_task(tmp_path, task_id="task_waiting_operator", status="waiting_on_operator", next_expected_action="Need operator input")

    payload = snapshot.build_operator_snapshot(tmp_path)

    status_language = payload["operator_summary"]["status_language"]
    assert status_language["state"] == "waiting"
    assert status_language["copy_key"] == "waiting_on_operator"
    assert status_language["label"] == "Waiting on operator"
    assert payload["work_items"][0]["state_copy_key"] == "waiting_on_operator"
    assert payload["work_items"][0]["state_label"] == "Waiting on operator"


def test_snapshot_self_check_blocked_prevents_idle_success_language(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    recovery.reconcile_startup(tmp_path, writer="snapshot.self_check")
    self_check.write_self_check_receipt(tmp_path, check_key="storage_write_probe", status="fail", details={"forced": True})

    payload = snapshot.build_operator_snapshot(tmp_path)

    assert payload["self_check"]["mode"] == "self_check_degraded"
    status_language = payload["operator_summary"]["status_language"]
    assert status_language["state"] == "degraded"
    assert status_language["copy_key"] == "self_check_degraded"
    assert status_language["success_claim_allowed"] is False
    assert payload["operator_summary"]["notifications"][0]["kind"] == "self_check_degraded"


def test_self_check_payload_refreshes_missing_probe_receipt(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)

    payload = routes._lab_assistant_self_check_payload(repo_root=tmp_path)

    assert payload["status"] == "ok"
    assert payload["mode"] == "self_check_ok"
    assert payload["missing_checks"] == []
    assert payload["checks"]["storage_write_probe"]["status"] == "pass"


def test_self_check_payload_keeps_failed_receipt_visible(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    self_check.write_self_check_receipt(tmp_path, check_key="storage_write_probe", status="fail", details={"forced": True})

    payload = routes._lab_assistant_self_check_payload(repo_root=tmp_path)

    assert payload["status"] == "degraded"
    assert payload["mode"] == "self_check_degraded"
    assert payload["failed_checks"] == ["storage_write_probe"]


def test_snapshot_status_language_exposes_host_down_copy_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    register.load_or_init_task_register(tmp_path)
    recovery.reconcile_startup(tmp_path, writer="snapshot.host_down_copy")

    def _host_down(*_args, **_kwargs):
        return {
            "schema_version": "lab_assistant_host_health_v1",
            "utc": "2026-04-02T00:00:00Z",
            "status": "blocked",
            "mode": "host_down_unreachable",
            "blocked": True,
            "summary": "Host is unreachable",
            "reasons": ["host_down_unreachable"],
            "restore_receipt_present": False,
            "surfaces": [],
        }

    monkeypatch.setattr(snapshot._host, "host_health_snapshot", _host_down)
    payload = snapshot.build_operator_snapshot(tmp_path)

    status_language = payload["operator_summary"]["status_language"]
    assert status_language["state"] == "unreachable"
    assert status_language["copy_key"] == "host_down"
    assert status_language["label"] == "Host down"
