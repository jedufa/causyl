from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.artifact_lineage import build_artifact_lineage, resolve_lineage_artifact
from infra.pipeline.artifact_normalization import normalize_patch_artifact
from infra.pipeline.core import PipelineError, build_envelope, emit_pipeline_error, load_json, load_pipeline_config, run_subprocess, save_json_atomic, utc_now_iso
from infra.pipeline.diff_utils import parse_unified_diff, repair_unified_diff_bare_hunks, repair_unified_diff_hunk_headers
from infra.pipeline.schemas import (
    validate_artifact_validation_result,
    validate_execution_contract_payload,
    validate_patch_artifact,
    validate_patch_plan_result,
    validate_stage_input,
    validate_verification_claim,
)
from infra.pipeline.path_policy import validate_candidate_paths

STAGE_KEY = "script_9_artifact_validate"


def _load_stage7_context(run_dir: Path, lineage: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    patch_plan_path = resolve_lineage_artifact(
        run_dir=run_dir,
        lineage=lineage,
        key="patch_plan",
        fallback_filename="07_patch_plan.json",
    )
    execution_contract_path = resolve_lineage_artifact(
        run_dir=run_dir,
        lineage=lineage,
        key="execution_contract",
        fallback_filename="07_execution_contract.json",
    )
    patch_plan_payload = load_json(patch_plan_path)
    validate_stage_input(patch_plan_payload, ["job_contract", "patch_plan"])
    validate_patch_plan_result(dict(patch_plan_payload.get("result") or {}))
    execution_contract_payload = load_json(execution_contract_path)
    validate_execution_contract_payload(execution_contract_payload)
    return {
        "patch_plan_payload": patch_plan_payload,
        "execution_contract_payload": execution_contract_payload,
        "patch_plan_artifact_ref": patch_plan_path.name,
        "execution_contract_artifact_ref": execution_contract_path.name,
    }


def _check(name: str, message: str) -> Dict[str, str]:
    return {"name": name, "status": "passed", "message": message}


def _resolve_validation_policy(config: Mapping[str, Any], execution_contract: Mapping[str, Any]) -> Dict[str, Any]:
    limits = dict(config.get("limits") or {})
    policy = dict(config.get("policy") or {})
    stage9 = dict(execution_contract.get("stage9_validation_requirements") or {})
    stage8 = dict(execution_contract.get("stage8_output_contract") or {})

    global_char_limit = int(limits.get("max_patch_artifact_chars") or 0)
    if global_char_limit <= 0:
        raise PipelineError("invalid_config_value", "limits.max_patch_artifact_chars must be positive", exit_code=3)
    contract_char_limit = int(stage9.get("artifact_size_limit_chars") or 0)
    effective_char_limit = global_char_limit if contract_char_limit <= 0 else min(global_char_limit, contract_char_limit)

    global_file_limit = int(limits.get("max_patch_artifact_files") or 0)
    if global_file_limit <= 0:
        raise PipelineError("invalid_config_value", "limits.max_patch_artifact_files must be positive", exit_code=3)

    return {
        "artifact_type": "unified_diff",
        "effective_char_limit": effective_char_limit,
        "global_char_limit": global_char_limit,
        "contract_char_limit": contract_char_limit if contract_char_limit > 0 else None,
        "effective_file_limit": global_file_limit,
        "allow_zip_artifacts": bool(policy.get("allow_zip_artifacts", False)) and bool(stage8.get("allow_zip_artifacts", False)),
        "allow_full_file_replacements": bool(policy.get("allow_full_file_replacements", False)) and bool(stage8.get("allow_full_file_replacements", False)),
    }


def _validate_creation_policy(sections: List[Any], execution_contract: Mapping[str, Any]) -> None:
    may_create_files = bool(execution_contract.get("may_create_files", False))
    must_not_create_files = bool(execution_contract.get("must_not_create_files", False))
    expected_new_files = {str(path) for path in list(execution_contract.get("expected_new_files") or [])}
    for section in sections:
        if not bool(getattr(section, "is_new_file", False)):
            continue
        target = str(section.target_path)
        if must_not_create_files or not may_create_files:
            raise PipelineError(
                "new_file_forbidden",
                "Patch attempts to create a new file but creation is not allowed",
                details={"path": target},
                exit_code=11,
            )
        if expected_new_files and target not in expected_new_files:
            raise PipelineError(
                "unexpected_new_file",
                "Patch attempts to create a file outside the expected_new_files contract",
                details={"path": target},
                exit_code=11,
            )


def _normalize_verification_claim(
    claim: Mapping[str, Any],
    *,
    repo_root: str,
    execution_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    validated = dict(validate_verification_claim(claim))
    allowed_paths = list(execution_contract.get("allowed_edit_files") or []) + list(execution_contract.get("expected_new_files") or [])
    forbidden_paths = list(execution_contract.get("forbidden_edit_files") or [])
    implementation_files = validate_candidate_paths(
        repo_root,
        list(validated.get("implementation_files") or []),
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    ) if list(validated.get("implementation_files") or []) else []
    changed_test_files = validate_candidate_paths(
        repo_root,
        list(validated.get("changed_test_files") or []),
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    ) if list(validated.get("changed_test_files") or []) else []
    normalized_tests: List[Dict[str, Any]] = []
    for item in list(validated.get("new_or_updated_tests") or []):
        if not isinstance(item, Mapping):
            continue
        normalized_tests.append(
            {
                "path": validate_candidate_paths(
                    repo_root,
                    [str(item.get("path") or "")],
                    allowed_paths=allowed_paths,
                    forbidden_paths=forbidden_paths,
                )[0],
                "name": str(item.get("name") or "").strip(),
                "proves": str(item.get("proves") or "").strip(),
            }
        )
    return {
        "implementation_files": implementation_files,
        "changed_test_files": changed_test_files,
        "new_or_updated_tests": normalized_tests,
        "behavior_claims": [str(item).strip() for item in list(validated.get("behavior_claims") or []) if str(item).strip()],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 9 artifact validator")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        raw_result = dict(payload.get("result") or {})
        artifact_lineage = build_artifact_lineage(
            raw_result.get("artifact_lineage"),
            additions={
                "patch_artifact": Path(args.input).name,
                "validated_artifact": Path(args.output).name,
            },
        )
        if raw_result.get("generation_skipped") is True:
            result = {
                "validation_skipped": True,
                "generation_blocked_reason": str(raw_result.get("generation_blocked_reason") or "patch generation skipped"),
                "artifact_lineage": artifact_lineage,
            }
            validate_artifact_validation_result(result)
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=str(payload.get("job_id") or ""),
                run_id=str(payload.get("run_id") or ""),
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0
        if str(raw_result.get("generation_status") or "") == "generation_failed":
            result = {
                "validation_skipped": True,
                "generation_blocked_reason": str(raw_result.get("generation_failed_reason") or "patch generation failed"),
                "upstream_generation_failed": True,
                "artifact_lineage": artifact_lineage,
            }
            validate_artifact_validation_result(result)
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=str(payload.get("job_id") or ""),
                run_id=str(payload.get("run_id") or ""),
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0
        env = validate_stage_input(payload, ["patch_artifact"])
        try:
            validate_patch_artifact(env.result)
            run_dir = Path(args.input).resolve().parent
            context = _load_stage7_context(run_dir, lineage=artifact_lineage)
            patch_plan_payload = context["patch_plan_payload"]
            execution_contract_payload = context["execution_contract_payload"]
            execution_contract = dict(execution_contract_payload.get("execution_contract") or {})
            repo_root = str(patch_plan_payload.get("result", {}).get("job_contract", {}).get("repo_root") or "").strip()
            if not repo_root:
                raise PipelineError("missing_repo_root", "job_contract.repo_root is required for artifact validation", exit_code=3)
            validation_policy = _resolve_validation_policy(config, execution_contract)

            validation_checks: List[Dict[str, str]] = []
            patch_artifact = dict(env.result.get("patch_artifact") or {})
            artifact_type = str(patch_artifact.get("artifact_type") or "")
            if artifact_type != str(validation_policy["artifact_type"]):
                raise PipelineError("unsupported_patch_artifact", "Only unified_diff artifacts are accepted", details={"artifact_type": artifact_type}, exit_code=11)
            validation_checks.append(_check("artifact_type", f"artifact_type is {validation_policy['artifact_type']}"))

            raw_content = str(patch_artifact.get("content") or "")
            repaired_bare_hunks = repair_unified_diff_bare_hunks(raw_content)
            if repaired_bare_hunks != raw_content:
                patch_artifact["content"] = repaired_bare_hunks
                validation_checks.append(_check("bare_hunk_repair", "repaired bare unified diff hunk headers before normalization"))

            normalized_artifact = normalize_patch_artifact(
                patch_artifact,
                repo_root=repo_root,
                allowed_paths=list(execution_contract.get("allowed_edit_files") or []) + list(execution_contract.get("expected_new_files") or []),
                forbidden_paths=list(execution_contract.get("forbidden_edit_files") or []),
            )
            repaired_content = repair_unified_diff_hunk_headers(str(normalized_artifact.get("content") or ""))
            if repaired_content != str(normalized_artifact.get("content") or ""):
                normalized_artifact["content"] = repaired_content
                validation_checks.append(_check("hunk_header_repair", "repaired unified diff hunk header counts before validation"))
            validation_checks.append(_check("diff_normalization", "diff content normalized and target_files reconciled"))
            sections = parse_unified_diff(str(normalized_artifact.get("content") or ""))
            validation_checks.append(_check("diff_parse", f"parsed {len(sections)} diff section(s)"))
            _validate_creation_policy(sections, execution_contract)
            validation_checks.append(_check("creation_policy", "file creation rules satisfied"))
            target_files = list(normalized_artifact.get("target_files") or [])
            if len(target_files) > int(validation_policy["effective_file_limit"]):
                raise PipelineError(
                    "artifact_too_many_files",
                    "Patch artifact touches more files than the configured limit allows",
                    details={
                        "artifact_file_count": len(target_files),
                        "artifact_file_limit": int(validation_policy["effective_file_limit"]),
                    },
                    exit_code=11,
                )
            validation_checks.append(_check("artifact_file_limit", f"artifact touches {len(target_files)} file(s) within limit {int(validation_policy['effective_file_limit'])}"))

            normalized_content = str(normalized_artifact.get("content") or "")
            size_limit = int(validation_policy["effective_char_limit"])
            if len(normalized_content) > size_limit:
                raise PipelineError(
                    "artifact_too_large",
                    "Patch artifact exceeds the configured size limit",
                    details={
                        "artifact_size_chars": len(normalized_content),
                        "artifact_size_limit_chars": size_limit,
                        "global_artifact_size_limit_chars": int(validation_policy["global_char_limit"]),
                        "contract_artifact_size_limit_chars": validation_policy["contract_char_limit"],
                    },
                    exit_code=11,
                )
            validation_checks.append(_check("artifact_size_limit", f"artifact size {len(normalized_content)} chars is within limit {size_limit}"))

            git_path = str(config.get("tools", {}).get("git_path") or "git")
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".diff", delete=False) as tmp:
                tmp.write(normalized_content)
                tmp.flush()
                patch_path = Path(tmp.name)
            try:
                git_check = run_subprocess(
                    [git_path, "apply", "--check", "--verbose", str(patch_path)],
                    cwd=repo_root,
                    timeout_seconds=int(config.get("timeouts", {}).get("subprocess_default_seconds", 180)),
                )
            finally:
                patch_path.unlink(missing_ok=True)
            if git_check.returncode != 0:
                raise PipelineError(
                    "git_apply_check_failed",
                    "git apply --check rejected the patch artifact",
                    details={"stdout": git_check.stdout, "stderr": git_check.stderr, "returncode": git_check.returncode},
                    exit_code=11,
                )
            validation_checks.append(_check("git_apply_check", "git apply --check passed cleanly"))

            verification_claim = None
            if raw_result.get("verification_claim") is not None:
                verification_claim = _normalize_verification_claim(
                    dict(raw_result.get("verification_claim") or {}),
                    repo_root=repo_root,
                    execution_contract=execution_contract,
                )

            validated_artifact = {
                "artifact_type": "unified_diff",
                "normalized_content": normalized_content,
                "target_files": list(normalized_artifact.get("target_files") or []),
                "validation_checks": validation_checks,
                "is_valid": True,
            }
            result = {
                "validated_artifact": validated_artifact,
                "verification_claim": verification_claim,
                "artifact_lineage": build_artifact_lineage(
                    artifact_lineage,
                    additions={
                        "patch_plan": str(context["patch_plan_artifact_ref"]),
                        "execution_contract": str(context["execution_contract_artifact_ref"]),
                    },
                ),
            }
            validate_artifact_validation_result(result)
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
        except PipelineError as exc:
            result = {
                "validation_failed": True,
                "validation_failed_reason": f"{exc.code}: {exc.message}",
                "failure_reason_code": str(exc.code),
                "failure_source": "artifact_validation",
                "artifact_lineage": artifact_lineage,
            }
            validate_artifact_validation_result(result)
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
