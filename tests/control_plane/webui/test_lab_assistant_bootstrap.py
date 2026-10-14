from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import continuation_packet_store as packet_store
from infra.controlplane import plane_mission_contract as mission_contract
from infra.webui import server_lab_assistant_correlation as correlation
from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)


def _bootstrap_host(root: Path) -> None:
    host.bootstrap_host_baseline(root)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _write_mission_contract(root: Path) -> None:
    payload = json.loads(Path("governance/plane_mission_contract_v1.json").read_text(encoding="utf-8"))
    target = root / mission_contract.CONTRACT_ARTIFACT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _seed_task_register(
    root: Path,
    *,
    assistant_id: str,
    generation: int,
    active_tasks: list[dict],
    open_loops: list[dict],
    constraints: list[dict],
    recent_execution_refs: list[dict],
) -> dict:
    register_obj = {
        "schema_version": register.TASK_REGISTER_SCHEMA_VERSION,
        "register_id": "task_register_test_seed",
        "assistant_id_ref": assistant_id,
        "write_generation": generation,
        "last_writer": "bootstrap.test.seed",
        "last_written_utc": "2026-03-12T21:40:11Z",
        "active_tasks": active_tasks,
        "open_loops": open_loops,
        "constraints": constraints,
        "recent_execution_refs": recent_execution_refs,
        "summary_cache_ref": {
            "path": str(register._summary_cache_path(root)),
            "derived_only": True,
            "schema_version": register.TASK_REGISTER_SUMMARY_SCHEMA_VERSION,
        },
        "summary_stale": False,
    }
    summary_cache = register._build_summary(register_obj)
    _write_json(register._register_path(root), register_obj)
    _write_json(register._summary_cache_path(root), summary_cache)
    return register_obj


def test_fresh_turn_bootstrap_restores_clean_reload_from_durable_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current_state = state.load_or_init_assistant_state(tmp_path)
    current_register = _seed_task_register(
        tmp_path,
        assistant_id=current_state["assistant_id"],
        generation=4,
        active_tasks=[
            {
                "task_id": "task_dd8c_reload",
                "kind": "software",
                "title": "Implement fresh-turn bootstrap",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "ledger_uid": "PIS-20260312-161622Z-DD8C",
                "delegation_refs": [],
                "next_expected_action": "verify bounded reload",
                "blocked_reason": "",
                "created_utc": "2026-03-12T21:40:11Z",
                "updated_utc": "2026-03-12T21:40:11Z",
            }
        ],
        open_loops=[
            {
                "loop_id": "loop_dd8c_operator",
                "kind": "waiting_on_operator",
                "status": "open",
                "linked_task_id": "task_dd8c_reload",
                "linked_ledger_uid": "",
                "note": "confirm degraded wording",
                "next_review_utc": "",
                "created_utc": "2026-03-12T21:40:11Z",
                "updated_utc": "2026-03-12T21:40:11Z",
                "resolved_utc": "",
            }
        ],
        constraints=[
            {
                "key": "proof_requirement",
                "value": "targeted tests plus py_compile",
                "scope": "global",
                "source": "operator",
                "updated_utc": "2026-03-12T21:40:11Z",
            }
        ],
        recent_execution_refs=[
            {
                "kind": "host_verification",
                "ref_id": "hv_123",
                "label": "targeted Android proof",
                "status": "queued",
                "task_id": "task_dd8c_reload",
                "ledger_uid": "",
                "recorded_utc": "2026-03-12T21:40:11Z",
            }
        ],
    )

    packet = bootstrap.build_fresh_turn_bootstrap(
        tmp_path,
        recent_chat_rows=[
            {"role": "user", "content": "Please keep the bootstrap durable-first."},
            {"role": "assistant", "content": "Using the task register as primary truth."},
        ],
    )
    assert packet["schema_version"] == bootstrap.BOOTSTRAP_SCHEMA_VERSION
    assert packet["status"] == "ready"
    assert packet["assistant_state"]["assistant_id"] == current_state["assistant_id"]
    assert packet["task_register"]["assistant_id_ref"] == current_state["assistant_id"]
    assert "task_dd8c_reload" in packet["text"]
    assert "confirm degraded wording" in packet["text"]
    assert "proof_requirement" in packet["text"]
    assert "targeted Android proof" in packet["text"]
    assert "supplemental only" in packet["text"]


def test_fresh_turn_bootstrap_degrades_when_durable_state_is_missing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[{"role": "user", "content": "hello"}])

    assert packet["status"] == "degraded"
    assert packet["degraded"] is True
    assert packet["assistant_state"] is None
    assert packet["task_register"] is None
    assert packet["degraded_reasons"] == sorted(set(packet["degraded_reasons"]))
    assert "assistant_state_missing" in packet["degraded_reasons"]
    assert "task_register_missing" in packet["degraded_reasons"]
    assert "- reload_status: degraded" in packet["text"]
    assert "- assistant_state: missing" in packet["text"]
    assert "- task_register: missing" in packet["text"]


