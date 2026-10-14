from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from infra.pipeline.core import build_envelope, load_json, save_json_atomic, utc_now_iso
from infra.pipeline.job_state import JobStateStore
from infra.pipeline.pipeline_runner import DEFAULT_PIPELINE, PipelineRunner, PipelineStage
from infra.pipeline.stages import finalize_job as stage15_finalize_job
from infra.pipeline.stages import job_report as stage16_job_report
from infra.pipeline.stages import repair_loop as stage14_repair_loop

REAL_REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE_SEQUENCE = [stage.key for stage in DEFAULT_PIPELINE]


def _ids(label: str) -> tuple[str, str]:
    token = uuid4().hex[:8]
    return f"JOB-{label}-{token}", f"RUN-{label}-{token}"


def _make_run_dir(tmp_path: Path, job_id: str, run_id: str) -> Path:
    run_dir = tmp_path / "jobs" / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def _job_dir(run_dir: Path) -> Path:
    return run_dir.parent.parent


def _write_env(path: Path, stage: str, job_id: str, run_id: str, result: dict) -> None:
    save_json_atomic(
        path,
        build_envelope(
            stage=stage,
            job_id=job_id,
            run_id=run_id,
            input_payload={"seed": True},
            result=result,
            warnings=[],
            tool_version="test",
            started_at_iso=utc_now_iso(),
            finished_at_iso=utc_now_iso(),
        ),
    )


def _result(path: Path, key: str) -> dict:
    return dict(load_json(path).get("result", {}).get(key) or {})


def _write_execution_contract(
    run_dir: Path,
    *,
    static_required: bool = True,
    runtime_required: bool = True,
    require_static_pass: bool = True,
) -> None:
    save_json_atomic(
        run_dir / "07_execution_contract.json",
        {
            "execution_contract": {
                "stage11_static_verification": {
                    "required": bool(static_required),
                },
                "stage12_runtime_verification": {
                    "required": bool(runtime_required),
                    "require_static_pass": bool(require_static_pass),
                },
            }
        },
    )


def _write_apply_result(
    run_dir: Path,
    job_id: str,
    run_id: str,
    *,
    apply_status: str = "applied_cleanly",
    changed_files: list[str] | None = None,
    diagnostics: dict | None = None,
) -> None:
    changed_files = list(changed_files or ["app.py"])
    apply_log = run_dir / "apply.log"
    apply_log.write_text("apply log\n", encoding="utf-8")
    _write_env(
        run_dir / "10_apply_result.json",
        "script_10_patch_apply",
        job_id,
        run_id,
        {
            "apply_result": {
                "apply_status": apply_status,
                "workspace_path": str(run_dir),
                "changed_files": changed_files,
                "logs": str(apply_log),
                "patch_stats": {
                    "files_changed": len(changed_files),
                    "insertions": 1,
                    "deletions": 0,
                },
                "diagnostics": diagnostics or {},
            }
        },
    )


def _write_static_verification(
    run_dir: Path,
    job_id: str,
    run_id: str,
    *,
    status: str = "passed",
    executed_checks: list[dict] | None = None,
    diagnostics: list[dict] | None = None,
) -> None:
    executed_checks = list(executed_checks or [])
    diagnostics = list(diagnostics or [])
    if not executed_checks and status == "passed":
        executed_checks = [
            {
                "name": "python_compile",
                "command": ["python", "-m", "py_compile", "app.py"],
                "status": "passed",
                "exit_code": 0,
                "duration_ms": 5,
                "log_path": None,
            }
        ]
    _write_env(
        run_dir / "11_static_verification.json",
        "script_11_verify_static",
        job_id,
        run_id,
        {
            "static_verification": {
                "static_verification_status": status,
                "required": True,
                "workspace_path": str(run_dir),
                "artifact_lineage": {},
                "executed_checks": executed_checks,
                "summary": {
                    "passed": 1 if status == "passed" else 0,
                    "failed": 1 if status == "failed" else 0,
                    "skipped": 1 if status == "skipped" else 0,
                },
                "diagnostics": diagnostics,
            }
        },
    )


