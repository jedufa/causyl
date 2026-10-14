from __future__ import annotations

import json
from pathlib import Path

from infra.pipeline import automation
from infra.pipeline.autonomous_controller import evaluate_stop_condition, prepare_retry_run
from infra.pipeline.core import build_envelope, save_json_atomic, utc_now_iso
from infra.pipeline.failure_similarity import build_failure_signature
from infra.pipeline.job_state import JobStateStore
from infra.pipeline.pipeline_runner import DEFAULT_PIPELINE
from infra.pipeline.strategy_policy import select_retry_strategy


def _write_envelope(path: Path, *, stage: str, job_id: str, run_id: str, result: dict) -> None:
    save_json_atomic(
        path,
        build_envelope(
            stage=stage,
            job_id=job_id,
            run_id=run_id,
            input_payload={"seed": True},
            result=result,
            tool_version="test",
            started_at_iso=utc_now_iso(),
            finished_at_iso=utc_now_iso(),
        ),
    )


def test_prepare_retry_run_seeds_stage8_inputs(tmp_path: Path) -> None:
    job_id = "JOB-retry-seed"
    run_id = "RUN-parent"
    job_dir = tmp_path / "jobs" / job_id
    run_dir = job_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    store = JobStateStore(job_dir)
    store.initialize_job(
        job_id=job_id,
        branch_id="BRANCH-main",
        run_id=run_id,
        schema_version="3",
        contract_version="1.2",
        stage_sequence=[stage.key for stage in DEFAULT_PIPELINE],
    )
    _write_envelope(
        run_dir / "07_patch_plan.json",
        stage="script_7_patch_plan",
        job_id=job_id,
        run_id=run_id,
        result={
            "job_contract": {"repo_root": str(tmp_path / "repo")},
            "patch_plan": {"modify_files": [{"path": "app.py"}]},
        },
    )
    save_json_atomic(
        run_dir / "07_execution_contract.json",
        {
            "execution_contract": {
                "objective": "fix bug",
                "generation_applicable": True,
                "allowed_edit_files": ["app.py"],
                "forbidden_edit_files": [],
                "implementation_target_files": ["app.py"],
                "validation_target_files": ["tests/test_app.py"],
                "must_not_create_files": True,
                "may_create_files": False,
                "expected_new_files": [],
                "included_files": [],
                "generation_files": [],
                "constraints": {
                    "acceptance_criteria": [],
                    "required_tests": [],
                    "must_not_change": [],
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
                    "artifact_size_limit_chars": 1000,
                },
                "stage10_apply_requirements": {
                    "isolated_workspace_required": True,
                    "source_repo_mutation_forbidden": True,
                    "changed_files_must_be_enumerated": True,
                    "apply_conflict_must_fail_closed": True,
                },
                "prompt_budget": {
                    "file_count": 1,
                    "full_file_count": 1,
                    "snippet_count": 0,
                    "reference_only_count": 0,
                    "estimated_prompt_size_chars": 10,
                    "estimated_prompt_tokens": 5,
                    "total_chars": 10,
                    "max_total_chars": 1000,
                    "degradation_policy": "none",
                    "degradation_applied": False,
                    "degradation_actions": [],
                },
            }
        },
    )

    next_run = prepare_retry_run(
        job_dir=job_dir,
        config={"mutation": {"escalation_cooldown": 1}, "branching": {"max_total_branches": 10}},
        source_run_id=run_id,
        repair_decision={
            "branch_action": "same_branch_reentry",
            "recommended_reentry_stage": "script_8_patch_generate",
            "retry_mode": "patch_repair",
            "retry_reason": "retry local fix",
            "retry_contract": {"selected_strategy": "local_fix", "negative_constraints": ["do not repeat malformed diff"]},
            "mutation_level": 1,
        },
    )

    seeded_patch_plan = json.loads((job_dir / "runs" / next_run["run_id"] / "07_patch_plan.json").read_text(encoding="utf-8"))
    seeded_execution_contract = json.loads((job_dir / "runs" / next_run["run_id"] / "07_execution_contract.json").read_text(encoding="utf-8"))
    retry_contract_file = json.loads((job_dir / "runs" / next_run["run_id"] / "retry_contract.json").read_text(encoding="utf-8"))

    assert seeded_patch_plan["run_id"] == next_run["run_id"]
    assert seeded_patch_plan["result"]["retry_contract"]["selected_strategy"] == "local_fix"
    assert seeded_execution_contract["execution_contract"]["retry_contract"]["negative_constraints"] == ["do not repeat malformed diff"]
    assert retry_contract_file["selected_strategy"] == "local_fix"