def test_fresh_turn_bootstrap_trims_deterministically_to_budget(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current_state = state.load_or_init_assistant_state(tmp_path)
    _seed_task_register(
        tmp_path,
        assistant_id=current_state["assistant_id"],
        generation=14,
        active_tasks=[
            {
                "task_id": f"task_trim_{idx:02d}",
                "kind": "software",
                "title": f"Task {idx} with enough descriptive text to consume the context budget deterministically",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "ledger_uid": "",
                "delegation_refs": [],
                "next_expected_action": "keep only the highest-priority context sections",
                "blocked_reason": "",
                "created_utc": "2026-03-12T21:40:11Z",
                "updated_utc": "2026-03-12T21:40:11Z",
            }
            for idx in range(5)
        ],
        open_loops=[
            {
                "loop_id": f"loop_trim_{idx:02d}",
                "kind": "waiting_on_verification",
                "status": "open",
                "linked_task_id": "task_trim_00",
                "linked_ledger_uid": "",
                "note": "verification remains pending and should be truthfully trimmed if needed",
                "next_review_utc": "",
                "created_utc": "2026-03-12T21:40:11Z",
                "updated_utc": "2026-03-12T21:40:11Z",
                "resolved_utc": "",
            }
            for idx in range(3)
        ],
        constraints=[
            {
                "key": f"constraint_{idx:02d}",
                "value": "keep the context bounded and deterministic",
                "scope": "global",
                "source": "operator",
                "updated_utc": "2026-03-12T21:40:11Z",
            }
            for idx in range(3)
        ],
        recent_execution_refs=[
            {
                "kind": "workflow",
                "ref_id": f"wf_{idx:02d}",
                "label": "long-running delegated work item that should be trimmed predictably",
                "status": "running",
                "task_id": "task_trim_00",
                "ledger_uid": "",
                "recorded_utc": "2026-03-12T21:40:11Z",
            }
            for idx in range(3)
        ],
    )

    recent_chat_rows = [
        {"role": "user", "content": f"user message {idx} with extra filler to stress the prompt budget"}
        if idx % 2 == 0
        else {"role": "assistant", "content": f"assistant reply {idx} with extra filler to stress the prompt budget"}
        for idx in range(8)
    ]

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=recent_chat_rows, max_chars=1200)

    assert packet["status"] == "ready"
    assert packet["used_chars"] <= 1200
    assert "Lab Assistant fresh-turn bootstrap" in packet["text"]
    assert "reload_status: ready" in packet["text"]
    assert packet["omitted_counts"]["recent_chat_rows"] > 0 or packet["omitted_counts"]["active_tasks"] > 0


def test_render_fresh_turn_bootstrap_text_appends_answer_vs_delegate_policy(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)

    text = bootstrap.render_fresh_turn_bootstrap_text(tmp_path, recent_chat_rows=[])

    assert "Answer-vs-delegate policy:" in text
    assert "every delegated request must declare owner_ref, expected_evidence, and next_decision before launch" in text


