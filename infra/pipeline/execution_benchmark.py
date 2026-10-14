from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import os
import shlex
import shutil
import subprocess
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from infra.pipeline.core import build_envelope, ensure_directory, load_json, make_run_id, save_json_atomic, save_text_atomic, utc_now_iso
from infra.pipeline.diff_utils import extract_target_files, normalize_diff_text
from infra.pipeline.path_policy import normalize_repo_relative_path
from infra.pipeline.schemas import validate_stage_output
from infra.pipeline.stages import artifact_validate as stage9_artifact_validate
from infra.pipeline.stages import patch_apply as stage10_patch_apply
from infra.pipeline.stages import static_verify as stage11_static_verify
from infra.pipeline.stages import runtime_verify as stage12_runtime_verify
from infra.pipeline.stages import contract_verify as stage13_contract_verify

STAGE_KEYS = {
    9: "script_9_artifact_validate",
    10: "script_10_patch_apply",
    11: "script_11_verify_static",
    12: "script_12_verify_runtime",
    13: "script_13_contract_verify",
}


class ExecutionBenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True)
class BenchmarkExpectation:
    stage_number: int
    verdict: str
    checks: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkCase:
    key: str
    description: str
    supported_stages: Sequence[int]
    setup: Callable[[Path, str, str], None]
    stage_expectations: Mapping[int, BenchmarkExpectation]

    def expectation_for(self, stage_number: int) -> Optional[BenchmarkExpectation]:
        expectation = self.stage_expectations.get(int(stage_number))
        return expectation if isinstance(expectation, BenchmarkExpectation) else None

    @property
    def terminal_expectation(self) -> BenchmarkExpectation:
        if not self.stage_expectations:
            raise ExecutionBenchmarkError(f"Execution benchmark case `{self.key}` does not declare any expectations")
        terminal_stage = max(int(stage_number) for stage_number in self.stage_expectations)
        expectation = self.expectation_for(terminal_stage)
        if expectation is None:
            raise ExecutionBenchmarkError(f"Execution benchmark case `{self.key}` is missing its terminal expectation")
        return expectation


@dataclass
class StageRunRecord:
    stage_number: int
    stage_key: str
    exit_code: int
    elapsed_seconds: float
    input_artifact_path: str
    artifact_path: str
    stdout_log_path: str
    stderr_log_path: str
    summary: Mapping[str, Any]
    verdict: str
    schema_validated: bool
    envelope_status: Optional[str] = None
    structured_error: Optional[Mapping[str, Any]] = None


def _git(args: Sequence[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _line_count(text: str) -> int:
    count = len(text.splitlines())
    return count if count > 0 else 1


def _summary(checks: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    passed = sum(1 for item in checks if str(item.get("status") or "") == "passed")
    failed = sum(1 for item in checks if str(item.get("status") or "") == "failed")
    skipped = sum(1 for item in checks if str(item.get("status") or "") == "skipped")
    return {"passed": passed, "failed": failed, "skipped": skipped}


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
    return "\n".join([f"diff --git a/{path} b/{path}", *lines]) + "\n"


def _base_python_repo(root: Path, *, app_text: str = "def greet() -> str:\n    return 'hello'\n") -> Path:
    repo_root = root / "target_repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], cwd=repo_root)
    _git(["config", "user.email", "benchmark@example.com"], cwd=repo_root)
    _git(["config", "user.name", "Execution Benchmark"], cwd=repo_root)
    (repo_root / "app.py").write_text(app_text, encoding="utf-8")
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_app.py").write_text(
        "from app import greet\n\n"
        "def test_greet():\n"
        "    assert greet() == 'hello world'\n",
        encoding="utf-8",
    )
    _git(["add", "."], cwd=repo_root)
    _git(["commit", "-q", "-m", "init"], cwd=repo_root)
    return repo_root


def _bundle_file(path: str, content: str, *, role: str, edit_allowed: bool) -> Dict[str, Any]:
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
        "selection_reasons": ["execution benchmark fixture"],
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
        "ownership_confidence": {"score": 0.9, "notes": ["benchmark fixture owner"]},
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
                "selected_because": "execution benchmark fixture",
            }
        ],
    }


def _stage11_contract(required: bool = True, checks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "required": required,
        "blocked_apply_statuses": ["conflict", "rejected", "skipped"],
        "checks": list(checks or [{"name": "python_compile_scope_files", "type": "python_compile_scope_files", "required": True, "timeout_seconds": 300}]),
    }


def _stage12_contract(required_tests: Sequence[str], *, required: Optional[bool] = None, checks: Optional[List[Dict[str, Any]]] = None, require_static_pass: bool = True) -> Dict[str, Any]:
    resolved_checks = list(checks or [])
    if not resolved_checks and required_tests and required is None:
        resolved_checks = [
            {"name": f"required_test_{idx}", "type": "command", "command": command, "required": True, "timeout_seconds": 900}
            for idx, command in enumerate(required_tests, start=1)
        ]
    return {
        "required": bool(required if required is not None else bool(required_tests or resolved_checks)),
        "require_static_pass": require_static_pass,
        "blocked_apply_statuses": ["conflict", "rejected", "skipped"],
        "checks": resolved_checks,
    }


def _stage13_contract(*, require_static_success: bool, require_runtime_success: bool) -> Dict[str, Any]:
    return {
        "require_static_success": require_static_success,
        "require_runtime_success": require_runtime_success,
        "fail_closed_on_missing_evidence": True,
        "enforce_required_files_touched": True,
        "enforce_scope": True,
    }


