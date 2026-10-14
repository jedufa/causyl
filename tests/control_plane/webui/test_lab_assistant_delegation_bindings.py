from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_mailbox as mailbox
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_routing_policy as routing


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "webui" / "verification_jobs").mkdir(parents=True, exist_ok=True)


def _bootstrap(root: Path) -> None:
    host.bootstrap_host_baseline(root)


def _seed_task(root: Path) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="delegation.test",
        task={
            "task_id": "task_df0c",
            "kind": "software",
            "title": "Implement DF0C",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260312-161622Z-DF0C",
            "next_expected_action": "bind delegated work",
        },
    )["task_register"]


def _current_run_checkpoint(snapshot: dict, *, task_id: str) -> dict:
    task = next(task for task in snapshot["active_tasks"] if task["task_id"] == task_id)
    run_id = task.get("current_run_id")
    run_row = next(row for row in task.get("task_runs") or [] if row.get("run_id") == run_id)
    return dict(run_row.get("checkpoint") or {})


def _classification(surface: str) -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": f"delegate via {surface}",
            "preferred_surface": surface,
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
        }
    )


def _request(
    surface: str,
    *,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    launch_turn_id: str | None = None,
) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification(surface),
        surface=surface,
        owner_ref={"task_id": "task_df0c", "ledger_uid": "PIS-20260312-161622Z-DF0C"},
        summary=f"Use {surface}",
        requested_action=f"launch {surface}",
        expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
        next_decision={"kind": "review_evidence", "summary": f"Review {surface} result", "owner": "assistant"},
        input_payload={"scope": "focused"},
        request_id=request_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        launch_turn_id=launch_turn_id,
    )


def test_bindings_adapt_existing_subsystem_ids_and_persist_refs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    gen = int(seeded["write_generation"])

    workflow = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=gen,
        writer="df0c.workflow",
        request=_request("workflow"),
        launch_result={"workflow_id": "wf_df0c_1"},
    )
    task = workflow["task_register"]["active_tasks"][0]
    assert task["delegation_refs"][0]["subsystem"] == "workflow"
    assert task["delegation_refs"][0]["subsystem_id"] == "wf_df0c_1"
    assert task["delegation_refs"][0]["status_refs"][0].endswith("workflow__wf_df0c_1.json")
    assert task["delegation_refs"][0]["correlation_id"].startswith("corr_")
    assert task["delegation_refs"][0]["launch_turn_id"].startswith("turn_")
    assert task["delegation_refs"][0]["mission_fit"]["status"] == "mission_aligned"

    gen = int(workflow["task_register"]["write_generation"])
    verifier = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=gen,
        writer="df0c.verifier",
        request=_request("verifier"),
        launch_result={
            "request_id": "vrf_df0c_1",
            "workflow_id": "wf_df0c_1",
            "action_id": "android_unit_test",
            "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.SmokeTest"},
        },
    )
    task = verifier["task_register"]["active_tasks"][0]
    verifier_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "verifier")
    assert verifier_ref["subsystem_id"] == "vrf_df0c_1"
    assert verifier_ref["evidence_candidates"][0].endswith("vrf_df0c_1/result.json")
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "verifier"
    assert ownership.get("execution_ref")
    assert ownership.get("binding_ref")
    assert ownership.get("job_uri")
    assert ownership.get("reference_status") == "execution_ref_active"

    gen = int(verifier["task_register"]["write_generation"])
    scheduler = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=gen,
        writer="df0c.scheduler",
        request=_request("scheduler"),
        launch_result={"job_id": "overnight_df0c", "run_id": "sj_20260312T220000Z_abcd12"},
    )
    task = scheduler["task_register"]["active_tasks"][0]
    scheduler_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "scheduler")
    assert scheduler_ref["subsystem_id"] == "overnight_df0c"
    assert scheduler_ref["evidence_candidates"][0].endswith("overnight_df0c/sj_20260312T220000Z_abcd12.json")

    gen = int(scheduler["task_register"]["write_generation"])
    queue = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=gen,
        writer="df0c.queue",
        request=_request("execution_queue"),
        launch_result={"run_id": "eq-20260312t220001z"},
    )
    task = queue["task_register"]["active_tasks"][0]
    queue_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "execution_queue")
    assert queue_ref["subsystem_id"] == "eq-20260312t220001z"
    assert queue_ref["status_refs"][0].endswith("execution_queue/state.json")

    reverse = queue["delegation_snapshot"]["reverse_index"]["bindings"]
    assert reverse["workflow:wf_df0c_1"]["task_id"] == "task_df0c"
    assert reverse["verifier:vrf_df0c_1"]["delegation_request_id"]


