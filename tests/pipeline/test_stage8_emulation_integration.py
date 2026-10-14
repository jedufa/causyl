
from __future__ import annotations

import builtins
import json
import subprocess
from pathlib import Path

import pytest

from infra.pipeline.core import load_json, load_pipeline_config
from infra.pipeline.pipeline_runner import DEFAULT_PIPELINE, PipelineRunner
from infra.pipeline.stages import patch_generate as patch_generate_stage

from tests.pipeline.test_execution_mvp_stages_8_10 import (
    REAL_REPO_ROOT,
    _case_ids,
    _init_target_repo,
    _make_unified_diff,
    _run_stage8_with_fixture,
    _write_stage7_fixtures,
)
from tests.pipeline.test_execution_mvp_stages_11_13 import (
    _case_ids as _verify_case_ids,
)
from tests.pipeline.test_execution_mvp_stages_11_13 import (
    _init_python_repo,
    _result,
    _stage11_contract,
    _stage12_contract,
    _stage13_contract,
    _write_stage7_artifacts,
    _write_validated_artifact,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "stage8_responses"


def _fixture(name: str) -> Path:
    return FIXTURE_DIR / name


def _assert_envelope(payload: dict) -> None:
    assert payload["stage"]
    assert payload["status"]
    assert payload["job_id"]
    assert payload["run_id"]
    assert isinstance(payload["duration_ms"], int)
    assert isinstance(payload["result"], dict)
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["errors"], list)




def _init_full_pipeline_python_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "full_pipeline_repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Full Pipeline Test"], cwd=repo_root, check=True)
    (repo_root / "app.py").write_text(
        "def greet() -> str:\n"
        "    return 'hello'\n",
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
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_root, check=True)
    return repo_root


def _write_full_pipeline_raw_request(job_dir: Path, *, job_id: str, run_id: str, repo_root: Path) -> None:
    request = {
        "job_id": job_id,
        "run_id": run_id,
        "title": "Update greeting behavior",
        "description": "Update app.py greet to return hello world.",
        "objective": "Modify app.py only so greet returns hello world.",
        "user_notes": "Please update app.py greet to return hello world. Only modify app.py.",
        "repo_root": str(repo_root),
        "allowed_paths": ["app.py"],
        "forbidden_paths": ["tests/test_app.py"],
        "acceptance_criteria": ["scope limited to allowed files", "static compile passes"],
        "test_commands": ["python3 -m pytest -q tests/test_app.py"],
        "changed_files_hint": ["app.py"],
    }
    input_dir = job_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "00_raw_request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")


def _strip_volatiles(payload: dict) -> dict:
    cloned = json.loads(json.dumps(payload))
    cloned.pop("duration_ms", None)
    cloned.pop("started_at", None)
    cloned.pop("finished_at", None)
    return cloned

def _stage8_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixture_name: str,
    target_path: str = "hello.txt",
    execution_mode: str = "fixture",
    allow_live_model_calls: bool = False,
) -> tuple[Path, dict]:
    job_id, run_id = _case_ids(f"stage8-{fixture_name}")
    repo_root = _init_target_repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_fixtures(run_dir, repo_root, target_path, job_id=job_id, run_id=run_id)
    if execution_mode != "fixture":
        monkeypatch.setenv("PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE", execution_mode)
    if allow_live_model_calls:
        monkeypatch.setenv("PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__ALLOW_LIVE_MODEL_CALLS", "true")
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(_fixture(fixture_name)))
    rc = patch_generate_stage.main(
        [
            "--input",
            str(run_dir / "07_patch_plan.json"),
            "--output",
            str(run_dir / "08_patch_artifact.json"),
            "--repo-root",
            str(REAL_REPO_ROOT),
        ]
    )
    assert rc == 0
    payload = load_json(run_dir / "08_patch_artifact.json")
    _assert_envelope(payload)
    return run_dir, payload


def test_fixture_manifest_and_meta_are_consistent() -> None:
    manifest = json.loads(_fixture("manifest.json").read_text(encoding="utf-8"))
    seen = set()
    allowed_families = {
        "unified_diff",
        "apply_patch",
        "invalid",
        "empty",
        "chatty_unified_diff",
        "chatty_apply_patch",
        "multiple_candidates",
    }
    for entry in manifest["fixtures"]:
        name = entry["fixture_name"]
        seen.add(name)
        assert _fixture(name).exists(), name
        meta = json.loads(_fixture(f"{name}.meta.json").read_text(encoding="utf-8"))
        assert meta["fixture_name"] == name
        assert meta["format_family"] in allowed_families
    assert "success_unified_diff.txt" in seen
    assert "realistic_chatty_mixed_response.txt" in seen