def _write_runtime_verification(
    run_dir: Path,
    job_id: str,
    run_id: str,
    *,
    status: str = "passed",
    test_runs: list[dict] | None = None,
    diagnostics: list[dict] | None = None,
) -> None:
    test_runs = list(test_runs or [])
    diagnostics = list(diagnostics or [])
    if not test_runs and status == "passed":
        test_runs = [
            {
                "name": "pytest_targeted",
                "command": ["pytest", "-q", "tests/test_app.py"],
                "status": "passed",
                "exit_code": 0,
                "duration_ms": 10,
                "log_path": None,
                "failing_tests": [],
            }
        ]
    _write_env(
        run_dir / "12_runtime_verification.json",
        "script_12_verify_runtime",
        job_id,
        run_id,
        {
            "runtime_verification": {
                "runtime_verification_status": status,
                "required": True,
                "require_static_pass": True,
                "workspace_path": str(run_dir),
                "artifact_lineage": {},
                "executed_checks": test_runs,
                "test_runs": test_runs,
                "summary": {
                    "passed": 1 if status == "passed" else 0,
                    "failed": 1 if status == "failed" else 0,
                    "skipped": 1 if status == "skipped" else 0,
                },
                "diagnostics": diagnostics,
            }
        },
    )


def _write_contract_verification(
    run_dir: Path,
    job_id: str,
    run_id: str,
    *,
    status: str = "satisfied",
    allowed_files_only: bool = True,
    changed_files: list[str] | None = None,
    reason_codes: list[str] | None = None,
    passes: bool | None = None,
) -> None:
    changed_files = list(changed_files or ["app.py"])
    if passes is None:
        passes = status == "satisfied"
    _write_env(
        run_dir / "13_contract_verification.json",
        "script_13_contract_verify",
        job_id,
        run_id,
        {
            "contract_verification": {
                "contract_verification_status": status,
                "workspace_path": str(run_dir),
                "passes": bool(passes),
                "decision": {
                    "status": status,
                    "reason_codes": list(reason_codes or []),
                    "summary": status,
                },
                "scope_assessment": {
                    "allowed_files_only": bool(allowed_files_only),
                    "required_files_touched": True,
                    "unexpected_files_touched": [] if allowed_files_only else changed_files,
                    "forbidden_files_touched": [] if allowed_files_only else changed_files,
                    "validated_target_mismatches": [] if allowed_files_only else changed_files,
                },
                "acceptance_assessment": {
                    "required_checks_present": True,
                    "static_passed": True,
                    "runtime_passed": True,
                    "static_required": True,
                    "runtime_required": True,
                    "required_tests_executed": True,
                    "required_tests_present": True,
                    "acceptance_criteria_present": True,
                    "acceptance_criteria_supported": bool(passes),
                    "acceptance_criteria_satisfied": bool(passes),
                    "core_artifacts_valid": True,
                },
                "checks": [],
                "artifact_lineage": {},
                "diagnostics": [],
            }
        },
    )


def _write_repair_artifact(
    run_dir: Path,
    job_id: str,
    run_id: str,
    *,
    retry_count: int = 0,
    retry_limit: int = 2,
) -> None:
    _write_env(
        run_dir / "14_repair_decision.json",
        "script_14_repair_loop",
        job_id,
        run_id,
        {
            "repair_decision": {
                "should_retry": False,
                "retry_reason": "historical repair artifact",
                "retry_mode": "abort",
                "retry_authorization_state": "not_allowed",
                "branch_action": "terminal_abort",
                "recommended_reentry_stage": None,
                "failure_summary": "historical repair artifact",
                "failure_classification": {
                    "primary_hypothesis": "REQUIRED_ARTIFACT_MISSING",
                    "competing_hypotheses": [],
                    "confidence_score": 1.0,
                },
                "rationale_trace": [],
                "minimal_feedback_bundle": {
                    "failing_checks": [],
                    "failing_tests": [],
                    "affected_files": [],
                    "excerpts": [],
                },
                "retry_count": retry_count,
                "retry_limit": retry_limit,
                "operator_decision_required": False,
                "operator_decision_reason": None,
                "approval_items": [],
                "auto_remediation_candidates": [],
                "blocking_reason_codes": ["REQUIRED_ARTIFACT_MISSING"],
            }
        },
    )


