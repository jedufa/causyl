from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from infra.pipeline.core import build_envelope, load_json, save_json_atomic, utc_now_iso
from infra.pipeline.diff_utils import repair_unified_diff_hunk_headers
from infra.pipeline.pipeline_runner import DEFAULT_PIPELINE, PipelineRunner
from infra.pipeline.stages import contract_verify as contract_verify_stage
from infra.pipeline.stages import patch_apply as patch_apply_stage
from infra.pipeline.stages import runtime_verify as runtime_verify_stage
from infra.pipeline.stages import static_verify as static_verify_stage

REAL_REPO_ROOT = Path(__file__).resolve().parents[2]


def _case_ids(label: str) -> tuple[str, str]:
    token = uuid4().hex[:10]
    return (f"JOB-{label}-{token}", f"RUN-{label}-{token}")


def _git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _init_python_repo(tmp_path: Path, *, initial_return: str = "hello") -> Path:
    repo_root = tmp_path / "target_repo"
    repo_root.mkdir()
    _git(["init", "-q"], cwd=repo_root)
    _git(["config", "user.email", "test@example.com"], cwd=repo_root)
    _git(["config", "user.name", "Verification MVP Test"], cwd=repo_root)
    (repo_root / "app.py").write_text(
        "def greet() -> str:\n"
        f"    return {initial_return!r}\n",
        encoding="utf-8",
    )
    tests_dir = repo_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(
        "from app import greet\n\n"
        "def test_greet():\n"
        "    assert greet() == 'hello world'\n",
        encoding="utf-8",
    )
    _git(["add", "."], cwd=repo_root)
    _git(["commit", "-q", "-m", "init"], cwd=repo_root)
    return repo_root


def _line_count(text: str) -> int:
    count = len(text.splitlines())
    return count if count > 0 else 1


def _bundle_file(path: str, content: str, *, role: str, edit_allowed: bool) -> dict:
    return {
        "path": path,
        "role": role,
        "bundle_role": role,
        "file_role": "test_code" if path.startswith("tests/") else "implementation_code",
        "candidate_role": "validation_code" if path.startswith("tests/") else "implementation_code",
        "ownership_role": "validation_owner" if path.startswith("tests/") else "core_owner",
        "selection_lane": "implementation_target" if edit_allowed else "validation_target",
        "edit_allowed": edit_allowed,
        "selection_score": 9.0,
        "targetability_score": 9.0 if edit_allowed else 3.0,
        "reference_value_score": 2.0 if edit_allowed else 6.0,
        "semantic_rescued": False,
        "selection_reasons": ["selected for verification MVP test fixture"],
        "responsibility": ["execution_contract"],
        "covers_failure_modes": ["system_failure_modes"],
        "subsystems": ["verification"],
        "coverage_detail": {
            "score": 1.0,
            "matched_failure_categories": ["system_failure_modes"],
            "matched_invariant_buckets": ["execution_contract"],
            "matched_cross_cutting_flags": ["validation_rigor"],
            "matched_subsystems": ["verification"],
            "matched_query_coverage": ["verification"],
            "coverage_breadth": 4,
        },
        "passive_relevance": {"score": 0.0, "categories": [], "reasons": []},
        "ownership_confidence": {"score": 0.9, "notes": ["fixture owner"]},
        "header_context": None,
        "snippets": [
            {
                "snippet_id": f"{path}::1-{_line_count(content)}::1",
                "kind": "line_window",
                "snippet_role": "primary_edit" if edit_allowed else "validation_context",
                "symbol": None,
                "start_line": 1,
                "end_line": _line_count(content),
                "allowed_edit_region": {"start_line": 1, "end_line": _line_count(content)} if edit_allowed else None,
                "edit_priority": "high" if edit_allowed else "medium",
                "content": content,
                "selected_because": "verification MVP fixture",
            }
        ],
    }