def test_stage8_fixture_missing_file_fails_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _case_ids("fixture-missing")
    repo_root = _init_target_repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_fixtures(run_dir, repo_root, "hello.txt", job_id=job_id, run_id=run_id)
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(tmp_path / "missing.diff"))
    rc = patch_generate_stage.main(["--input", str(run_dir / "07_patch_plan.json"), "--output", str(run_dir / "08_patch_artifact.json"), "--repo-root", str(REAL_REPO_ROOT)])
    assert rc == 0
    payload = load_json(run_dir / "08_patch_artifact.json")
    _assert_envelope(payload)
    result = payload["result"]
    assert result["generation_status"] == "generation_failed"
    assert result["generation_metadata"]["failure_source"] == "fixture"
    assert result["result_kind"] == "generation_failed"


def test_stage8_fixture_unreadable_file_fails_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _case_ids("fixture-unreadable")
    repo_root = _init_target_repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_stage7_fixtures(run_dir, repo_root, "hello.txt", job_id=job_id, run_id=run_id)
    fixture_path = tmp_path / "fixture.txt"
    fixture_path.write_text("not used", encoding="utf-8")

    original_read_text = Path.read_text

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        if self == fixture_path:
            raise OSError("synthetic unreadable fixture")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _boom)
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(fixture_path))
    rc = patch_generate_stage.main(["--input", str(run_dir / "07_patch_plan.json"), "--output", str(run_dir / "08_patch_artifact.json"), "--repo-root", str(REAL_REPO_ROOT)])
    assert rc == 0
    result = load_json(run_dir / "08_patch_artifact.json")["result"]
    assert result["generation_status"] == "generation_failed"
    assert result["generation_metadata"]["failure_class"] == "runtime_execution_error"
    assert result["generation_metadata"]["failure_source"] == "fixture"
    assert result["result_kind"] == "generation_failed"


def test_stage8_empty_response_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, payload = _stage8_output(tmp_path, monkeypatch, fixture_name="empty_response.txt")
    result = payload["result"]
    assert result["generation_status"] == "generation_failed"
    assert result["generation_metadata"]["failure_class"] in {"normalization_error", "runtime_execution_error"}
    assert result["result_kind"] == "generation_failed"


def test_multiple_candidate_diff_selection_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, payload = _stage8_output(tmp_path, monkeypatch, fixture_name="realistic_chatty_mixed_response.txt")
    result = payload["result"]
    assert result["generation_status"] == "generation_failed"
    assert "ambiguous_patch_output" in result["generation_failed_reason"]


def test_stage8_respects_patch_plan_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, payload = _stage8_output(tmp_path, monkeypatch, fixture_name="valid_but_wrong_file_patch.txt")
    result = payload["result"]
    assert result["generation_status"] == "generation_failed"
    assert result["generation_metadata"]["failure_source"] == "normalization"


def test_no_provider_module_import_in_fixture_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def _guarded_import(name: str, *args: object, **kwargs: object):
        if name == "openai":
            raise AssertionError("openai import should not occur in fixture mode")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    _, payload = _stage8_output(tmp_path, monkeypatch, fixture_name="success_unified_diff.txt")
    result = payload["result"]
    assert result["generation_status"] == "generated"
    assert result["generation_metadata"]["execution_source"] == "fixture"


def test_disabled_mode_skips_without_provider_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def _guarded_import(name: str, *args: object, **kwargs: object):
        if name == "openai":
            raise AssertionError("openai import should not occur when execution_mode=disabled")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    _, payload = _stage8_output(
        tmp_path,
        monkeypatch,
        fixture_name="success_unified_diff.txt",
        execution_mode="disabled",
    )
    result = payload["result"]
    assert result["result_kind"] == "generation_skipped"
    assert result["generation_skipped"] is True


def test_live_blocked_does_not_fallback_or_init_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def _guarded_import(name: str, *args: object, **kwargs: object):
        if name == "openai":
            raise AssertionError("openai import should not occur when live calls are policy-blocked")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    _, payload = _stage8_output(
        tmp_path,
        monkeypatch,
        fixture_name="success_unified_diff.txt",
        execution_mode="live",
        allow_live_model_calls=False,
    )
    result = payload["result"]
    assert result["result_kind"] == "generation_failed"
    assert result["generation_metadata"]["failure_class"] == "policy_block"
    assert result["generation_metadata"]["failure_source"] == "policy"


