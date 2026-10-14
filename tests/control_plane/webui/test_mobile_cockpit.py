from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import campaign_operator_summary as campaign_summary
from infra.controlplane import continuation_packet_store as store
from infra.webui import server_mobile_cockpit as cockpit
from infra.webui import server_lab_assistant_task_register as register


def _base_packet() -> dict:
    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": "pkt-mobile",
        "packet_revision": 1,
        "created_utc": "2026-06-28T09:01:00Z",
        "source_run": {
            "job_id": "job-mobile",
            "run_id": "run-mobile",
            "attempt_id": "attempt-1",
            "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/mobile.json"],
        },
        "handoff_brief": "Continue from the durable checkpoint instead of guessing from chat.",
        "result_summary": {"outcome": "partial", "summary": "Checkpoint exists but terminal completion is not proven."},
        "evidence_created": [
            {
                "evidence_id": "ev-mobile",
                "kind": "unit_test",
                "summary": "Mobile cockpit continuation projection test",
                "refs": ["tests/control_plane/webui/test_mobile_cockpit.py"],
            }
        ],
        "artifact_index": [
            {
                "artifact_id": "art-mobile",
                "kind": "json",
                "uri": "runtime/state/lab_assistant/detached_jobs/jobs/mobile.json",
                "summary": "job record",
            }
        ],
        "open_questions": [{"question_id": "q1", "question": "What remaining work should resume from this checkpoint?"}],
        "failed_paths": [{"path_id": "fp1", "summary": "Do not treat this partial checkpoint as finished work."}],
        "recommended_next_steps": [{"step_id": "s1", "priority": "now", "step": "Resume from the durable checkpoint and verify completion separately."}],
        "confidence": {"level": "medium", "rationale": "Checkpoint exists but terminal success is still pending."},
        "applicability": {"scope": "same_family", "limitations": ["Lifecycle truth remains authoritative."]},
    }


def _seed_task_register(tmp_path: Path, *, ledger_uid: str) -> None:
    snapshot = register.read_task_register_snapshot(tmp_path, initialize_if_missing=True)
    generation = int((snapshot.get("task_register") or {}).get("write_generation") or 0)
    register.upsert_task(
        tmp_path,
        expected_generation=generation,
        writer="test",
        task={
            "task_id": "task-mobile",
            "kind": "software",
            "title": "Mobile continuation surface test",
            "status": "active",
            "owner": "builder",
            "next_expected_action": "Resume from checkpoint",
            "ledger_uid": ledger_uid,
            "current_run_id": "job-mobile",
            "task_runs": [
                {
                    "run_id": "job-mobile",
                    "run_state": "stale",
                    "checkpoint": {"checkpoint_id": "cp-mobile"},
                    "resume_decision": {"decision": "resume"},
                }
            ],
        },
    )


def test_mobile_cockpit_economics_payload_degrades_honestly_when_token_usage_missing() -> None:
    payload = cockpit._economics_payload(
        queue_payload={
            "state": {"status": "running"},
            "batch_report": {"completed_count": 0, "run_id": "eq-1"},
        },
        liveness_payload={
            "queue": {"status": "running", "run_id": "eq-1"},
            "workflow": {"workflow_id": "wf-1", "token_usage": {}},
        },
    )

    assert payload["status"] == "degraded"
    assert payload["waste_warning"] == "token_usage_unavailable"
    assert payload["token_usage"]["total_tokens"] == 0


def test_mobile_cockpit_failure_classification_maps_failed_queue_to_actions() -> None:
    payload = cockpit._classify_failure(
        queue_state={"status": "failed", "last_error": "compile failed"},
        workflow_state={"status": "failed", "last_error": "compile failed"},
    )

    assert payload["status"] == "failed"
    assert payload["failure_class"] == "workflow_failure"
    assert "inspect_workflow" in payload["actions"]


