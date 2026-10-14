from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, utc_now_iso
from infra.pipeline.schemas import (
    validate_apply_result,
    validate_execution_contract_payload,
    validate_patch_plan_result,
    validate_runtime_verification_result,
    validate_stage_input,
    validate_static_verification_result,
)
from infra.pipeline.verification_policy import apply_status_blocks_verification, build_runtime_profile
from infra.pipeline.verification_runner import (
    execute_verification_check,
    extract_failing_test_identifiers,
    prepare_verification_workspace,
    summarize_check_statuses,
)

STAGE_KEY = "script_12_verify_runtime"


def _load_patch_plan_context(run_dir: Path) -> Dict[str, Any]:
    payload = load_json(run_dir / "07_patch_plan.json")
    env = validate_stage_input(payload, ["job_contract", "patch_plan"])
    validate_patch_plan_result(dict(payload.get("result") or {}))
    return {
        "payload": payload,
        "job_contract": dict(env.result.get("job_contract") or {}),
        "patch_plan": dict(env.result.get("patch_plan") or {}),
    }


def _load_execution_contract(run_dir: Path) -> Dict[str, Any]:
    payload = load_json(run_dir / "07_execution_contract.json")
    validate_execution_contract_payload(payload)
    return {"payload": payload, "execution_contract": dict(payload.get("execution_contract") or {})}


def _artifact_lineage(run_dir: Path, *, produced_logs: List[str]) -> Dict[str, Any]:
    return {
        "run_dir": str(run_dir.resolve()),
        "source_artifacts": {
            "patch_plan": str((run_dir / "07_patch_plan.json").resolve()),
            "execution_contract": str((run_dir / "07_execution_contract.json").resolve()),
            "apply_result": str((run_dir / "10_apply_result.json").resolve()),
            "static_verification": str((run_dir / "11_static_verification.json").resolve()),
        },
        "produced_logs": list(produced_logs),
    }


def _diagnostic(reason_code: str, message: str, evidence: str = "") -> Dict[str, str]:
    item: Dict[str, str] = {
        "reason_code": str(reason_code),
        "message": str(message),
    }
    evidence_text = str(evidence or "").strip()
    if evidence_text:
        item["evidence"] = evidence_text
    return item


