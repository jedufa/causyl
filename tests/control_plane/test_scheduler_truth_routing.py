from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# The snapshot contains an unrelated syntax error under infra.pipeline.planning_ontology.
# Stub the rich-reply module before importing scheduler/server_agents so these narrow
# control-plane tests can still exercise the audited paths.
sys.modules.setdefault("infra.webui.server_context_rich_reply", types.ModuleType("infra.webui.server_context_rich_reply"))

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_agents as agents
from infra.webui import server_lab_assistant_task_register as register


def _seed_task_register(repo_root: Path, *, task_id: str) -> None:
    current = register.load_or_init_task_register(repo_root)
    register.upsert_task(
        repo_root,
        expected_generation=int(current.get("write_generation") or 0),
        writer="scheduler.truth.test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": f"task {task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "scheduler",
            "next_expected_action": "run scheduled job",
        },
    )


def test_normalize_runtime_infers_model_family_and_provider_from_model_only() -> None:
    runtime = sched._normalize_runtime({"model": "deepseek-reasoner"}, job_kind="single_turn")
    assert runtime["agent_tech"] == "deepseek"
    assert runtime["provider"] == "deepseek"
    assert runtime["model"] == "deepseek-reasoner"
    assert "deepseek-chat" in list(runtime.get("fallback_models") or [])

    stage8 = sched._pipeline_backend_model_config({"agent_tech": "openai", "model": "deepseek-reasoner", "provider": "openai"})
    assert stage8["stage8_agent_tech"] == "deepseek"
    assert stage8["stage8_provider"] == "deepseek"