def test_stage8_success_fixture_roundtrip_stage8_to_10_and_control_plane_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _case_ids("roundtrip")
    repo_root = _init_target_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_fixtures(run_dir, repo_root, "hello.txt", job_id=job_id, run_id=run_id)
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(_fixture("success_unified_diff.txt")))
    runner = PipelineRunner(repo_root=REAL_REPO_ROOT, stages=DEFAULT_PIPELINE[7:10], config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}})
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] == "FINALIZED"

    apply_payload = load_json(run_dir / "10_apply_result.json")
    _assert_envelope(apply_payload)
    apply_result = apply_payload["result"]["apply_result"]
    assert apply_result["apply_status"] == "applied_cleanly"
    assert (Path(apply_result["workspace_path"]) / "hello.txt").read_text(encoding="utf-8") == "hello world\n"

    job_state = load_json(jobs_root / job_id / "job_state.json")
    assert job_state["status"] == "FINALIZED"
    assert job_state["current_stage"] == "script_10_patch_apply"
    assert job_state["active_head_run_id"] == run_id
    assert job_state["job_id"] == job_id
    assert job_state["runs"][run_id]["completed_stages"] == [
        "script_8_patch_generate",
        "script_9_artifact_validate",
        "script_10_patch_apply",
    ]

    artifact_registry = load_json(jobs_root / job_id / "artifact_registry.json")
    registered_stages = [entry["stage"] for entry in artifact_registry["artifacts"]]
    assert registered_stages.count("script_8_patch_generate") >= 1
    assert registered_stages.count("script_9_artifact_validate") >= 1
    assert registered_stages.count("script_10_patch_apply") >= 1
    assert artifact_registry["indexes"]["by_run_id"][run_id]
    assert artifact_registry["indexes"]["by_stage"]["script_8_patch_generate"]
    assert artifact_registry["indexes"]["by_stage"]["script_9_artifact_validate"]
    assert artifact_registry["indexes"]["by_stage"]["script_10_patch_apply"]


def test_stage8_determinism_repeat_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a").mkdir()
    run_dir_a, payload_a = _stage8_output(tmp_path / "a", monkeypatch, fixture_name="success_unified_diff.txt")
    monkeypatch.delenv("PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE", raising=False)
    monkeypatch.delenv("PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__ALLOW_LIVE_MODEL_CALLS", raising=False)
    (tmp_path / "b").mkdir()
    run_dir_b, payload_b = _stage8_output(tmp_path / "b", monkeypatch, fixture_name="success_unified_diff.txt")
    result_a = payload_a["result"]
    result_b = payload_b["result"]
    assert result_a["patch_artifact"]["content"] == result_b["patch_artifact"]["content"]
    assert result_a["patch_artifact"]["target_files"] == result_b["patch_artifact"]["target_files"]
    assert result_a["generation_metadata"]["selected_model"] == result_b["generation_metadata"]["selected_model"]
    assert result_a["generation_metadata"]["execution_source"] == result_b["generation_metadata"]["execution_source"]
    assert Path(result_a["generation_metadata"]["raw_output_path"]).exists()
    assert Path(result_b["generation_metadata"]["raw_output_path"]).exists()
    assert run_dir_a != run_dir_b


def test_success_fixture_roundtrip_stage8_to_13(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("success-stage13")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(run_dir, repo_root, job_id=job_id, run_id=run_id)
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    runner = PipelineRunner(repo_root=REAL_REPO_ROOT, stages=DEFAULT_PIPELINE[9:13], config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}})
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] == "FINALIZED"
    assert _result(run_dir / "13_contract_verification.json", "contract_verification")["contract_verification_status"] == "satisfied"


def test_stage10_success_but_verification_failure_reaches_13_and_finalization(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("runtime-failure")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=True),
    )
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello runtime fail'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    runner = PipelineRunner(repo_root=REAL_REPO_ROOT, stages=DEFAULT_PIPELINE[9:16], config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}})
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] == "WAITING_REVIEW"

    apply_result = _result(run_dir / "10_apply_result.json", "apply_result")
    contract_result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    finalization = _result(run_dir / "15_finalization.json", "finalization")
    report = _result(run_dir / "16_job_report.json", "job_report")

    assert apply_result["apply_status"] == "applied_cleanly"
    assert contract_result["contract_verification_status"] == "violated"
    assert contract_result["acceptance_assessment"]["runtime_passed"] is False
    assert finalization["final_status"] == "needs_review"
    assert report["final_status"] == "needs_review"
    assert "13_contract_verification.json" in report["artifact_refs"]
    assert "REAL_TEST_FAILURE" in finalization["decision_basis"]["blocking_reason_codes"]


