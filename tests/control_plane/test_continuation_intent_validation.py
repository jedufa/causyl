from __future__ import annotations

from pathlib import Path

import pytest

from infra.controlplane import continuation_context_attachment as attachment
from infra.controlplane import continuation_packet_store as packet_store
from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


LEDGER_UID = "PIS-20260407-063108Z-H8K8"


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _seed_task(root: Path, *, task_id: str, job_id: str) -> dict:
    current = register.load_or_init_task_register(root)
    seeded = register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="continuation.intent.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "Continuation family intent validation",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": LEDGER_UID,
            "next_expected_action": "reuse durable continuation state truthfully",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        root,
        expected_generation=int(seeded["write_generation"]),
        writer="continuation.intent.seed",
        task_id=task_id,
        selected_surface="workflow",
        execution_id=job_id,
        execution_ref=f"workflow:{job_id}",
        binding_ref=f"binding:{job_id}",
        job_uri=f"runtime/state/lab_assistant/detached_jobs/jobs/{job_id}.json",
        trace_id=f"trace:{job_id}",
    )["task_register"]
    return register.record_task_run_checkpoint(
        root,
        expected_generation=int(with_run["write_generation"]),
        writer="continuation.intent.seed",
        task_id=task_id,
        run_id=job_id,
        checkpoint={
            "checkpoint_id": f"cp:{job_id}",
            "checkpoint_status": "recorded",
            "checkpoint_note": "safe continuation point",
            "progress_summary": "bounded continuation checkpoint captured",
            "evidence_refs": [f"runtime/state/lab_assistant/checkpoints/{job_id}.json"],
        },
    )["task_register"]


def _spec() -> dict:
    return sched.validate_job_spec(
        {
            "job_id": "continuation_intent_validation_job",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": LEDGER_UID},
            "job_kind": "build_verify_loop",
            "loop": {"max_iterations": 1},
            "prompt": "Validate the bound continuation intent family.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T11:21:43Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
            "runtime": {"executor_agent_tech": "codex"},
            "display_title": "Continuation intent validation",
            "required_context_bundle": "lab_core_v1",
            "context_refs": [],
            "additional_context_refs": [],
            "forbidden_context_refs": [],
        },
        now_utc="2026-04-07T11:20:00Z",
    )


def _packet(
    *,
    packet_id: str,
    job_id: str,
    created_utc: str = "2026-04-07T09:00:00Z",
    scope: str = "same_family",
) -> dict:
    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": packet_id,
        "packet_revision": 1,
        "created_utc": created_utc,
        "source_run": {
            "job_id": job_id,
            "run_id": f"req_{job_id}",
            "attempt_id": f"turn_{job_id}",
            "receipt_refs": [f"runtime/state/lab_assistant/detached_jobs/jobs/{job_id}.json"],
        },
        "handoff_brief": "Resume from the durable checkpoint without transcript archaeology.",
        "result_summary": {"outcome": "interrupted", "summary": "Continuation packet and bounded follow-up guidance exist."},
        "evidence_created": [
            {
                "evidence_id": f"ev_{job_id}",
                "kind": "unit_test",
                "summary": "continuation intent validation",
                "refs": ["tests/control_plane/test_continuation_intent_validation.py"],
            }
        ],
        "artifact_index": [
            {
                "artifact_id": f"art_{job_id}",
                "kind": "json",
                "uri": f"runtime/state/lab_assistant/detached_jobs/jobs/{job_id}.json",
                "summary": "job record",
            }
        ],
        "open_questions": [{"question_id": "q_resume", "question": "Resume immediately from the checkpoint?"}],
        "failed_paths": [{"path_id": "fp_transcript", "summary": "Transcript-first fallback is forbidden."}],
        "recommended_next_steps": [{"step_id": "step_resume", "priority": "now", "step": "Resume from the durable checkpoint."}],
        "confidence": {"level": "medium", "rationale": "Packet and checkpoint truth are durable."},
        "applicability": {"scope": scope, "limitations": ["Lifecycle truth remains authoritative for raw status."]},
    }