def _write_stage7_artifacts(
    run_dir: Path,
    repo_root: Path,
    *,
    job_id: str,
    run_id: str,
    modify_files: Optional[Sequence[str]] = None,
    allowed_edit_files: Optional[Sequence[str]] = None,
    forbidden_paths: Optional[Sequence[str]] = None,
    required_tests: Optional[Sequence[str]] = None,
    expected_new_files: Optional[Sequence[str]] = None,
    acceptance_criteria: Optional[Sequence[str]] = None,
    stage11_contract: Optional[Mapping[str, Any]] = None,
    stage12_contract: Optional[Mapping[str, Any]] = None,
    stage13_contract: Optional[Mapping[str, Any]] = None,
) -> None:
    app_text = (repo_root / "app.py").read_text(encoding="utf-8")
    test_text = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    modify_files = list(modify_files or ["app.py"])
    allowed_edit_files = list(allowed_edit_files or modify_files)
    forbidden_paths = list(forbidden_paths or [])
    required_tests = list(required_tests or ["python3 -m pytest -q tests/test_app.py"])
    expected_new_files = list(expected_new_files or [])
    acceptance_criteria = list(acceptance_criteria or ["The bounded code change is applied in scope and verified."])
    stage11 = dict(stage11_contract or _stage11_contract(required=True))
    stage12 = dict(stage12_contract or _stage12_contract(required_tests))
    stage13 = dict(stage13_contract or _stage13_contract(require_static_success=bool(stage11.get("required", False)), require_runtime_success=bool(stage12.get("required", False))))

    patch_plan = {
        "objective": "Apply the bounded benchmark change.",
        "modify_files": [
            {"path": path, "intended_modifications": ["Update benchmark-controlled behavior"], "rationale": ["Owned by benchmark fixture"]}
            for path in modify_files
        ],
        "non_goals": [],
        "assumptions": [],
        "unresolved_uncertainties": [],
        "required_tests": required_tests,
        "should_escalate": False,
        "planning_confidence": 0.95,
        "acceptance_criteria": acceptance_criteria,
        "must_not_change": forbidden_paths,
        "files_read_only_context": ["tests/test_app.py"],
        "forbidden_targets": forbidden_paths,
        "risks": [],
    }
    patch_plan_result = {
        "job_contract": {
            "job_id": job_id,
            "repo_root": str(repo_root),
            "title": "Execution benchmark case",
            "description": "Prototype execution benchmark fixture.",
            "objective": "Apply the bounded benchmark change.",
            "constraints": ["modify only declared files"],
            "acceptance_criteria": acceptance_criteria,
            "issue_context": {"changed_files_hint": list(modify_files)},
            "forbidden_paths": forbidden_paths,
            "allowed_paths": allowed_edit_files,
        },
        "patch_plan": patch_plan,
    }
    patch_plan_env = build_envelope(
        stage="script_7_patch_plan",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result=patch_plan_result,
        warnings=[],
        tool_version="benchmark",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "07_patch_plan.json", patch_plan_env)

    execution_contract = {
        "task_mode": "patch_generation",
        "execution_mode": "patch_generation",
        "output_contract_mode": "patch_contract",
        "objective": "Apply the bounded benchmark change.",
        "generation_applicable": True,
        "allowed_edit_files": list(allowed_edit_files),
        "forbidden_edit_files": list(forbidden_paths),
        "must_not_create_files": True,
        "may_create_files": False,
        "expected_new_files": list(expected_new_files),
        "included_files": [
            {
                "path": "app.py",
                "file_role": "implementation_code",
                "bundle_role": "primary_target",
                "include_mode": "full_file",
                "reason_for_inclusion": "benchmark target file",
            },
            {
                "path": "tests/test_app.py",
                "file_role": "test_code",
                "bundle_role": "paired_test",
                "include_mode": "full_file",
                "reason_for_inclusion": "benchmark paired test",
            },
        ],
        "generation_files": [
            {"path": "app.py", "include_mode": "full_file", "content": app_text},
            {"path": "tests/test_app.py", "include_mode": "full_file", "content": test_text},
        ],
        "constraints": {
            "acceptance_criteria": acceptance_criteria,
            "required_tests": required_tests,
            "must_not_change": list(forbidden_paths),
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
            "artifact_size_limit_chars": 500000,
        },
        "stage10_apply_requirements": {
            "isolated_workspace_required": True,
            "source_repo_mutation_forbidden": True,
            "changed_files_must_be_enumerated": True,
            "apply_conflict_must_fail_closed": True,
        },
        "prompt_budget": {
            "file_count": 2,
            "full_file_count": 2,
            "snippet_count": 0,
            "reference_only_count": 0,
            "estimated_prompt_size_chars": len(app_text) + len(test_text),
            "estimated_prompt_tokens": 200,
            "total_chars": len(app_text) + len(test_text),
            "max_total_chars": 40000,
            "degradation_policy": "none",
            "degradation_applied": False,
            "degradation_actions": [],
        },
        "stage11_static_verification": stage11,
        "stage12_runtime_verification": stage12,
        "stage13_contract_verification": stage13,
    }
    save_json_atomic(
        run_dir / "07_execution_contract.json",
        {
            "job_id": job_id,
            "run_id": run_id,
            "stage": "script_7_patch_plan",
            "source_patch_plan_artifact": "07_patch_plan.json",
            "source_context_bundle_artifact": "06_context_bundle.json",
            "execution_contract": execution_contract,
        },
    )


def _write_patch_artifact(run_dir: Path, *, job_id: str, run_id: str, patch_text: str, target_files: Sequence[str]) -> None:
    envelope = build_envelope(
        stage="script_8_patch_generate",
        job_id=job_id,
        run_id=run_id,
        input_payload={"seed": True},
        result={
            "patch_artifact": {
                "artifact_type": "unified_diff",
                "producer": {"provider": "benchmark", "model": "fixture"},
                "target_files": list(target_files),
                "content": patch_text,
            },
            "generation_metadata": {
                "provider": "benchmark",
                "model": "fixture",
                "duration_ms": 1,
                "raw_output_path": str((run_dir / "08_patch_artifact.raw.txt").resolve()),
                "output_format_requested": "unified_diff",
                "output_format_received": "unified_diff",
            },
        },
        warnings=[],
        tool_version="benchmark",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "08_patch_artifact.json", envelope)


def _write_runtime_result(run_dir: Path, *, job_id: str, run_id: str, status: str, required: bool, workspace_path: str, diagnostics: Optional[List[Mapping[str, Any]]] = None, executed_checks: Optional[List[Mapping[str, Any]]] = None, require_static_pass: bool = True) -> None:
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
        tool_version="benchmark",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "12_runtime_verification.json", envelope)


def _setup_happy_path(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["app.py"])


def _setup_malformed_diff(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text="diff --git a/app.py b/app.py\n@@ broken hunk @@\n", target_files=["app.py"])


def _setup_out_of_scope(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id, modify_files=["app.py"], allowed_edit_files=["app.py"])
    before = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    after = before + "\n\ndef test_extra():\n    assert True\n"
    patch_text = _make_unified_diff("tests/test_app.py", before, after)
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["tests/test_app.py"])


def _setup_repairable_target_metadata(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["tests/test_app.py"])


def _setup_repairable_absolute_paths(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    abs_path = str((repo_root / "app.py").resolve())
    patch_text = (
        f"diff --git a/{abs_path} b/{abs_path}\n"
        f"--- a/{abs_path}\n"
        f"+++ b/{abs_path}\n"
        "@@ -1,2 +1,2 @@\n"
        " def greet() -> str:\n"
        "-    return 'hello'\n"
        "+    return 'hello world'\n"
    )
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["app.py"])


def _setup_repairable_dotdot_paths(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    patch_text = (
        "diff --git a/../target_repo/app.py b/../target_repo/app.py\n"
        "--- a/../target_repo/app.py\n"
        "+++ b/../target_repo/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def greet() -> str:\n"
        "-    return 'hello'\n"
        "+    return 'hello world'\n"
    )
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["app.py"])


def _setup_apply_conflict(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    stale_before = "def greet() -> str:\n    return 'hello'\n"
    patch_text = _make_unified_diff("app.py", stale_before, "def greet() -> str:\n    return 'hello world'\n")
    (repo_root / "app.py").write_text("def greet() -> str:\n    return 'hola'\n", encoding="utf-8")
    _git(["add", "app.py"], cwd=repo_root)
    _git(["commit", "-q", "-m", "diverge"], cwd=repo_root)
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["app.py"])


def _setup_static_failure(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str\n    return 'broken'\n")
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["app.py"])


def _setup_runtime_failure(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'bonjour'\n")
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["app.py"])


def _setup_contract_scope_violation(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        modify_files=["app.py"],
        allowed_edit_files=["app.py", "tests/test_app.py"],
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=False),
    )
    before = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    after = before.replace("hello world", "hello")
    patch_text = _make_unified_diff("tests/test_app.py", before, after)
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["tests/test_app.py"])
    # runtime evidence is optional for this case; seed a deterministic passing artifact so Stage 13 can run even if stage 12 is not executed.
    _write_runtime_result(run_dir, job_id=job_id, run_id=run_id, status="passed", required=False, workspace_path=str(repo_root), diagnostics=[], executed_checks=[], require_static_pass=True)


def _setup_repo_escape_disguised_as_normalization(root: Path, job_id: str, run_id: str) -> None:
    repo_root = _base_python_repo(root)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    patch_text = (
        "diff --git a/../../evil.py b/../../evil.py\n"
        "--- a/../../evil.py\n"
        "+++ b/../../evil.py\n"
        "@@ -1 +1 @@\n"
        "-print(1)\n"
        "+print(2)\n"
    )
    _write_patch_artifact(run_dir, job_id=job_id, run_id=run_id, patch_text=patch_text, target_files=["../../evil.py"])