def test_binding_persists_partial_mission_fit_note_in_delegation_ref(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "repo-adjacent side request",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "mission_alignment": "partially_aligned",
            "mission_alignment_note": "Allowed because it directly unblocks the current lab software change.",
        }
    )
    request = policy.build_typed_delegation_request(
        classification=classification,
        surface="workflow",
        owner_ref={"task_id": "task_df0c", "ledger_uid": "PIS-20260312-161622Z-DF0C"},
        summary="Use workflow",
        requested_action="launch workflow",
        expected_evidence=[{"kind": "status_artifact", "description": "workflow evidence", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"scope": "focused"},
    )

    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="df0c.partial_mission",
        request=request,
        launch_result={"workflow_id": "wf_partial_mission"},
    )

    task = bound["task_register"]["active_tasks"][0]
    ref = next(ref for ref in task["delegation_refs"] if ref["subsystem_id"] == "wf_partial_mission")
    assert ref["mission_fit"]["status"] == "partially_aligned"
    assert ref["mission_fit"]["note"] == "Allowed because it directly unblocks the current lab software change."


def test_task_register_load_backfills_legacy_delegation_ref_mission_fit_status(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="df0c.legacy_mission_fit",
        request=_request("workflow"),
        launch_result={"workflow_id": "wf_legacy_mission_fit"},
    )

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    payload = json.loads(register_path.read_text(encoding="utf-8"))
    payload["active_tasks"][0]["delegation_refs"][0]["mission_fit"].pop("status", None)
    register_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    reloaded = register.load_or_init_task_register(tmp_path)
    task = reloaded["active_tasks"][0]
    ref = next(ref for ref in task["delegation_refs"] if ref["subsystem_id"] == "wf_legacy_mission_fit")
    assert ref["mission_fit"]["status"] == "mission_aligned"


def test_task_register_load_accepts_legacy_delegation_ref_without_mission_fit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="df0c.legacy_missing_mission_fit",
        request=_request("workflow"),
        launch_result={"workflow_id": "wf_legacy_missing_mission_fit"},
    )

    register_path = tmp_path / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    payload = json.loads(register_path.read_text(encoding="utf-8"))
    payload["active_tasks"][0]["delegation_refs"][0].pop("mission_fit", None)
    register_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    reloaded = register.load_or_init_task_register(tmp_path)
    task = reloaded["active_tasks"][0]
    ref = next(ref for ref in task["delegation_refs"] if ref["subsystem_id"] == "wf_legacy_missing_mission_fit")
    assert ref["subsystem_id"] == "wf_legacy_missing_mission_fit"
    assert ref.get("mission_fit") in ({}, None)


def test_surface_launch_records_execution_surface_ownership_for_verifier(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    result = bindings.bind_surface_launch(
        tmp_path,
        surface="verifier",
        binding={"type": "task_id", "id": "task_df0c"},
        summary="Host verification request",
        requested_action="run host verification",
        launch_result={
            "request_id": "vrf_surface_1",
            "workflow_id": "wf_surface",
            "action_id": "android_unit_test",
            "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.HostVerificationSentinelTest"},
            "reason": "host verification",
            "reference_status": "execution_ref_active",
            "reference_status_reason": "host_verification_launched",
        },
        writer="df0c.surface.bind",
    )
    assert result["ok"] is True

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = snapshot["active_tasks"][0]
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "verifier"
    assert ownership.get("execution_ref") == "vrf_surface_1"
    assert ownership.get("binding_ref") == "vrf_surface_1"
    assert ownership.get("job_uri", "").endswith("status.json")
    assert ownership.get("reference_status") == "execution_ref_active"