def test_contract_violation_detected_non_live(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("contract-violation")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        allowed_edit_files=["app.py"],
        modify_files=["app.py"],
        stage11_contract=_stage11_contract(required=True),
        stage12_contract=_stage12_contract(required_tests=["python3 -m pytest -q tests/test_app.py"]),
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=True),
    )
    before_app = (repo_root / "app.py").read_text(encoding="utf-8")
    before_test = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before_app, "def greet() -> str:\n    return 'hello world'\n") + _make_unified_diff("tests/test_app.py", before_test, before_test + "\n# scope drift\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py", "tests/test_app.py"], job_id=job_id, run_id=run_id)
    runner = PipelineRunner(repo_root=REAL_REPO_ROOT, stages=DEFAULT_PIPELINE[9:13], config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}})
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] == "WAITING_RETRY_DECISION"
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"


def test_missing_required_tests_evidence(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("missing-required-tests")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    required_command = "python3 -m pytest -q tests/test_app.py"
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        required_tests=[required_command],
        stage12_contract=_stage12_contract(required_tests=[], required=True, checks=[]),
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=True),
    )
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    runner = PipelineRunner(repo_root=REAL_REPO_ROOT, stages=DEFAULT_PIPELINE[9:13], config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}})
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] == "WAITING_RETRY_DECISION"
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert result["acceptance_assessment"]["required_tests_present"] is True
    assert result["acceptance_assessment"]["required_tests_executed"] is False
    required_check = next(item for item in result["checks"] if item["name"] == "required_tests_executed")
    assert required_check["status"] == "failed"
    assert required_command in required_check["evidence"]


def test_no_silent_success_after_stage8_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _case_ids("no-silent-success")
    repo_root = _init_target_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_fixtures(run_dir, repo_root, "hello.txt", job_id=job_id, run_id=run_id)
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(_fixture("empty_response.txt")))
    runner = PipelineRunner(repo_root=REAL_REPO_ROOT, stages=DEFAULT_PIPELINE[7:16], config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}})
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] in {"WAITING_REVIEW", "ABORTED", "WAITING_RETRY_DECISION"}

    stage8 = load_json(run_dir / "08_patch_artifact.json")["result"]
    stage9 = load_json(run_dir / "09_validated_artifact.json")["result"]
    stage10 = load_json(run_dir / "10_apply_result.json")["result"]["apply_result"]
    stage11 = load_json(run_dir / "11_static_verification.json")["result"]["static_verification"]
    stage12 = load_json(run_dir / "12_runtime_verification.json")["result"]["runtime_verification"]
    stage15 = load_json(run_dir / "15_finalization.json")["result"]["finalization"]

    assert stage8["result_kind"] == "generation_failed"
    assert stage9["validation_skipped"] is True
    assert stage10["apply_status"] == "skipped"
    assert stage11["static_verification_status"] == "skipped"
    assert stage12["runtime_verification_status"] == "skipped"
    assert stage15["final_status"] != "accepted"


def test_execution_mode_config_precedence() -> None:
    config = load_pipeline_config(
        repo_root=REAL_REPO_ROOT,
        stage_key="script_8_patch_generate",
        cli_overrides={"stages": {"script_8_patch_generate": {"execution_mode": "disabled"}}},
        env={
            "PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE": "live",
        },
    )
    stage_cfg = config["_stage"]
    assert stage_cfg["execution_mode"] == "disabled"


def test_stage8_target_files_subset_of_patch_plan_modify_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir, payload = _stage8_output(tmp_path, monkeypatch, fixture_name="success_unified_diff.txt")
    result = payload["result"]
    assert result["generation_status"] == "generated"
    target_files = set(result["patch_artifact"]["target_files"])
    patch_plan_payload = load_json(run_dir / "07_patch_plan.json")
    modify_files = {
        str(entry.get("path") or "")
        for entry in patch_plan_payload["result"]["patch_plan"]["modify_files"]
        if isinstance(entry, dict)
    }
    assert target_files
    assert target_files.issubset(modify_files)