BUILTIN_CASES: List[BenchmarkCase] = [
    BenchmarkCase(
        "happy_path",
        "Stages 9-13 pass on a valid bounded patch.",
        [9, 10, 11, 12, 13],
        _setup_happy_path,
        {
            9: BenchmarkExpectation(9, "passed", {"is_valid": True}),
            10: BenchmarkExpectation(10, "applied_cleanly", {"apply_status": "applied_cleanly"}),
            11: BenchmarkExpectation(11, "passed", {"static_verification_status": "passed"}),
            12: BenchmarkExpectation(12, "passed", {"runtime_verification_status": "passed"}),
            13: BenchmarkExpectation(13, "satisfied", {"decision.status": "satisfied"}),
        },
    ),
    BenchmarkCase(
        "malformed_diff",
        "Stage 9 rejects malformed unified diffs.",
        [9],
        _setup_malformed_diff,
        {9: BenchmarkExpectation(9, "failed", {"error_code": "git_apply_check_failed"})},
    ),
    BenchmarkCase(
        "out_of_scope",
        "Stage 9 detects out-of-scope file edits and stops for explicit confirmation.",
        [9],
        _setup_out_of_scope,
        {9: BenchmarkExpectation(9, "confirmation_required", {"error_code": "path_outside_allowlist"})},
    ),
    BenchmarkCase(
        "repairable_target_metadata",
        "Stage 9 auto-fixes declared target metadata that conflicts with in-scope diff intent.",
        [9],
        _setup_repairable_target_metadata,
        {9: BenchmarkExpectation(9, "passed", {"is_valid": True})},
    ),
    BenchmarkCase(
        "repairable_absolute_paths",
        "Stage 9 auto-normalizes absolute in-repo diff paths before validation.",
        [9],
        _setup_repairable_absolute_paths,
        {9: BenchmarkExpectation(9, "passed", {"is_valid": True})},
    ),
    BenchmarkCase(
        "repairable_dotdot_paths",
        "Stage 9 auto-normalizes equivalent in-repo dotdot diff paths before validation.",
        [9],
        _setup_repairable_dotdot_paths,
        {9: BenchmarkExpectation(9, "passed", {"is_valid": True})},
    ),
    BenchmarkCase(
        "apply_conflict",
        "Stage 9 rejects patches that no longer apply cleanly to repo state.",
        [9],
        _setup_apply_conflict,
        {9: BenchmarkExpectation(9, "failed", {"error_code": "git_apply_check_failed"})},
    ),
    BenchmarkCase(
        "static_failure",
        "Stage 11 detects syntax/static failures.",
        [9, 10, 11],
        _setup_static_failure,
        {
            9: BenchmarkExpectation(9, "passed", {"is_valid": True}),
            10: BenchmarkExpectation(10, "applied_cleanly", {"apply_status": "applied_cleanly"}),
            11: BenchmarkExpectation(11, "failed", {"static_verification_status": "failed"}),
        },
    ),
    BenchmarkCase(
        "runtime_failure",
        "Stage 12 detects failing runtime checks.",
        [9, 10, 11, 12],
        _setup_runtime_failure,
        {
            9: BenchmarkExpectation(9, "passed", {"is_valid": True}),
            10: BenchmarkExpectation(10, "applied_cleanly", {"apply_status": "applied_cleanly"}),
            11: BenchmarkExpectation(11, "passed", {"static_verification_status": "passed"}),
            12: BenchmarkExpectation(12, "failed", {"runtime_verification_status": "failed"}),
        },
    ),
    BenchmarkCase(
        "contract_scope_violation",
        "Stage 13 catches contract/file-touch drift after earlier stages pass.",
        [9, 10, 11, 12, 13],
        _setup_contract_scope_violation,
        {
            9: BenchmarkExpectation(9, "passed", {"is_valid": True}),
            10: BenchmarkExpectation(10, "applied_cleanly", {"apply_status": "applied_cleanly"}),
            11: BenchmarkExpectation(11, "passed", {"static_verification_status": "passed"}),
            12: BenchmarkExpectation(12, "passed", {"runtime_verification_status": "passed"}),
            13: BenchmarkExpectation(13, "violated", {"decision.status": "violated"}),
        },
    ),
    BenchmarkCase(
        "repo_escape_disguised_as_normalization",
        "Stage 9 keeps true repo-escape paths as hard failures even when they look fixable.",
        [9],
        _setup_repo_escape_disguised_as_normalization,
        {9: BenchmarkExpectation(9, "failed", {"error_code": "path_traversal"})},
    ),
]


def _default_progress(message: str) -> None:
    print(f"[execution-benchmark] {message}", flush=True)


def _emit_progress(progress: Optional[Callable[[str], None]], message: str) -> None:
    if progress is not None:
        progress(str(message))


def list_execution_cases() -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for case in BUILTIN_CASES:
        payload.append(
            {
                "key": case.key,
                "description": case.description,
                "supported_stages": list(case.supported_stages),
                "terminal_stage": case.terminal_expectation.stage_number,
                "terminal_verdict": case.terminal_expectation.verdict,
                "stage_expectations": [
                    {
                        "stage_number": stage_number,
                        "verdict": expectation.verdict,
                        "checks": dict(expectation.checks or {}),
                    }
                    for stage_number, expectation in sorted(case.stage_expectations.items())
                ],
            }
        )
    return payload


def _case_lookup(selected: Optional[Iterable[str]] = None) -> List[BenchmarkCase]:
    wanted = {str(item).strip() for item in list(selected or []) if str(item).strip()}
    cases = [case for case in BUILTIN_CASES if not wanted or case.key in wanted]
    if wanted and not cases:
        raise ExecutionBenchmarkError(f"No execution benchmark cases matched selection: {sorted(wanted)}")
    return cases


def _parse_stage_numbers(values: Optional[Iterable[str]]) -> List[int]:
    if not values:
        return [9, 10, 11, 12, 13]
    out: List[int] = []
    for raw in values:
        for token in str(raw).split(","):
            txt = token.strip().lower()
            if not txt:
                continue
            if txt == "all":
                return [9, 10, 11, 12, 13]
            try:
                value = int(txt)
            except ValueError as exc:
                raise ExecutionBenchmarkError(f"Invalid execution stage selector: {token}") from exc
            if value not in STAGE_KEYS:
                raise ExecutionBenchmarkError(f"Unsupported execution stage: {value}")
            if value not in out:
                out.append(value)
    return sorted(out)


STAGE_INPUTS: Dict[int, str] = {
    9: "08_patch_artifact.json",
    10: "09_validated_artifact.json",
    11: "10_apply_result.json",
    12: "11_static_verification.json",
    13: "12_runtime_verification.json",
}

STAGE_OUTPUTS: Dict[int, str] = {
    9: "09_validated_artifact.json",
    10: "10_apply_result.json",
    11: "11_static_verification.json",
    12: "12_runtime_verification.json",
    13: "13_contract_verification.json",
}


def _stage_runner(stage_number: int) -> Callable[[Sequence[str]], int]:
    if stage_number == 9:
        return stage9_artifact_validate.main
    if stage_number == 10:
        return stage10_patch_apply.main
    if stage_number == 11:
        return stage11_static_verify.main
    if stage_number == 12:
        return stage12_runtime_verify.main
    if stage_number == 13:
        return stage13_contract_verify.main
    raise ExecutionBenchmarkError(f"Unsupported execution benchmark stage: {stage_number}")