def test_surface_launch_retries_stale_task_register_generation_before_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    original = bindings.bind_delegation_request
    calls = {"count": 0}

    def _flaky_bind(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] <= 4:
            raise bindings.DelegationBindingsError(
                "stale write rejected before binding: expected_generation=10 current_generation=11"
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(bindings, "bind_delegation_request", _flaky_bind)

    result = bindings.bind_surface_launch(
        tmp_path,
        surface="verifier",
        binding={"type": "task_id", "id": "task_df0c"},
        summary="Host verification request",
        requested_action="run host verification",
        launch_result={
            "request_id": "vrf_surface_retry",
            "workflow_id": "wf_surface_retry",
            "action_id": "android_unit_test",
            "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.HostVerificationSentinelTest"},
            "reason": "host verification",
            "reference_status": "execution_ref_active",
            "reference_status_reason": "host_verification_launched",
        },
        writer="df0c.surface.retry",
        stale_retry_backoff_s=0.0,
    )
    assert result["ok"] is True
    assert len(result["attempts"]) == 4
    assert calls["count"] == 5

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = snapshot["active_tasks"][0]
    ownership = dict(task.get("execution_surface_ownership") or {})
    assert ownership.get("selected_surface") == "verifier"
    assert ownership.get("execution_ref") == "vrf_surface_retry"
    assert ownership.get("reference_status") == "execution_ref_active"


def test_rebuild_delegation_snapshot_uses_read_only_reconciliation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    calls: list[dict] = []
    original = bindings.synthesize_task_status

    def _wrapped(repo_root: Path, *, task_id: str, task_row: dict | None = None, persist_reconciliation: bool = True) -> dict:
        calls.append(
            {
                "task_id": task_id,
                "task_row_present": isinstance(task_row, dict),
                "persist_reconciliation": persist_reconciliation,
            }
        )
        return original(
            repo_root,
            task_id=task_id,
            task_row=task_row,
            persist_reconciliation=persist_reconciliation,
        )

    monkeypatch.setattr(bindings, "synthesize_task_status", _wrapped)

    snapshot = bindings.rebuild_delegation_snapshot(tmp_path, writer="df0c.snapshot")

    assert snapshot["snapshot"]["tasks"]
    assert calls
    assert all(call["task_row_present"] for call in calls)
    assert all(call["persist_reconciliation"] is False for call in calls)


def test_bindings_record_checkpoint_for_managed_surfaces(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    gen = int(seeded["write_generation"])

    cases = {
        "workflow": (
            {"workflow_id": "wf_df0c_checkpoint"},
            "workflow:wf_df0c_checkpoint",
            None,
        ),
        "verifier": (
            {
                "request_id": "vrf_df0c_checkpoint",
                "workflow_id": "wf_df0c_checkpoint",
                "action_id": "android_unit_test",
                "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.SmokeTest"},
            },
            "verifier:vrf_df0c_checkpoint",
            None,
        ),
        "scheduler": (
            {"job_id": "overnight_df0c_checkpoint", "run_id": "sj_20260312T220000Z_chk"},
            "scheduler:overnight_df0c_checkpoint",
            None,
        ),
        "execution_queue": (
            {"run_id": "eq_df0c_checkpoint"},
            "execution_queue:eq_df0c_checkpoint",
            None,
        ),
        "retrieval": (
            {"ref_id": "retrieval_df0c_checkpoint"},
            "retrieval:retrieval_df0c_checkpoint",
            None,
        ),
        "admin": (
            {"operation_id": "admin_df0c_checkpoint"},
            "admin:admin_df0c_checkpoint",
            None,
        ),
        "watchdog": (
            {},
            "watchdog:watchdog_req_checkpoint",
            "watchdog_req_checkpoint",
        ),
        "healer": (
            {},
            "healer:healer_req_checkpoint",
            "healer_req_checkpoint",
        ),
        "ledger": (
            {"uid": "PIS-20260312-161622Z-DF0C"},
            "ledger:PIS-20260312-161622Z-DF0C",
            None,
        ),
        "mailbox": (
            {},
            "mailbox:mailbox_req_checkpoint",
            "mailbox_req_checkpoint",
        ),
    }
    expected_surfaces = set(policy.ALLOWED_V1_SURFACES) | {"mailbox"}
    assert set(cases) == expected_surfaces

    for surface, (launch_result, expected_checkpoint_id, request_id) in cases.items():
        request = policy.build_typed_delegation_request(
            classification={"classification": "delegate", "reason_codes": ["checkpoint_binding_test"]},
            surface=surface,
            owner_ref={"task_id": "task_df0c", "ledger_uid": "PIS-20260312-161622Z-DF0C"},
            summary=f"Use {surface}",
            requested_action=f"launch {surface}",
            expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
            next_decision={"kind": "review_evidence", "summary": f"Review {surface} result", "owner": "assistant"},
            input_payload={"scope": "focused"},
            request_id=request_id,
            idempotency_key=request_id or f"idem_{surface}",
        )
        result = bindings.bind_delegation_request(
            tmp_path,
            expected_generation=gen,
            writer=f"df0c.checkpoint.{surface}",
            request=request,
            launch_result=launch_result,
        )
        snapshot = result["task_register"]
        checkpoint = _current_run_checkpoint(snapshot, task_id="task_df0c")
        assert checkpoint["checkpoint_id"] == expected_checkpoint_id
        assert checkpoint["checkpoint_status"] == "recorded"
        gen = int(snapshot["write_generation"])