def _write_common_clean_artifacts(run_dir: Path, job_id: str, run_id: str) -> None:
    _write_env(
        run_dir / "08_patch_artifact.json",
        "script_8_patch_generate",
        job_id,
        run_id,
        {
            "patch_artifact": {
                "artifact_type": "unified_diff",
                "producer": {"provider": "openai", "model": "gpt-5.3-codex-spark"},
                "target_files": ["app.py"],
                "content": "diff --git a/app.py b/app.py\n",
            },
            "generation_metadata": {
                "estimated_input_tokens": 64,
                "estimated_output_tokens": 12,
                "estimated_total_tokens": 76,
                "execution_source": "fixture",
            },
        },
    )
    _write_execution_contract(run_dir)
    _write_apply_result(run_dir, job_id, run_id)
    _write_static_verification(run_dir, job_id, run_id)
    _write_runtime_verification(run_dir, job_id, run_id)
    _write_contract_verification(run_dir, job_id, run_id)


def _run_stage14(run_dir: Path) -> dict:
    assert stage14_repair_loop.main([
        "--input",
        str(run_dir / "13_contract_verification.json"),
        "--output",
        str(run_dir / "14_repair_decision.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ]) == 0
    return _result(run_dir / "14_repair_decision.json", "repair_decision")


def _run_stage15(run_dir: Path) -> dict:
    assert stage15_finalize_job.main([
        "--input",
        str(run_dir / "14_repair_decision.json"),
        "--output",
        str(run_dir / "15_finalization.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ]) == 0
    return _result(run_dir / "15_finalization.json", "finalization")


def _run_stage16(run_dir: Path) -> dict:
    assert stage16_job_report.main([
        "--input",
        str(run_dir / "15_finalization.json"),
        "--output",
        str(run_dir / "16_job_report.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ]) == 0
    return _result(run_dir / "16_job_report.json", "job_report")


def _initialize_job_state(run_dir: Path, job_id: str, run_id: str) -> JobStateStore:
    store = JobStateStore(_job_dir(run_dir))
    store.initialize_job(
        job_id=job_id,
        branch_id="BRANCH-main",
        run_id=run_id,
        schema_version="2",
        contract_version="1.0",
        stage_sequence=STAGE_SEQUENCE,
    )
    return store


def _set_retry_counts(store: JobStateStore, run_id: str, *, retry_count_total: int, retry_count_in_branch: int | None = None) -> None:
    state = store.load()
    run = dict(state["runs"][run_id])
    retry_context = dict(run.get("retry_context") or {})
    retry_context["retry_count_total"] = int(retry_count_total)
    retry_context["retry_count_in_branch"] = int(retry_count_in_branch if retry_count_in_branch is not None else retry_count_total)
    run["retry_context"] = retry_context
    state["runs"][run_id] = run
    store.save(state)


@pytest.mark.parametrize("label", ["clean-pass"])
def test_stages_14_16_clean_pass(tmp_path: Path, label: str) -> None:
    job_id, run_id = _ids(label)
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_common_clean_artifacts(run_dir, job_id, run_id)

    repair = _run_stage14(run_dir)
    assert repair["should_retry"] is False
    assert repair["retry_mode"] == "none"
    assert repair["blocking_reason_codes"] == []

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "accepted"

    report = _run_stage16(run_dir)
    assert report["final_status"] == "accepted"
    assert "15_finalization.json" in report["artifact_refs"]
    assert report["missing_optional_inputs"] == []
    assert report["quick_read"]
    assert any(str(item).startswith("Status: accepted") for item in report["quick_read"])
    assert any(str(item).startswith("Tokens: ") for item in report["quick_read"])
    assert any(str(item).startswith("Verification: static=passed, runtime=passed, contract=satisfied") for item in report["quick_read"])
    assert report["compact_stage_summary"]
    assert any(item.get("stage") == "7" for item in report["compact_stage_summary"])


def test_stage16_quick_read_surfaces_selected_targets_and_primary_blocker(tmp_path: Path) -> None:
    job_id, run_id = _ids("summary-review")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_common_clean_artifacts(run_dir, job_id, run_id)

    _write_env(
        run_dir / "02_task_profile.json",
        "script_2_task_classifier",
        job_id,
        run_id,
        {
            "task_profile": {
                "task_archetype": "implementation_change",
                "primary_task_type": "bugfix",
                "task_families": ["generic"],
            }
        },
    )
    _write_env(
        run_dir / "05_context_ranked.json",
        "script_5_context_ranker",
        job_id,
        run_id,
        {
            "ranked_context": {
                "implementation_targets": [{"path": "app.py"}],
                "selected_files": [{"path": "app.py"}, {"path": "tests/test_app.py"}],
            }
        },
    )
    _write_env(
        run_dir / "08_patch_artifact.json",
        "script_8_patch_generate",
        job_id,
        run_id,
        {
            "patch_artifact": {
                "artifact_type": "unified_diff",
                "producer": {"provider": "openai", "model": "gpt-5.3-codex-spark"},
                "target_files": ["app.py"],
                "content": "diff --git a/app.py b/app.py\n",
            },
            "generation_metadata": {
                "estimated_input_tokens": 123,
                "estimated_output_tokens": 17,
                "estimated_total_tokens": 140,
                "execution_source": "fixture",
            },
        },
    )
    _write_env(
        run_dir / "07_patch_plan.json",
        "script_7_patch_plan",
        job_id,
        run_id,
        {
            "patch_plan": {
                "modify_files": [],
                "plan_status": "INSUFFICIENT_CONTEXT",
                "plan_outcome": "INSUFFICIENT_CONTEXT",
                "execution_status": "NEEDS_MORE_CONTEXT",
                "unresolved_uncertainties": ["No editable implementation targets survived planning filters"],
            }
        },
    )

    _run_stage14(run_dir)
    _run_stage15(run_dir)
    report = _run_stage16(run_dir)

    assert any(str(item).startswith("Selected targets: app.py") for item in report["quick_read"])
    assert any(str(item).startswith("Blocker: No editable implementation targets survived planning filters") for item in report["quick_read"])
    assert any(str(item).startswith("Tokens: input=123 output=17 estimated") for item in report["quick_read"])
    assert any(
        item.get("stage") == "7" and "blocker=No editable implementation targets survived planning filters" in str(item.get("summary") or "")
        for item in report["compact_stage_summary"]
    )


def test_stage16_prefers_downstream_apply_failure_as_primary_blocker(tmp_path: Path) -> None:
    job_id, run_id = _ids("summary-apply-failure")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_common_clean_artifacts(run_dir, job_id, run_id)

    _write_env(
        run_dir / "02_task_profile.json",
        "script_2_task_classifier",
        job_id,
        run_id,
        {
            "task_profile": {
                "task_archetype": "implementation_change",
                "primary_task_type": "bugfix",
                "task_families": ["generic"],
            }
        },
    )
    _write_env(
        run_dir / "05_context_ranked.json",
        "script_5_context_ranker",
        job_id,
        run_id,
        {
            "ranked_context": {
                "implementation_targets": [{"path": "app.py"}],
                "selected_files": [{"path": "app.py"}],
            }
        },
    )
    _write_env(
        run_dir / "07_patch_plan.json",
        "script_7_patch_plan",
        job_id,
        run_id,
        {
            "patch_plan": {
                "modify_files": [{"path": "app.py"}],
                "plan_status": "READY_FOR_REVIEW",
                "plan_outcome": "READY_FOR_REVIEW",
                "execution_status": "READY_FOR_EXECUTION",
                "unresolved_uncertainties": ["model planning disabled by config; deterministic fallback used"],
            }
        },
    )
    _write_apply_result(
        run_dir,
        job_id,
        run_id,
        apply_status="skipped",
        changed_files=[],
        diagnostics={
            "failure_detail": "fixture mode requested but PIPELINE_STAGE8_PATCH_RESPONSE_FILE is missing",
            "failure_reason_code": "upstream_generation_failed",
            "failure_stage": "script_9_artifact_validate",
        },
    )
    _write_contract_verification(
        run_dir,
        job_id,
        run_id,
        status="violated",
        allowed_files_only=True,
        changed_files=[],
        reason_codes=["upstream_generation_failed"],
        passes=False,
    )

    _run_stage14(run_dir)
    _run_stage15(run_dir)
    report = _run_stage16(run_dir)

    assert any(
        str(item).startswith("Blocker: fixture mode requested but PIPELINE_STAGE8_PATCH_RESPONSE_FILE is missing")
        for item in report["quick_read"]
    )
    assert any(
        item.get("stage") == "7" and "blocker=fixture mode requested but PIPELINE_STAGE8_PATCH_RESPONSE_FILE is missing" in str(item.get("summary") or "")
        for item in report["compact_stage_summary"]
    )


def test_scope_violation_authorizes_strategic_retry(tmp_path: Path) -> None:
    job_id, run_id = _ids("scope")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_execution_contract(run_dir)
    _write_apply_result(run_dir, job_id, run_id, changed_files=["forbidden.py"])
    _write_static_verification(run_dir, job_id, run_id)
    _write_runtime_verification(run_dir, job_id, run_id)
    _write_contract_verification(
        run_dir,
        job_id,
        run_id,
        status="violated",
        allowed_files_only=False,
        changed_files=["forbidden.py"],
        reason_codes=["scope_violation"],
        passes=False,
    )

    repair = _run_stage14(run_dir)
    assert repair["retry_authorization_state"] == "authorized"
    assert repair["operator_decision_required"] is False
    assert repair["branch_action"] == "fork_new_branch"
    assert repair["selected_strategy"] == "plan_revision"
    assert "CONTRACT_SCOPE_VIOLATION" in repair["blocking_reason_codes"]

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "needs_review"
    assert "CONTRACT_SCOPE_VIOLATION" in finalization["decision_basis"]["blocking_reason_codes"]


def test_mechanical_verification_issue_emits_remediation_candidate(tmp_path: Path) -> None:
    job_id, run_id = _ids("mechanical")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_execution_contract(run_dir)
    _write_apply_result(run_dir, job_id, run_id)
    missing_tool_log = run_dir / "missing_tool.log"
    missing_tool_log.write_text("ruff not found\n", encoding="utf-8")
    _write_static_verification(
        run_dir,
        job_id,
        run_id,
        status="failed",
        executed_checks=[
            {
                "name": "ruff_check",
                "command": ["ruff", "check", "."],
                "status": "failed",
                "exit_code": 127,
                "duration_ms": 4,
                "failure_reason": "missing_executable",
                "log_path": str(missing_tool_log),
            }
        ],
    )
    _write_runtime_verification(run_dir, job_id, run_id)
    _write_contract_verification(run_dir, job_id, run_id)

    repair = _run_stage14(run_dir)
    assert repair["retry_mode"] == "patch_repair"
    assert any(item.get("candidate") == "retry_with_repo_local_tool" for item in repair["auto_remediation_candidates"])

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "needs_review"


def test_missing_required_artifact_emits_exact_stage14_abort_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _ids("missing-required")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    monkeypatch.setenv("PIPELINE_JOB_ID", job_id)
    monkeypatch.setenv("PIPELINE_RUN_ID", run_id)

    repair = _run_stage14(run_dir)
    assert repair["should_retry"] is False
    assert repair["retry_mode"] == "abort"
    assert repair["retry_authorization_state"] == "not_allowed"
    assert repair["branch_action"] == "terminal_abort"
    assert "REQUIRED_ARTIFACT_MISSING" in repair["blocking_reason_codes"]

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "aborted"


def test_report_degradation_is_honest_without_rewriting_stage15_truth(tmp_path: Path) -> None:
    job_id, run_id = _ids("report-degradation")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_common_clean_artifacts(run_dir, job_id, run_id)

    _run_stage14(run_dir)
    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "accepted"

    (run_dir / "11_static_verification.json").unlink()
    report = _run_stage16(run_dir)
    assert report["final_status"] == "accepted"
    assert "11_static_verification.json" in report["missing_optional_inputs"]
    assert any(item.get("section") == "static_verification" for item in report["degraded_sections"])


def test_retry_limit_exceeded_forces_abort_and_is_reported_honestly(tmp_path: Path) -> None:
    job_id, run_id = _ids("retry-limit")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_common_clean_artifacts(run_dir, job_id, run_id)
    store = _initialize_job_state(run_dir, job_id, run_id)
    _set_retry_counts(store, run_id, retry_count_total=30)

    repair = _run_stage14(run_dir)
    assert repair["retry_mode"] == "abort"
    assert repair["retry_authorization_state"] == "not_allowed"
    assert repair["branch_action"] == "terminal_abort"
    assert "RETRY_LIMIT_EXHAUSTED" in repair["blocking_reason_codes"]

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "aborted"

    report = _run_stage16(run_dir)
    assert report["final_status"] == "aborted"
    assert any("retry budget exhausted" in item for item in report["residual_risks"])


def test_retry_lineage_disagreement_aborts_cleanly(tmp_path: Path) -> None:
    job_id, run_id = _ids("lineage-disagreement")
    parent_run_id = f"{run_id}-parent"
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    parent_run_dir = _make_run_dir(tmp_path, job_id, parent_run_id)
    _write_common_clean_artifacts(run_dir, job_id, run_id)
    _write_repair_artifact(parent_run_dir, job_id, parent_run_id)

    store = JobStateStore(_job_dir(run_dir))
    store.initialize_job(
        job_id=job_id,
        branch_id="BRANCH-main",
        run_id=parent_run_id,
        schema_version="2",
        contract_version="1.0",
        stage_sequence=STAGE_SEQUENCE,
    )
    store.create_child_run(
        branch_id="BRANCH-main",
        run_id=run_id,
        parent_run_id=parent_run_id,
        reentry_stage="script_14_repair_loop",
        retry_mode="patch_repair",
        retry_reason="retry lineage disagreement fixture",
    )
    _set_retry_counts(store, run_id, retry_count_total=0, retry_count_in_branch=0)

    repair = _run_stage14(run_dir)
    assert "RETRY_LINEAGE_DISAGREEMENT" in repair["blocking_reason_codes"]
    assert repair["retry_authorization_state"] == "not_allowed"
    assert repair["branch_action"] == "terminal_abort"

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "aborted"


def test_silent_absence_of_optional_artifact_aborts_cleanly(tmp_path: Path) -> None:
    job_id, run_id = _ids("silent-absence")
    run_dir = _make_run_dir(tmp_path, job_id, run_id)
    _write_apply_result(run_dir, job_id, run_id)
    _write_static_verification(run_dir, job_id, run_id)
    _write_contract_verification(run_dir, job_id, run_id)

    repair = _run_stage14(run_dir)
    assert "UPSTREAM_EVIDENCE_INCOMPLETE" in repair["blocking_reason_codes"]
    assert repair["retry_authorization_state"] == "not_allowed"
    assert repair["branch_action"] == "terminal_abort"

    finalization = _run_stage15(run_dir)
    assert finalization["final_status"] == "aborted"
    assert "12_runtime_verification.json" in finalization["decision_basis"]["optional_artifacts_missing"]


def test_pipeline_runner_preserves_stage15_truth_when_stage16_fails(tmp_path: Path) -> None:
    job_id, run_id = _ids("runner-stage16-failure")
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_common_clean_artifacts(run_dir, job_id, run_id)

    failing_stage16 = tmp_path / "failing_job_report.py"
    failing_stage16.write_text(
        "from __future__ import annotations\n"
        "raise SystemExit(5)\n",
        encoding="utf-8",
    )

    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=[DEFAULT_PIPELINE[13], DEFAULT_PIPELINE[14], PipelineStage("script_16_job_report", str(failing_stage16))],
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary = runner.run(job_id=job_id, run_id=run_id)

    assert summary["status"] == "FINALIZED"
    assert summary["current_stage"] == "script_15_finalize_job"
    assert not (run_dir / "16_job_report.json").exists()
    state = load_json(jobs_root / job_id / "job_state.json")
    assert state["status"] == "FINALIZED"
    assert state["runs"][run_id]["current_stage"] == "script_15_finalize_job"
    assert _result(run_dir / "15_finalization.json", "finalization")["final_status"] == "accepted"