def test_agent_send_with_retry_and_fallback_infers_deepseek_from_model_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def _send_scheduled_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append(dict(options or {}))
        return {"ok": True, "assistant": {"content": "ok", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_scheduled_message", _send_scheduled_message)

    result, runtime_send = sched._agent_send_with_retry_and_fallback(
        role="generalist",
        binding={"type": "task_id", "id": "abc"},
        message="Route this correctly.",
        options={},
        retries=0,
        backoff_seconds=1,
        runtime={"model": "deepseek-reasoner"},
    )

    assert result["ok"] is True
    assert runtime_send["selected_agent_tech"] == "deepseek"
    assert runtime_send["selected_model"] == "deepseek-reasoner"
    assert calls == [{"agent_tech": "deepseek", "model": "deepseek-reasoner"}]


def test_server_agent_resolve_agent_tech_prefers_model_family_over_conflicting_option() -> None:
    assert agents._resolve_agent_tech({"model": "deepseek-reasoner"}) == "deepseek"
    assert agents._resolve_agent_tech({"agent_tech": "openai", "model": "deepseek-reasoner"}) == "deepseek"
    assert agents._resolve_agent_tech({"agent_tech": "codex", "model": "gpt-5.3-codex-spark"}) == "codex"


def test_record_nonlaunch_decision_updates_primary_receipt_and_clears_run_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _seed_task_register(tmp_path, task_id="mb3")

    sched.save_job(
        {
            "job_id": "nonlaunch_truth_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Skip via nonlaunch path.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
        }
    )

    spec = sched.load_job("nonlaunch_truth_001", repo_root=tmp_path)
    prior_state = {
        "run_now_requested_utc": "2026-03-08T03:00:00Z",
        "run_now_request_source": "manual",
        "last_receipt_path": "/stale/receipt.json",
        "last_report_path": "/stale/report.md",
        "last_status": "running",
    }
    sched._write_state("nonlaunch_truth_001", prior_state, repo_root=tmp_path)

    decision = sched._record_nonlaunch_decision(
        spec=spec,
        state=prior_state,
        due={"eligible": False, "reason": "not_due", "slot_key": "", "due_utc": ""},
        run_id="sj_nonlaunch_truth_001",
        trigger="manual",
        reason="repo_guard_dirty_worktree",
        status="blocked",
        event="blocked",
        repo_root=tmp_path,
    )

    state = sched.load_state("nonlaunch_truth_001", repo_root=tmp_path)
    assert decision["recorded"] is True
    assert state["last_run_id"] == "sj_nonlaunch_truth_001"
    assert state["last_status"] == "blocked"
    assert state["last_started_utc"] == ""
    assert state["last_finished_utc"]
    assert state["last_receipt_path"] == decision["receipt_path"]
    assert state["last_report_path"] == decision["report_path"]
    assert state["run_now_requested_utc"] == ""
    assert state["run_now_request_source"] == ""
    assert state["last_shape_canonical_state"] == "blocked"
    assert state["last_shape_summary_bucket"] == "terminal_operator_actionable"
    assert state["last_shape_recovery_class"] == "rerun_from_last_iteration_boundary"


def test_apply_active_run_recovery_clears_stale_nonlaunch_markers(tmp_path: Path) -> None:
    state = {
        "active_run_id": "sj_recover_001",
        "active_started_utc": "2026-03-08T03:31:00Z",
        "run_now_requested_utc": "2026-03-08T03:20:00Z",
        "run_now_request_source": "manual",
        "last_nonlaunch_reason": "stale_nonlaunch",
        "last_nonlaunch_status": "deferred",
        "last_nonlaunch_utc": "2026-03-08T03:10:00Z",
        "last_nonlaunch_run_id": "sj_old_nonlaunch_001",
        "last_nonlaunch_receipt_path": "/stale/nonlaunch.json",
        "last_nonlaunch_report_path": "/stale/nonlaunch.md",
        "last_nonlaunch_signature": "stale-signature",
    }
    sched._write_state("recover_truth_001", state, repo_root=tmp_path)

    sched._apply_active_run_recovery(
        job_id="recover_truth_001",
        state=state,
        recovery={"recover": True, "recovery_reason": "stale_active_claim"},
        trigger="scheduler",
        repo_root=tmp_path,
    )

    updated = sched.load_state("recover_truth_001", repo_root=tmp_path)
    assert updated["active_run_id"] == ""
    assert updated["last_run_id"] == "sj_recover_001"
    assert updated["last_status"] == "interrupted"
    assert updated["run_now_requested_utc"] == ""
    assert updated["last_nonlaunch_reason"] == ""
    assert updated["last_nonlaunch_receipt_path"] == ""
    assert updated["last_nonlaunch_signature"] == ""


def test_apply_active_run_recovery_synthesizes_success_for_completed_governance_decomposition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_dir = tmp_path / "parent_pis"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": "PIS-RECOVER-0001",
                "integration_meta": {
                    "child_uids": ["PIS-CHILD-0001", "PIS-CHILD-0002"],
                    "recommended_execution_order": ["PIS-CHILD-0001", "PIS-CHILD-0002"],
                    "recommended_first_child_uid": "PIS-CHILD-0001",
                    "required_final_child_uid": "PIS-CHILD-0002",
                    "deferred_parent_sections": ["ui-heavy redesign"],
                    "reuse_notes": "reused nothing",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sched,
        "_resolve_ledger_item_dir_for_uid",
        lambda uid, repo_root=None: parent_dir if str(uid) == "PIS-RECOVER-0001" else None,
    )
    monkeypatch.setattr(
        sched,
        "_queue_child_implementation_jobs_after_pass",
        lambda spec, finished_utc, repo_root=None: {
            "enabled": True,
            "created_count": 0,
            "skipped_count": 2,
            "children": [],
        },
    )

    sched.save_job(
        {
            "job_id": "recover_decompose_parent_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": "PIS-RECOVER-0001"},
            "prompt": "Decompose this parent PIS.",
            "expected_output": {"kind": "governance_decomposition_patch", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": "2026-03-30T03:30:00Z", "timezone": "UTC"},
            "loop": {"loop_policy": "default_conditional_repair_loop", "max_iterations": 1},
            "policy": {"max_runtime_seconds": 60},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        "recover_decompose_parent_001",
        {
            "job_id": "recover_decompose_parent_001",
            "active_run_id": "sj_recover_decompose_001",
            "active_started_utc": "2026-03-30T03:30:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    claim_path = sched._claim_path("recover_decompose_parent_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "recover_decompose_parent_001",
                "run_id": "sj_recover_decompose_001",
                "pid": 999999,
                "claimed_utc": "2026-03-30T03:30:00Z",
                "heartbeat_utc": "2026-03-30T03:30:00Z",
            }
        ),
        encoding="utf-8",
    )

    sched._apply_active_run_recovery(
        job_id="recover_decompose_parent_001",
        state=sched.load_state("recover_decompose_parent_001", repo_root=tmp_path),
        recovery={"recover": True, "recovery_reason": "active_run_claim_pid_dead"},
        trigger="scheduler",
        repo_root=tmp_path,
    )

    updated = sched.load_state("recover_decompose_parent_001", repo_root=tmp_path)
    assert updated["active_run_id"] == ""
    assert updated["last_status"] == "ok"
    assert updated["last_trigger"] == "recovery"
    assert updated["last_slot_key"] == "2026-03-30T03:30:00Z"
    assert Path(updated["last_receipt_path"]).exists()
    assert Path(updated["last_report_path"]).exists()
    assert not claim_path.exists()
    receipt = sched.load_receipt("recover_decompose_parent_001", "sj_recover_decompose_001", repo_root=tmp_path)
    assert receipt["status"] == "ok"
    assert receipt["decomposition_recovery"]["child_uids"] == ["PIS-CHILD-0001", "PIS-CHILD-0002"]
    assert receipt["recovery"]["synthetic_closeout"] is True


def test_cancel_job_clears_stale_nonlaunch_markers_and_projects_terminal_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _seed_task_register(tmp_path, task_id="mb3")

    sched.save_job(
        {
            "job_id": "cancel_truth_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Cancel this job.",
            "schedule": {"mode": "once", "run_at": "2026-03-20T10:00:00Z", "timezone": "UTC"},
        }
    )

    run_id = "sj_cancel_truth_001"
    sched._write_state(
        "cancel_truth_001",
        {
            "active_run_id": run_id,
            "active_started_utc": "2026-03-20T10:00:00Z",
            "last_status": "running",
            "last_nonlaunch_reason": "old_nonlaunch",
            "last_nonlaunch_status": "blocked",
            "last_nonlaunch_utc": "2026-03-19T00:00:00Z",
            "last_nonlaunch_run_id": "sj_old_nonlaunch_001",
            "last_nonlaunch_receipt_path": "/stale/nonlaunch.json",
            "last_nonlaunch_report_path": "/stale/nonlaunch.md",
            "last_nonlaunch_signature": "stale-signature",
        },
        repo_root=tmp_path,
    )
    claim_path = sched._claim_path("cancel_truth_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "cancel_truth_001",
                "run_id": run_id,
                "pid": os.getpid(),
                "claimed_utc": sched._utc_iso(),
            }
        ),
        encoding="utf-8",
    )

    result = sched.cancel_job("cancel_truth_001", reason="operator_test", repo_root=tmp_path)
    state = sched.load_state("cancel_truth_001", repo_root=tmp_path)

    assert result["ok"] is True
    assert state["active_run_id"] == ""
    assert state["last_run_id"] == run_id
    assert state["last_status"] == "cancelled"
    assert state["last_receipt_path"] == result["receipt_path"]
    assert state["last_nonlaunch_reason"] == ""
    assert state["last_nonlaunch_receipt_path"] == ""
    assert state["last_nonlaunch_signature"] == ""
    assert claim_path.exists() is False