def test_synthesis_reports_evidence_present_missing_interrupted_and_ambiguous(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    workflow_ref = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="df0c.synth.bind",
        request=_request("workflow"),
        launch_result={"workflow_id": "wf_df0c_synth"},
    )["delegation_ref"]

    state_path = Path(workflow_ref["status_refs"][0])
    digest_path = Path(workflow_ref["evidence_candidates"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.parent.mkdir(parents=True, exist_ok=True)

    state_path.write_text(json.dumps({"workflow_id": "wf_df0c_synth", "status": "completed"}), encoding="utf-8")
    digest_path.write_text(json.dumps({"summary": "proof"}), encoding="utf-8")
    present = bindings.synthesize_task_status(tmp_path, task_id="task_df0c")
    assert present["latest"]["status"] == "completed"
    assert present["latest"]["evidence_status"] == "present"
    assert present["latest"]["schema_version"] == policy.DELEGATION_RESULT_SCHEMA_VERSION
    assert present["latest"]["request_id"] == workflow_ref["delegation_request_id"]
    assert present["latest"]["correlation_id"] == workflow_ref["correlation_id"]

    digest_path.unlink()
    missing = bindings.synthesize_task_status(tmp_path, task_id="task_df0c")
    assert missing["latest"]["status"] == "evidence_missing"
    assert "Collect durable evidence" in missing["latest"]["next_decision"]["summary"]

    alt = digest_path.parent / "secondary.json"
    digest_path.write_text(json.dumps({"summary": "proof"}), encoding="utf-8")
    alt.write_text(json.dumps({"summary": "other"}), encoding="utf-8")
    current = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = current["active_tasks"][0]
    ref = dict(task["delegation_refs"][0])
    ref["evidence_candidates"] = [str(digest_path), str(alt)]
    task["delegation_refs"] = [ref]
    updated = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="df0c.ambiguous",
        task=task,
    )["task_register"]
    ambiguous = bindings.synthesize_task_status(tmp_path, task_id="task_df0c")
    assert ambiguous["latest"]["status"] == "ambiguous_artifact"

    state_path.unlink()
    interrupted = bindings.synthesize_task_status(tmp_path, task_id="task_df0c")
    assert interrupted["latest"]["status"] == "interrupted"
    assert interrupted["latest"]["evidence_status"] == "missing_native_record"

    residue_summary = (
        tmp_path
        / "governance"
        / "agents"
        / "runtime"
        / "orchestration"
        / "workflow__wf_df0c_synth.artifacts"
        / "repo_residue"
        / "summary.json"
    )
    residue_summary.parent.mkdir(parents=True, exist_ok=True)
    residue_summary.write_text(json.dumps({"status": "captured"}), encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "workflow_id": "wf_df0c_synth",
                "status": "interrupted",
                "interrupted_context": {
                    "reason": "Workflow restart left builder_run without a persisted safe checkpoint.",
                    "residue_summary_artifact": "governance/agents/runtime/orchestration/workflow__wf_df0c_synth.artifacts/repo_residue/summary.json",
                },
            }
        ),
        encoding="utf-8",
    )
    reconciled = bindings.synthesize_task_status(tmp_path, task_id="task_df0c")
    assert reconciled["latest"]["status"] == "interrupted"
    assert reconciled["latest"]["evidence_status"] == "ambiguous"
    assert any(str(path).endswith("summary.json") for path in reconciled["latest"]["evidence_refs"])