def test_prepare_retry_run_fork_branch_advances_attempt_index(tmp_path: Path) -> None:
    job_id = "JOB-fork-attempt"
    run_id = "RUN-parent"
    job_dir = tmp_path / "jobs" / job_id
    run_dir = job_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    store = JobStateStore(job_dir)
    store.initialize_job(
        job_id=job_id,
        branch_id="BRANCH-main",
        run_id=run_id,
        schema_version="3",
        contract_version="1.2",
        stage_sequence=[stage.key for stage in DEFAULT_PIPELINE],
    )
    _write_envelope(
        run_dir / "06_context_bundle.json",
        stage="script_6_context_bundle",
        job_id=job_id,
        run_id=run_id,
        result={
            "context_bundle": {
                "editable_targets": [{"path": "app.py"}],
                "validation_targets": [{"path": "tests/test_app.py"}],
                "read_only_context": [],
            }
        },
    )

    next_run = prepare_retry_run(
        job_dir=job_dir,
        config={"mutation": {"escalation_cooldown": 1}, "branching": {"max_total_branches": 10}},
        source_run_id=run_id,
        repair_decision={
            "branch_action": "fork_new_branch",
            "recommended_reentry_stage": "script_7_patch_plan",
            "retry_mode": "escalate",
            "retry_reason": "fork and replan",
            "retry_contract": {"selected_strategy": "plan_revision"},
            "mutation_level": 2,
            "next_branch_purpose": "plan_revision",
        },
    )

    state = store.load()
    child_run = dict(state["runs"][next_run["run_id"]])
    assert child_run["attempt_index"] == 1
    assert child_run["parent_run_id"] == run_id
    assert child_run["branch_id"] != "BRANCH-main"