def test_fresh_turn_bootstrap_projects_canonical_mission_contract_when_present(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    _write_mission_contract(tmp_path)

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert packet["mission_contract"]["ok"] is True
    assert packet["mission_contract"]["runtime_projection"]["product_promise"]
    assert packet["mission_contract"]["runtime_projection"]["claim_ceiling_summary"]
    assert packet["mission_contract"]["runtime_projection"]["refresh_scope_summary"]
    assert packet["mission_contract"]["runtime_projection"]["source_fingerprint"]
    assert "Mission contract:" in packet["text"]
    assert "claim_ceiling=" in packet["text"]
    assert "refresh_scope=" in packet["text"]


def test_fresh_turn_bootstrap_surfaces_missing_mission_contract_explicitly(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert packet["mission_contract"]["ok"] is False
    assert packet["mission_contract"]["failure_class"] == "mission_contract_missing"
    assert packet["mission_contract"]["summary"] == "Canonical plane mission contract is missing."
    assert "Mission contract:" in packet["text"]


def test_fresh_turn_bootstrap_fails_closed_when_claim_ceiling_fields_are_missing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    payload = json.loads(Path("governance/plane_mission_contract_v1.json").read_text(encoding="utf-8"))
    payload.pop("claim_ceiling_summary", None)
    target = tmp_path / mission_contract.CONTRACT_ARTIFACT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[])

    assert packet["mission_contract"]["ok"] is False
    assert packet["mission_contract"]["failure_class"] == "mission_contract_invalid"
    assert any(row["field"] == "claim_ceiling_summary" and row["code"] == "missing_field" for row in packet["mission_contract"]["errors"])


def test_fresh_turn_bootstrap_surfaces_clear_and_uncertain_detached_result_correlation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task_register(
        tmp_path,
        assistant_id=state.load_or_init_assistant_state(tmp_path)["assistant_id"],
        generation=1,
        active_tasks=[],
        open_loops=[],
        constraints=[],
        recent_execution_refs=[],
    )

    correlation.record_delegation_binding(
        tmp_path,
        delegation_ref={
            "correlation_id": "corr_bootstrap_ok",
            "launch_turn_id": "turn_bootstrap_ok",
            "delegation_request_id": "req_bootstrap_ok",
            "request_id": "req_bootstrap_ok",
            "surface": "mailbox",
            "summary": "Bootstrap positive correlation record",
            "requested_action": "refresh bootstrap truth",
            "owner_ref": {"task_id": "task_bootstrap_ok", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
        },
        writer="v7x9.bootstrap.ok.binding",
    )
    correlation.record_detached_result(
        tmp_path,
        writer="v7x9.bootstrap.ok.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_bootstrap_ok",
                "request_id": "req_bootstrap_ok",
                "surface": "mailbox",
                "status": "completed",
                "summary": "Bootstrapped work completed",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/correlation/records/corr_bootstrap_ok.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Review completion", "owner": "assistant"},
                "created_utc": "2026-03-13T03:00:00Z",
                "updated_utc": "2026-03-13T03:00:00Z",
                "correlation_id": "corr_bootstrap_ok",
                "launch_turn_id": "turn_bootstrap_ok",
                "detached_job_id": "job_bootstrap_ok",
                "result_correlation_basis": "launch_turn_id",
                "worker_ref": "mailbox:job_bootstrap_ok",
                "producer": "test",
                "owner_ref": {"task_id": "task_bootstrap_ok", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            }
        ),
    )
    correlation.record_detached_result(
        tmp_path,
        writer="v7x9.bootstrap.bad.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_bootstrap_bad",
                "request_id": "missing_request_bootstrap",
                "surface": "workflow",
                "status": "failed",
                "summary": "No durable correlation target",
                "native_status": "failed",
                "evidence_status": "missing",
                "evidence_refs": ["runtime/state/lab_assistant/correlation/results/result_bootstrap_bad.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Investigate unresolved result", "owner": "assistant"},
                "created_utc": "2026-03-13T03:01:00Z",
                "updated_utc": "2026-03-13T03:01:00Z",
                "correlation_id": "corr_bootstrap_missing",
                "launch_turn_id": "turn_bootstrap_missing",
                "detached_job_id": "job_bootstrap_missing",
                "result_correlation_basis": "detached_job_id",
                "worker_ref": "workflow:job_bootstrap_missing",
                "producer": "test",
                "owner_ref": {"task_id": "task_bootstrap_missing", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            }
        ),
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[], max_chars=12000)

    assert packet["status"] == "degraded"
    assert packet["correlation_snapshot"]["delivery_counts"]["pending_reattach"] == 1
    assert packet["correlation_snapshot"]["delivery_counts"]["delivered"] == 0
    assert packet["correlation_snapshot"]["delivery_counts"]["unresolved"] == 1
    assert "correlation_delivery" in packet["text"]
    assert "detached_result_delivery_ambiguity_detected" in packet["degraded_reasons"]