def test_verifier_and_scheduler_synthesis_use_native_records_without_guesswork(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    current_generation = int(seeded["write_generation"])

    verifier_result = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=current_generation,
        writer="df0c.verify.bind",
        request=_request("verifier"),
        launch_result={
            "request_id": "vrf_df0c_native",
            "workflow_id": "wf_df0c_native",
            "action_id": "android_unit_test",
            "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.SmokeTest"},
        },
    )
    current_generation = int(verifier_result["task_register"]["write_generation"])
    scheduler_result = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=current_generation,
        writer="df0c.scheduler.bind",
        request=_request("scheduler"),
        launch_result={"job_id": "overnight_df0c_native", "run_id": "sj_20260312T220500Z_abcd12"},
    )

    register_now = scheduler_result["task_register"]
    task = register_now["active_tasks"][0]
    verifier_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "verifier")
    scheduler_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "scheduler")

    verifier_status_path = Path(verifier_ref["status_refs"][0])
    verifier_result_path = Path(verifier_ref["evidence_candidates"][0])
    verifier_status_path.parent.mkdir(parents=True, exist_ok=True)
    verifier_status_path.write_text(json.dumps({"status": "running", "request_id": "vrf_df0c_native"}), encoding="utf-8")
    waiting = bindings.synthesize_delegation_ref(tmp_path, verifier_ref)
    assert waiting["status"] == "waiting_on_verification"
    assert waiting["schema_version"] == policy.DELEGATION_RESULT_SCHEMA_VERSION

    verifier_result_path.write_text(json.dumps({"ok": True, "status": "passed"}), encoding="utf-8")
    verified = bindings.synthesize_delegation_ref(tmp_path, verifier_ref)
    assert verified["status"] == "completed"
    assert verified["evidence_refs"] == [str(verifier_result_path.resolve())]

    scheduler_state_path = Path(scheduler_ref["status_refs"][0])
    scheduler_receipt_path = Path(scheduler_ref["evidence_candidates"][0])
    scheduler_state_path.parent.mkdir(parents=True, exist_ok=True)
    scheduler_receipt_path.parent.mkdir(parents=True, exist_ok=True)
    scheduler_state_path.write_text(
        json.dumps({"job_id": "overnight_df0c_native", "last_status": "ok", "last_receipt_path": str(scheduler_receipt_path)}),
        encoding="utf-8",
    )
    no_receipt = bindings.synthesize_delegation_ref(tmp_path, scheduler_ref)
    assert no_receipt["status"] == "evidence_missing"

    scheduler_receipt_path.write_text(json.dumps({"status": "ok", "run_id": "sj_20260312T220500Z_abcd12"}), encoding="utf-8")
    completed = bindings.synthesize_delegation_ref(tmp_path, scheduler_ref)
    assert completed["status"] == "completed"
    assert completed["evidence_refs"] == [str(scheduler_receipt_path.resolve())]


def test_routed_scheduler_launch_preserves_decision_id_and_proves_idempotent_ref_binding(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)

    routed = routing.route_typed_delegation_request(
        _request(
            "scheduler",
            request_id="delegate_task_r5t7",
            idempotency_key="idem_r5t7",
        ),
        routing_facts={
            "requires_schedule": True,
            "needs_background_schedule": True,
            "needs_immediate_start": False,
            "scheduler_safe": True,
            "scheduler_job_kind": "build_verify_loop",
            "code_changes_expected": True,
            "scheduler_max_iterations": 4,
            "explicit_scheduler_justification": "R5T7 route handoff contract",
        },
    )
    assert routed["ok"] is True
    assert routed["decision"]["selected_surface"] == "scheduler"
    routing_decision_id = routed["request"]["input_payload"]["routing_decision"]["decision_id"]

    first = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="r5t7.scheduler.bind",
        request=routed["request"],
        launch_result={"job_id": "overnight_r5t7", "run_id": "sj_20260313T210000Z_abc123"},
    )
    ref = first["delegation_ref"]
    assert ref["surface"] == "scheduler"
    assert ref["routing_decision"]["decision_id"] == routing_decision_id
    assert ref["evidence_candidates"][0].endswith("overnight_r5t7/sj_20260313T210000Z_abc123.json")

    second = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="r5t7.scheduler.rebind",
        request=routed["request"],
        launch_result={"job_id": "overnight_r5t7", "run_id": "sj_20260313T210000Z_abc123"},
    )
    assert second["duplicate_suppressed"] is True
    assert second["delegation_ref"]["delegation_request_id"] == ref["delegation_request_id"]