def test_mobile_cockpit_continuation_payload_surfaces_failed_paths_questions_and_next_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_uid = "PIS-20260407-063107Z-G7J7"
    _seed_task_register(tmp_path, ledger_uid=ledger_uid)
    store.append_packet_revision_once(_base_packet(), repo_root=tmp_path)
    monkeypatch.setattr(cockpit, "_repo_root", lambda: tmp_path)

    payload = cockpit._continuation_payload(primary_uid=ledger_uid)

    assert payload["status"] == "ready"
    assert payload["authoritative_source"] == "continuation_packet"
    assert payload["packet_id"] == "pkt-mobile"
    assert payload["packet_freshness"]["status"] == "fresh"
    assert payload["failed_paths"] == ["Do not treat this partial checkpoint as finished work."]
    assert payload["open_questions"] == ["What remaining work should resume from this checkpoint?"]
    assert payload["next_steps"] == ["Resume from the durable checkpoint and verify completion separately."]


def test_mobile_cockpit_continuation_payload_degrades_stale_packet_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_uid = "PIS-20260407-063107Z-G7J7"
    stale_packet = _base_packet()
    stale_packet["created_utc"] = "2026-03-01T09:01:00Z"
    _seed_task_register(tmp_path, ledger_uid=ledger_uid)
    store.append_packet_revision_once(stale_packet, repo_root=tmp_path)
    monkeypatch.setattr(cockpit, "_repo_root", lambda: tmp_path)

    payload = cockpit._continuation_payload(primary_uid=ledger_uid)

    assert payload["status"] == "degraded"
    assert payload["handoff_status"] == "degraded"
    assert payload["authoritative_source"] == "continuation_packet"
    assert payload["packet_freshness"]["status"] == "stale"
    assert any("selected continuation packet is stale" in item for item in payload["visible_caveats"])


def test_mobile_cockpit_shell_payload_reports_low_trust_and_fallback_actions() -> None:
    payload = cockpit._shell_payload(
        contracts={"contract_check": {"ok": True}},
        artifacts={"status": "degraded"},
        continuation={"status": "blocked"},
        run_triage={"status": "blocked", "workflow": {"workflow_id": "wf-1"}},
        economics={"status": "warn"},
        archive_snapshots={"status": "ok"},
        latest_campaign={"campaign_id": "camp-1", "queue_run_id": "eq-1"},
    )

    assert payload["status"] == "low_trust"
    assert "export_handoff" in payload["fallback_actions"]
    assert "refresh_projection" in payload["fallback_actions"]
    assert payload["executor_identity"]["workflow_id"] == "wf-1"