def _parse_structured_error(text: str) -> Optional[Dict[str, Any]]:
    for raw_line in reversed(str(text or "").splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
            return dict(payload.get("error") or {})
    return None


def _path_lookup(payload: Any, dotted_key: str) -> Any:
    current: Any = payload
    for segment in str(dotted_key).split("."):
        if isinstance(current, Mapping):
            current = current.get(segment)
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except Exception:
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _clone_record(record: StageRunRecord, **overrides: Any) -> StageRunRecord:
    payload = asdict(record)
    payload.update(overrides)
    return StageRunRecord(**payload)


def _load_json_mapping(path: Path) -> Dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise ExecutionBenchmarkError(f"Expected JSON object in {path}")
    return dict(payload)


def _normalize_candidate_path(repo_root: Path, candidate: str) -> Optional[str]:
    raw = str(candidate or "").strip().replace("\\", "/")
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        try:
            resolved = path.resolve()
            return resolved.relative_to(repo_root.resolve()).as_posix()
        except Exception:
            return None
    try:
        return normalize_repo_relative_path(str(repo_root), raw)
    except Exception:
        return None


def _extract_patch_artifact(run_dir: Path) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    env = _load_json_mapping(run_dir / "08_patch_artifact.json")
    result = dict(env.get("result") or {})
    artifact = dict(result.get("patch_artifact") or {})
    if not artifact:
        raise ExecutionBenchmarkError("Patch artifact fixture is missing result.patch_artifact")
    return env, result, artifact


def _load_execution_contract_context(run_dir: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    env = _load_json_mapping(run_dir / "07_execution_contract.json")
    contract = dict(env.get("execution_contract") or {})
    if not contract:
        raise ExecutionBenchmarkError("Execution benchmark fixture is missing execution_contract")
    return env, contract


def _load_patch_plan_context(run_dir: Path) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    env = _load_json_mapping(run_dir / "07_patch_plan.json")
    result = dict(env.get("result") or {})
    patch_plan = dict(result.get("patch_plan") or {})
    job_contract = dict(result.get("job_contract") or {})
    if not patch_plan or not job_contract:
        raise ExecutionBenchmarkError("Patch-plan fixture is missing result.patch_plan or result.job_contract")
    return env, result, patch_plan | {"__job_contract__": job_contract}


def _resolve_fixture_repo_root(run_dir: Path) -> Path:
    patch_plan_env = _load_json_mapping(run_dir / "07_patch_plan.json")
    patch_plan_result = dict(patch_plan_env.get("result") or {})
    job_contract = dict(patch_plan_result.get("job_contract") or {})
    repo_root = str(job_contract.get("repo_root") or "").strip()
    if not repo_root:
        raise ExecutionBenchmarkError("Benchmark fixture is missing job_contract.repo_root")
    return Path(repo_root).resolve()


def _extract_diff_targets_from_artifact(artifact: Mapping[str, Any]) -> List[str]:
    content = str(artifact.get("content") or "")
    if not content.strip():
        return []
    try:
        return list(extract_target_files(content))
    except Exception:
        return []


def _resolve_remediation_targets(*, repo_root: Path, run_dir: Path, record: StageRunRecord) -> List[str]:
    _, _, artifact = _extract_patch_artifact(run_dir)
    diff_targets = [_normalize_candidate_path(repo_root, path) for path in _extract_diff_targets_from_artifact(artifact)]
    normalized_diff_targets = [path for path in diff_targets if path]
    if normalized_diff_targets:
        return list(dict.fromkeys(normalized_diff_targets))
    detail_targets = _path_lookup(record.summary, "details.target_files") or _path_lookup(record.summary, "details.extracted")
    if isinstance(detail_targets, list):
        normalized_detail_targets = [_normalize_candidate_path(repo_root, str(path)) for path in detail_targets]
        return [path for path in normalized_detail_targets if path]
    return []


def _split_patch_header_suffix(token: str) -> tuple[str, str]:
    text = str(token or "")
    if "\t" in text:
        head, tail = text.split("\t", 1)
        return head, "\t" + tail
    return text, ""


def _rewrite_diff_side_token(*, repo_root: Path, token: str, side_prefix: str) -> Optional[str]:
    raw = str(token or "").strip()
    if raw in {"/dev/null", f"{side_prefix}/dev/null"}:
        return "/dev/null"
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    normalized = _normalize_candidate_path(repo_root, raw)
    if not normalized:
        return None
    return f"{side_prefix}/{normalized}"


def _normalize_patch_content_paths(*, repo_root: Path, patch_text: str) -> tuple[str, List[str]]:
    normalized_text = normalize_diff_text(patch_text)
    rewritten_lines: List[str] = []
    changed = False
    for line in normalized_text.splitlines():
        if line.startswith("diff --git "):
            remainder = line[len("diff --git ") :].strip()
            try:
                parts = shlex.split(remainder)
            except Exception as exc:
                raise ExecutionBenchmarkError("Unable to parse diff --git header during benchmark remediation") from exc
            if len(parts) < 2:
                raise ExecutionBenchmarkError("Malformed diff --git header encountered during benchmark remediation")
            old_token = _rewrite_diff_side_token(repo_root=repo_root, token=parts[0], side_prefix="a")
            new_token = _rewrite_diff_side_token(repo_root=repo_root, token=parts[1], side_prefix="b")
            if not old_token or not new_token:
                raise ExecutionBenchmarkError("Patch path normalization would escape repo_root; refusing benchmark auto-fix")
            new_line = f"diff --git {old_token} {new_token}"
            changed = changed or (new_line != line)
            rewritten_lines.append(new_line)
            continue
        if line.startswith("--- "):
            head, suffix = _split_patch_header_suffix(line[4:])
            new_head = _rewrite_diff_side_token(repo_root=repo_root, token=head, side_prefix="a")
            if not new_head:
                raise ExecutionBenchmarkError("Patch header normalization would escape repo_root; refusing benchmark auto-fix")
            new_line = f"--- {new_head}{suffix}"
            changed = changed or (new_line != line)
            rewritten_lines.append(new_line)
            continue
        if line.startswith("+++ "):
            head, suffix = _split_patch_header_suffix(line[4:])
            new_head = _rewrite_diff_side_token(repo_root=repo_root, token=head, side_prefix="b")
            if not new_head:
                raise ExecutionBenchmarkError("Patch header normalization would escape repo_root; refusing benchmark auto-fix")
            new_line = f"+++ {new_head}{suffix}"
            changed = changed or (new_line != line)
            rewritten_lines.append(new_line)
            continue
        rewritten_lines.append(line)
    rewritten_text = "\n".join(rewritten_lines) + "\n"
    targets = [_normalize_candidate_path(repo_root, path) for path in _extract_diff_targets_from_artifact({"content": rewritten_text})]
    normalized_targets = [path for path in targets if path]
    if not changed:
        raise ExecutionBenchmarkError("No benchmark-local patch path normalization was necessary")
    if not normalized_targets:
        raise ExecutionBenchmarkError("Normalized patch content did not yield any in-repo target files")
    return rewritten_text, list(dict.fromkeys(normalized_targets))


def _persist_patch_artifact(run_dir: Path, *, artifact_env: Mapping[str, Any], artifact_result: Mapping[str, Any], patch_artifact: Mapping[str, Any]) -> None:
    env = dict(artifact_env)
    result = dict(artifact_result)
    result["patch_artifact"] = dict(patch_artifact)
    env["result"] = result
    save_json_atomic(run_dir / "08_patch_artifact.json", env)


def _auto_fix_patch_target_metadata(*, repo_root: Path, run_dir: Path) -> Optional[Dict[str, Any]]:
    artifact_env, artifact_result, artifact = _extract_patch_artifact(run_dir)
    normalized_diff_targets = _resolve_remediation_targets(repo_root=repo_root, run_dir=run_dir, record=StageRunRecord(9, STAGE_KEYS[9], 0, 0.0, "", "", "", "", {}, "failed", False))
    if not normalized_diff_targets:
        return None
    execution_contract_env, execution_contract = _load_execution_contract_context(run_dir)
    allowed = [_normalize_candidate_path(repo_root, path) for path in list(execution_contract.get("allowed_edit_files") or [])]
    allowed_targets = [path for path in allowed if path]
    if allowed_targets and any(path not in set(allowed_targets) for path in normalized_diff_targets):
        return None
    declared_targets = [_normalize_candidate_path(repo_root, path) for path in list(artifact.get("target_files") or [])]
    normalized_declared = [path for path in declared_targets if path]
    if normalized_declared == normalized_diff_targets:
        return None
    patched_artifact = dict(artifact)
    patched_artifact["target_files"] = normalized_diff_targets
    _persist_patch_artifact(run_dir, artifact_env=artifact_env, artifact_result=artifact_result, patch_artifact=patched_artifact)
    return {"action": "reconcile_target_metadata", "details": {"target_files": normalized_diff_targets}}


def _auto_fix_patch_content_paths(*, repo_root: Path, run_dir: Path) -> Optional[Dict[str, Any]]:
    artifact_env, artifact_result, artifact = _extract_patch_artifact(run_dir)
    rewritten_text, normalized_targets = _normalize_patch_content_paths(repo_root=repo_root, patch_text=str(artifact.get("content") or ""))
    patched_artifact = dict(artifact)
    patched_artifact["content"] = rewritten_text
    patched_artifact["target_files"] = normalized_targets
    _persist_patch_artifact(run_dir, artifact_env=artifact_env, artifact_result=artifact_result, patch_artifact=patched_artifact)
    return {"action": "normalize_patch_paths", "details": {"target_files": normalized_targets}}


def _apply_allowlist_confirmation_fix(*, repo_root: Path, run_dir: Path, target_files: Sequence[str]) -> Dict[str, Any]:
    normalized_targets = [path for path in (_normalize_candidate_path(repo_root, item) for item in target_files) if path]
    if not normalized_targets:
        raise ExecutionBenchmarkError("Confirmation fix could not determine any in-repo target files")

    patch_plan_env = _load_json_mapping(run_dir / "07_patch_plan.json")
    patch_plan_result = dict(patch_plan_env.get("result") or {})
    patch_plan = dict(patch_plan_result.get("patch_plan") or {})
    job_contract = dict(patch_plan_result.get("job_contract") or {})
    if not patch_plan or not job_contract:
        raise ExecutionBenchmarkError("Patch-plan fixture is missing patch_plan/job_contract fields for confirmation remediation")

    allowed_paths = [path for path in list(job_contract.get("allowed_paths") or []) if str(path).strip()]
    for target in normalized_targets:
        if target not in allowed_paths:
            allowed_paths.append(target)
    job_contract["allowed_paths"] = allowed_paths

    issue_context = dict(job_contract.get("issue_context") or {})
    changed_files_hint = [path for path in list(issue_context.get("changed_files_hint") or []) if str(path).strip()]
    for target in normalized_targets:
        if target not in changed_files_hint:
            changed_files_hint.append(target)
    issue_context["changed_files_hint"] = changed_files_hint
    job_contract["issue_context"] = issue_context

    modify_files = [dict(item) for item in list(patch_plan.get("modify_files") or []) if isinstance(item, Mapping)]
    existing_paths = {str(item.get("path") or "") for item in modify_files}
    for target in normalized_targets:
        if target not in existing_paths:
            modify_files.append(
                {
                    "path": target,
                    "intended_modifications": ["Benchmark-local confirmed scope extension"],
                    "rationale": ["Explicit operator confirmation for execution benchmark"],
                }
            )
    patch_plan["modify_files"] = modify_files
    patch_plan_result["patch_plan"] = patch_plan
    patch_plan_result["job_contract"] = job_contract
    patch_plan_env["result"] = patch_plan_result
    save_json_atomic(run_dir / "07_patch_plan.json", patch_plan_env)

    execution_env, execution_contract = _load_execution_contract_context(run_dir)
    allowed_edit_files = [path for path in list(execution_contract.get("allowed_edit_files") or []) if str(path).strip()]
    for target in normalized_targets:
        if target not in allowed_edit_files:
            allowed_edit_files.append(target)
    execution_contract["allowed_edit_files"] = allowed_edit_files
    execution_env["execution_contract"] = execution_contract
    save_json_atomic(run_dir / "07_execution_contract.json", execution_env)
    return {"action": "apply_confirmed_scope_extension", "details": {"target_files": normalized_targets}}


def _extract_stage_summary(stage_number: int, payload: Mapping[str, Any]) -> tuple[str, Dict[str, Any]]:
    result = dict(payload.get("result") or {})
    if stage_number == 9:
        if result.get("validation_skipped") is True:
            return "skipped", {
                "generation_blocked_reason": result.get("generation_blocked_reason"),
                "validation_skipped": True,
            }
        if result.get("validation_failed") is True:
            return "failed", {
                "error_code": str(result.get("failure_reason_code") or "validation_failed"),
                "message": str(result.get("validation_failed_reason") or "validation failed"),
                "failure_source": str(result.get("failure_source") or ""),
                "validation_failed": True,
                "target_files": list(dict(result.get("artifact_lineage") or {}).get("target_files") or []),
            }
        artifact = dict(result.get("validated_artifact") or {})
        return (
            "passed" if artifact.get("is_valid") else "failed",
            {
                "is_valid": bool(artifact.get("is_valid", False)),
                "validation_checks": list(artifact.get("validation_checks") or []),
                "target_files": list(artifact.get("target_files") or []),
            },
        )
    if stage_number == 10:
        apply_result = dict(result.get("apply_result") or {})
        return str(apply_result.get("apply_status") or "unknown"), {
            "apply_status": str(apply_result.get("apply_status") or "unknown"),
            "changed_files": list(apply_result.get("changed_files") or []),
            "diagnostics": list(apply_result.get("diagnostics") or []),
            "patch_stats": dict(apply_result.get("patch_stats") or {}),
        }
    if stage_number == 11:
        verification = dict(result.get("static_verification") or {})
        return str(verification.get("static_verification_status") or "unknown"), {
            "static_verification_status": str(verification.get("static_verification_status") or "unknown"),
            "summary": dict(verification.get("summary") or {}),
            "diagnostics": list(verification.get("diagnostics") or []),
            "failed_checks": list(verification.get("failed_checks") or []),
        }
    if stage_number == 12:
        verification = dict(result.get("runtime_verification") or {})
        return str(verification.get("runtime_verification_status") or "unknown"), {
            "runtime_verification_status": str(verification.get("runtime_verification_status") or "unknown"),
            "summary": dict(verification.get("summary") or {}),
            "diagnostics": list(verification.get("diagnostics") or []),
            "failing_tests": [
                test_name
                for check in list(verification.get("executed_checks") or [])
                for test_name in list(dict(check).get("failing_tests") or [])
            ],
        }
    verification = dict(result.get("contract_verification") or {})
    decision = dict(verification.get("decision") or {})
    return str(decision.get("status") or verification.get("contract_verification_status") or "unknown"), {
        "contract_verification_status": str(verification.get("contract_verification_status") or "unknown"),
        "decision": decision,
        "scope_assessment": dict(verification.get("scope_assessment") or {}),
        "acceptance_assessment": dict(verification.get("acceptance_assessment") or {}),
    }


def _resolve_case_expectation(case: BenchmarkCase, stage_number: int, *, apply_confirmed_fixes: bool) -> BenchmarkExpectation:
    expectation = case.expectation_for(stage_number)
    if expectation is None:
        raise ExecutionBenchmarkError(f"Case `{case.key}` does not define an expectation for target stage {stage_number}")
    if apply_confirmed_fixes and expectation.verdict == "confirmation_required":
        checks = dict(expectation.checks or {})
        checks.pop("error_code", None)
        checks["confirmation_applied"] = True
        return BenchmarkExpectation(stage_number, "passed", checks)
    return expectation


def _attempt_stage9_remediations(
    *,
    initial_record: StageRunRecord,
    repo_root: Path,
    run_dir: Path,
    job_id: str,
    run_id: str,
    logs_dir: Path,
    apply_confirmed_fixes: bool,
) -> tuple[List[StageRunRecord], List[Dict[str, Any]], int, bool, bool]:
    records: List[StageRunRecord] = []
    events: List[Dict[str, Any]] = []
    auto_fix_count = 0
    confirmation_required = False
    confirmation_applied = False
    current = initial_record
    fixture_repo_root = _resolve_fixture_repo_root(run_dir)
    remediation_steps = [
        _auto_fix_patch_content_paths,
        _auto_fix_patch_target_metadata,
    ]
    attempted_steps: set[str] = set()

    while True:
        records.append(current)
        error_code = str(_path_lookup(current.summary, "error_code") or "")
        if current.verdict == "passed":
            return records, events, auto_fix_count, confirmation_required, confirmation_applied

        applied = False
        for helper in remediation_steps:
            helper_name = helper.__name__
            if helper_name in attempted_steps:
                continue
            try:
                event = helper(repo_root=fixture_repo_root, run_dir=run_dir)
            except ExecutionBenchmarkError:
                attempted_steps.add(helper_name)
                continue
            attempted_steps.add(helper_name)
            if event is None:
                continue
            events.append(event)
            auto_fix_count += 1
            rerun = _run_stage(9, repo_root=repo_root, run_dir=run_dir, job_id=job_id, run_id=run_id, logs_dir=logs_dir)
            current = _clone_record(
                rerun,
                summary={**dict(rerun.summary), "auto_fix_applied": True},
            )
            applied = True
            break
        if not applied:
            if error_code == "path_outside_allowlist":
                targets = _resolve_remediation_targets(repo_root=fixture_repo_root, run_dir=run_dir, record=current)
                if apply_confirmed_fixes:
                    event = _apply_allowlist_confirmation_fix(repo_root=fixture_repo_root, run_dir=run_dir, target_files=targets)
                    confirmation_applied = True
                    events.append(event)
                    rerun = _run_stage(9, repo_root=repo_root, run_dir=run_dir, job_id=job_id, run_id=run_id, logs_dir=logs_dir)
                    current = _clone_record(
                        rerun,
                        summary={**dict(rerun.summary), "confirmation_applied": True},
                    )
                    continue
                confirmation_required = True
                records[-1] = _clone_record(
                    current,
                    verdict="confirmation_required",
                    summary={**dict(current.summary), "confirmation_required": True},
                )
                return records, events, auto_fix_count, confirmation_required, confirmation_applied
            return records, events, auto_fix_count, confirmation_required, confirmation_applied


def _matches_expectation(record: StageRunRecord, expectation: BenchmarkExpectation) -> bool:
    if record.stage_number != expectation.stage_number:
        return False
    if str(record.verdict) != str(expectation.verdict):
        return False
    for key, expected in dict(expectation.checks or {}).items():
        current = _path_lookup(record.summary, str(key))
        if current != expected:
            return False
    return True


def _run_stage(
    stage_number: int,
    *,
    repo_root: Path,
    run_dir: Path,
    job_id: str,
    run_id: str,
    logs_dir: Path,
) -> StageRunRecord:
    artifact_in = run_dir / STAGE_INPUTS[stage_number]
    artifact_out = run_dir / STAGE_OUTPUTS[stage_number]
    stage_key = STAGE_KEYS[stage_number]
    stdout_log_path = logs_dir / f"{stage_number:02d}_{stage_key}.stdout.log"
    stderr_log_path = logs_dir / f"{stage_number:02d}_{stage_key}.stderr.log"
    argv = ["--input", str(artifact_in), "--output", str(artifact_out), "--repo-root", str(repo_root)]
    started = time.perf_counter()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        exit_code = _stage_runner(stage_number)(argv)
    elapsed = time.perf_counter() - started
    stdout_text = stdout_buffer.getvalue()
    stderr_text = stderr_buffer.getvalue()
    save_text_atomic(stdout_log_path, stdout_text)
    save_text_atomic(stderr_log_path, stderr_text)

    if exit_code != 0:
        structured_error = _parse_structured_error(stderr_text)
        summary = {
            "error_code": str((structured_error or {}).get("code") or f"exit_{exit_code}"),
            "message": str((structured_error or {}).get("message") or f"Stage {stage_number} exited with code {exit_code}"),
            "details": dict((structured_error or {}).get("details") or {}),
        }
        return StageRunRecord(
            stage_number=stage_number,
            stage_key=stage_key,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            input_artifact_path=str(artifact_in),
            artifact_path=str(artifact_out),
            stdout_log_path=str(stdout_log_path),
            stderr_log_path=str(stderr_log_path),
            summary=summary,
            verdict="failed",
            schema_validated=False,
            structured_error=structured_error,
        )

    if not artifact_out.exists():
        return StageRunRecord(
            stage_number=stage_number,
            stage_key=stage_key,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            input_artifact_path=str(artifact_in),
            artifact_path=str(artifact_out),
            stdout_log_path=str(stdout_log_path),
            stderr_log_path=str(stderr_log_path),
            summary={"error_code": "missing_output_artifact", "message": f"{artifact_out.name} was not written"},
            verdict="failed",
            schema_validated=False,
        )

    payload = load_json(artifact_out)
    try:
        validated_env = validate_stage_output(
            payload,
            expected_stage=stage_key,
            expected_job_id=job_id,
            expected_run_id=run_id,
        )
    except Exception as exc:
        return StageRunRecord(
            stage_number=stage_number,
            stage_key=stage_key,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            input_artifact_path=str(artifact_in),
            artifact_path=str(artifact_out),
            stdout_log_path=str(stdout_log_path),
            stderr_log_path=str(stderr_log_path),
            summary={
                "error_code": "invalid_stage_output",
                "message": str(exc),
                "errors": list(payload.get("errors") or []),
            },
            verdict="failed",
            schema_validated=False,
            envelope_status=str(payload.get("status") or ""),
        )

    verdict, summary = _extract_stage_summary(stage_number, payload)
    structured_error = None
    if verdict == "failed" and summary.get("error_code"):
        structured_error = {
            "code": str(summary.get("error_code") or ""),
            "message": str(summary.get("message") or ""),
            "details": dict(summary.get("details") or {}),
        }
    return StageRunRecord(
        stage_number=stage_number,
        stage_key=stage_key,
        exit_code=exit_code,
        elapsed_seconds=elapsed,
        input_artifact_path=str(artifact_in),
        artifact_path=str(artifact_out),
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
        summary=summary,
        verdict=verdict,
        schema_validated=True,
        structured_error=structured_error,
        envelope_status=validated_env.status,
    )


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Execution Benchmark Run",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- status: `{report.get('status')}`",
        f"- selected_stages: `{', '.join(str(item) for item in list(report.get('selected_stages') or []))}`",
        f"- executed_cases: `{report.get('executed_case_count')}`",
        f"- matched_cases: `{report.get('matched_case_count')}`",
        f"- mismatched_cases: `{report.get('mismatched_case_count')}`",
        f"- confirmation_required_count: `{report.get('confirmation_required_count')}`",
        f"- confirmation_applied_count: `{report.get('confirmation_applied_count')}`",
        f"- auto_fix_count: `{report.get('auto_fix_count')}`",
        f"- elapsed_seconds: `{report.get('elapsed_seconds')}`",
        f"- bundle_path: `{report.get('bundle_path')}`" if report.get("bundle_path") else "- bundle_path: `(not generated)`",
        "",
        "## Stage Summary",
        "",
    ]
    for stage in list(report.get("stage_summary") or []):
        lines.append(
            f"- stage {stage.get('stage_number')}: executions={stage.get('execution_count')} "
            f"target_matches={stage.get('target_expectation_matches')}/{stage.get('target_case_count')} "
            f"avg_seconds={stage.get('average_elapsed_seconds')}"
        )
    lines.extend(["", "## Cases", ""])
    for case in list(report.get("cases") or []):
        lines.append(f"### {case.get('case_key')}")
        lines.append("")
        lines.append(f"- status: `{case.get('status')}`")
        lines.append(f"- target_stage: `{case.get('target_stage')}`")
        lines.append(f"- expected_verdict: `{case.get('expected_verdict')}`")
        lines.append(f"- actual_verdict: `{case.get('actual_verdict')}`")
        lines.append(f"- elapsed_seconds: `{case.get('elapsed_seconds')}`")
        if case.get("confirmation_required"):
            lines.append("- confirmation_required: `true`")
        if case.get("confirmation_applied"):
            lines.append("- confirmation_applied: `true`")
        terminal_error = str(case.get('terminal_error_code') or '').strip()
        if terminal_error:
            lines.append(f"- terminal_error_code: `{terminal_error}`")
        remediation_events = list(case.get("remediation_events") or [])
        if remediation_events:
            lines.append("- remediation_events:")
            for event in remediation_events:
                event_map = dict(event or {}) if isinstance(event, Mapping) else {"action": str(event)}
                lines.append(f"  - `{event_map.get('action')}` {json.dumps(dict(event_map.get('details') or {}), ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _add_path_to_zip(zf: zipfile.ZipFile, source: Path, arc_prefix: str, added: set[str]) -> None:
    if not source.exists():
        return
    if source.is_file():
        arcname = f"{arc_prefix}/{source.name}" if arc_prefix else source.name
        if arcname in added:
            return
        zf.write(source, arcname)
        added.add(arcname)
        return
    for root, _, files in os.walk(source):
        root_path = Path(root)
        rel_root = root_path.relative_to(source)
        for name in sorted(files):
            file_path = root_path / name
            rel_file = rel_root / name
            arcname = str(Path(arc_prefix) / rel_file) if arc_prefix else str(rel_file)
            if arcname in added:
                continue
            zf.write(file_path, arcname)
            added.add(arcname)


def _create_share_bundle(*, output_root: Path, report: Mapping[str, Any]) -> Path:
    bundle_path = output_root.parent / f"execution_benchmark_{output_root.name}.zip"
    manifest = {
        "run_id": str(report.get("run_id") or ""),
        "status": str(report.get("status") or ""),
        "created_at": utc_now_iso(),
        "out_dir": str(output_root),
        "case_keys": [str(case.get("case_key") or "") for case in list(report.get("cases") or [])],
    }
    added: set[str] = set()
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _add_path_to_zip(zf, output_root / "execution_benchmark_report.json", output_root.name, added)
        _add_path_to_zip(zf, output_root / "execution_benchmark_report.md", output_root.name, added)
        for case in list(report.get("cases") or []):
            case_key = str(case.get("case_key") or "").strip()
            if case_key:
                _add_path_to_zip(zf, output_root / case_key, f"{output_root.name}/{case_key}", added)
        zf.writestr(f"{output_root.name}/bundle_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return bundle_path


def _cleanup_expanded_execution_results(output_root: Path) -> List[str]:
    if not output_root.exists():
        return []
    try:
        shutil.rmtree(output_root)
    except Exception:
        return []
    return [str(output_root)]


def run_execution_benchmark(
    *,
    repo_root: str | Path = ".",
    result_root: Optional[str | Path] = None,
    case_keys: Optional[Iterable[str]] = None,
    selected_stages: Optional[Iterable[str]] = None,
    progress: Optional[Callable[[str], None]] = None,
    apply_confirmed_fixes: bool = False,
) -> Dict[str, Any]:
    repo_root_path = Path(repo_root).resolve()
    stages = _parse_stage_numbers(selected_stages)
    candidate_cases = _case_lookup(case_keys)
    cases = [case for case in candidate_cases if any(stage in case.supported_stages for stage in stages)]
    if not cases:
        raise ExecutionBenchmarkError(
            f"No execution benchmark cases support stage selection {stages} for cases {[case.key for case in candidate_cases] or 'ALL'}"
        )
    run_id = make_run_id().replace("RUN-", "", 1)
    output_stamp = time.strftime("%Y.%m.%d-%H%M%S", time.gmtime())
    output_root = Path(result_root).resolve() if result_root else repo_root_path / "benchmarks" / "pipeline" / "_results" / output_stamp
    ensure_directory(output_root)

    overall_started = time.perf_counter()
    case_reports: List[Dict[str, Any]] = []
    stage_records: Dict[int, List[StageRunRecord]] = {stage: [] for stage in stages}
    overall_ok = True

    for case_index, case in enumerate(cases, start=1):
        case_root = output_root / case.key
        if case_root.exists():
            shutil.rmtree(case_root)
        case_root.mkdir(parents=True, exist_ok=True)
        sandbox_root = case_root / "sandbox"
        sandbox_root.mkdir(parents=True, exist_ok=True)
        logs_dir = case_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"JOB-{case.key}"
        run_name = f"RUN-{run_id}-{case.key}"
        case.setup(sandbox_root, job_id, run_name)
        run_dir = sandbox_root / "run"

        target_stage = max(stage for stage in stages if stage in case.supported_stages)
        expectation = _resolve_case_expectation(case, target_stage, apply_confirmed_fixes=apply_confirmed_fixes)

        _emit_progress(
            progress,
            f"case {case_index}/{len(cases)} `{case.key}` start target_stage={target_stage} expected={expectation.verdict}",
        )
        case_started = time.perf_counter()
        records: List[StageRunRecord] = []
        terminal_record: Optional[StageRunRecord] = None
        remediation_events: List[Dict[str, Any]] = []
        case_confirmation_required = False
        case_confirmation_applied = False
        case_auto_fix_count = 0
        for stage_number in [9, 10, 11, 12, 13]:
            if stage_number not in case.supported_stages:
                continue
            if stage_number > target_stage:
                break
            _emit_progress(progress, f"case {case_index}/{len(cases)} `{case.key}` stage {stage_number} running")
            record = _run_stage(
                stage_number,
                repo_root=repo_root_path,
                run_dir=run_dir,
                job_id=job_id,
                run_id=run_name,
                logs_dir=logs_dir,
            )
            stage_run_records = [record]
            if stage_number == 9:
                stage_run_records, stage_events, auto_fix_delta, confirmation_required, confirmation_applied = _attempt_stage9_remediations(
                    initial_record=record,
                    repo_root=repo_root_path,
                    run_dir=run_dir,
                    job_id=job_id,
                    run_id=run_name,
                    logs_dir=logs_dir,
                    apply_confirmed_fixes=apply_confirmed_fixes,
                )
                remediation_events.extend(stage_events)
                case_auto_fix_count += auto_fix_delta
                case_confirmation_required = case_confirmation_required or confirmation_required
                case_confirmation_applied = case_confirmation_applied or confirmation_applied
            records.extend(stage_run_records)
            if stage_number in stages:
                stage_records.setdefault(stage_number, []).extend(stage_run_records)
            terminal_record = stage_run_records[-1]
            _emit_progress(
                progress,
                f"case {case_index}/{len(cases)} `{case.key}` stage {stage_number} verdict={terminal_record.verdict} exit={terminal_record.exit_code} elapsed={round(terminal_record.elapsed_seconds, 2)}s",
            )
            if stage_number >= target_stage:
                break
            if stage_number == 9 and terminal_record.verdict != "passed":
                break
            if stage_number == 10 and terminal_record.verdict != "applied_cleanly":
                break
            if stage_number == 11 and terminal_record.verdict not in {"passed", "not_required"}:
                break
            if stage_number == 12 and terminal_record.verdict not in {"passed", "not_required", "skipped"}:
                break

        if terminal_record is None:
            raise ExecutionBenchmarkError(f"Case `{case.key}` did not execute any stages")

        case_ok = _matches_expectation(terminal_record, expectation)
        overall_ok = overall_ok and case_ok
        elapsed_seconds = round(time.perf_counter() - case_started, 4)
        case_report = {
            "case_key": case.key,
            "description": case.description,
            "status": "ok" if case_ok else "failed",
            "target_stage": target_stage,
            "target_stage_key": STAGE_KEYS[target_stage],
            "expected_terminal_stage": case.terminal_expectation.stage_number,
            "expected_terminal_verdict": case.terminal_expectation.verdict,
            "expected_stage": expectation.stage_number,
            "expected_verdict": expectation.verdict,
            "expected_checks": dict(expectation.checks or {}),
            "terminal_stage": terminal_record.stage_number,
            "terminal_stage_key": terminal_record.stage_key,
            "actual_verdict": terminal_record.verdict,
            "matched_expectation": case_ok,
            "elapsed_seconds": elapsed_seconds,
            "terminal_error_code": _path_lookup(terminal_record.summary, "error_code"),
            "confirmation_required": case_confirmation_required,
            "confirmation_applied": case_confirmation_applied,
            "remediation_events": remediation_events,
            "auto_fix_count": case_auto_fix_count,
            "records": [asdict(record) for record in records],
            "sandbox_root": str(sandbox_root),
            "logs_dir": str(logs_dir),
        }
        case_reports.append(case_report)
        save_json_atomic(case_root / "execution_case_report.json", case_report)
        _emit_progress(
            progress,
            f"case {case_index}/{len(cases)} `{case.key}` {'ok' if case_ok else 'FAILED'} expected={target_stage}:{expectation.verdict} actual={terminal_record.stage_number}:{terminal_record.verdict} elapsed={round(elapsed_seconds, 2)}s",
        )

    stage_summary: List[Dict[str, Any]] = []
    confirmation_required_count = sum(1 for case in case_reports if bool(case.get("confirmation_required")))
    confirmation_applied_count = sum(1 for case in case_reports if bool(case.get("confirmation_applied")))
    auto_fix_count = sum(int(case.get("auto_fix_count") or 0) for case in case_reports)
    for stage_number in stages:
        records = stage_records.get(stage_number, [])
        if not records:
            continue
        target_case_count = sum(1 for case in case_reports if int(case.get("target_stage") or 0) == stage_number)
        target_expectation_matches = sum(
            1 for case in case_reports if int(case.get("target_stage") or 0) == stage_number and bool(case.get("matched_expectation"))
        )
        invalid_output_count = sum(1 for record in records if record.exit_code == 0 and not record.schema_validated)
        stage_summary.append(
            {
                "stage_number": stage_number,
                "stage_key": STAGE_KEYS[stage_number],
                "execution_count": len(records),
                "zero_exit_count": sum(1 for record in records if record.exit_code == 0),
                "nonzero_exit_count": sum(1 for record in records if record.exit_code != 0),
                "invalid_output_count": invalid_output_count,
                "target_case_count": target_case_count,
                "target_expectation_matches": target_expectation_matches,
                "target_expectation_mismatches": target_case_count - target_expectation_matches,
                "average_elapsed_seconds": round(sum(record.elapsed_seconds for record in records) / max(len(records), 1), 4),
            }
        )

    report = {
        "run_id": run_id,
        "mode": "execution",
        "repo_root": str(repo_root_path),
        "status": "ok" if overall_ok else "partial_failure",
        "selected_stages": stages,
        "selected_case_keys": [case.key for case in cases],
        "executed_case_count": len(case_reports),
        "matched_case_count": sum(1 for case in case_reports if bool(case.get("matched_expectation"))),
        "mismatched_case_count": sum(1 for case in case_reports if not bool(case.get("matched_expectation"))),
        "elapsed_seconds": round(time.perf_counter() - overall_started, 4),
        "confirmation_required_count": confirmation_required_count,
        "confirmation_applied_count": confirmation_applied_count,
        "auto_fix_count": auto_fix_count,
        "cases": case_reports,
        "stage_summary": stage_summary,
        "out_dir": str(output_root),
    }
    save_json_atomic(output_root / "execution_benchmark_report.json", report)
    save_text_atomic(output_root / "execution_benchmark_report.md", _render_markdown(report))
    bundle_path = _create_share_bundle(output_root=output_root, report=report)
    report["bundle_path"] = str(bundle_path)
    report["cleanup_deleted_paths"] = []
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run prototype execution benchmark suites for stages 9-13.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--result-root", default="")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--stage", action="append", default=[])
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply-confirmed-fixes", action="store_true")
    args = parser.parse_args(argv)

    if args.list_cases:
        payload = {"cases": list_execution_cases()}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for item in payload["cases"]:
                print(
                    f"{item['key']}	stages={','.join(str(v) for v in item['supported_stages'])}	target={item['terminal_stage']}:{item['terminal_verdict']}	{item['description']}"
                )
        return 0

    report = run_execution_benchmark(
        repo_root=args.repo_root,
        result_root=args.result_root or None,
        case_keys=args.case,
        selected_stages=args.stage,
        progress=None if args.json else _default_progress,
        apply_confirmed_fixes=args.apply_confirmed_fixes,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"run_id={report['run_id']}")
        print(f"status={report['status']}")
        print(f"out_dir={report['out_dir']}")
        if report.get("bundle_path"):
            print(f"bundle_path={report['bundle_path']}")
        if report.get("cleanup_deleted_paths"):
            print(f"cleanup_deleted_paths={len(list(report.get('cleanup_deleted_paths') or []))}")
        print(
            f"cases={report['executed_case_count']} matched={report['matched_case_count']} mismatched={report['mismatched_case_count']} elapsed_seconds={report['elapsed_seconds']}"
        )
        if report.get("confirmation_required_count") or report.get("confirmation_applied_count") or report.get("auto_fix_count"):
            print(
                f"confirmation_required={report.get('confirmation_required_count', 0)} "
                f"confirmation_applied={report.get('confirmation_applied_count', 0)} "
                f"auto_fixes={report.get('auto_fix_count', 0)}"
            )
        for stage in report["stage_summary"]:
            print(
                f"stage={stage['stage_number']} executions={stage['execution_count']} "
                f"target_matches={stage['target_expectation_matches']}/{stage['target_case_count']} avg_seconds={stage['average_elapsed_seconds']}"
            )
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