def test_acceptance_criteria_mismatch_detected_non_live(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("acceptance-mismatch")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        expected_new_files=["notes.txt"],
        acceptance_criteria=["expected new file created", "runtime tests pass"],
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=True),
    )
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=DEFAULT_PIPELINE[9:13],
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary = runner.run(job_id=job_id, run_id=run_id)
    assert summary["status"] == "WAITING_RETRY_DECISION"
    result = _result(run_dir / "13_contract_verification.json", "contract_verification")
    assert result["contract_verification_status"] == "violated"
    assert result["acceptance_assessment"]["runtime_passed"] is True
    assert result["acceptance_assessment"]["acceptance_criteria_satisfied"] is False
    failed = set(result["acceptance_assessment"]["failed_acceptance_criteria"])
    assert "expected new file created" in failed


def test_scope_mismatch_despite_valid_patch_is_not_semantically_accepted(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("scope-mismatch-explicit")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        allowed_edit_files=["app.py"],
        modify_files=["app.py"],
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=True),
    )
    before_app = (repo_root / "app.py").read_text(encoding="utf-8")
    before_test = (repo_root / "tests" / "test_app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before_app, "def greet() -> str:\n    return 'hello world'\n") + _make_unified_diff(
        "tests/test_app.py",
        before_test,
        before_test + "\n# out of scope drift\n",
    )
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
    assert result["scope_ok"] is False
    assert "required_files_not_touched" in set(result["decision"]["reason_codes"]) or "acceptance_criteria_not_satisfied" in set(result["decision"]["reason_codes"])


def test_artifact_immutability_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id_a = _case_ids("immutability")
    _, run_id_b = _case_ids("immutability")
    repo_root = _init_target_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"

    def _prepare_run(run_id: str) -> Path:
        run_dir = jobs_root / job_id / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_stage7_fixtures(run_dir, repo_root, "hello.txt", job_id=job_id, run_id=run_id)
        return run_dir

    run_dir_a = _prepare_run(run_id_a)
    run_dir_b = _prepare_run(run_id_b)
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(_fixture("success_unified_diff.txt")))
    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=DEFAULT_PIPELINE[7:10],
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary_a = runner.run(job_id=job_id, run_id=run_id_a)
    summary_b = runner.run(job_id=job_id, run_id=run_id_b)

    assert summary_a["run_id"] == run_id_a
    assert summary_b["run_id"] == run_id_b
    assert run_dir_a.exists()
    assert run_dir_b.exists()
    assert load_json(run_dir_a / "10_apply_result.json")["result"]["apply_result"]["apply_status"] == "applied_cleanly"
    assert load_json(run_dir_b / "10_apply_result.json")["result"]["apply_result"]["apply_status"] == "applied_cleanly"

    registry = load_json(jobs_root / job_id / "artifact_registry.json")
    artifacts = list(registry["artifacts"])
    assert any(entry["run_id"] == run_id_a for entry in artifacts)
    assert any(entry["run_id"] == run_id_b for entry in artifacts)
    assert len(registry["indexes"]["by_run_id"][run_id_a]) >= 3
    assert len(registry["indexes"]["by_run_id"][run_id_b]) >= 3


def test_stage15_rejects_when_stage13_rejects_and_report_stays_evidence_bound(tmp_path: Path) -> None:
    job_id, run_id = _verify_case_ids("stage15-stage16-honesty")
    repo_root = _init_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    run_dir = jobs_root / job_id / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_stage7_artifacts(
        run_dir,
        repo_root,
        job_id=job_id,
        run_id=run_id,
        expected_new_files=["notes.txt"],
        acceptance_criteria=["expected new file created", "static compile passes"],
        stage13_contract=_stage13_contract(require_static_success=True, require_runtime_success=False),
    )
    before = (repo_root / "app.py").read_text(encoding="utf-8")
    patch_text = _make_unified_diff("app.py", before, "def greet() -> str:\n    return 'hello world'\n")
    _write_validated_artifact(run_dir, patch_text, ["app.py"], job_id=job_id, run_id=run_id)
    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=DEFAULT_PIPELINE[9:16],
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary = runner.run(job_id=job_id, run_id=run_id)

    assert summary["status"] == "WAITING_REVIEW"
    contract = _result(run_dir / "13_contract_verification.json", "contract_verification")
    finalization = _result(run_dir / "15_finalization.json", "finalization")
    report = _result(run_dir / "16_job_report.json", "job_report")

    assert contract["contract_verification_status"] == "violated"
    assert finalization["final_status"] == "needs_review"
    assert "CONTRACT_NOT_SATISFIED" in finalization["review_reason_codes"]
    assert report["final_status"] == "needs_review"
    assert "contract verification did not reach a satisfied state" in report["residual_risks"]
    assert "13_contract_verification.json" in report["artifact_refs"]
    assert "15_finalization.json" in report["artifact_refs"]
    assert "accepted" not in report["summary"].lower()