def test_run_full_auto_loops_until_terminal_outcome(monkeypatch, tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    job_id = "JOB-loop"
    run_ids = ["RUN-001", "RUN-002"]
    call_count = {"value": 0}
    config = {"_derived": {"jobs_root": str(jobs_root)}, "user_budgets": {"max_attempts_total": 30}}

    monkeypatch.setattr(automation, "load_pipeline_config", lambda **kwargs: config)
    monkeypatch.setattr(
        automation,
        "record_attempt_result",
        lambda **kwargs: {"branches": {"BRANCH-main": {"stagnant_attempts": 0}}, "totals": {"attempts_total": call_count["value"] + 1}},
    )
    monkeypatch.setattr(
        automation,
        "prepare_retry_run",
        lambda **kwargs: {"run_id": run_ids[1], "branch_id": "BRANCH-main", "reentry_stage": "script_8_patch_generate"},
    )

    def _fake_evaluate_stop_condition(*, repair_decision, **kwargs):
        if repair_decision.get("should_retry"):
            return {"stop": False, "reason_code": None, "message": "", "recommended_next_step": "local_fix"}
        return {"stop": True, "reason_code": "terminal_outcome", "message": "done", "recommended_next_step": None}

    monkeypatch.setattr(automation, "evaluate_stop_condition", _fake_evaluate_stop_condition)

    class FakeRunner:
        def __init__(self, *, repo_root, stages=None, config_overrides=None, progress_callback=None) -> None:
            self.progress_callback = progress_callback

        def run(self, job_id: str, resume: bool = False, stop_after_stage: str = "") -> dict:
            run_id = run_ids[call_count["value"]]
            call_count["value"] += 1
            run_dir = jobs_root / job_id / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            if run_id == "RUN-001":
                _write_envelope(
                    run_dir / "14_repair_decision.json",
                    stage="script_14_repair_loop",
                    job_id=job_id,
                    run_id=run_id,
                    result={"repair_decision": {"should_retry": True, "retry_authorization_state": "authorized", "selected_strategy": "local_fix"}},
                )
                _write_envelope(
                    run_dir / "15_finalization.json",
                    stage="script_15_finalize_job",
                    job_id=job_id,
                    run_id=run_id,
                    result={"finalization": {"final_status": "needs_review"}},
                )
                return {"job_id": job_id, "run_id": run_id, "job_dir": str(jobs_root / job_id), "status": "WAITING_REVIEW", "branch_id": "BRANCH-main"}
            _write_envelope(
                run_dir / "14_repair_decision.json",
                stage="script_14_repair_loop",
                job_id=job_id,
                run_id=run_id,
                result={"repair_decision": {"should_retry": False, "retry_authorization_state": "not_allowed", "selected_strategy": None}},
            )
            _write_envelope(
                run_dir / "15_finalization.json",
                stage="script_15_finalize_job",
                job_id=job_id,
                run_id=run_id,
                result={"finalization": {"final_status": "accepted"}},
            )
            return {"job_id": job_id, "run_id": run_id, "job_dir": str(jobs_root / job_id), "status": "FINALIZED", "branch_id": "BRANCH-main"}

    monkeypatch.setattr(automation, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(automation, "persist_controller_stop", lambda **kwargs: {})
    monkeypatch.setattr(automation, "render_review", lambda **kwargs: {})
    monkeypatch.setattr(automation, "persist_operator_decision", lambda **kwargs: {"decision": "APPROVE"})
    monkeypatch.setattr(
        automation,
        "build_run_summary",
        lambda **kwargs: {"job_id": job_id, "run_id": kwargs["run_id"], "status": kwargs.get("status"), "artifacts": {}, "selected_files": []},
    )
    monkeypatch.setattr(
        automation,
        "persist_run_summary",
        lambda **kwargs: {"json_path": str(jobs_root / job_id / "summary.json"), "markdown_path": str(jobs_root / job_id / "summary.md")},
    )

    events: list[str] = []

    summary = automation.run_full_auto(
        repo_root=tmp_path,
        prompt_text="loop until accepted",
        job_id=job_id,
        progress_callback=lambda event, payload: events.append(event),
    )

    assert summary["run_id"] == "RUN-002"
    assert call_count["value"] == 2
    assert "retry_prepared" in events


def test_run_full_auto_honors_user_stop_after_current_attempt(monkeypatch, tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    job_id = "JOB-user-stop"
    run_id = "RUN-001"
    config = {"_derived": {"jobs_root": str(jobs_root)}, "user_budgets": {"max_attempts_total": 30}}
    recorded_runs: list[str] = []

    monkeypatch.setattr(automation, "load_pipeline_config", lambda **kwargs: config)
    monkeypatch.setattr(
        automation,
        "record_attempt_result",
        lambda **kwargs: (recorded_runs.append(str(kwargs["run_id"])), {"branches": {"BRANCH-main": {"stagnant_attempts": 0}}, "totals": {"attempts_total": 1}})[1],
    )

    class FakeRunner:
        def __init__(self, *, repo_root, stages=None, config_overrides=None, progress_callback=None) -> None:
            self.progress_callback = progress_callback

        def run(self, job_id: str, resume: bool = False, stop_after_stage: str = "") -> dict:
            run_dir = jobs_root / job_id / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            _write_envelope(
                run_dir / "14_repair_decision.json",
                stage="script_14_repair_loop",
                job_id=job_id,
                run_id=run_id,
                result={
                    "repair_decision": {
                        "should_retry": True,
                        "retry_authorization_state": "authorized",
                        "selected_strategy": "local_fix",
                        "recommended_reentry_stage": "script_8_patch_generate",
                    }
                },
            )
            _write_envelope(
                run_dir / "15_finalization.json",
                stage="script_15_finalize_job",
                job_id=job_id,
                run_id=run_id,
                result={"finalization": {"final_status": "needs_review"}},
            )
            return {"job_id": job_id, "run_id": run_id, "job_dir": str(jobs_root / job_id), "status": "WAITING_REVIEW", "branch_id": "BRANCH-main"}

    monkeypatch.setattr(automation, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(automation, "persist_controller_stop", lambda **kwargs: {})
    monkeypatch.setattr(automation, "render_review", lambda **kwargs: {})
    monkeypatch.setattr(automation, "persist_operator_decision", lambda **kwargs: {"decision": "APPROVE"})
    monkeypatch.setattr(
        automation,
        "build_run_summary",
        lambda **kwargs: {"job_id": job_id, "run_id": kwargs["run_id"], "status": kwargs.get("status"), "artifacts": {}, "selected_files": []},
    )
    monkeypatch.setattr(
        automation,
        "persist_run_summary",
        lambda **kwargs: {"json_path": str(jobs_root / job_id / "summary.json"), "markdown_path": str(jobs_root / job_id / "summary.md")},
    )

    summary = automation.run_full_auto(
        repo_root=tmp_path,
        prompt_text="stop after first attempt",
        job_id=job_id,
        stop_requested=lambda: True,
    )

    assert summary["run_id"] == run_id
    assert summary["controller_stop"]["reason_code"] == "user_cancelled"
    assert summary["controller_stop"]["recommended_next_step"] == "script_8_patch_generate"
    assert recorded_runs == [run_id]


def test_prepare_retry_run_reuses_same_retry_branch_for_same_purpose(tmp_path: Path) -> None:
    job_id = "JOB-reuse-branch"
    run_id = "RUN-parent"
    job_dir = tmp_path / "jobs" / job_id
    run_dir = job_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    store = JobStateStore(job_dir)
    store.initialize_job(
        job_id=job_id,
        branch_id="BRANCH-main",
        run_id=run_id,
        schema_version="3",
        contract_version="1.2",
        stage_sequence=[stage.key for stage in DEFAULT_PIPELINE],
    )
    _write_envelope(
        run_dir / "06_context_bundle.json",
        stage="script_6_context_bundle",
        job_id=job_id,
        run_id=run_id,
        result={
            "context_bundle": {
                "editable_targets": [{"path": "app.py"}],
                "validation_targets": [{"path": "tests/test_app.py"}],
                "read_only_context": [],
            }
        },
    )

    first = prepare_retry_run(
        job_dir=job_dir,
        config={"mutation": {"escalation_cooldown": 1}, "branching": {"max_total_branches": 10}, "orchestrator": {"allow_same_branch_reentry": True}},
        source_run_id=run_id,
        repair_decision={
            "branch_action": "fork_new_branch",
            "recommended_reentry_stage": "script_7_patch_plan",
            "retry_mode": "escalate",
            "retry_reason": "first plan revision",
            "retry_contract": {"selected_strategy": "plan_revision"},
            "mutation_level": 2,
            "next_branch_purpose": "plan_revision",
        },
    )
    second = prepare_retry_run(
        job_dir=job_dir,
        config={"mutation": {"escalation_cooldown": 1}, "branching": {"max_total_branches": 10}, "orchestrator": {"allow_same_branch_reentry": True}},
        source_run_id=first["run_id"],
        repair_decision={
            "branch_action": "fork_new_branch",
            "recommended_reentry_stage": "script_7_patch_plan",
            "retry_mode": "escalate",
            "retry_reason": "second plan revision",
            "retry_contract": {"selected_strategy": "plan_revision"},
            "mutation_level": 2,
            "next_branch_purpose": "plan_revision",
        },
    )

    assert first["branch_id"] != "BRANCH-main"
    assert second["branch_id"] == first["branch_id"]


def test_build_failure_signature_ignores_volatile_run_and_artifact_ids() -> None:
    left = build_failure_signature(
        repo_root=".",
        primary_hypothesis="contract_not_satisfied",
        failure_summary="empty_patch_output from RUN-20260326-123225-08d0b8 in /tmp/jobs/JOB-20260326-123225-x/runs/RUN-20260326-123225-08d0b8/14_repair_decision.json",
        blocking_reason_codes=["empty_patch_output"],
        feedback_bundle={"affected_files": [], "failing_tests": [], "excerpts": ["artifact ART-a1d97a94bbd8397328f8 recorded empty_patch_output"]},
        apply_result={"changed_files": []},
        diagnostics=[],
    )
    right = build_failure_signature(
        repo_root=".",
        primary_hypothesis="contract_not_satisfied",
        failure_summary="empty_patch_output from RUN-20260326-125405-1b4ace in /tmp/jobs/JOB-20260326-125405-y/runs/RUN-20260326-125405-1b4ace/14_repair_decision.json",
        blocking_reason_codes=["empty_patch_output"],
        feedback_bundle={"affected_files": [], "failing_tests": [], "excerpts": ["artifact ART-b45ce88ed39c046e2d72 recorded empty_patch_output"]},
        apply_result={"changed_files": []},
        diagnostics=[],
    )

    assert left["normalized_message_hash"] == right["normalized_message_hash"]
    assert left["key_error_tokens"] == right["key_error_tokens"]


def test_run_full_auto_converts_retry_preparation_failure_into_controller_stop(monkeypatch, tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    job_id = "JOB-branch-budget"
    run_id = "RUN-001"
    config = {"_derived": {"jobs_root": str(jobs_root)}, "user_budgets": {"max_attempts_total": 30}}

    monkeypatch.setattr(automation, "load_pipeline_config", lambda **kwargs: config)
    monkeypatch.setattr(
        automation,
        "record_attempt_result",
        lambda **kwargs: {"branches": {"BRANCH-main": {"stagnant_attempts": 0}}, "totals": {"attempts_total": 1}},
    )
    monkeypatch.setattr(
        automation,
        "evaluate_stop_condition",
        lambda **kwargs: {"stop": False, "reason_code": None, "message": "", "recommended_next_step": "plan_revision"},
    )

    class FakeRunner:
        def __init__(self, *, repo_root, stages=None, config_overrides=None, progress_callback=None) -> None:
            self.progress_callback = progress_callback

        def run(self, job_id: str, resume: bool = False, stop_after_stage: str = "") -> dict:
            run_dir = jobs_root / job_id / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            _write_envelope(
                run_dir / "14_repair_decision.json",
                stage="script_14_repair_loop",
                job_id=job_id,
                run_id=run_id,
                result={
                    "repair_decision": {
                        "should_retry": True,
                        "retry_authorization_state": "authorized",
                        "selected_strategy": "plan_revision",
                        "recommended_reentry_stage": "script_7_patch_plan",
                        "branch_action": "fork_new_branch",
                    }
                },
            )
            _write_envelope(
                run_dir / "15_finalization.json",
                stage="script_15_finalize_job",
                job_id=job_id,
                run_id=run_id,
                result={"finalization": {"final_status": "needs_review"}},
            )
            return {"job_id": job_id, "run_id": run_id, "job_dir": str(jobs_root / job_id), "status": "WAITING_REVIEW", "branch_id": "BRANCH-main"}

    monkeypatch.setattr(automation, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(
        automation,
        "prepare_retry_run",
        lambda **kwargs: (_ for _ in ()).throw(
            automation.PipelineError("branch_budget_exhausted", "maximum retry branches exceeded", exit_code=3)
        ),
    )
    monkeypatch.setattr(automation, "persist_controller_stop", lambda **kwargs: {})
    monkeypatch.setattr(automation, "render_review", lambda **kwargs: {})
    monkeypatch.setattr(automation, "persist_operator_decision", lambda **kwargs: {"decision": "APPROVE"})
    monkeypatch.setattr(
        automation,
        "build_run_summary",
        lambda **kwargs: {"job_id": job_id, "run_id": kwargs["run_id"], "status": kwargs.get("status"), "artifacts": {}, "selected_files": []},
    )
    monkeypatch.setattr(
        automation,
        "persist_run_summary",
        lambda **kwargs: {"json_path": str(jobs_root / job_id / "summary.json"), "markdown_path": str(jobs_root / job_id / "summary.md")},
    )

    summary = automation.run_full_auto(
        repo_root=tmp_path,
        prompt_text="convert retry-preparation failure into controller stop",
        job_id=job_id,
    )

    assert summary["run_id"] == run_id
    assert summary["controller_stop"]["reason_code"] == "branch_budget_exhausted"


def test_select_retry_strategy_escalates_repeated_plan_review_block() -> None:
    config = {"mutation": {"min_attempts_per_level": 1}, "orchestrator": {"low_confidence_threshold": 0.6}}

    context_expansion = select_retry_strategy(
        config=config,
        branch_state={
            "current_mutation_level": 2,
            "same_level_attempts": 1,
            "identical_attempt_repeats": 1,
            "cooldown_remaining": 0,
            "failure_clusters": {},
        },
        failure_cluster_id="CLUSTER-1",
        primary_hypothesis="contract_not_satisfied",
        blocking_reason_codes=["CONTRACT_SCOPE_VIOLATION"],
        plan_execution_status="READY_FOR_REVIEW",
        contract_status="violated",
        confidence_score=0.9,
        equivalent_attempt_detected=False,
        progress_snapshot={
            "plan_review_blocked": True,
            "stagnation_detected": True,
            "regression_detected": False,
            "materially_improved": False,
        },
    )
    task_reinterpretation = select_retry_strategy(
        config=config,
        branch_state={
            "current_mutation_level": 3,
            "same_level_attempts": 1,
            "identical_attempt_repeats": 2,
            "cooldown_remaining": 0,
            "failure_clusters": {},
        },
        failure_cluster_id="CLUSTER-1",
        primary_hypothesis="contract_not_satisfied",
        blocking_reason_codes=["CONTRACT_SCOPE_VIOLATION"],
        plan_execution_status="READY_FOR_REVIEW",
        contract_status="violated",
        confidence_score=0.9,
        equivalent_attempt_detected=False,
        progress_snapshot={
            "plan_review_blocked": True,
            "stagnation_detected": True,
            "regression_detected": False,
            "materially_improved": False,
        },
    )

    assert context_expansion["selected_strategy"] == "context_expansion"
    assert task_reinterpretation["selected_strategy"] == "task_reinterpretation"


def test_evaluate_stop_condition_stops_after_identical_retries_exhaust_escalation() -> None:
    controller = {
        "budgets": {"max_attempts_total": 30, "max_model_tokens_total": 100000, "max_wall_clock_minutes": 60, "max_stagnant_attempts": 5},
        "totals": {"attempts_total": 6, "model_tokens_total": 1000, "wall_clock_started_at": utc_now_iso()},
        "branches": {
            "BRANCH-main": {
                "stagnant_attempts": 2,
                "identical_attempt_repeats": 2,
                "current_mutation_level": 4,
            }
        },
    }

    decision = evaluate_stop_condition(
        controller=controller,
        branch_id="BRANCH-main",
        repair_decision={
            "should_retry": True,
            "retry_authorization_state": "authorized",
            "selected_strategy": "task_reinterpretation",
        },
    )

    assert decision["stop"] is True
    assert decision["reason_code"] == "no_progress"