def test_continuation_intent_validation_proves_restart_bootstrap_attachment_and_memory_projection(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    task_register = _seed_task(tmp_path, task_id="task_h8k8_positive", job_id="job_h8k8_positive")
    packet_store.append_packet_revision_once(
        _packet(packet_id="detached_job:job_h8k8_positive", job_id="job_h8k8_positive"),
        repo_root=tmp_path,
    )

    fresh_turn = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[], max_chars=12000)
    materialized = attachment.materialize_attachment_for_job_spec(_spec(), repo_root=tmp_path, as_of_utc="2026-04-07T12:00:00Z")
    inspection = attachment.inspect_continuation_state_for_binding(
        {"type": "task_id", "id": "task_h8k8_positive"},
        task_register=task_register,
        repo_root=tmp_path,
        as_of_utc="2026-04-07T12:00:00Z",
    )

    assert fresh_turn["continuation_packet_snapshot"]["ready_count"] == 1
    assert "Continuation packets restored:" in fresh_turn["text"]
    assert materialized["attachment"]["selected_source"] == "continuation_packet"
    assert materialized["attachment"]["status"] == "ready"
    assert inspection["packet"]["status"] == "ready"
    assert inspection["memory"]["status"] == "ready"
    assert inspection["memory"]["activity_counts"]["active"] >= 1
    assert inspection["handoff"]["authoritative_source"] == "continuation_packet"
    assert "Failed path: Transcript-first fallback is forbidden." in inspection["handoff"]["summary_text"]
    assert "Open question: Resume immediately from the checkpoint?" in inspection["handoff"]["summary_text"]


def test_continuation_intent_validation_proves_ambiguous_packet_lookup_fails_closed_without_transcript_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_h8k8_ambiguous", job_id="job_h8k8_ambiguous")
    packet_store.append_packet_revision_once(
        _packet(packet_id="detached_job:job_h8k8_ambiguous", job_id="job_h8k8_ambiguous"),
        repo_root=tmp_path,
    )

    monkeypatch.setattr(
        attachment._packet_bootstrap,
        "packet_snapshot_for_binding",
        lambda *args, **kwargs: {
            "mode": "blocked",
            "reason": "ambiguous latest-valid lookup for job_id=job_h8k8_ambiguous",
            "lookup_candidates": [{"lookup_key": "run_id", "lookup_value": "job_h8k8_ambiguous", "source": "current_run_id"}],
            "packet_record": None,
        },
    )

    materialized = attachment.materialize_attachment_for_job_spec(_spec(), repo_root=tmp_path, as_of_utc="2026-04-07T12:00:00Z")
    payload = materialized["attachment"]

    assert payload["status"] == "blocked"
    assert payload["selected_source"] == "none"
    assert payload["reason"] == "ambiguous latest-valid lookup for job_id=job_h8k8_ambiguous"
    assert payload["selection_metadata"]["transcript_fallback"] == "forbidden"
    assert payload["bounded_context"]["direct_current_context_facts"]


