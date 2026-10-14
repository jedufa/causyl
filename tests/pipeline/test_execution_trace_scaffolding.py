from __future__ import annotations

from pathlib import Path

from infra.pipeline.core import build_envelope, save_json_atomic, utc_now_iso
from infra.pipeline.decision_trace import (
    build_context_bundle_decision_trace,
    build_context_ranker_decision_trace,
    build_patch_plan_decision_trace,
    build_search_plan_decision_trace,
    build_task_classifier_decision_trace,
)
from infra.pipeline.execution_trace import build_execution_trace
from infra.pipeline.job_state import JobStateStore
from infra.pipeline.retry_scaffolding import build_execution_attempt_scaffold
from infra.pipeline.schemas import (
    validate_execution_attempt_scaffold_payload,
    validate_execution_trace_payload,
)


def _config(tmp_path: Path) -> dict:
    return {
        "pipeline": {"schema_version": "3", "contract_version": "1.2", "tool_version": "test"},
        "_derived": {"jobs_root": str(tmp_path), "repo_root": str(tmp_path)},
    }


def test_decision_trace_builders_return_structured_entries() -> None:
    stage2 = build_task_classifier_decision_trace({
        "task_profile": {
            "task_archetype": "implementation_change",
            "planning_profiles": {"retrieval_profile": "owner_first", "ranking_profile": "implementation", "bundle_profile": "execution_ready"},
            "rationale": ["prompt mentions bugfix"],
            "task_families": ["tool_spec_implementation"],
            "confidence": {"score": 0.9},
        }
    })
    assert stage2 and stage2[0]["stage"] == "script_2_task_classifier"

    stage3 = build_search_plan_decision_trace({
        "search_plan": {
            "lexical_queries": ["retry context"],
            "conceptual_queries": ["ownership control"],
            "negative_targets": ["generated artifacts"],
            "subsystem_hypotheses": ["scheduler"],
            "confidence": {"score": 0.8},
        }
    })
    assert stage3 and stage3[0]["stage"] == "script_3_search_plan"

    stage5 = build_context_ranker_decision_trace({
        "ranked_context": {
            "selected_files": [{"path": "tools/x.py", "bundle_role": "edit_target", "reasons": ["top lexical hit"]}],
            "implementation_targets": [{"path": "tools/x.py"}],
            "reference_context": [{"path": "docs/y.md"}],
            "confidence": {"score": 0.7},
        }
    })
    assert stage5 and stage5[0]["stage"] == "script_5_context_ranker"

    stage6 = build_context_bundle_decision_trace({
        "context_bundle": {
            "files": [{"path": "tools/x.py", "bundle_role": "primary_target"}],
            "bundle_quality_status": "normal",
            "bundle_summary": {"owner_present": True, "support_count": 0, "reference_count": 0, "witness_count": 0, "fallback_hub_count": 0},
            "confidence": {"score": 0.72},
        }
    })
    assert stage6 and stage6[0]["stage"] == "script_6_context_bundle"

    stage7 = build_patch_plan_decision_trace({
        "patch_plan": {
            "modify_files": [{"path": "tools/x.py"}],
            "planning_confidence": 0.75,
            "unresolved_uncertainties": ["Need exact retry semantics"],
            "output_contract_mode": "patch_contract",
        }
    }, execution_contract={"execution_contract": {"execution_mode": "patch_generation", "generation_applicable": True, "allowed_edit_files": ["tools/x.py"]}})
    assert stage7 and stage7[0]["stage"] == "script_7_patch_plan"


def test_execution_trace_and_attempt_scaffold_are_buildable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    job_dir = tmp_path / "JOB-T1"
    run_id = "RUN-T1"
    branch_id = "BRANCH-main"
    state_store = JobStateStore(job_dir)
    state_store.initialize_job(
        job_id="JOB-T1",
        branch_id=branch_id,
        run_id=run_id,
        schema_version="3",
        contract_version="1.2",
        stage_sequence=["script_1_job_intake", "script_7_patch_plan"],
    )
    run_dir = job_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # stage output with decision trace
    envelope = build_envelope(
        stage="script_1_job_intake",
        job_id="JOB-T1",
        run_id=run_id,
        input_payload={"seed": True},
        result={"job_contract": {"job_id": "JOB-T1", "repo_root": str(tmp_path)}, "decision_trace": [{"stage": "script_1_job_intake", "kind": "decision", "summary": "normalized raw request"}]},
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    save_json_atomic(run_dir / "01_job_contract.json", envelope)
    save_json_atomic(run_dir / "logs" / "01_job_intake.exec.json", {"result": {"duration_ms": 5}})
    save_json_atomic(
        run_dir / "07_execution_contract.json",
        {
            "job_id": "JOB-T1",
            "run_id": run_id,
            "stage": "script_7_patch_plan",
            "source_patch_plan_artifact": "07_patch_plan.json",
            "source_context_bundle_artifact": "06_context_bundle.json",
            "execution_contract": {
                "task_mode": "implementation_change",
                "execution_mode": "patch_generation",
                "output_contract_mode": "patch_contract",
                "objective": "fix retry bug",
                "generation_applicable": True,
                "allowed_edit_files": ["tools/x.py"],
            },
        },
    )
    state_store.mark_stage_complete(run_id=run_id, stage_key="script_1_job_intake", artifact_id="ART-1", execution_log_id="ART-LOG1")
    scaffold = build_execution_attempt_scaffold(job_dir=job_dir, run_id=run_id, config=config)
    validate_execution_attempt_scaffold_payload(scaffold)
    assert scaffold["planned_tool_sequence"] == [
        "generate_patch",
        "validate_patch",
        "apply_patch",
        "verify_static",
        "verify_runtime",
        "verify_contract",
    ]

    trace = build_execution_trace(job_dir=job_dir, run_id=run_id, config=config, status="WAITING_REVIEW")
    validate_execution_trace_payload(trace)
    assert trace["stage_trace"][0]["decision_count"] == 1
