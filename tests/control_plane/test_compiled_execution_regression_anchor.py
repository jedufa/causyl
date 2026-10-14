from __future__ import annotations

from pathlib import Path

import pytest

from infra.controlplane import compiled_execution_ir as cei
from infra.controlplane import scheduled_jobs as sched


pytestmark = [pytest.mark.regression, pytest.mark.integration]


def test_compiled_execution_regression_anchor_valid_single_turn_shadow_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda **kwargs: {"ok": True, "assistant": {"content": "bounded analysis reply", "status": "ok", "meta": {}}},
    )
    sched.save_job(
        {
            "job_id": "compiled_regression_valid_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "abc"},
            "prompt": "Explain the bound work.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T17:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_regression_valid_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T17:05:00Z")
    assert result["ok"] is True
    receipt = sched.load_receipt("compiled_regression_valid_001", result["run_id"], repo_root=tmp_path)
    assert receipt["compiled_execution"]["enabled"] is True
    assert receipt["compiled_execution"]["execution_mode"] == "shadow_compile"
    persistence = receipt["compiled_execution"]["artifact_persistence"]
    assert Path(persistence["paths"]["compiled_execution_shadow"]).exists()
    assert Path(persistence["paths"]["compiled_prompts"]["generalist"]).exists()


def test_compiled_execution_regression_anchor_invalid_ir_fails_closed() -> None:
    result = cei.validate_execution_ir(
        {
            "ir_version": cei.IR_VERSION,
            "task_id": "bad_ir",
            "title": "Bad IR",
            "goal": "Write files",
            "constraints": [],
            "required_context_bundle": "lab_core_v1",
            "additional_context_refs": [],
            "forbidden_context_refs": [],
            "required_output_kind": "unified_diff",
            "allowed_files": [],
            "validation_plan": [],
            "claim_scope": "bounded_repo_change",
            "mode": "build",
            "metadata": {},
        }
    )
    assert result["ok"] is False
    codes = {row["code"] for row in result["errors"]}
    assert "missing_allowed_files" in codes
    assert "missing_validation_plan" in codes


def test_compiled_execution_regression_anchor_malformed_verifier_output_is_visible_and_terminal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None):
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": "Changed files:\n- app.py\n\nValidation Commands Run:\n- pytest -q tests/foo.py\n",
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": "Verdict: FAIL\nnot canonical", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})
    sched.save_job(
        {
            "job_id": "compiled_regression_verifier_bad_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-DE3A"},
            "prompt": "Verify the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T17:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_regression_verifier_bad_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T17:05:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("compiled_regression_verifier_bad_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    cls = receipt["loop"]["iterations"][0]["verifier"]["meta"]["compiled_output_classification"]
    assert cls["ok"] is False
    assert cls["reason"] in {"missing_verdict_header", "verdict_not_on_first_line"}
    persistence = receipt["compiled_execution"]["artifact_persistence"]
    assert Path(persistence["paths"]["compiled_execution_shadow"]).exists()
    assert Path(persistence["paths"]["compiled_classifications"]).exists()


def test_compiled_execution_regression_anchor_structural_failure_emits_retry_disposition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0}

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None):
        if role == "builder":
            seen["builder"] += 1
            return {"ok": True, "assistant": {"content": "unstructured builder output", "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nirrelevant", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})
    sched.save_job(
        {
            "job_id": "compiled_regression_structural_retry_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-32B6"},
            "prompt": "Verify the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T17:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_regression_structural_retry_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T17:05:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("compiled_regression_structural_retry_001", result["run_id"], repo_root=tmp_path)
    assert receipt["compiled_retry_disposition"]["source"] == "compiled_structural_policy"
    assert receipt["job_retry"]["final_failure_class"] == "proof_gap"
    assert 1 <= seen["builder"] <= 2
