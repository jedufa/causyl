from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import continuation_context_attachment as attachment
from infra.controlplane import continuation_packet_store as store
from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_task_register as register


def _base_packet(*, packet_id: str, packet_revision: int, supersedes_packet_id: str = "") -> dict:
    packet = {
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
                "refs": ["tests/control_plane/test_continuation_context_attachment.py"],
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
    if supersedes_packet_id:
        packet["supersedes_packet_id"] = supersedes_packet_id
    return packet


def _seed_task_register(tmp_path: Path, *, task_id: str, ledger_uid: str, job_id: str) -> None:
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


def test_compiled_manifest_includes_generated_attachment_and_prefers_latest_packet(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(tmp_path, task_id="task_ctx_ready", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=1), repo_root=tmp_path)
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=2), repo_root=tmp_path)

    manifest = sched.compiled_context_manifest_from_job_spec(_spec(ledger_uid), repo_root=tmp_path)

    assert manifest["ok"] is True
    attachment_ref = next(
        row["path"]
        for row in manifest["manifest"]["files"]
        if "runtime/state/controlplane/continuation_context_attachments/" in row["path"]
    )
    payload = json.loads((tmp_path / attachment_ref).read_text(encoding="utf-8"))

    assert payload["selected_source"] == "continuation_packet"
    assert payload["status"] == "ready"
    assert payload["bounded_context"]["continuation_packet"]["packet_revision"] == 2
    assert "packet_derived_memory_suppressed_by_fresher_packet" in payload["selection_metadata"]["suppressed_sources"]


def test_attachment_falls_back_to_packet_derived_memory_when_latest_packet_is_invalid(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(tmp_path, task_id="task_ctx_memory", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=1), repo_root=tmp_path)
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=2), repo_root=tmp_path)
    store.invalidate_packet(
        "pkt_ctx",
        packet_revision=2,
        reason="operator_rejected_packet",
        recorded_at_utc="2026-04-07T09:04:00Z",
        repo_root=tmp_path,
    )

    result = attachment.materialize_attachment_for_job_spec(_spec(ledger_uid), repo_root=tmp_path, as_of_utc="2026-04-07T09:05:00Z")
    payload = result["attachment"]

    assert payload["selected_source"] == "memory_records"
    assert payload["status"] == "ready"
    assert payload["bounded_context"]["memory_records"]
    assert "no_valid_continuation_packet_for_resumed_or_successor_work" in payload["selection_metadata"]["visible_gaps"]


def test_attachment_marks_selected_packet_staleness_in_attachment_metadata(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(tmp_path, task_id="task_ctx_stale_packet", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=1), repo_root=tmp_path)

    result = attachment.materialize_attachment_for_job_spec(_spec(ledger_uid), repo_root=tmp_path, as_of_utc="2026-04-20T09:05:00Z")
    payload = result["attachment"]

    assert payload["selected_source"] == "continuation_packet"
    assert payload["status"] == "degraded"
    assert payload["selection_metadata"]["selected_packet_freshness"]["status"] == "stale"
    assert any("selected continuation packet is stale" in item for item in payload["selection_metadata"]["visible_gaps"])


def test_attachment_marks_packet_derived_memory_staleness_in_attachment_metadata(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(tmp_path, task_id="task_ctx_stale_memory", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=1), repo_root=tmp_path)
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=2), repo_root=tmp_path)
    store.invalidate_packet(
        "pkt_ctx",
        packet_revision=2,
        reason="operator_rejected_packet",
        recorded_at_utc="2026-04-07T09:04:00Z",
        repo_root=tmp_path,
    )

    result = attachment.materialize_attachment_for_job_spec(
        _spec(ledger_uid),
        repo_root=tmp_path,
        as_of_utc="2026-04-20T09:05:00Z",
    )
    payload = result["attachment"]

    assert payload["selected_source"] == "memory_records"
    assert payload["status"] == "degraded"
    assert payload["selection_metadata"]["memory_source_packet_freshness"]["status"] == "stale"
    assert any("stale packet" in item for item in payload["selection_metadata"]["visible_gaps"])


def test_attachment_degrades_to_direct_current_context_without_transcript_blob_fallback(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(tmp_path, task_id="task_ctx_direct", ledger_uid=ledger_uid, job_id="job_ctx_demo")

    result = attachment.materialize_attachment_for_job_spec(_spec(ledger_uid), repo_root=tmp_path, as_of_utc="2026-04-07T09:05:00Z")
    payload = result["attachment"]
    raw = json.dumps(payload, sort_keys=True)

    assert payload["selected_source"] == "direct_current_context"
    assert payload["status"] == "degraded"
    assert payload["selection_metadata"]["transcript_fallback"] == "forbidden"
    assert "no_valid_continuation_packet_for_resumed_or_successor_work" in payload["selection_metadata"]["visible_gaps"]
    assert "session__" not in raw


def test_attachment_fails_closed_when_packet_snapshot_is_ambiguous_even_if_memory_candidates_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_uid = "PIS-20260407-063106Z-F6H6"
    _seed_task_register(tmp_path, task_id="task_ctx_ambiguous", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx_old", packet_revision=1), repo_root=tmp_path)

    monkeypatch.setattr(
        attachment._packet_bootstrap,
        "packet_snapshot_for_binding",
        lambda *args, **kwargs: {
            "mode": "blocked",
            "reason": "ambiguous latest-valid lookup for job_id=job_ctx_demo",
            "lookup_candidates": [{"lookup_key": "run_id", "lookup_value": "run_ctx_demo", "source": "current_run_id"}],
            "packet_record": None,
        },
    )

    result = attachment.materialize_attachment_for_job_spec(_spec(ledger_uid), repo_root=tmp_path, as_of_utc="2026-04-07T09:05:00Z")
    payload = result["attachment"]

    assert payload["status"] == "blocked"
    assert payload["selected_source"] == "none"
    assert payload["reason"] == "ambiguous latest-valid lookup for job_id=job_ctx_demo"
    assert payload["bounded_context"]["direct_current_context_facts"]
    assert payload["selection_metadata"]["visible_gaps"] == [
        "ambiguous latest-valid lookup for job_id=job_ctx_demo"
    ]


def test_surface_projection_rendering_exposes_packet_freshness_and_memory_caveat(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063107Z-G7J7"
    _seed_task_register(tmp_path, task_id="task_ctx_surface", ledger_uid=ledger_uid, job_id="job_ctx_demo")
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ctx", packet_revision=1), repo_root=tmp_path)

    projection = attachment.inspect_continuation_state_for_binding(
        {"type": "task_id", "id": "task_ctx_surface"},
        task_register=(register.read_task_register_snapshot(tmp_path, initialize_if_missing=False) or {}).get("task_register"),
        repo_root=tmp_path,
        as_of_utc="2026-04-20T09:05:00Z",
    )
    lines = attachment.render_surface_projection_lines(projection)

    assert projection["packet"]["freshness"]["status"] == "stale"
    assert projection["handoff"]["status"] == "degraded"
    assert projection["handoff"]["authoritative_source"] == "continuation_packet"
    assert any("selected continuation packet is stale" in item for item in projection["handoff"]["visible_caveats"])
    assert any("freshness=stale" in line for line in lines)
    assert any("memory=ready" in line for line in lines)
    assert any("handoff=degraded" in line for line in lines)
