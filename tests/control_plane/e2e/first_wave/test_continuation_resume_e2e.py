from __future__ import annotations

from infra.controlplane import continuation_context_attachment as attachment
from infra.controlplane import continuation_packet_store as store
from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_task_register as register

from .helpers.contracts import ScenarioContract, assert_scenario_contract


CONTRACT = ScenarioContract(
    scenario_id="continuation_resume_e2e",
    operator_job="Resume interrupted work from durable continuation state instead of replaying transcript bulk or guessing context.",
    capability="Latest valid continuation packet is selected, attached, and projected as the authoritative resume handoff.",
    durable_truth=(
        "task register ownership and checkpoint state",
        "continuation packet store",
        "materialized attachment artifact",
        "rendered continuation surface lines",
    ),
    manual_repair_eliminated="Manually reconstructing resume context after interruption or restart.",
    primary_intent_sheets=("IS-06",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic continuation packet revisions",
        "temp-repo task register ownership seeding",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "fabricating continuation packet state after selection",
        "writing projection records manually",
    ),
)


def _base_packet(*, packet_id: str, packet_revision: int) -> dict:
    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": packet_id,
        "packet_revision": packet_revision,
        "created_utc": f"2026-04-07T09:0{packet_revision}:00Z",
        "source_run": {
            "job_id": "job_ctx_demo",
            "run_id": "run_ctx_demo",
            "attempt_id": "attempt_1",
            "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/demo.json"],
        },
        "handoff_brief": "Reuse durable continuation state instead of replaying transcript bulk.",
        "result_summary": {"outcome": "partial", "summary": "Bounded continuation context exists."},
        "evidence_created": [
            {
                "evidence_id": "ev_ctx",
                "kind": "unit_test",
                "summary": "Continuation attachment tests",
                "refs": ["tests/control_plane/e2e/first_wave/test_continuation_resume_e2e.py"],
            }
        ],
        "artifact_index": [
            {
                "artifact_id": "art_ctx",
                "kind": "json",
                "uri": "runtime/state/lab_assistant/detached_jobs/jobs/demo.json",
                "summary": "job record",
            }
        ],
        "open_questions": [{"question_id": "q1", "question": "Resume or retry?"}],
        "failed_paths": [{"path_id": "fp1", "summary": "Transcript fallback is forbidden."}],
        "recommended_next_steps": [{"step_id": "step1", "priority": "now", "step": "Resume from the durable checkpoint."}],
        "confidence": {"level": "high", "rationale": "Targeted tests back the packet."},
        "applicability": {"scope": "same_family", "limitations": ["Lifecycle truth remains authoritative."]},
    }


def _seed_task_register(tmp_path, *, task_id: str, ledger_uid: str, job_id: str) -> None:
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=True)
    generation = int((snapshot.get("task_register") or {}).get("write_generation") or 0)
    upserted = register.upsert_task(
        tmp_path,
        expected_generation=generation,
        writer="test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "Continuation attachment test task",
            "status": "active",
            "owner": "builder",
            "next_expected_action": "Resume from durable continuation context.",
            "ledger_uid": ledger_uid,
        },
    )
    generation = int((upserted.get("task_register") or {}).get("write_generation") or 0)
    register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=generation,
        writer="test",
        task_id=task_id,
        selected_surface="workflow",
        execution_id=job_id,
        execution_ref=f"workflow:{job_id}",
        binding_ref=f"binding:{ledger_uid}",
        job_uri="runtime/state/lab_assistant/detached_jobs/jobs/demo.json",
        trace_id=f"trace:{job_id}",
        checkpoint={
            "checkpoint_id": f"cp:{job_id}",
            "checkpoint_status": "recorded",
            "checkpoint_note": "safe resume point",
            "checkpoint_utc": "2026-04-07T09:03:00Z",
            "checkpoint_source": "test",
            "checkpoint_revision": 1,
            "progress_summary": "halfway there",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/demo.json"],
        },
        resume_decision={
            "decision": "resume",
            "decision_reason": "job_state:interrupted",
            "decision_summary": "resume from durable checkpoint",
            "decision_source": "test",
            "decision_utc": "2026-04-07T09:03:00Z",
            "checkpoint_id": f"cp:{job_id}",
            "evidence_refs": ["runtime/state/lab_assistant/checkpoints/demo.json"],
        },
        reference_status="execution_ref_active",
    )


def _spec(ledger_uid: str) -> dict:
    return sched.validate_job_spec(
        {
            "job_id": "compiled_ctx_attachment_job",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": ledger_uid},
            "job_kind": "build_verify_loop",
            "loop": {"max_iterations": 1},
            "prompt": "Implement the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T09:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
            "runtime": {"executor_agent_tech": "codex"},
            "display_title": "Continuation context attachment",
            "required_context_bundle": "lab_core_v1",
            "context_refs": [],
            "additional_context_refs": [],
            "forbidden_context_refs": [],
        },
        now_utc="2026-04-07T09:20:00Z",
    )


def test_continuation_resume_e2e(first_wave_root) -> None:
    assert_scenario_contract(CONTRACT)
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(first_wave_root, task_id="task_ctx_ready", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=1), repo_root=first_wave_root)
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=2), repo_root=first_wave_root)

    result = attachment.materialize_attachment_for_job_spec(
        _spec(ledger_uid),
        repo_root=first_wave_root,
        as_of_utc="2026-04-07T09:05:00Z",
    )
    payload = result["attachment"]
    assert payload["status"] == "ready"
    assert payload["selected_source"] == "continuation_packet"
    assert payload["selection_metadata"]["selected_packet_freshness"]["status"] == "fresh"
    assert payload["bounded_context"]["continuation_packet"]["packet_revision"] == 2

    task_register = (register.read_task_register_snapshot(first_wave_root, initialize_if_missing=False) or {}).get("task_register")
    projection = attachment.inspect_continuation_state_for_binding(
        {"type": "ledger_uid", "id": ledger_uid},
        task_register=task_register,
        repo_root=first_wave_root,
        as_of_utc="2026-04-07T09:05:00Z",
    )
    assert projection["packet"]["status"] == "ready"
    assert projection["packet"]["packet_revision"] == 2
    assert projection["handoff"]["authoritative_source"] == "continuation_packet"
    assert projection["handoff"]["status"] == "ready"

    lines = attachment.render_surface_projection_lines(projection)
    assert any("handoff=ready; source=continuation_packet" in line for line in lines)
    assert any("packet=pkt_ctx:r2" in line for line in lines)
    assert any("memory=ready" in line for line in lines)