def test_full_pipeline_fixture_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, run_id = _case_ids("full-pipeline")
    repo_root = _init_full_pipeline_python_repo(tmp_path)
    jobs_root = tmp_path / "jobs"
    workspace_root = tmp_path / "workspaces"
    job_dir = jobs_root / job_id
    _write_full_pipeline_raw_request(job_dir, job_id=job_id, run_id=run_id, repo_root=repo_root)
    monkeypatch.setenv("PIPELINE_STAGE8_PATCH_RESPONSE_FILE", str(_fixture("full_pipeline_app_success.txt")))

    original_import = builtins.__import__

    def _guarded_import(name: str, *args: object, **kwargs: object):
        if name == "openai":
            raise AssertionError("openai import should not occur in full fixture-backed pipeline run")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)

    runner = PipelineRunner(
        repo_root=REAL_REPO_ROOT,
        stages=DEFAULT_PIPELINE,
        config_overrides={"pipeline": {"jobs_root": str(jobs_root), "workspace_root": str(workspace_root)}},
    )
    summary = runner.run(job_id=job_id, run_id=run_id)

    assert summary["status"] == "FINALIZED"
    run_dir = job_dir / "runs" / run_id
    for stage_num, name in [
        ("01", "01_job_contract.json"),
        ("07", "07_patch_plan.json"),
        ("08", "08_patch_artifact.json"),
        ("09", "09_validated_artifact.json"),
        ("10", "10_apply_result.json"),
        ("13", "13_contract_verification.json"),
        ("15", "15_finalization.json"),
        ("16", "16_job_report.json"),
    ]:
        assert (run_dir / name).exists(), stage_num

    stage8 = load_json(run_dir / "08_patch_artifact.json")
    stage9 = load_json(run_dir / "09_validated_artifact.json")
    stage10 = load_json(run_dir / "10_apply_result.json")
    stage13 = load_json(run_dir / "13_contract_verification.json")
    stage15 = load_json(run_dir / "15_finalization.json")
    stage16 = load_json(run_dir / "16_job_report.json")
    for payload in [stage8, stage9, stage10, stage13, stage15, stage16]:
        _assert_envelope(payload)

    assert stage8["result"]["result_kind"] == "generation_succeeded"
    assert stage8["result"]["generation_metadata"]["execution_source"] == "fixture"
    assert stage9["result"]["validated_artifact"]["is_valid"] is True
    assert stage10["result"]["apply_result"]["apply_status"] == "applied_cleanly"
    assert stage10["result"]["apply_result"]["changed_files"] == ["app.py"]
    assert stage13["result"]["contract_verification"]["contract_verification_status"] == "satisfied"
    assert stage15["result"]["finalization"]["final_status"] == "accepted"
    assert stage16["result"]["job_report"]["final_status"] == "accepted"
    assert "accepted" in stage16["result"]["job_report"]["summary"].lower()

    job_state = load_json(job_dir / "job_state.json")
    assert job_state["job_id"] == job_id
    assert job_state["current_stage"] == "script_16_job_report"
    run_state = job_state["runs"][run_id]
    assert run_state["run_id"] == run_id
    assert run_state["current_stage"] == "script_16_job_report"
    assert run_state["status"] == "FINALIZED"
    assert "script_16_job_report" in run_state["completed_stages"]

    registry = load_json(job_dir / "artifact_registry.json")
    artifacts = list(registry["artifacts"])
    by_stage = registry["indexes"]["by_stage"]
    for stage in [
        "script_8_patch_generate",
        "script_9_artifact_validate",
        "script_10_patch_apply",
        "script_13_contract_verify",
        "script_15_finalize_job",
        "script_16_job_report",
    ]:
        assert stage in by_stage
        assert by_stage[stage]
    assert any(item["run_id"] == run_id and item["stage"] == "script_16_job_report" for item in artifacts)
    assert len(registry["indexes"]["by_run_id"][run_id]) >= 16

    report = stage16["result"]["job_report"]
    finalization = stage15["result"]["finalization"]
    assert report["final_status"] == finalization["final_status"] == "accepted"
    assert "13_contract_verification.json" in report["artifact_refs"]
    assert "15_finalization.json" in report["artifact_refs"]