def _final_result(
    *,
    status: str,
    required: bool,
    workspace_path: str,
    source_workspace_path: str,
    profile_source: str,
    require_static_pass: bool,
    executed_checks: List[Mapping[str, Any]],
    diagnostics: List[Mapping[str, Any]],
    run_dir: Path,
) -> Dict[str, Any]:
    summary = summarize_check_statuses(executed_checks)
    result = {
        "runtime_verification": {
            "runtime_verification_status": str(status),
            "required": bool(required),
            "workspace_path": str(workspace_path),
            "source_workspace_path": str(source_workspace_path),
            "profile_source": str(profile_source),
            "require_static_pass": bool(require_static_pass),
            "executed_checks": [dict(item) for item in executed_checks],
            "summary": summary,
            "diagnostics": [dict(item) for item in diagnostics],
            "artifact_lineage": _artifact_lineage(run_dir, produced_logs=[str(item.get("log_path") or "") for item in executed_checks if str(item.get("log_path") or "").strip()]),
            "overall_status": str(status),
            "test_runs": [dict(item) for item in executed_checks],
            "build_runs": [],
            "failed_checks": [str(item.get("name") or "") for item in executed_checks if str(item.get("status") or "") == "failed"],
        }
    }
    validate_runtime_verification_result(result)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 12 runtime verification")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["static_verification"])
        validate_static_verification_result(dict(env.result))
        static_verification = dict(env.result.get("static_verification") or {})
        run_dir = Path(args.input).resolve().parent
        patch_plan_context = _load_patch_plan_context(run_dir)
        execution_context = _load_execution_contract(run_dir)
        apply_payload = load_json(run_dir / "10_apply_result.json")
        validate_stage_input(apply_payload, ["apply_result"])
        validate_apply_result({"apply_result": apply_payload.get("result", {}).get("apply_result")})
        apply_result = dict(apply_payload.get("result", {}).get("apply_result") or {})
        job_contract = dict(patch_plan_context.get("job_contract") or {})
        execution_contract = dict(execution_context.get("execution_contract") or {})

        apply_status = str(apply_result.get("apply_status") or "")
        source_workspace_path = str(apply_result.get("workspace_path") or "")
        repo_root = str(job_contract.get("repo_root") or "").strip()
        execution_mode = str(execution_contract.get("execution_mode") or "review_only")
        generation_applicable = bool(execution_contract.get("generation_applicable", False))
        static_status = str(static_verification.get("static_verification_status") or "")
        static_required = bool(static_verification.get("required", False))

        diagnostics: List[Mapping[str, Any]] = []
        executed_checks: List[Mapping[str, Any]] = []

        try:
            profile = build_runtime_profile(config=config, execution_contract=execution_contract, apply_result=apply_result)
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid_runtime_profile", f"runtime verification profile could not be resolved: {exc}"))
            result = _final_result(
                status="failed",
                required=True,
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source="invalid",
                require_static_pass=True,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        if execution_mode != "patch_generation" or not generation_applicable:
            diagnostics.append(_diagnostic("non_generation_mode", "runtime verification skipped because Stage 7 marked execution as non-generative or review-only", execution_mode))
            result = _final_result(
                status="skipped",
                required=bool(profile.required),
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source=profile.profile_source,
                require_static_pass=profile.require_static_pass,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        if apply_status_blocks_verification(apply_status=apply_status, profile=profile):
            diagnostics.append(_diagnostic("apply_not_clean", "runtime verification skipped because apply_result.apply_status is blocked by policy or not applied_cleanly", apply_status))
            result = _final_result(
                status="skipped",
                required=bool(profile.required),
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source=profile.profile_source,
                require_static_pass=profile.require_static_pass,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        if not repo_root:
            diagnostics.append(_diagnostic("missing_repo_root", "job_contract.repo_root is required to build the verification workspace"))
            result = _final_result(
                status="failed",
                required=bool(profile.required),
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source=profile.profile_source,
                require_static_pass=profile.require_static_pass,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        if profile.require_static_pass and ((static_required and static_status != "passed") or static_status == "failed"):
            diagnostics.append(_diagnostic("static_verification_not_passed", "runtime verification did not run because static verification was not in a passed state", static_status))
            result = _final_result(
                status="skipped",
                required=bool(profile.required),
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source=profile.profile_source,
                require_static_pass=profile.require_static_pass,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        if profile.missing_profile_reason_code:
            diagnostics.append(_diagnostic(profile.missing_profile_reason_code, "runtime verification was required but no bounded runtime profile could be resolved"))
            result = _final_result(
                status="failed",
                required=bool(profile.required),
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source=profile.profile_source,
                require_static_pass=profile.require_static_pass,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        if not profile.checks:
            diagnostics.append(_diagnostic("no_runtime_checks_selected", "no runtime checks were selected for this apply result"))
            result = _final_result(
                status="skipped",
                required=bool(profile.required),
                workspace_path=source_workspace_path,
                source_workspace_path=source_workspace_path,
                profile_source=profile.profile_source,
                require_static_pass=profile.require_static_pass,
                executed_checks=executed_checks,
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        workspace = prepare_verification_workspace(
            repo_root=repo_root,
            workspace_root=str(config.get("_derived", {}).get("workspace_root") or ""),
            source_workspace_path=source_workspace_path,
            changed_files=list(apply_result.get("changed_files") or []),
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            stage_key=STAGE_KEY,
        )
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for index, spec in enumerate(profile.checks, start=1):
            log_path = logs_dir / f"12_runtime_check_{index:02d}_{spec.name.replace('/', '_').replace(' ', '_')}.log"
            check_result = execute_verification_check(spec=spec, workspace_path=workspace.workspace_path, log_path=str(log_path))
            failing_tests = extract_failing_test_identifiers(Path(str(check_result.get("log_path") or "")).read_text(encoding="utf-8", errors="replace")) if str(check_result.get("log_path") or "").strip() else []
            check_result["failing_tests"] = failing_tests
            executed_checks.append(check_result)

        summary = summarize_check_statuses(executed_checks)
        if summary["failed"] > 0:
            status = "failed"
            diagnostics.append(_diagnostic("runtime_check_failed", "one or more runtime verification checks failed", ", ".join(str(item.get("name") or "") for item in executed_checks if str(item.get("status") or "") == "failed")))
        elif summary["passed"] > 0:
            status = "passed"
        else:
            status = "skipped"
            diagnostics.append(_diagnostic("no_runtime_checks_executed", "runtime verification selected checks but none produced executable evidence"))

        result = _final_result(
            status=status,
            required=bool(profile.required),
            workspace_path=workspace.workspace_path,
            source_workspace_path=source_workspace_path,
            profile_source=profile.profile_source,
            require_static_pass=profile.require_static_pass,
            executed_checks=executed_checks,
            diagnostics=diagnostics,
            run_dir=run_dir,
        )
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            input_payload=payload,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