def test_pipeline_full_auto_review_state_preserves_pipeline_terminal_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched, "_runtime_backend_telemetry", lambda: {})
    monkeypatch.setattr(sched, "_compiled_execution_shadow_bundle_for_job", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        sched,
        "_execute_build_verify_loop_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy scheduler loop should not run for pipeline full_auto")),
    )
    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex controller should not be used for pipeline full_auto")),
    )

    proof_dir = tmp_path / "pipeline-proof-review"
    proof_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths = {}
    for name in [
        "execution_contract_path",
        "validated_artifact_path",
        "apply_result_path",
        "contract_verification_path",
        "repair_decision_path",
        "finalization_path",
    ]:
        p = proof_dir / f"{name}.json"
        p.write_text("{}\n", encoding="utf-8")
        artifact_paths[name] = str(p)
    summary_json = proof_dir / "summary.json"
    summary_md = proof_dir / "summary.md"
    summary_json.write_text("{}\n", encoding="utf-8")
    summary_md.write_text("summary\n", encoding="utf-8")

    def _fake_run_full_auto(**kwargs):
        return {
            "run_id": "RUN-PIPE-REVIEW-001",
            "status": "WAITING_REVIEW",
            "finalization_state": {"final_status": "needs_review"},
            "repair_decision_summary": {"retry_authorization_state": "operator_review", "blocking_reason_codes": []},
            "controller": {"best_run_id": "RUN-PIPE-REVIEW-001", "stop_reason": {"reason_code": "needs_review"}},
            "summary_paths": {"json_path": str(summary_json), "markdown_path": str(summary_md)},
            "artifacts": dict(artifact_paths),
        }

    pipeline_automation = types.ModuleType("infra.pipeline.automation")
    pipeline_automation.run_full_auto = _fake_run_full_auto
    monkeypatch.setitem(sys.modules, "infra.pipeline.automation", pipeline_automation)

    sched.save_job(
        {
            "job_id": "pipeline_review_truth_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "Implement and verify the bounded fix.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 900, "post_success_commit_tag": False},
            "runtime": {
                "executor_agent_tech": "pipeline",
                "pipeline_executor_mode": "full_auto",
                "agent_tech": "deepseek",
                "model": "deepseek-chat",
            },
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 2,
                "pass_marker": "VERDICT: PASS",
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "pipeline_review_truth_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=True,
        now_utc="2026-03-08T04:00:00Z",
    )

    receipt = sched.load_receipt("pipeline_review_truth_001", result["run_id"], repo_root=tmp_path)
    state = sched.load_state("pipeline_review_truth_001", repo_root=tmp_path)

    assert result["ok"] is False
    assert receipt["status"] == "failed_verification"
    assert receipt["pipeline_summary_status"] == "WAITING_REVIEW"
    assert receipt["pipeline_final_status"] == "needs_review"
    assert receipt["pipeline_retry_authorization_state"] == "operator_review"
    assert receipt["pipeline_execution"]["scheduler_projection"]["summary_status"] == "WAITING_REVIEW"
    assert state["last_status"] == "failed_verification"
    assert state["last_no_auto_retry"] is True
    assert state["last_pipeline_summary_status"] == "WAITING_REVIEW"
    assert state["last_pipeline_final_status"] == "needs_review"
    assert state["last_pipeline_retry_authorization_state"] == "operator_review"
    assert state["last_reason_code"] == "needs_review"