def test_continuation_intent_validation_proves_missing_packet_degrades_to_direct_context_without_transcript_fallback(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_h8k8_missing_packet", job_id="job_h8k8_missing_packet")

    materialized = attachment.materialize_attachment_for_job_spec(
        _spec(),
        repo_root=tmp_path,
        as_of_utc="2026-04-07T12:00:00Z",
    )
    inspection = attachment.inspect_continuation_state_for_binding(
        {"type": "task_id", "id": "task_h8k8_missing_packet"},
        task_register=register.load_or_init_task_register(tmp_path),
        repo_root=tmp_path,
        as_of_utc="2026-04-07T12:00:00Z",
    )
    operator_snapshot = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-07T12:00:00Z")
    operator_projection = next(
        row for row in operator_snapshot["continuation_inspection"] if row["task_ref"]["task_id"] == "task_h8k8_missing_packet"
    )

    assert materialized["attachment"]["status"] == "degraded"
    assert materialized["attachment"]["selected_source"] == "direct_current_context"
    assert materialized["attachment"]["reason"] == "no_durable_continuation_context_available"
    assert materialized["attachment"]["packet_snapshot"]["mode"] == "degraded"
    assert materialized["attachment"]["packet_snapshot"]["reason"] == "no_valid_continuation_packet_for_resumed_or_successor_work"
    assert materialized["attachment"]["selection_metadata"]["transcript_fallback"] == "forbidden"
    assert (
        "no_valid_continuation_packet_for_resumed_or_successor_work"
        in materialized["attachment"]["selection_metadata"]["visible_gaps"]
    )
    assert materialized["attachment"]["bounded_context"]["direct_current_context_facts"]

    assert inspection["packet"]["status"] == "degraded"
    assert inspection["packet"]["reason"] == "no_valid_continuation_packet_for_resumed_or_successor_work"
    assert inspection["memory"]["status"] == "none"
    assert inspection["memory"]["reason"] == "no_promotable_packet_backing_memory"
    assert inspection["handoff"]["status"] == "degraded"
    assert inspection["handoff"]["authoritative_source"] == "direct_current_context"
    assert "No durable continuation packet or usable derived memory is available." in inspection["handoff"]["summary_text"]
    assert "no_valid_continuation_packet_for_resumed_or_successor_work" in inspection["handoff"]["visible_caveats"]

    assert operator_projection["packet"]["status"] == "degraded"
    assert operator_projection["handoff"]["authoritative_source"] == "direct_current_context"
    assert "No durable continuation packet or usable derived memory is available." in operator_projection["handoff"]["summary_text"]


def test_continuation_intent_validation_proves_stale_packet_and_expired_memory_surface_truthfully_after_restart(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_h8k8_stale", job_id="job_h8k8_stale")
    packet_store.append_packet_revision_once(
        _packet(
            packet_id="detached_job:job_h8k8_stale",
            job_id="job_h8k8_stale",
            created_utc="2026-03-01T09:00:00Z",
            scope="same_family",
        ),
        repo_root=tmp_path,
    )

    payload = snapshot.build_operator_snapshot(tmp_path, now_utc="2026-04-20T12:00:00Z")

    inspection = next(row for row in payload["continuation_inspection"] if row["task_ref"]["task_id"] == "task_h8k8_stale")
    assert inspection["packet"]["status"] == "ready"
    assert inspection["packet"]["freshness"]["status"] == "stale"
    assert inspection["memory"]["status"] == "degraded"
    assert inspection["memory"]["activity_counts"]["expired"] >= 1
    assert inspection["handoff"]["status"] == "degraded"
    assert inspection["handoff"]["authoritative_source"] == "continuation_packet"
    assert "Failed path: Transcript-first fallback is forbidden." in inspection["handoff"]["summary_text"]
    assert any("selected continuation packet is stale" in item for item in inspection["handoff"]["visible_caveats"])


def test_continuation_intent_validation_proves_failed_paths_and_open_questions_survive_memory_only_reuse(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    _seed_task(tmp_path, task_id="task_h8k8_memory_reuse", job_id="job_h8k8_memory_reuse")
    packet_store.append_packet_revision_once(
        _packet(
            packet_id="detached_job:job_h8k8_memory_reuse",
            job_id="job_h8k8_memory_reuse",
            created_utc="2026-04-07T09:00:00Z",
        ),
        repo_root=tmp_path,
    )
    second_revision = _packet(
        packet_id="detached_job:job_h8k8_memory_reuse",
        job_id="job_h8k8_memory_reuse",
        created_utc="2026-04-07T10:00:00Z",
    )
    second_revision["packet_revision"] = 2
    second_revision["supersedes_packet_id"] = "detached_job:job_h8k8_memory_reuse"
    packet_store.append_packet_revision_once(second_revision, repo_root=tmp_path)
    packet_store.invalidate_packet(
        "detached_job:job_h8k8_memory_reuse",
        packet_revision=2,
        reason="operator_rejected_packet",
        recorded_at_utc="2026-04-07T12:30:00Z",
        repo_root=tmp_path,
    )

    materialized = attachment.materialize_attachment_for_job_spec(_spec(), repo_root=tmp_path, as_of_utc="2026-04-07T13:00:00Z")
    inspection = attachment.inspect_continuation_state_for_binding(
        {"type": "task_id", "id": "task_h8k8_memory_reuse"},
        task_register=register.load_or_init_task_register(tmp_path),
        repo_root=tmp_path,
        as_of_utc="2026-04-07T13:00:00Z",
    )

    assert materialized["attachment"]["selected_source"] == "memory_records"
    assert inspection["handoff"]["authoritative_source"] == "memory_records"
    assert "Failed path: Transcript-first fallback is forbidden." in inspection["handoff"]["summary_text"]
    assert "Open question: Resume immediately from the checkpoint?" in inspection["handoff"]["summary_text"]
    assert "Next: Resume from the durable checkpoint." in inspection["handoff"]["summary_text"]