def test_mobile_cockpit_chronicle_projection_mixes_campaign_queue_and_archive_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_root = tmp_path / "governance" / "agents" / "runtime" / "execution_queue"
    queue_root.mkdir(parents=True, exist_ok=True)
    (queue_root / "events.jsonl").write_text(
        json.dumps({"utc": "2026-04-02T10:00:00Z", "event": "queue_started", "payload": {"uid": "PIS-1", "workflow_id": "wf-1"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cockpit, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cockpit,
        "_archive_snapshot_payload",
        lambda: {
            "status": "ok",
            "latest_repo_snapshot": {"snapshot_id": "repo.zip"},
            "repo_archives": [],
            "run_archives": [],
        },
    )

    payload = cockpit._chronicle_payload(
        primary_uid="",
        latest_campaign={"campaign_id": "camp-1", "created_utc": "2026-04-02T09:59:00Z", "launch_outcome": "started"},
        queue_payload={"state": {"run_id": "eq-1"}},
    )

    assert payload["status"] == "ok"
    kinds = {row["kind"] for row in payload["entries"]}
    assert "campaign" in kinds
    assert "run" in kinds
    assert "archive_snapshot" in kinds


def test_mobile_cockpit_campaign_summary_projects_durable_review_and_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    latest_campaign = {
        "campaign_id": "camp-1",
        "campaign_record": {
            "schema_version": "campaign_record_contract_v1",
            "campaign_id": "camp-1",
            "parent_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
            "campaign_state": "active",
            "campaign_truth_refs": {
                "ledger_uids": ["PIS-20260409-121217Z-2A6A"],
                "job_ids": [],
                "run_ids": [],
                "receipt_refs": [],
                "continuation_packet_ids": [],
                "artifact_refs": [],
            },
            "record_event_refs": ["governance/demo/events.jsonl"],
            "phases": [
                {
                    "phase_id": "phase_a",
                    "label": "Phase A",
                    "job_shape": "verification_loop_job",
                    "state": "declared",
                    "depends_on": [],
                    "output_refs": [],
                    "truth_refs": {
                        "ledger_uids": ["PIS-20260409-121602Z-L1P4"],
                        "job_ids": ["job_phase_a"],
                        "run_ids": [],
                        "receipt_refs": ["runtime/receipts/phase_a.json"],
                        "continuation_packet_ids": ["pkt-a"],
                        "artifact_refs": ["artifacts/phase_a.md"],
                    },
                    "phase_event_refs": [],
                }
            ],
        },
        "phase_gate_decisions": [
            {
                "schema_version": "campaign_phase_gate_contract_v1",
                "campaign_id": "camp-1",
                "phase_id": "phase_a",
                "decision_id": "decision_a",
                "verdict": "waiting_review",
                "evidence_refs": ["governance/phase_a.json"],
                "caveats": [],
                "proof_gaps": ["operator review missing"],
                "downstream_unlock": {"policy": "review_required", "allowed": False, "unlocked_phase_ids": []},
                "handoff": {
                    "summary": "Phase A needs review.",
                    "evidence_refs": ["governance/phase_a.json"],
                    "caveats": [],
                    "proof_gaps": ["operator review missing"],
                    "next_decision": {
                        "decision_owner": "operator",
                        "question": "Approve phase A?",
                        "recommended_action": "await_review",
                    },
                },
            }
        ],
        "phase_runtime_facts": [{"phase_id": "phase_a", "continuation_packet_ids": ["pkt-a"]}],
    }

    monkeypatch.setattr(cockpit, "_load_latest_campaign", lambda: latest_campaign)
    monkeypatch.setattr(cockpit._execution_queue, "status", lambda: {"state": {}, "queue_preview": [], "batch_report": {}, "worker_alive": False})
    monkeypatch.setattr(cockpit, "backlog_projection", lambda **_: {"items": [], "counts": {}, "groups": [], "scheduled_bindings": {}})
    monkeypatch.setattr(cockpit, "_artifact_payload", lambda **_: {"status": "degraded"})
    monkeypatch.setattr(cockpit, "_continuation_payload", lambda **_: {"status": "degraded"})
    monkeypatch.setattr(cockpit, "_run_liveness_payload", lambda **_: {"status": "idle", "workflow": {}, "queue": {}})
    monkeypatch.setattr(cockpit, "_economics_payload", lambda **_: {"status": "ok"})
    monkeypatch.setattr(cockpit, "_archive_snapshot_payload", lambda: {"status": "ok"})
    monkeypatch.setattr(cockpit, "_chronicle_payload", lambda **_: {"status": "ok", "entries": []})

    payload = cockpit.cockpit_payload()

    summary_payload = payload["campaigns"]["summary"]
    assert summary_payload["status"] == "ok"
    assert summary_payload["operator_readiness"] == "waiting_review"
    assert summary_payload["review_summary"]["count"] == 1
    assert summary_payload["operator_evidence"]["continuation_packet_ids"] == ["pkt-a"]


def test_mobile_cockpit_campaign_summary_projects_runtime_only_continuation_packets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest_campaign = {
        "campaign_id": "camp-runtime-only",
        "campaign_record": {
            "schema_version": "campaign_record_contract_v1",
            "campaign_id": "camp-runtime-only",
            "parent_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
            "campaign_state": "active",
            "campaign_truth_refs": {
                "ledger_uids": ["PIS-20260409-121217Z-2A6A"],
                "job_ids": [],
                "run_ids": [],
                "receipt_refs": [],
                "continuation_packet_ids": [],
                "artifact_refs": [],
            },
            "record_event_refs": ["governance/demo/events.jsonl"],
            "phases": [
                {
                    "phase_id": "phase_runtime",
                    "label": "Phase Runtime",
                    "job_shape": "verification_loop_job",
                    "state": "declared",
                    "depends_on": [],
                    "output_refs": [],
                    "truth_refs": {
                        "ledger_uids": ["PIS-20260409-121602Z-L1P4"],
                        "job_ids": ["job_phase_runtime"],
                        "run_ids": [],
                        "receipt_refs": [],
                        "continuation_packet_ids": [],
                        "artifact_refs": ["artifacts/phase_runtime.md"],
                    },
                    "phase_event_refs": [],
                }
            ],
        },
        "phase_gate_decisions": [
            {
                "schema_version": "campaign_phase_gate_contract_v1",
                "campaign_id": "camp-runtime-only",
                "phase_id": "phase_runtime",
                "decision_id": "decision_runtime",
                "verdict": "waiting_review",
                "evidence_refs": ["governance/phase_runtime.json"],
                "caveats": [],
                "proof_gaps": ["operator review missing"],
                "downstream_unlock": {"policy": "review_required", "allowed": False, "unlocked_phase_ids": []},
                "handoff": {
                    "summary": "Phase Runtime needs review.",
                    "evidence_refs": ["governance/phase_runtime.json"],
                    "caveats": [],
                    "proof_gaps": ["operator review missing"],
                    "next_decision": {
                        "decision_owner": "operator",
                        "question": "Approve phase runtime?",
                        "recommended_action": "await_review",
                    },
                },
            }
        ],
        "phase_runtime_facts": [{"phase_id": "phase_runtime", "continuation_packet_ids": ["pkt-runtime-only"]}],
    }

    builder_summary = campaign_summary.build_campaign_operator_summary(
        campaign_record=latest_campaign["campaign_record"],
        phase_gate_decisions=latest_campaign["phase_gate_decisions"],
        phase_runtime_facts=latest_campaign["phase_runtime_facts"],
        repo_root=tmp_path,
    )

    assert builder_summary["operator_evidence"]["continuation_packet_ids"] == ["pkt-runtime-only"]

    monkeypatch.setattr(cockpit, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cockpit, "_load_latest_campaign", lambda: latest_campaign)
    monkeypatch.setattr(cockpit._execution_queue, "status", lambda: {"state": {}, "queue_preview": [], "batch_report": {}, "worker_alive": False})
    monkeypatch.setattr(cockpit, "backlog_projection", lambda **_: {"items": [], "counts": {}, "groups": [], "scheduled_bindings": {}})
    monkeypatch.setattr(cockpit, "_artifact_payload", lambda **_: {"status": "degraded"})
    monkeypatch.setattr(cockpit, "_continuation_payload", lambda **_: {"status": "degraded"})
    monkeypatch.setattr(cockpit, "_run_liveness_payload", lambda **_: {"status": "idle", "workflow": {}, "queue": {}})
    monkeypatch.setattr(cockpit, "_economics_payload", lambda **_: {"status": "ok"})
    monkeypatch.setattr(cockpit, "_archive_snapshot_payload", lambda: {"status": "ok"})
    monkeypatch.setattr(cockpit, "_chronicle_payload", lambda **_: {"status": "ok", "entries": []})

    payload = cockpit.cockpit_payload()
    summary_payload = payload["campaigns"]["summary"]

    assert summary_payload["status"] == "ok"
    assert summary_payload["operator_evidence"]["continuation_packet_ids"] == ["pkt-runtime-only"]


def test_mobile_cockpit_campaign_summary_degrades_without_campaign_record() -> None:
    payload = cockpit._campaign_summary_payload(latest_campaign={"campaign_id": "camp-1"})
    assert payload["status"] == "degraded"
    assert payload["reason"] == "campaign_record_missing"