def test_scheduler_launch_deferred_without_receipt_stays_non_green_until_terminal_artifact(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    routed = routing.route_typed_delegation_request(
        _request(
            "scheduler",
            request_id="delegate_task_r5t7_deferred",
            idempotency_key="idem_r5t7_deferred",
        ),
        routing_facts={
            "requires_schedule": True,
            "needs_background_schedule": True,
            "needs_immediate_start": False,
            "scheduler_safe": True,
            "scheduler_job_kind": "build_verify_loop",
            "code_changes_expected": True,
            "scheduler_max_iterations": 3,
            "explicit_scheduler_justification": "R5T7 deferred-state proof",
        },
    )
    assert routed["ok"] is True

    seeded = register.load_or_init_task_register(tmp_path)
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="r5t7.scheduler.defer.bind",
        request=routed["request"],
        launch_result={"job_id": "overnight_r5t7_deferred", "run_id": "sj_20260313T211500Z_abc999"},
    )["delegation_ref"]

    state_path = Path(bound["status_refs"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "job_id": "overnight_r5t7_deferred",
                "last_status": "running",
                "active_run_id": "sj_20260313T211500Z_abc999",
            }
        ),
        encoding="utf-8",
    )

    running = bindings.synthesize_delegation_ref(tmp_path, bound)
    assert running["status"] == "waiting_on_job"
    assert running["evidence_status"] == "pending"

    state_path.write_text(
        json.dumps(
            {
                "job_id": "overnight_r5t7_deferred",
                "last_status": "ok",
                "last_receipt_path": str(Path(bound["evidence_candidates"][0])),
            }
        ),
        encoding="utf-8",
    )
    evidence_missing = bindings.synthesize_delegation_ref(tmp_path, bound)
    assert evidence_missing["status"] == "evidence_missing"
    assert evidence_missing["evidence_status"] == "missing"
    assert "Collect durable evidence" in evidence_missing["next_decision"]["summary"]

    Path(bound["evidence_candidates"][0]).parent.mkdir(parents=True, exist_ok=True)
    Path(bound["evidence_candidates"][0]).write_text(
        json.dumps({"status": "ok", "run_id": "sj_20260313T211500Z_abc999"}),
        encoding="utf-8",
    )

    completed = bindings.synthesize_delegation_ref(tmp_path, bound)
    assert completed["status"] == "completed"
    assert completed["native_status"] == "ok"
    assert completed["evidence_refs"] == [str(Path(bound["evidence_candidates"][0]).resolve())]


def test_scheduler_terminal_proof_gap_receipt_is_not_projected_as_waiting_on_job(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    seeded = register.load_or_init_task_register(tmp_path)
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="watchdog.scheduler.bind",
        request=_request("scheduler"),
        launch_result={"job_id": "audit_watchdog_job", "run_id": "sj_20260402T010000Z_gap001"},
    )["delegation_ref"]

    state_path = Path(bound["status_refs"][0])
    receipt_path = Path(bound["evidence_candidates"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "job_id": "audit_watchdog_job",
                "active_run_id": "",
                "last_status": "proof_gap",
                "last_receipt_path": str(receipt_path),
            }
        ),
        encoding="utf-8",
    )
    receipt_path.write_text(
        json.dumps(
            {
                "job_id": "audit_watchdog_job",
                "run_id": "sj_20260402T010000Z_gap001",
                "status": "proof_gap",
                "error": "terminal proof gap",
            }
        ),
        encoding="utf-8",
    )

    synthesized = bindings.synthesize_delegation_ref(tmp_path, bound)

    assert synthesized["status"] == "failed"
    assert synthesized["native_status"] == "proof_gap"
    assert synthesized["evidence_status"] == "present"
    assert synthesized["evidence_refs"] == [str(receipt_path.resolve())]


def test_scheduler_conditional_pass_repair_receipt_projects_completed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    seeded = register.load_or_init_task_register(tmp_path)
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="watchdog.scheduler.bind",
        request=_request("scheduler"),
        launch_result={"job_id": "repair_watchdog_job", "run_id": "sj_20260409T010000Z_repair001"},
    )["delegation_ref"]

    state_path = Path(bound["status_refs"][0])
    receipt_path = Path(bound["evidence_candidates"][0])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "job_id": "repair_watchdog_job",
                "active_run_id": "",
                "last_status": "conditional_pass_repair_applied",
                "last_receipt_path": str(receipt_path),
            }
        ),
        encoding="utf-8",
    )
    receipt_path.write_text(
        json.dumps(
            {
                "job_id": "repair_watchdog_job",
                "run_id": "sj_20260409T010000Z_repair001",
                "status": "conditional_pass_repair_applied",
            }
        ),
        encoding="utf-8",
    )

    synthesized = bindings.synthesize_delegation_ref(tmp_path, bound)

    assert synthesized["status"] == "completed"
    assert synthesized["native_status"] == "conditional_pass_repair_applied"
    assert synthesized["evidence_status"] == "present"
    assert synthesized["evidence_refs"] == [str(receipt_path.resolve())]


