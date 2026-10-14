from __future__ import annotations

from pathlib import Path

import pytest

from infra.controlplane import continuation_packet_bootstrap as bootstrap
from infra.controlplane import continuation_packet_store as store


def _base_packet(*, packet_id: str, job_id: str) -> dict:
    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": packet_id,
        "packet_revision": 1,
        "created_utc": "2026-04-07T09:00:00Z",
        "source_run": {
            "job_id": job_id,
            "run_id": f"req_{job_id}",
            "attempt_id": "attempt_1",
            "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/demo.json"],
        },
        "handoff_brief": "Resume from the latest durable checkpoint.",
        "result_summary": {"outcome": "interrupted", "summary": "Worker stopped after a durable checkpoint."},
        "evidence_created": [{"evidence_id": "ev_1", "kind": "unit_test", "summary": "packet bootstrap test", "refs": ["tests/control_plane/test_continuation_packet_bootstrap.py"]}],
        "artifact_index": [{"artifact_id": "art_1", "kind": "json", "uri": "runtime/state/lab_assistant/detached_jobs/jobs/demo.json", "summary": "job record"}],
        "open_questions": [{"question_id": "q_1", "question": "Resume or retry?"}],
        "failed_paths": [{"path_id": "fp_1", "summary": "No clean terminal result exists yet."}],
        "recommended_next_steps": [{"step_id": "step_1", "priority": "now", "step": "Resume from the checkpoint."}],
        "confidence": {"level": "medium", "rationale": "Checkpoint truth is durable."},
        "applicability": {"scope": "immediate_successor", "limitations": ["Lifecycle truth remains authoritative for raw status."]},
    }


def _task(job_id: str) -> dict:
    return {
        "task_id": f"task_{job_id}",
        "kind": "software",
        "title": "Resume successor task",
        "status": "active",
        "current_run_id": job_id,
        "task_runs": [
            {
                "run_id": job_id,
                "run_state": "running",
                "checkpoint": {
                    "checkpoint_id": f"cp_{job_id}",
                    "checkpoint_status": "recorded",
                    "checkpoint_note": "safe resume point",
                    "checkpoint_utc": "2026-04-07T09:01:00Z",
                    "checkpoint_source": "test",
                    "checkpoint_revision": 1,
                    "progress_summary": "halfway there",
                    "evidence_refs": ["runtime/state/lab_assistant/checkpoints/demo.json"],
                },
                "resume_decision": {
                    "decision": "resume",
                    "decision_reason": "job_state:interrupted",
                    "decision_summary": "resume from durable checkpoint",
                    "decision_source": "test",
                    "decision_utc": "2026-04-07T09:01:00Z",
                    "checkpoint_id": f"cp_{job_id}",
                    "evidence_refs": ["runtime/state/lab_assistant/checkpoints/demo.json"],
                },
                "revision": 2,
            }
        ],
        "execution_surface_ownership": {
            "selected_surface": "workflow",
            "execution_id": job_id,
            "execution_ref": f"workflow:{job_id}",
            "binding_ref": f"binding_{job_id}",
            "job_uri": "runtime/state/lab_assistant/detached_jobs/jobs/demo.json",
            "trace_id": f"trace_{job_id}",
            "reference_status": "execution_ref_active",
        },
    }


def _task_run_only(*, run_id: str) -> dict:
    return {
        "task_id": f"task_{run_id}",
        "kind": "software",
        "title": "Resume successor task from run-only identity",
        "status": "active",
        "current_run_id": run_id,
        "task_runs": [
            {
                "run_id": run_id,
                "run_state": "interrupted",
                "checkpoint": {
                    "checkpoint_id": f"cp_{run_id}",
                    "checkpoint_status": "recorded",
                    "checkpoint_note": "safe resume point",
                    "checkpoint_utc": "2026-04-07T09:01:00Z",
                    "checkpoint_source": "test",
                    "checkpoint_revision": 1,
                    "progress_summary": "halfway there",
                    "evidence_refs": ["runtime/state/lab_assistant/checkpoints/demo.json"],
                },
            }
        ],
    }


def test_packet_snapshot_for_task_prefers_latest_valid_durable_packet(tmp_path: Path) -> None:
    job_id = "job_resume_ready"
    store.append_packet_revision_once(_base_packet(packet_id="pkt_ready", job_id=job_id), repo_root=tmp_path)

    snapshot = bootstrap.packet_snapshot_for_task(_task(job_id), repo_root=tmp_path)

    assert snapshot["mode"] == "ready"
    assert snapshot["packet_record"]["packet_id"] == "pkt_ready"
    assert snapshot["selected_lookup"]["lookup_key"] == "job_id"
    assert snapshot["selected_lookup"]["lookup_value"] == job_id


def test_packet_snapshot_for_task_uses_run_id_lookup_when_job_identity_is_unavailable(tmp_path: Path) -> None:
    run_id = "run_resume_ready"
    packet = _base_packet(packet_id="pkt_run_ready", job_id="job_resume_ready")
    packet["source_run"]["run_id"] = run_id
    store.append_packet_revision_once(packet, repo_root=tmp_path)

    snapshot = bootstrap.packet_snapshot_for_task(_task_run_only(run_id=run_id), repo_root=tmp_path)

    assert snapshot["mode"] == "ready"
    assert snapshot["packet_record"]["packet_id"] == "pkt_run_ready"
    assert snapshot["selected_lookup"]["lookup_key"] == "run_id"
    assert snapshot["selected_lookup"]["lookup_value"] == run_id


def test_packet_snapshot_for_task_prefers_latest_valid_packet_across_superseded_history(tmp_path: Path) -> None:
    job_id = "job_resume_superseded"
    store.append_packet_revision_once(_base_packet(packet_id="pkt_old", job_id=job_id), repo_root=tmp_path)
    packet = _base_packet(packet_id="pkt_new", job_id=job_id)
    packet["supersedes_packet_id"] = "pkt_old"
    store.append_packet_revision_once(packet, repo_root=tmp_path)

    snapshot = bootstrap.packet_snapshot_for_task(_task(job_id), repo_root=tmp_path)

    assert snapshot["mode"] == "ready"
    assert snapshot["packet_record"]["packet_id"] == "pkt_new"
    assert snapshot["packet_record"]["packet"]["supersedes_packet_id"] == "pkt_old"
    assert snapshot["selected_lookup"]["lookup_key"] == "job_id"
    assert snapshot["selected_lookup"]["lookup_value"] == job_id


def test_packet_snapshot_for_task_degrades_when_resumed_work_has_no_valid_packet(tmp_path: Path) -> None:
    snapshot = bootstrap.packet_snapshot_for_task(_task("job_resume_missing"), repo_root=tmp_path)

    assert snapshot["mode"] == "degraded"
    assert snapshot["reason"] == "no_valid_continuation_packet_for_resumed_or_successor_work"


def test_packet_snapshot_for_task_blocks_on_ambiguous_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_ambiguous(*args, **kwargs):
        raise store.AmbiguousLatestValidPacketError("ambiguous latest-valid lookup for job_id=job_resume_ambiguous")

    monkeypatch.setattr(bootstrap._continuation_packet_store, "latest_valid_packet", _raise_ambiguous)

    snapshot = bootstrap.packet_snapshot_for_task(_task("job_resume_ambiguous"), repo_root=tmp_path)

    assert snapshot["mode"] == "blocked"
    assert "ambiguous latest-valid lookup" in snapshot["reason"]