def test_fresh_turn_bootstrap_surfaces_ready_continuation_packet_for_resumed_task(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current_state = state.load_or_init_assistant_state(tmp_path)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="bootstrap.packet",
        task={
            "task_id": "task_packet_resume",
            "kind": "software",
            "title": "Resume from packet-first bootstrap",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260407-063104Z-D4F4",
            "next_expected_action": "resume with durable packet",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="bootstrap.packet",
        task_id="task_packet_resume",
        selected_surface="workflow",
        execution_id="job_packet_resume",
        execution_ref="workflow:job_packet_resume",
        binding_ref="binding_packet_resume",
        job_uri="runtime/state/lab_assistant/detached_jobs/jobs/job_packet_resume.json",
        trace_id="trace_packet_resume",
    )["task_register"]
    updated = register.record_task_run_checkpoint(
        tmp_path,
        expected_generation=int(with_run["write_generation"]),
        writer="bootstrap.packet",
        task_id="task_packet_resume",
        run_id="job_packet_resume",
        checkpoint={
            "checkpoint_id": "cp_packet_resume",
            "checkpoint_status": "recorded",
            "checkpoint_note": "resume from packet",
            "progress_summary": "checkpoint captured",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/cp_packet_resume.json"],
        },
    )["task_register"]
    packet_store.append_packet_revision_once(
        {
            "schema_version": "continuation_packet_v1",
            "packet_id": "detached_job:job_packet_resume",
            "packet_revision": 1,
            "created_utc": "2026-04-07T09:00:00Z",
            "source_run": {
                "job_id": "job_packet_resume",
                "run_id": "req_packet_resume",
                "attempt_id": "turn_packet_resume",
                "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_packet_resume.json"],
            },
            "handoff_brief": "Resume from durable packet rather than transcript archaeology.",
            "result_summary": {"outcome": "interrupted", "summary": "Worker stopped after a safe checkpoint."},
            "evidence_created": [{"evidence_id": "ev_packet", "kind": "unit_test", "summary": "bootstrap packet integration", "refs": ["tests/control_plane/webui/test_lab_assistant_bootstrap.py"]}],
            "artifact_index": [{"artifact_id": "art_packet", "kind": "json", "uri": "runtime/state/lab_assistant/detached_jobs/jobs/job_packet_resume.json", "summary": "job record"}],
            "open_questions": [{"question_id": "q_packet", "question": "Resume the successor from the checkpoint?"}],
            "failed_paths": [{"path_id": "fp_packet", "summary": "Terminal success has not been proven."}],
            "recommended_next_steps": [{"step_id": "step_packet", "priority": "now", "step": "Resume from the durable checkpoint."}],
            "confidence": {"level": "medium", "rationale": "Checkpoint and lifecycle truth are durable."},
            "applicability": {"scope": "immediate_successor", "limitations": ["Lifecycle truth remains authoritative for raw state."]},
        },
        repo_root=tmp_path,
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[], max_chars=12000)

    assert packet["status"] == "ready"
    assert packet["assistant_state"]["assistant_id"] == current_state["assistant_id"]
    assert packet["task_register"]["write_generation"] == updated["write_generation"]
    assert "Continuation packets restored:" in packet["text"]
    assert "Continuation inspection restored:" in packet["text"]
    assert "detached_job:job_packet_resume:r1" in packet["text"]
    assert "Terminal success has not been proven." in packet["text"]
    assert packet["continuation_packet_snapshot"]["ready_count"] == 1
    assert packet["continuation_packet_snapshot"]["degraded_count"] == 0


def test_fresh_turn_bootstrap_includes_correlation_delivery_summary_when_available(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current_state = state.load_or_init_assistant_state(tmp_path)
    _seed_task_register(
        tmp_path,
        assistant_id=current_state["assistant_id"],
        generation=2,
        active_tasks=[],
        open_loops=[],
        constraints=[],
        recent_execution_refs=[],
    )

    correlation.record_delegation_binding(
        tmp_path,
        delegation_ref={
            "correlation_id": "corr_bootstrap_ready",
            "launch_turn_id": "turn_bootstrap_ready",
            "delegation_request_id": "req_bootstrap_ready",
            "surface": "mailbox",
            "summary": "Ready correlation summary",
            "requested_action": "deliver bootstrap evidence",
            "owner_ref": {"task_id": "task_bootstrap_ready", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
        },
        writer="v7x9.bootstrap.ready.binding",
    )
    correlation.record_detached_result(
        tmp_path,
        writer="v7x9.bootstrap.ready.result",
        result=policy.validate_typed_delegation_result(
            {
                "schema_version": policy.DELEGATION_RESULT_SCHEMA_VERSION,
                "result_id": "result_bootstrap_ready",
                "request_id": "req_bootstrap_ready",
                "surface": "mailbox",
                "status": "completed",
                "summary": "Pending detached result summary",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["runtime/state/lab_assistant/correlation/results/result_bootstrap_ready.json"],
                "artifact_refs": [],
                "next_decision": {"kind": "assistant_continue", "summary": "Review bootstrap result", "owner": "assistant"},
                "created_utc": "2026-03-13T03:10:00Z",
                "updated_utc": "2026-03-13T03:10:00Z",
                "correlation_id": "corr_bootstrap_ready",
                "launch_turn_id": "turn_bootstrap_ready",
                "detached_job_id": "job_bootstrap_ready",
                "result_correlation_basis": "request_id",
                "worker_ref": "mailbox:job_bootstrap_ready",
                "producer": "test",
                "owner_ref": {"task_id": "task_bootstrap_ready", "ledger_uid": "PIS-20260313-094305Z-V7X9"},
            }
        ),
    )

    packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[], max_chars=12000)

    assert packet["status"] == "ready"
    assert packet["correlation_snapshot"]["delivery_counts"]["pending_reattach"] == 1
    assert packet["correlation_snapshot"]["delivery_counts"]["delivered"] == 0
    assert packet["correlation_snapshot"]["delivery_counts"]["ambiguous"] == 0
    assert packet["correlation_snapshot"]["delivery_counts"]["unresolved"] == 0
    assert "- correlation_delivery: pending=1; delivered=0; ambiguous=0; unresolved=0" in packet["text"]