def test_task_status_reconciliation_surfaces_conflicting_substrate_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    current_generation = int(seeded["write_generation"])

    workflow_result = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=current_generation,
        writer="y5z7.bind.workflow",
        request=_request("workflow"),
        launch_result={"workflow_id": "wf_y5z7_conflict"},
    )
    current_generation = int(workflow_result["task_register"]["write_generation"])
    scheduler_result = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=current_generation,
        writer="y5z7.bind.scheduler",
        request=_request("scheduler"),
        launch_result={"job_id": "job_y5z7_conflict", "run_id": "sj_20260313T100000Z_y5z7"},
    )

    task = scheduler_result["task_register"]["active_tasks"][0]
    workflow_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "workflow")
    scheduler_ref = next(ref for ref in task["delegation_refs"] if ref["surface"] == "scheduler")

    workflow_state_path = Path(workflow_ref["status_refs"][0])
    workflow_digest_path = Path(workflow_ref["evidence_candidates"][0])
    workflow_state_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_digest_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_state_path.write_text(json.dumps({"workflow_id": "wf_y5z7_conflict", "status": "completed"}), encoding="utf-8")
    workflow_digest_path.write_text(json.dumps({"summary": "ok"}), encoding="utf-8")

    scheduler_state_path = Path(scheduler_ref["status_refs"][0])
    scheduler_receipt_path = Path(scheduler_ref["evidence_candidates"][0])
    scheduler_state_path.parent.mkdir(parents=True, exist_ok=True)
    scheduler_receipt_path.parent.mkdir(parents=True, exist_ok=True)
    scheduler_state_path.write_text(
        json.dumps({"job_id": "job_y5z7_conflict", "last_status": "error", "last_receipt_path": str(scheduler_receipt_path)}),
        encoding="utf-8",
    )
    scheduler_receipt_path.write_text(json.dumps({"status": "error"}), encoding="utf-8")

    task_status = bindings.synthesize_task_status(tmp_path, task_id="task_df0c")
    latest = task_status["latest"]
    reconciliation = task_status["reconciliation"]

    assert latest["status"] == "ambiguous_artifact"
    assert latest["evidence_status"] == "ambiguous"
    assert reconciliation["projection"]["conflict_count"] >= 1
    assert Path(reconciliation["report_paths"]["report_path"]).exists()
    assert Path(reconciliation["report_paths"]["receipt_path"]).exists()


