from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest
from infra.pipeline.execution_benchmark import (
    ExecutionBenchmarkError,
    list_execution_cases,
    run_execution_benchmark,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_execution_benchmark_joint_batch_records_case_and_stage_results(tmp_path: Path) -> None:
    report = run_execution_benchmark(repo_root=REPO_ROOT, result_root=tmp_path / "results")
    assert report["status"] == "ok"
    assert report["mode"] == "execution"
    assert report["executed_case_count"] == report["matched_case_count"]
    assert report["mismatched_case_count"] == 0
    assert report["cases"]
    assert report["confirmation_required_count"] == 1
    assert report["confirmation_applied_count"] == 0
    assert report["auto_fix_count"] >= 2
    happy = next(item for item in report["cases"] if item["case_key"] == "happy_path")
    assert happy["actual_verdict"] == "satisfied"
    malformed = next(item for item in report["cases"] if item["case_key"] == "malformed_diff")
    assert malformed["terminal_error_code"] == "git_apply_check_failed"
    malformed_record = malformed["records"][0]
    assert Path(malformed_record["stderr_log_path"]).exists()
    assert malformed_record["structured_error"]["code"] == "git_apply_check_failed"
    stage_13 = next(item for item in report["stage_summary"] if item["stage_number"] == 13)
    assert stage_13["target_expectation_matches"] == stage_13["target_case_count"]
    assert (Path(report["out_dir"]) / "execution_benchmark_report.json").exists()
    assert (Path(report["out_dir"]) / "execution_benchmark_report.md").exists()
    out_of_scope = next(item for item in report["cases"] if item["case_key"] == "out_of_scope")
    assert out_of_scope["actual_verdict"] == "confirmation_required"
    assert out_of_scope["confirmation_required"] is True


def test_execution_benchmark_stage_11_slice_uses_stage_specific_expectations(tmp_path: Path) -> None:
    report = run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=tmp_path / "stage11-results",
        selected_stages=["11"],
        case_keys=["happy_path", "static_failure"],
    )
    assert [item["stage_number"] for item in report["stage_summary"]] == [11]
    assert report["matched_case_count"] == 2
    case_lookup = {item["case_key"]: item for item in report["cases"]}
    assert case_lookup["happy_path"]["target_stage"] == 11
    assert case_lookup["happy_path"]["expected_verdict"] == "passed"
    assert case_lookup["happy_path"]["actual_verdict"] == "passed"
    assert case_lookup["static_failure"]["target_stage"] == 11
    assert case_lookup["static_failure"]["expected_verdict"] == "failed"
    assert case_lookup["static_failure"]["actual_verdict"] == "failed"


def test_execution_benchmark_confirmation_required_can_be_applied(tmp_path: Path) -> None:
    report = run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=tmp_path / "confirm-results",
        selected_stages=["9"],
        case_keys=["out_of_scope"],
        apply_confirmed_fixes=True,
    )
    assert report["status"] == "ok"
    assert report["confirmation_required_count"] == 0
    assert report["confirmation_applied_count"] == 1
    case = report["cases"][0]
    assert case["actual_verdict"] == "passed"
    assert case["confirmation_applied"] is True


def test_execution_benchmark_safe_auto_fix_cases_pass(tmp_path: Path) -> None:
    report = run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=tmp_path / "auto-fix-results",
        selected_stages=["9"],
        case_keys=["repairable_target_metadata", "repairable_absolute_paths", "repairable_dotdot_paths"],
    )
    assert report["status"] == "ok"
    assert report["auto_fix_count"] >= 3
    case_lookup = {item["case_key"]: item for item in report["cases"]}
    assert case_lookup["repairable_target_metadata"]["actual_verdict"] == "passed"
    assert case_lookup["repairable_absolute_paths"]["actual_verdict"] == "passed"
    assert case_lookup["repairable_dotdot_paths"]["actual_verdict"] == "passed"


def test_execution_benchmark_repo_escape_stays_hard_fail(tmp_path: Path) -> None:
    report = run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=tmp_path / "repo-escape-results",
        selected_stages=["9"],
        case_keys=["repo_escape_disguised_as_normalization"],
        apply_confirmed_fixes=True,
    )
    case = report["cases"][0]
    assert case["actual_verdict"] == "failed"
    assert case["confirmation_required"] is False
    assert case["confirmation_applied"] is False
    assert report["auto_fix_count"] == 0


def test_execution_benchmark_progress_callback_emits_useful_feedback(tmp_path: Path) -> None:
    messages: list[str] = []
    run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=tmp_path / "progress-results",
        selected_stages=["9", "10"],
        case_keys=["happy_path"],
        progress=messages.append,
    )
    assert any("case 1/1 `happy_path` start" in message for message in messages)
    assert any("stage 9 running" in message for message in messages)
    assert any("stage 10 verdict=applied_cleanly" in message for message in messages)
    assert any("`happy_path` ok expected=10:applied_cleanly actual=10:applied_cleanly" in message for message in messages)


def test_execution_benchmark_rejects_empty_stage_case_intersection(tmp_path: Path) -> None:
    with pytest.raises(ExecutionBenchmarkError):
        run_execution_benchmark(
            repo_root=REPO_ROOT,
            result_root=tmp_path / "invalid-selection",
            selected_stages=["13"],
            case_keys=["malformed_diff"],
        )


def test_execution_benchmark_can_rerun_same_result_root(tmp_path: Path) -> None:
    result_root = tmp_path / "repeatable-results"
    first = run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=result_root,
        case_keys=["malformed_diff"],
        selected_stages=["9"],
    )
    second = run_execution_benchmark(
        repo_root=REPO_ROOT,
        result_root=result_root,
        case_keys=["malformed_diff"],
        selected_stages=["9"],
    )
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["matched_case_count"] == 1
    assert second["cases"][0]["terminal_error_code"] == "git_apply_check_failed"


def test_execution_benchmark_json_mode_success_stdout_is_clean(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "bin" / "pipeline_benchmark.py"),
            "--mode",
            "execution",
            "--case",
            "happy_path",
            "--json",
            "--result-root",
            str(tmp_path / "json-clean-results"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    payload = __import__("json").loads(proc.stdout)
    assert payload["status"] == "ok"
    assert proc.stderr == ""


def test_execution_benchmark_cli_errors_go_to_stderr(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "bin" / "pipeline_benchmark.py"),
            "--mode",
            "execution",
            "--case",
            "malformed_diff",
            "--stage",
            "13",
            "--json",
            "--result-root",
            str(tmp_path / "cli-error-results"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "No execution benchmark cases support stage selection" in proc.stderr


def test_list_execution_cases_exposes_stage_expectations() -> None:
    payload = list_execution_cases()
    assert payload
    happy = next(item for item in payload if item["key"] == "happy_path")
    assert happy["supported_stages"] == [9, 10, 11, 12, 13]
    assert happy["terminal_stage"] == 13
    assert happy["terminal_verdict"] == "satisfied"
    assert any(item["stage_number"] == 11 and item["verdict"] == "passed" for item in happy["stage_expectations"])
    out_of_scope = next(item for item in payload if item["key"] == "out_of_scope")
    assert out_of_scope["terminal_verdict"] == "confirmation_required"