def _make_unified_diff(path: str, before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if not lines:
        return f"diff --git a/{path} b/{path}\n"
    return repair_unified_diff_hunk_headers("\n".join([f"diff --git a/{path} b/{path}", *lines]) + "\n")


def _stage11_contract(*, required: bool = True, checks: list[dict] | None = None) -> dict:
    resolved_checks = list(checks) if checks is not None else [
        {
            "name": "python_compile_scope_files",
            "type": "python_compile_scope_files",
            "required": True,
            "timeout_seconds": 300,
        }
    ]
    return {
        "required": required,
        "blocked_apply_statuses": ["conflict", "rejected", "skipped"],
        "checks": resolved_checks,
    }


def _stage12_contract(*, required_tests: list[str], required: bool | None = None, checks: list[dict] | None = None, require_static_pass: bool = True) -> dict:
    resolved_required = bool(required if required is not None else bool(required_tests or checks))
    resolved_checks = list(checks or [])
    if not resolved_checks and required_tests and required is None:
        resolved_checks = [
            {
                "name": f"required_test_{idx}",
                "type": "command",
                "command": command,
                "required": True,
                "timeout_seconds": 900,
            }
            for idx, command in enumerate(required_tests, start=1)
        ]
    return {
        "required": resolved_required,
        "require_static_pass": require_static_pass,
        "blocked_apply_statuses": ["conflict", "rejected", "skipped"],
        "checks": resolved_checks,
    }


def _stage13_contract(
    *,
    require_static_success: bool,
    require_runtime_success: bool,
    fail_closed_on_missing_evidence: bool = True,
    enforce_required_files_touched: bool = True,
    enforce_scope: bool = True,
) -> dict:
    return {
        "require_static_success": require_static_success,
        "require_runtime_success": require_runtime_success,
        "fail_closed_on_missing_evidence": fail_closed_on_missing_evidence,
        "enforce_required_files_touched": enforce_required_files_touched,
        "enforce_scope": enforce_scope,
    }


def _write_stage7_artifacts(
    run_dir: Path,
    repo_root: Path,
    *,
    job_id: str,
    run_id: str,
    modify_files: list[str] | None = None,
    allowed_edit_files: list[str] | None = None,
    required_tests: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    expected_new_files: list[str] | None = None,
    stage11_contract: dict | None = None,
    stage12_contract: dict | None = None,
    stage13_contract: dict | None = None,
    acceptance_criteria: list[str] | None = None,
    job_acceptance_criteria: list[str] | None = None,
    execution_mode: str = "patch_generation",
    generation_applicable: bool = True,
) -> None:
    app_text = (repo_root / "app.py").read_text(encoding="utf-8")
    test_text = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    modify_files = list(modify_files or ["app.py"])
    allowed_edit_files = list(allowed_edit_files or modify_files)
    required_tests = list(required_tests) if required_tests is not None else ["python3 -m pytest -q tests/test_app.py"]
    forbidden_paths = list(forbidden_paths) if forbidden_paths is not None else ["tests/test_app.py"]
    expected_new_files = list(expected_new_files or [])
    acceptance_criteria = list(acceptance_criteria) if acceptance_criteria is not None else ["The greeting behavior is updated without touching forbidden files."]
    job_acceptance_criteria = list(job_acceptance_criteria) if job_acceptance_criteria is not None else ["the greeting behavior changes exactly as requested"]
    stage11 = dict(stage11_contract or _stage11_contract(required=True))
    stage12 = dict(stage12_contract or _stage12_contract(required_tests=required_tests))
    stage13 = dict(stage13_contract or _stage13_contract(require_static_success=bool(stage11.get("required", False)), require_runtime_success=bool(stage12.get("required", False))))

    patch_plan = {
        "objective": "Update the greeting behavior inside the allowed source file.",
        "modify_files": [
            {
                "path": path,
                "intended_modifications": ["Adjust the greeting logic"],
                "rationale": ["Owner file for the requested behavior"],
            }
            for path in modify_files
        ],
        "non_goals": [],
        "assumptions": [],
        "unresolved_uncertainties": [],
        "required_tests": required_tests,
        "should_escalate": False,
        "planning_confidence": 0.92,
        "execution_status": "READY_FOR_EXECUTION" if generation_applicable else "NEEDS_MORE_CONTEXT",
        "output_contract_mode": "patch_contract",
        "acceptance_criteria": acceptance_criteria,
        "must_not_change": forbidden_paths,
        "files_read_only_context": ["tests/test_app.py"],
        "forbidden_targets": forbidden_paths,
        "risks": [],
    }
    stage7_result = {
        "job_contract": {
            "job_id": job_id,
            "repo_root": str(repo_root),
            "title": "Update greeting behavior",
            "description": "Apply a bounded greeting change inside the allowed source file.",
            "objective": "Update the greeting behavior.",
            "constraints": ["modify only the allowed source file"],
            "acceptance_criteria": job_acceptance_criteria,
            "issue_context": {"changed_files_hint": modify_files},
            "forbidden_paths": forbidden_paths,
        },
        "task_profile": {
            "primary_task_type": "bugfix",
            "secondary_tags": ["verification_mvp"],
            "task_families": ["verification_mvp"],
            "cross_cutting_flags": ["validation_rigor"],
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [
                _bundle_file("app.py", app_text, role="primary_target", edit_allowed=True),
                _bundle_file("tests/test_app.py", test_text, role="paired_test", edit_allowed=False),
            ],
            "editable_targets": [_bundle_file("app.py", app_text, role="primary_target", edit_allowed=True)],
            "read_only_context": [_bundle_file("tests/test_app.py", test_text, role="paired_test", edit_allowed=False)],
            "planning_prompt_material": {
                "issue_summary": "Update the greeting behavior.",
                "constraints": ["modify only the allowed source file"],
                "acceptance_criteria": job_acceptance_criteria,
                "replace_spec": None,
                "selection_mode": "targeted_ranked_context",
                "task_archetype": "implementation_change",
                "output_contract_mode": "patch_contract",
                "editable_target_paths": ["app.py"],
                "read_only_context_paths": ["tests/test_app.py"],
            },
            "bundle_metrics": {
                "file_count": 2,
                "editable_target_count": 1,
                "read_only_context_count": 1,
                "snippet_count": 2,
                "total_chars": len(app_text) + len(test_text),
                "truncated": False,
            },
            "bundle_confidence": 0.91,
            "bundle_ready_for_execution": generation_applicable,
            "bundle_ready_for_task": True,
            "bundle_role_counts": {"primary_target": 1, "paired_test": 1},
            "file_role_counts": {"implementation_code": 1, "test_code": 1},
            "bundle_gap_warning": False,
            "bundle_gap_details": [],
            "coverage_validation": {
                "bundle_ready_for_execution": generation_applicable,
                "weakness_reasons": [],
                "editable_target_count": 1,
                "read_only_context_count": 1,
            },
            "bundle_weakness_reasons": [],
            "truncation_notes": [],
        },
        "patch_plan": patch_plan,
    }
    patch_plan_envelope = build_envelope(
        stage="script_7_patch_plan",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result=stage7_result,
        warnings=[],
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "07_patch_plan.json", patch_plan_envelope)

    execution_contract = {
        "task_mode": "implementation_change",
        "execution_mode": execution_mode,
        "output_contract_mode": "patch_contract",
        "objective": patch_plan["objective"],
        "generation_applicable": generation_applicable,
        "generation_blocked_reason": None if generation_applicable else "operator review required",
        "allowed_edit_files": allowed_edit_files,
        "fallback_selection_used": False,
        "forbidden_edit_files": forbidden_paths,
        "must_not_create_files": True,
        "may_create_files": False,
        "expected_new_files": expected_new_files,
        "included_files": [
            {
                "path": "app.py",
                "file_role": "implementation_code",
                "bundle_role": "primary_target",
                "include_mode": "full_file",
                "reason_for_inclusion": "approved edit target from patch plan",
            },
            {
                "path": "tests/test_app.py",
                "file_role": "test_code",
                "bundle_role": "paired_test",
                "include_mode": "snippets_only",
                "reason_for_inclusion": "supporting validation context",
            },
        ],
        "generation_files": [
            {
                "path": "app.py",
                "include_mode": "full_file",
                "content": app_text,
                "truncated": False,
            },
            {
                "path": "tests/test_app.py",
                "include_mode": "snippets_only",
                "snippets": [
                    {
                        "start_line": 1,
                        "end_line": _line_count(test_text),
                        "symbol": None,
                        "snippet_role": "validation_context",
                        "content": test_text,
                    }
                ],
                "truncated": False,
            },
        ],
        "constraints": {
            "acceptance_criteria": patch_plan["acceptance_criteria"],
            "required_tests": required_tests,
            "must_not_change": forbidden_paths,
            "assumptions": [],
            "unresolved_uncertainties": [],
            "execution_risks": [],
        },
        "stage8_output_contract": {
            "required_patch_artifact_type": "unified_diff",
            "allow_full_file_replacements": False,
            "allow_zip_artifacts": False,
            "return_prose": False,
        },
        "stage9_validation_requirements": {
            "diff_must_parse": True,
            "paths_must_normalize_under_repo_root": True,
            "no_out_of_scope_paths": True,
            "no_absolute_paths": True,
            "no_path_traversal": True,
            "artifact_size_below_limit": True,
            "git_apply_check_required": True,
            "artifact_size_limit_chars": 40000,
        },
        "stage10_apply_requirements": {
            "isolated_workspace_required": True,
            "source_repo_mutation_forbidden": True,
            "changed_files_must_be_enumerated": True,
            "apply_conflict_must_fail_closed": True,
        },
        "stage11_static_verification": stage11,
        "stage12_runtime_verification": stage12,
        "stage13_contract_verification": stage13,
        "prompt_budget": {
            "file_count": 2,
            "full_file_count": 1,
            "snippet_count": 1,
            "reference_only_count": 0,
            "estimated_prompt_size_chars": len(app_text) + len(test_text),
            "estimated_prompt_tokens": 64,
            "total_chars": len(app_text) + len(test_text),
            "max_total_chars": 40000,
            "degradation_policy": "none",
            "degradation_applied": False,
            "degradation_actions": [],
        },
    }
    execution_payload = {
        "job_id": job_id,
        "run_id": run_id,
        "stage": "script_7_patch_plan",
        "source_patch_plan_artifact": "07_patch_plan.json",
        "source_context_bundle_artifact": "06_context_bundle.json",
        "execution_contract": execution_contract,
    }
    save_json_atomic(run_dir / "07_execution_contract.json", execution_payload)


def _write_validated_artifact(run_dir: Path, patch_text: str, target_files: list[str], *, job_id: str, run_id: str) -> None:
    validated = {
        "artifact_type": "unified_diff",
        "normalized_content": patch_text,
        "target_files": list(target_files),
        "validation_checks": [{"name": "fixture", "status": "passed", "message": "fixture artifact"}],
        "is_valid": True,
    }
    envelope = build_envelope(
        stage="script_9_artifact_validate",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result={"validated_artifact": validated},
        warnings=[],
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "09_validated_artifact.json", envelope)


def _write_apply_result(run_dir: Path, *, job_id: str, run_id: str, apply_status: str, workspace_path: str, changed_files: list[str] | None = None) -> None:
    envelope = build_envelope(
        stage="script_10_patch_apply",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result={
            "apply_result": {
                "apply_status": apply_status,
                "changed_files": list(changed_files or []),
                "workspace_path": workspace_path,
                "logs": f"fixture {apply_status}",
                "patch_stats": {"files_changed": len(list(changed_files or [])), "insertions": 1, "deletions": 1},
            }
        },
        warnings=[],
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "10_apply_result.json", envelope)


def _summary(executed_checks: list[dict]) -> dict:
    passed = sum(1 for item in executed_checks if item.get("status") == "passed")
    failed = sum(1 for item in executed_checks if item.get("status") == "failed")
    skipped = sum(1 for item in executed_checks if item.get("status") == "skipped")
    return {"passed": passed, "failed": failed, "skipped": skipped}


def _write_static_result(
    run_dir: Path,
    *,
    job_id: str,
    run_id: str,
    status: str,
    required: bool,
    workspace_path: str,
    diagnostics: list[dict] | None = None,
    executed_checks: list[dict] | None = None,
) -> None:
    checks = list(executed_checks or [])
    envelope = build_envelope(
        stage="script_11_verify_static",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result={
            "static_verification": {
                "static_verification_status": status,
                "required": required,
                "workspace_path": workspace_path,
                "source_workspace_path": workspace_path,
                "profile_source": "fixture",
                "executed_checks": checks,
                "summary": _summary(checks),
                "diagnostics": list(diagnostics or []),
                "artifact_lineage": {"run_dir": str(run_dir.resolve())},
                "overall_status": status,
                "checks": checks,
                "failed_checks": [str(item.get("name") or "") for item in checks if item.get("status") == "failed"],
            }
        },
        warnings=[],
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "11_static_verification.json", envelope)


def _write_runtime_result(
    run_dir: Path,
    *,
    job_id: str,
    run_id: str,
    status: str,
    required: bool,
    workspace_path: str,
    diagnostics: list[dict] | None = None,
    executed_checks: list[dict] | None = None,
    require_static_pass: bool = True,
) -> None:
    checks = list(executed_checks or [])
    envelope = build_envelope(
        stage="script_12_verify_runtime",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result={
            "runtime_verification": {
                "runtime_verification_status": status,
                "required": required,
                "require_static_pass": require_static_pass,
                "workspace_path": workspace_path,
                "source_workspace_path": workspace_path,
                "profile_source": "fixture",
                "executed_checks": checks,
                "summary": _summary(checks),
                "diagnostics": list(diagnostics or []),
                "artifact_lineage": {"run_dir": str(run_dir.resolve())},
                "overall_status": status,
                "test_runs": checks,
                "build_runs": [],
            }
        },
        warnings=[],
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "12_runtime_verification.json", envelope)


def _run_stage10(run_dir: Path) -> int:
    return patch_apply_stage.main([
        "--input",
        str(run_dir / "09_validated_artifact.json"),
        "--output",
        str(run_dir / "10_apply_result.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ])


def _run_stage11(run_dir: Path) -> int:
    return static_verify_stage.main([
        "--input",
        str(run_dir / "10_apply_result.json"),
        "--output",
        str(run_dir / "11_static_verification.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ])


def _run_stage12(run_dir: Path) -> int:
    return runtime_verify_stage.main([
        "--input",
        str(run_dir / "11_static_verification.json"),
        "--output",
        str(run_dir / "12_runtime_verification.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ])


def _run_stage13(run_dir: Path) -> int:
    return contract_verify_stage.main([
        "--input",
        str(run_dir / "12_runtime_verification.json"),
        "--output",
        str(run_dir / "13_contract_verification.json"),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ])


def _result(path: Path, key: str) -> dict:
    return dict(load_json(path).get("result", {}).get(key) or {})


@pytest.fixture()
def python_repo(tmp_path: Path) -> Path:
    return _init_python_repo(tmp_path)


def test_stage11_passes_after_clean_apply_with_default_compile(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage11-pass")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0

    result = _result(run_dir / "11_static_verification.json", "static_verification")
    assert result["static_verification_status"] == "passed"
    assert result["summary"]["passed"] == 1


def test_stage11_fails_when_static_compile_check_fails(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage11-fail")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str\n    return 'broken'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0

    result = _result(run_dir / "11_static_verification.json", "static_verification")
    assert result["static_verification_status"] == "failed"
    assert any(item["reason_code"] == "static_check_failed" for item in result["diagnostics"])


def test_stage11_fails_when_required_profile_is_missing(tmp_path: Path, python_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _case_ids("stage11-missing-profile")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("PIPELINE_STAGES__SCRIPT_11_VERIFY_STATIC__ENABLE_DEFAULT_PYTHON_COMPILE", "false")
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        stage11_contract=_stage11_contract(required=True, checks=[]),
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0

    result = _result(run_dir / "11_static_verification.json", "static_verification")
    assert result["static_verification_status"] == "failed"
    assert any(item["reason_code"] == "missing_static_profile" for item in result["diagnostics"])


def test_stage11_skips_when_apply_conflicted(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage11-conflict")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="conflict", workspace_path=str(python_repo), changed_files=[])

    assert _run_stage11(run_dir) == 0

    result = _result(run_dir / "11_static_verification.json", "static_verification")
    assert result["static_verification_status"] == "skipped"
    assert any(item["reason_code"] == "apply_not_clean" for item in result["diagnostics"])


def test_stage12_passes_after_static_success(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage12-pass")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    result = _result(run_dir / "12_runtime_verification.json", "runtime_verification")
    assert result["runtime_verification_status"] == "passed"
    assert result["summary"]["passed"] == 1


def test_stage12_fails_when_runtime_check_fails(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage12-fail")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'bonjour'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    result = _result(run_dir / "12_runtime_verification.json", "runtime_verification")
    assert result["runtime_verification_status"] == "failed"
    failing_tests = result["executed_checks"][0].get("failing_tests") or []
    assert "tests/test_app.py::test_greet" in failing_tests
    assert any(item["reason_code"] == "runtime_check_failed" for item in result["diagnostics"])


def test_stage12_fails_when_required_runtime_profile_is_missing(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage12-missing-profile")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=True, checks=[]),
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    result = _result(run_dir / "12_runtime_verification.json", "runtime_verification")
    assert result["runtime_verification_status"] == "failed"
    assert any(item["reason_code"] == "missing_runtime_profile" for item in result["diagnostics"])


def test_stage12_skips_when_static_verification_failed(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage12-static-block")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str\n    return 'broken'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    result = _result(run_dir / "12_runtime_verification.json", "runtime_verification")
    assert result["runtime_verification_status"] == "skipped"
    assert any(item["reason_code"] == "static_verification_not_passed" for item in result["diagnostics"])


def test_stage13_satisfied_when_scope_and_required_evidence_match(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-pass")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0
    assert _run_stage13(run_dir) == 0

    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "satisfied"
    assert result["decision"]["status"] == "satisfied"


def test_stage13_violated_for_out_of_scope_changed_file(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-scope")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id, allowed_edit_files=["app.py"], modify_files=["app.py"])
    before_app = (python_repo / "app.py").read_text(encoding="utf-8")
    before_test = (python_repo / "tests" / "test_app.py").read_text(encoding="utf-8")
    after_app = "def greet() -> str:\n    return 'hello world'\n"
    after_test = before_test + "\n# out-of-scope comment\n"
    patch_text = _make_unified_diff("app.py", before_app, after_app) + _make_unified_diff("tests/test_app.py", before_test, after_test)
    _write_validated_artifact(run_dir, patch_text, ["app.py", "tests/test_app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0
    assert _run_stage13(run_dir) == 0

    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert "tests/test_app.py" in result["scope_assessment"]["unexpected_files_touched"]


def test_stage13_inconclusive_when_required_runtime_evidence_is_missing(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-missing-runtime")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=True, checks=[]),
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0
    assert _run_stage13(run_dir) == 0

    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "inconclusive"
    assert "missing_runtime_evidence" in result["decision"]["reason_codes"]


def test_stage13_never_satisfied_when_apply_conflicted(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-apply-conflict")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id, required_tests=[])
    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="conflict", workspace_path=str(python_repo), changed_files=[])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), diagnostics=[{"reason_code": "apply_not_clean", "message": "skipped", "evidence": "conflict"}], executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), diagnostics=[{"reason_code": "apply_not_clean", "message": "skipped", "evidence": "conflict"}], executed_checks=[])

    assert _run_stage13(run_dir) == 0

    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] != "satisfied"
    assert "apply_not_clean" in result["decision"]["reason_codes"]


def test_stage13_violated_when_required_tests_did_not_run(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-tests-missing")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    required_tests = ["python3 -m pytest -q tests/test_app.py"]
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id, required_tests=required_tests)
    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(
        run_dir,
        job_id=job_id,
        run_id=run_id,
        status="passed",
        required=True,
        workspace_path=str(python_repo),
        executed_checks=[{"name": "python_compile_scope_files", "command": ["python3", "-m", "py_compile", "app.py"], "exit_code": 0, "duration_ms": 1, "status": "passed", "log_path": str(run_dir / "logs/static.log")}],
    )
    _write_runtime_result(
        run_dir,
        job_id=job_id,
        run_id=run_id,
        status="passed",
        required=True,
        workspace_path=str(python_repo),
        executed_checks=[{"name": "smoke", "command": ["python3", "-c", "print('ok')"], "exit_code": 0, "duration_ms": 1, "status": "passed", "log_path": str(run_dir / "logs/runtime.log"), "failing_tests": []}],
    )

    assert _run_stage13(run_dir) == 0

    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert "required_tests_missing" in result["decision"]["reason_codes"]


def test_stage11_rejects_runtime_commands_in_static_profile(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage11-static-command-guard")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        stage11_contract=_stage11_contract(
            required=True,
            checks=[
                {
                    "name": "runtime_pytest_is_not_static",
                    "type": "command",
                    "command": ["pytest", "-q", "tests/test_app.py"],
                    "required": True,
                    "timeout_seconds": 60,
                }
            ],
        ),
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0

    result = _result(run_dir / "11_static_verification.json", "static_verification")
    assert result["static_verification_status"] == "failed"
    assert any(item["reason_code"] == "invalid_static_profile" for item in result["diagnostics"])


def test_stage11_honors_blocked_apply_statuses_from_contract(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage11-blocked-clean")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        stage11_contract={
            "required": True,
            "blocked_apply_statuses": ["applied_cleanly"],
            "checks": [
                {
                    "name": "python_compile_scope_files",
                    "type": "python_compile_scope_files",
                    "required": True,
                    "timeout_seconds": 60,
                }
            ],
        },
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0

    result = _result(run_dir / "11_static_verification.json", "static_verification")
    assert result["static_verification_status"] == "skipped"
    assert any(item["reason_code"] == "apply_not_clean" for item in result["diagnostics"])


def test_stage12_rejects_inline_python_runtime_commands(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage12-inline-python")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=["python3 -c \"print('nope')\""],
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    result = _result(run_dir / "12_runtime_verification.json", "runtime_verification")
    assert result["runtime_verification_status"] == "failed"
    assert any(item["reason_code"] == "invalid_runtime_profile" for item in result["diagnostics"])


def test_stage12_honors_blocked_apply_statuses_from_contract(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage12-blocked-clean")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        stage12_contract={
            "required": True,
            "require_static_pass": True,
            "blocked_apply_statuses": ["applied_cleanly"],
            "checks": [
                {
                    "name": "required_test_1",
                    "type": "command",
                    "command": ["python3", "-m", "pytest", "-q", "tests/test_app.py"],
                    "required": True,
                    "timeout_seconds": 300,
                }
            ],
        },
    )
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    result = _result(run_dir / "12_runtime_verification.json", "runtime_verification")
    assert result["runtime_verification_status"] == "skipped"
    assert any(item["reason_code"] == "apply_not_clean" for item in result["diagnostics"])


def test_stage13_inconclusive_when_runtime_artifact_is_malformed(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-invalid-runtime")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    runtime_payload = load_json(run_dir / "12_runtime_verification.json")
    del runtime_payload["result"]["runtime_verification"]["summary"]
    save_json_atomic(run_dir / "12_runtime_verification.json", runtime_payload)

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "inconclusive"
    assert "missing_required_runtime_verification" in result["decision"]["reason_codes"]
    assert any(item["reason_code"] == "invalid_runtime_verification" for item in result["diagnostics"])


def test_stage13_inconclusive_when_patch_plan_artifact_is_malformed(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-invalid-patch-plan")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    assert _run_stage10(run_dir) == 0
    assert _run_stage11(run_dir) == 0
    assert _run_stage12(run_dir) == 0

    patch_plan_payload = load_json(run_dir / "07_patch_plan.json")
    del patch_plan_payload["result"]["patch_plan"]["modify_files"]
    save_json_atomic(run_dir / "07_patch_plan.json", patch_plan_payload)

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "inconclusive"
    assert "missing_required_patch_plan" in result["decision"]["reason_codes"]
    assert any(item["reason_code"] == "invalid_patch_plan" for item in result["diagnostics"])


def test_stage13_violated_when_validated_artifact_is_invalid(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-invalid-validated")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(run_dir, python_repo, job_id=job_id, run_id=run_id)
    before = (python_repo / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    validated_payload = load_json(run_dir / "09_validated_artifact.json")
    validated_payload["result"]["validated_artifact"]["is_valid"] = False
    save_json_atomic(run_dir / "09_validated_artifact.json", validated_payload)

    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[{"name": "required_test_1", "command": ["python3", "-m", "pytest", "-q", "tests/test_app.py"], "exit_code": 0, "duration_ms": 1, "status": "passed", "log_path": str(run_dir / "logs/runtime.log"), "failing_tests": []}])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert "validated_artifact_invalid" in result["decision"]["reason_codes"]


def test_stage13_violated_when_expected_new_file_was_not_created(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-expected-new-file")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=False, checks=[]),
        expected_new_files=["new_module.py"],
        allowed_edit_files=["app.py", "new_module.py"],
        acceptance_criteria=[],
        job_acceptance_criteria=[],
    )
    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), executed_checks=[])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert "expected_new_files_missing" in result["decision"]["reason_codes"]
    assert "new_module.py" in result["scope_assessment"]["missing_expected_new_files"]


def test_stage13_prefers_execution_contract_required_test_commands_over_descriptive_patch_plan_entries(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-required-tests-source")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=["focused regression coverage for the updated greeting"],
        acceptance_criteria=[],
        job_acceptance_criteria=[],
    )
    execution_contract_payload = load_json(run_dir / "07_execution_contract.json")
    execution_contract_payload["execution_contract"]["constraints"]["required_tests"] = ["python3 -m pytest -q tests/test_app.py"]
    execution_contract_payload["execution_contract"]["stage12_runtime_verification"] = _stage12_contract(
        required_tests=["python3 -m pytest -q tests/test_app.py"]
    )
    save_json_atomic(run_dir / "07_execution_contract.json", execution_contract_payload)

    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(
        run_dir,
        job_id=job_id,
        run_id=run_id,
        status="passed",
        required=True,
        workspace_path=str(python_repo),
        executed_checks=[
            {
                "name": "required_test_1",
                "command": ["python3", "-m", "pytest", "-q", "tests/test_app.py"],
                "exit_code": 0,
                "duration_ms": 1,
                "status": "passed",
                "log_path": str(run_dir / "logs/runtime.log"),
                "failing_tests": [],
            }
        ],
    )

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "satisfied"
    assert result["acceptance_assessment"]["required_tests_executed"] is True
    assert "required_tests_missing" not in result["decision"]["reason_codes"]


def test_stage13_ignores_non_command_patch_plan_required_test_guidance_when_execution_contract_has_no_runtime_checks(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-descriptive-required-tests")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[
            "targeted regression coverage for the primary owner behavior",
            "integration or smoke validation for the touched execution path",
        ],
        stage11_contract=_stage11_contract(required=False, checks=[]),
        stage12_contract=_stage12_contract(required_tests=[], required=False, checks=[]),
        acceptance_criteria=[],
        job_acceptance_criteria=[],
    )

    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), executed_checks=[])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "satisfied"
    assert result["acceptance_assessment"]["required_tests_present"] is False
    assert result["acceptance_assessment"]["required_tests_executed"] is True
    assert "required_tests_missing" not in result["decision"]["reason_codes"]


def test_stage13_flags_unexpected_exception_behavior_when_objective_names_allowed_exception_types(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-unexpected-exception")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=False, checks=[]),
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=False),
        acceptance_criteria=[],
        job_acceptance_criteria=[],
    )
    patch_plan_payload = load_json(run_dir / "07_patch_plan.json")
    patch_plan_payload["result"]["patch_plan"]["objective"] = "Add max_length support and raise ValueError when max_length is negative."
    patch_plan_payload["result"]["job_contract"]["objective"] = "Add max_length support and raise ValueError when max_length is negative."
    patch_plan_payload["result"]["job_contract"]["description"] = "Add max_length support and raise ValueError when max_length is negative."
    save_json_atomic(run_dir / "07_patch_plan.json", patch_plan_payload)

    execution_contract_payload = load_json(run_dir / "07_execution_contract.json")
    execution_contract_payload["execution_contract"]["objective"] = "Add max_length support and raise ValueError when max_length is negative."
    save_json_atomic(run_dir / "07_execution_contract.json", execution_contract_payload)

    patch_text = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,8 @@\n"
        "-def greet() -> str:\n"
        "-    return 'hello'\n"
        "+def greet(max_length=None) -> str:\n"
        "+    if max_length is not None and not isinstance(max_length, int):\n"
        "+        raise TypeError('max_length must be an integer or None')\n"
        "+    if max_length is not None and max_length < 0:\n"
        "+        raise ValueError('max_length must be non-negative')\n"
        "+    return 'hello'\n"
    )
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), executed_checks=[])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert "unexpected_exception_behavior" in result["decision"]["reason_codes"]
    assert result["scope_assessment"]["unexpected_added_exception_types"] == ["TypeError"]


def test_stage13_accepts_exception_types_implied_by_exact_requested_test_names(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-snake-case-exception-allow")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=False, checks=[]),
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=False),
        acceptance_criteria=[],
        job_acceptance_criteria=[],
    )
    objective = (
        "Update tests/test_app.py by adding these exact tests: "
        "test_negative_max_length_raises_value_error and test_invalid_type_raises_type_error."
    )
    patch_plan_payload = load_json(run_dir / "07_patch_plan.json")
    patch_plan_payload["result"]["patch_plan"]["objective"] = objective
    patch_plan_payload["result"]["job_contract"]["objective"] = objective
    patch_plan_payload["result"]["job_contract"]["description"] = objective
    save_json_atomic(run_dir / "07_patch_plan.json", patch_plan_payload)

    execution_contract_payload = load_json(run_dir / "07_execution_contract.json")
    execution_contract_payload["execution_contract"]["objective"] = objective
    save_json_atomic(run_dir / "07_execution_contract.json", execution_contract_payload)

    patch_text = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,8 @@\n"
        "-def greet() -> str:\n"
        "-    return 'hello'\n"
        "+def greet(value, max_length=None) -> str:\n"
        "+    if not isinstance(value, str):\n"
        "+        raise TypeError('value must be a string')\n"
        "+    if max_length is not None and max_length < 0:\n"
        "+        raise ValueError('max_length must be non-negative')\n"
        "+    return value if max_length is None else value[:max_length]\n"
    )
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), executed_checks=[])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["scope_assessment"]["unexpected_added_exception_types"] == []
    assert "unexpected_exception_behavior" not in result["decision"]["reason_codes"]


def test_stage13_inconclusive_when_acceptance_criteria_lack_deterministic_evidence(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-acceptance-unsupported")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=False, checks=[]),
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=False),
        acceptance_criteria=["the greeting sounds genuinely friendlier to the end user"],
        job_acceptance_criteria=["the greeting sounds genuinely friendlier to the end user"],
    )
    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="skipped", required=False, workspace_path=str(python_repo), executed_checks=[])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "inconclusive"
    assert "acceptance_criteria_unsupported" in result["decision"]["reason_codes"]
    assert result["acceptance_assessment"]["unsupported_acceptance_criteria"]


def test_stage13_honors_stage13_contract_when_runtime_success_not_required(tmp_path: Path, python_repo: Path) -> None:
    job_id, run_id = _case_ids("stage13-runtime-optional")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_artifacts(
        run_dir,
        python_repo,
        job_id=job_id,
        run_id=run_id,
        required_tests=[],
        stage12_contract=_stage12_contract(required_tests=[], required=True, checks=[]),
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=False),
        acceptance_criteria=[],
        job_acceptance_criteria=[],
    )
    _write_validated_artifact(run_dir, "diff --git a/app.py b/app.py\n", ["app.py"], job_id=job_id, run_id=run_id)
    _write_apply_result(run_dir, job_id=job_id, run_id=run_id, apply_status="applied_cleanly", workspace_path=str(python_repo), changed_files=["app.py"])
    _write_static_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=True, workspace_path=str(python_repo), executed_checks=[])
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="failed", required=True, workspace_path=str(python_repo), diagnostics=[{"reason_code": "runtime_check_failed", "message": "fixture failure"}], executed_checks=[])

    assert _run_stage13(run_dir) == 0
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "satisfied"
    assert "runtime_verification_failed" not in result["decision"]["reason_codes"]


def test_pipeline_runner_stage10_to_stage13_finalizes_on_satisfied_contract(tmp_path: Path) -> None:
    job_id, run_id = _case_ids("runner-satisfied")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)

    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=DEFAULT_PIPELINE[9:13],
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary = runner.run(job_id=job_id, run_id=run_id)

    assert summary["status"] == "FINALIZED"
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "satisfied"


def test_pipeline_runner_stage10_to_stage13_waits_for_retry_on_violated_contract(tmp_path: Path) -> None:
    job_id, run_id = _case_ids("runner-violated")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id, allowed_edit_files=["app.py"], modify_files=["app.py"])
    before_app = (repo_root / "app.py").read_text(encoding="utf-8")
    before_test = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before_app, "def greet() -> str:\n    return 'hello world'\n") + _make_unified_diff("tests/test_app.py", before_test, before_test + "\n# scope drift\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py", "tests/test_app.py"], job_id=job_id, run_id=run_id)

    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=DEFAULT_PIPELINE[9:13],
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary = runner.run(job_id=job_id, run_id=run_id)

    assert summary["status"] == "WAITING_RETRY_DECISION"
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