def test_mailbox_binding_and_synthesis_track_claim_to_job_handoff_boundary(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    mailbox_request = policy.build_typed_delegation_request(
        classification=_classification("workflow"),
        surface="mailbox",
        owner_ref={"task_id": "task_df0c", "ledger_uid": "PIS-20260312-161622Z-DF0C"},
        summary="Queue mailbox work",
        requested_action="enqueue mailbox request",
        expected_evidence=[{"kind": "mailbox_record", "description": "mailbox request state", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review mailbox transport state", "owner": "assistant"},
        request_id="delegate_mailbox_df0c",
        idempotency_key="idem_mailbox_df0c",
        correlation_id="corr_mailbox_df0c",
        launch_turn_id="turn_mailbox_df0c",
        launch_cause="scheduled_build",
    )
    bound = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="df0c.mailbox.bind",
        request=mailbox_request,
        launch_result={},
    )
    ref = bound["delegation_ref"]
    assert ref["surface"] == "mailbox"
    assert ref["subsystem_id"] == "delegate_mailbox_df0c"
    assert Path(ref["status_refs"][0]).exists()

    queued = bindings.synthesize_delegation_ref(tmp_path, ref)
    assert queued["status"] == "accepted"
    assert queued["native_status"] == "queued"

    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_mailbox_df0c",
        writer="df0c.mailbox.claim",
        lease_s=60,
    )["request_record"]
    running = bindings.synthesize_delegation_ref(tmp_path, ref)
    assert running["status"] == "running"
    assert running["native_status"] == "claimed"

    handed_off = mailbox.accept_claim_handoff(
        tmp_path,
        request_id="delegate_mailbox_df0c",
        worker_id="worker_mailbox_df0c",
        claim_id=str(claimed["active_claim"]["claim_id"]),
        detached_job_id="wf_df0c_mailbox",
        job_surface="workflow",
        writer="df0c.mailbox.handoff",
    )
    assert handed_off["request_record"]["handoff"]["detached_job_id"] == "wf_df0c_mailbox"
    waiting = bindings.synthesize_delegation_ref(tmp_path, ref)
    assert waiting["status"] == "waiting_on_job"
    assert waiting["detached_job_id"] == "wf_df0c_mailbox"

    jobs.heartbeat_job(
        tmp_path,
        detached_job_id="wf_df0c_mailbox",
        writer="df0c.mailbox.job.heartbeat",
        worker_id="worker_mailbox_df0c",
        progress_message="executing",
        now_utc="2026-03-12T23:01:00Z",
    )
    running_job = bindings.synthesize_delegation_ref(tmp_path, ref)
    assert running_job["status"] == "running"
    assert running_job["native_status"] == "running"

    jobs.record_job_result(
        tmp_path,
        detached_job_id="wf_df0c_mailbox",
        status="completed",
        summary="workflow finished",
        writer="df0c.mailbox.job.result",
        producer="test",
        result_id="job_result_df0c",
        now_utc="2026-03-12T23:02:00Z",
    )
    completed_job = bindings.synthesize_delegation_ref(tmp_path, ref)
    assert completed_job["status"] == "completed"
    assert completed_job["native_status"] == "completed"


def test_failed_surface_binding_records_conflict_truth(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    _seed_task(tmp_path)

    def _fail_bind(*_args, **_kwargs) -> dict:
        raise RuntimeError("simulated_bind_failure")

    monkeypatch.setattr(bindings, "bind_delegation_request", _fail_bind)

    result = bindings.bind_surface_launch(
        tmp_path,
        surface="verifier",
        binding={"type": "task_id", "id": "task_df0c"},
        summary="Host verification failure path",
        requested_action="verify host",
        launch_result={
            "request_id": "vrf_fail_001",
            "workflow_id": "wf_fail_001",
            "action_id": "android_unit_test",
            "params": {"gradle_task": ":app:testDebugUnitTest", "test_selector": "com.eiel.chat.HostVerificationSentinelTest"},
        },
        writer="df0c.failed",
    )
    assert result["ok"] is False
    truth = result.get("truth_record", {})
    assert truth.get("ok") is True

    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task = snapshot["active_tasks"][0]
    ownership = task.get("execution_surface_ownership") or {}
    assert ownership.get("selected_surface") == "verifier"
    assert ownership.get("reference_status") == "execution_ref_conflict"


def test_mailbox_handoff_without_lifecycle_stays_interrupted_not_waiting_on_job(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    _bootstrap(tmp_path)
    seeded = _seed_task(tmp_path)
    request = policy.build_typed_delegation_request(
        classification=_classification("workflow"),
        surface="mailbox",
        owner_ref={"task_id": "task_df0c", "ledger_uid": "PIS-20260312-161622Z-DF0C"},
        summary="Queue mailbox work",
        requested_action="enqueue mailbox request",
        expected_evidence=[{"kind": "mailbox_record", "description": "mailbox request state", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review mailbox transport state", "owner": "assistant"},
        request_id="delegate_mailbox_df0c",
        idempotency_key="idem_mailbox_df0c",
        correlation_id="corr_mailbox_df0c",
        launch_turn_id="turn_mailbox_df0c",
        launch_cause="scheduled_build",
    )
    bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="df0c.mailbox.lifecycle_gap.bind",
        request=request,
        launch_result={},
    )
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    ref = snapshot["active_tasks"][0]["delegation_refs"][0]

    claimed = mailbox.claim_next_request(
        tmp_path,
        worker_id="worker_mailbox_df0c_gap",
        writer="df0c.mailbox.lifecycle_gap.claim",
        lease_s=60,
    )["request_record"]

    def _flaky_accept(*args, **kwargs):
        raise mailbox._job_lifecycle.DetachedJobLifecycleError("simulated_lifecycle_write_failure")

    monkeypatch.setattr(mailbox._job_lifecycle, "accept_mailbox_handoff", _flaky_accept)
    with pytest.raises(mailbox.MailboxError, match="simulated_lifecycle_write_failure"):
        mailbox.accept_claim_handoff(
            tmp_path,
            request_id="delegate_mailbox_df0c",
            worker_id="worker_mailbox_df0c_gap",
            claim_id=str(claimed["active_claim"]["claim_id"]),
            detached_job_id="wf_df0c_mailbox_gap",
            job_surface="workflow",
            writer="df0c.mailbox.lifecycle_gap.handoff",
        )

    synthesis = bindings.synthesize_delegation_ref(tmp_path, ref)
    assert synthesis["status"] == "interrupted"
    assert synthesis["evidence_status"] == "ambiguous"
    assert "lifecycle record is not durably present" in synthesis["summary"]
