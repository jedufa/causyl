from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.command_policy import canonical_command_string, normalize_command
from infra.pipeline.core import (
    build_envelope,
    emit_pipeline_error,
    load_json,
    load_pipeline_config,
    normalize_repo_relative_path,
    save_json_atomic,
    utc_now_iso,
)
from infra.pipeline.schemas import (
    EnvelopeSchema,
    validate_apply_result,
    validate_artifact_validation_result,
    validate_contract_verification_result,
    validate_execution_contract_payload,
    validate_patch_plan_result,
    validate_runtime_verification_result,
    validate_stage_input,
    validate_static_verification_result,
)
from infra.pipeline.verification_policy import runtime_verification_required, static_verification_required

STAGE_KEY = "script_13_contract_verify"
_SCOPE_TERMS = (
    "allowed file",
    "allowed files",
    "forbidden",
    "must not change",
    "must_not_change",
    "out-of-scope",
    "scope",
    "touching forbidden",
    "without touching",
)
_NEW_FILE_TERMS = (
    "added file",
    "create file",
    "created file",
    "expected new file",
    "expected new files",
    "new file",
    "new files",
)
_STATIC_TERMS = (
    "compile",
    "config validation",
    "import",
    "lint",
    "mypy",
    "pyright",
    "ruff",
    "schema",
    "semgrep",
    "static",
    "syntax",
    "type",
)
_TEST_PATH_RE = re.compile(r"(^|/)(test_.*\.py|tests?/.*\.py)$")


def _objective_requests_test_edits(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    mentions_tests = any(token in normalized for token in [" test", " tests", "test ", "tests/", "test_", "testing"])
    requests_change = any(token in normalized for token in ["add ", "update ", "modify ", "change ", "create ", "write "])
    return bool(mentions_tests and requests_change)


def _looks_like_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(str(path or "").strip()))
_RUNTIME_TERMS = (
    "behavior",
    "behaviour",
    "build",
    "change",
    "changes",
    "cli",
    "fix",
    "fixed",
    "integration",
    "logic",
    "package",
    "regression",
    "requested",
    "runtime",
    "smoke",
    "test",
    "tests",
    "update",
    "updated",
    "validation",
)


def _diagnostic(reason_code: str, message: str, evidence: str = "") -> Dict[str, str]:
    item: Dict[str, str] = {"reason_code": str(reason_code), "message": str(message)}
    evidence_text = str(evidence or "").strip()
    if evidence_text:
        item["evidence"] = evidence_text
    return item


def _check(name: str, status: str, evidence: str) -> Dict[str, str]:
    evidence_text = str(evidence or "").strip() or "n/a"
    return {"name": str(name), "status": str(status), "evidence": evidence_text}


def _artifact_lineage(run_dir: Path) -> Dict[str, Any]:
    return {
        "run_dir": str(run_dir.resolve()),
        "source_artifacts": {
            "patch_plan": str((run_dir / "07_patch_plan.json").resolve()),
            "execution_contract": str((run_dir / "07_execution_contract.json").resolve()),
            "validated_artifact": str((run_dir / "09_validated_artifact.json").resolve()),
            "apply_result": str((run_dir / "10_apply_result.json").resolve()),
            "static_verification": str((run_dir / "11_static_verification.json").resolve()),
            "runtime_verification": str((run_dir / "12_runtime_verification.json").resolve()),
        },
    }


def _safe_load_envelope(run_dir: Path, filename: str, required_keys: Sequence[str]) -> Tuple[Optional[EnvelopeSchema], Optional[Dict[str, str]]]:
    path = run_dir / filename
    if not path.exists():
        return None, _diagnostic("missing_artifact", f"required upstream artifact is missing: {filename}", str(path))
    try:
        payload = load_json(path)
        env = validate_stage_input(payload, required_keys)
        return env, None
    except Exception as exc:
        return None, _diagnostic("invalid_artifact", f"upstream artifact is malformed: {filename}", str(exc))


def _canonical_required_test(command: str) -> str:
    try:
        return canonical_command_string(normalize_command(command))
    except Exception:
        return str(command or "").strip()


def _looks_like_executable_command(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    try:
        argv = normalize_command(text)
    except Exception:
        argv = text.split()
    if not argv:
        return False
    head = str(argv[0] or "").strip()
    if not head:
        return False
    normalized = head.lower()
    if head.startswith(("./", "/")) or "/" in head:
        return True
    if normalized.endswith((".sh", ".py", ".pl", ".rb")):
        return True
    return normalized in {
        "adb",
        "bash",
        "cargo",
        "dotnet",
        "go",
        "gradle",
        "java",
        "javac",
        "make",
        "node",
        "npm",
        "npx",
        "perl",
        "php",
        "pnpm",
        "poetry",
        "pytest",
        "python",
        "python3",
        "ruby",
        "rustc",
        "sh",
        "swift",
        "uv",
        "xcodebuild",
        "yarn",
    }


def _normalize_paths(repo_root: str, values: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in list(values or []):
        txt = str(value or "").strip()
        if not txt:
            continue
        try:
            normalized = normalize_repo_relative_path(repo_root, txt)
        except Exception:
            normalized = txt.replace("\\", "/").strip("/")
        if normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def _dedupe_strings(values: Sequence[object]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in list(values or []):
        txt = str(value or "").strip()
        if not txt or txt in seen:
            continue
        out.append(txt)
        seen.add(txt)
    return out


def _append_reason(reason_codes: List[str], code: str) -> None:
    normalized = str(code or "").strip()
    if normalized and normalized not in reason_codes:
        reason_codes.append(normalized)


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = str(text or "").strip().lower()
    return any(term in lowered for term in terms)


def _status_from_checks(required: bool, verification: Mapping[str, Any], *, valid: bool) -> str:
    if not required:
        return "not_required"
    if not valid:
        return "missing"
    status = str(verification.get("static_verification_status") or verification.get("runtime_verification_status") or "").strip().lower()
    return status or "missing"


def _resolve_contract_policy(execution_contract: Mapping[str, Any]) -> Dict[str, bool]:
    section = dict(execution_contract.get("stage13_contract_verification") or {}) if isinstance(execution_contract, Mapping) else {}
    return {
        "require_static_success": bool(section.get("require_static_success", static_verification_required(execution_contract))),
        "require_runtime_success": bool(section.get("require_runtime_success", runtime_verification_required(execution_contract))),
        "fail_closed_on_missing_evidence": bool(section.get("fail_closed_on_missing_evidence", True)),
        "enforce_required_files_touched": bool(section.get("enforce_required_files_touched", True)),
        "enforce_scope": bool(section.get("enforce_scope", True)),
    }


def _load_patch_plan_context(run_dir: Path, diagnostics: List[Mapping[str, Any]]) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
    env, diag = _safe_load_envelope(run_dir, "07_patch_plan.json", ["job_contract", "patch_plan"])
    if diag:
        diagnostics.append(diag)
        return False, {}, {}
    patch_plan = dict(env.result.get("patch_plan") or {}) if env else {}
    job_contract = dict(env.result.get("job_contract") or {}) if env else {}
    try:
        validate_patch_plan_result(dict(env.result or {}))  # type: ignore[union-attr]
        return True, patch_plan, job_contract
    except Exception as exc:
        diagnostics.append(_diagnostic("invalid_patch_plan", "patch plan artifact is malformed", str(exc)))
        return False, patch_plan, job_contract


def _load_execution_contract(run_dir: Path, diagnostics: List[Mapping[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    path = run_dir / "07_execution_contract.json"
    if not path.exists():
        diagnostics.append(_diagnostic("missing_execution_contract", "execution contract is missing or empty", str(path.resolve())))
        return False, {}
    payload = load_json(path)
    execution_contract = dict(payload.get("execution_contract") or {})
    if not execution_contract:
        diagnostics.append(_diagnostic("missing_execution_contract", "execution contract is missing or empty", str(path.resolve())))
        return False, {}
    try:
        validate_execution_contract_payload(payload)
        return True, execution_contract
    except Exception as exc:
        diagnostics.append(_diagnostic("invalid_execution_contract", "execution contract artifact is malformed", str(exc)))
        return False, execution_contract


def _load_static_verification(run_dir: Path, diagnostics: List[Mapping[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    env, diag = _safe_load_envelope(run_dir, "11_static_verification.json", ["static_verification"])
    if diag:
        diagnostics.append(diag)
        return False, {}
    verification = dict(env.result.get("static_verification") or {}) if env else {}
    try:
        validate_static_verification_result(dict(env.result))  # type: ignore[union-attr]
        return True, verification
    except Exception as exc:
        diagnostics.append(_diagnostic("invalid_static_verification", "static verification artifact is malformed", str(exc)))
        return False, {}


def _load_runtime_verification_from_input(
    payload: Mapping[str, Any],
    *,
    run_dir: Path,
    diagnostics: List[Mapping[str, Any]],
) -> Tuple[bool, Dict[str, Any], EnvelopeSchema]:
    env = validate_stage_input(payload, ["runtime_verification"])
    verification = dict(env.result.get("runtime_verification") or {})
    try:
        validate_runtime_verification_result(dict(env.result))
        return True, verification, env
    except Exception as exc:
        diagnostics.append(_diagnostic("invalid_runtime_verification", "runtime verification artifact is malformed", str(exc)))
        return False, {}, env


def _load_apply_result(run_dir: Path, diagnostics: List[Mapping[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    env, diag = _safe_load_envelope(run_dir, "10_apply_result.json", ["apply_result"])
    if diag:
        diagnostics.append(diag)
        return False, {}
    apply_result = dict(env.result.get("apply_result") or {}) if env else {}
    try:
        validate_apply_result({"apply_result": env.result.get("apply_result")})  # type: ignore[union-attr]
        return True, apply_result
    except Exception as exc:
        diagnostics.append(_diagnostic("invalid_apply_result", "apply result artifact is malformed", str(exc)))
        return False, apply_result


def _load_validated_artifact(run_dir: Path, diagnostics: List[Mapping[str, Any]]) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
    path = run_dir / "09_validated_artifact.json"
    if not path.exists():
        diagnostics.append(_diagnostic("missing_validated_artifact", "validated artifact is missing", str(path.resolve())))
        return False, {}, {}
    payload = load_json(path)
    raw_result = dict(payload.get("result") or {})
    validated_artifact = dict(raw_result.get("validated_artifact") or payload.get("validated_artifact") or {})
    verification_claim = dict(raw_result.get("verification_claim") or {})
    try:
        env = validate_stage_input(payload, ["validated_artifact"])
        validate_artifact_validation_result(dict(env.result))
        return True, dict(env.result.get("validated_artifact") or {}), dict(env.result.get("verification_claim") or {})
    except Exception as exc:
        if bool(raw_result.get("validation_skipped")):
            diagnostics.append(_diagnostic("validation_artifact_skipped", "validated artifact stage was skipped and cannot satisfy Stage 13", str(raw_result.get("generation_blocked_reason") or "validation skipped")))
        else:
            diagnostics.append(_diagnostic("invalid_validated_artifact", "validated artifact is malformed or not valid", str(exc)))
        return False, validated_artifact, verification_claim


def _named_test_present(*, workspace_path: Path, rel_path: str, test_name: str) -> bool:
    path = workspace_path / rel_path
    if not path.exists() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return bool(re.search(rf"\b{re.escape(str(test_name or '').strip())}\b", text))


EXCEPTION_TYPE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*Error)\b")
SNAKE_EXCEPTION_CONTEXT_RE = re.compile(r"(?:^|[^a-z0-9])(?:raise|raises|raising)_([a-z][a-z0-9]*(?:_[a-z0-9]+)*)_error\b")
ADDED_EXCEPTION_RE = re.compile(r"^\+.*\braise\s+([A-Za-z_][A-Za-z0-9_]*Error)\b", re.MULTILINE)


def _canonical_exception_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    if "_" not in raw:
        return raw
    lowered = raw.lower()
    if not lowered.endswith("_error"):
        return raw
    stem = lowered[: -len("_error")]
    parts = [part for part in stem.split("_") if part]
    if not parts:
        return raw
    return "".join(part.capitalize() for part in parts) + "Error"


def _mentioned_exception_types(text: str) -> List[str]:
    seen = set()
    out: List[str] = []
    for match in EXCEPTION_TYPE_RE.finditer(str(text or "")):
        name = _canonical_exception_name(str(match.group(1) or "").strip())
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    for match in SNAKE_EXCEPTION_CONTEXT_RE.finditer(str(text or "")):
        name = _canonical_exception_name(str(match.group(1) or "").strip() + "_error")
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _added_exception_types(diff_text: str) -> List[str]:
    seen = set()
    out: List[str] = []
    for match in ADDED_EXCEPTION_RE.finditer(str(diff_text or "")):
        name = _canonical_exception_name(str(match.group(1) or "").strip())
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _evaluate_acceptance_criteria(
    criteria: Sequence[str],
    *,
    allowed_files_only: bool,
    required_files_touched: bool,
    expected_new_files_present: bool,
    static_passed: bool,
    runtime_passed: bool,
    runtime_evidence_present: bool,
    required_tests_present: bool,
    required_tests_executed: bool,
    verification_claim_supported: bool,
) -> Dict[str, Any]:
    passed: List[str] = []
    failed: List[str] = []
    unsupported: List[str] = []
    evaluations: List[Dict[str, Any]] = []
    runtime_evidence_satisfied = runtime_passed and (not required_tests_present or required_tests_executed)
    for criterion in _dedupe_strings(criteria):
        evidence_labels: List[str] = []
        evidence_outcomes: List[bool] = []
        if _contains_any(criterion, _SCOPE_TERMS):
            evidence_labels.extend(["scope", "required_files"])
            evidence_outcomes.extend([allowed_files_only, required_files_touched])
        if _contains_any(criterion, _NEW_FILE_TERMS):
            evidence_labels.append("expected_new_files")
            evidence_outcomes.append(expected_new_files_present)
        if _contains_any(criterion, _STATIC_TERMS):
            evidence_labels.append("static")
            evidence_outcomes.append(static_passed)
        if _contains_any(criterion, _RUNTIME_TERMS):
            if runtime_evidence_present:
                evidence_labels.append("runtime")
                evidence_outcomes.append(runtime_evidence_satisfied)
        if verification_claim_supported and _contains_any(criterion, ("owner", "validation", "test", "implementation contract")):
            evidence_labels.append("verification_claim")
            evidence_outcomes.append(True)
        if not evidence_labels:
            unsupported.append(criterion)
            evaluations.append({"criterion": criterion, "status": "unsupported", "evidence_labels": []})
            continue
        if all(evidence_outcomes):
            passed.append(criterion)
            evaluations.append({"criterion": criterion, "status": "passed", "evidence_labels": evidence_labels})
        else:
            failed.append(criterion)
            evaluations.append({"criterion": criterion, "status": "failed", "evidence_labels": evidence_labels})
    return {
        "passed": passed,
        "failed": failed,
        "unsupported": unsupported,
        "evaluations": evaluations,
    }


def _final_result(
    *,
    status: str,
    workspace_path: str,
    checks: List[Mapping[str, Any]],
    reason_codes: List[str],
    summary_text: str,
    scope_assessment: Mapping[str, Any],
    acceptance_assessment: Mapping[str, Any],
    diagnostics: List[Mapping[str, Any]],
    run_dir: Path,
) -> Dict[str, Any]:
    result = {
        "contract_verification": {
            "contract_verification_status": str(status),
            "workspace_path": str(workspace_path),
            "decision": {
                "status": str(status),
                "reason_codes": [str(code) for code in reason_codes],
                "summary": str(summary_text),
            },
            "checks": [dict(item) for item in checks],
            "scope_assessment": dict(scope_assessment),
            "acceptance_assessment": dict(acceptance_assessment),
            "diagnostics": [dict(item) for item in diagnostics],
            "artifact_lineage": _artifact_lineage(run_dir),
            "passes": status == "satisfied",
            "scope_ok": bool(scope_assessment.get("allowed_files_only") and scope_assessment.get("required_files_touched")),
        }
    }
    validate_contract_verification_result(result)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 13 contract verification")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        run_dir = Path(args.input).resolve().parent

        diagnostics: List[Mapping[str, Any]] = []
        try:
            runtime_verification_valid, runtime_verification, env = _load_runtime_verification_from_input(payload, run_dir=run_dir, diagnostics=diagnostics)
            job_id = str(env.job_id)
            run_id = str(env.run_id)
        except Exception as exc:
            result = _final_result(
                status="inconclusive",
                workspace_path="",
                checks=[_check("runtime_verification_input", "failed", str(exc))],
                reason_codes=["invalid_runtime_input_artifact"],
                summary_text="Required verification evidence was malformed before Stage 13 could evaluate contract satisfaction.",
                scope_assessment={"allowed_files_only": False, "required_files_touched": False, "unexpected_files_touched": []},
                acceptance_assessment={"required_checks_present": False, "static_passed": False, "runtime_passed": False},
                diagnostics=[_diagnostic("invalid_runtime_input_artifact", "runtime verification input artifact is malformed", str(exc))],
                run_dir=run_dir,
            )
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=str(payload.get("job_id") or "unknown_job"),
                run_id=str(payload.get("run_id") or "unknown_run"),
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0

        patch_plan_valid, patch_plan, job_contract = _load_patch_plan_context(run_dir, diagnostics)
        execution_contract_valid, execution_contract = _load_execution_contract(run_dir, diagnostics)
        static_verification_valid, static_verification = _load_static_verification(run_dir, diagnostics)
        apply_result_valid, apply_result = _load_apply_result(run_dir, diagnostics)
        validated_artifact_valid, validated_artifact, verification_claim = _load_validated_artifact(run_dir, diagnostics)

        workspace_path = str(runtime_verification.get("workspace_path") or static_verification.get("workspace_path") or apply_result.get("workspace_path") or "")
        execution_mode = str(execution_contract.get("execution_mode") or "unknown").strip()
        generation_applicable = bool(execution_contract.get("generation_applicable", False)) if execution_contract_valid else True
        if execution_contract_valid and (execution_mode != "patch_generation" or not generation_applicable):
            result = _final_result(
                status="skipped",
                workspace_path=workspace_path,
                checks=[_check("execution_mode", "skipped", execution_mode or "review_only")],
                reason_codes=["non_generation_mode"],
                summary_text="Contract verification skipped because Stage 7 marked execution as review-only or non-generative.",
                scope_assessment={"allowed_files_only": True, "required_files_touched": False, "unexpected_files_touched": []},
                acceptance_assessment={"required_checks_present": False, "static_passed": False, "runtime_passed": False},
                diagnostics=diagnostics,
                run_dir=run_dir,
            )
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=job_id,
                run_id=run_id,
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0

        policy = _resolve_contract_policy(execution_contract if execution_contract_valid else {})
        repo_root = str(job_contract.get("repo_root") or "").strip()
        changed_files = _normalize_paths(repo_root, apply_result.get("changed_files") or []) if repo_root else _dedupe_strings(apply_result.get("changed_files") or [])
        allowed_edit_files = _normalize_paths(repo_root, execution_contract.get("allowed_edit_files") or []) if repo_root else _dedupe_strings(execution_contract.get("allowed_edit_files") or [])
        validation_target_files = _normalize_paths(repo_root, execution_contract.get("validation_target_files") or []) if repo_root else _dedupe_strings(execution_contract.get("validation_target_files") or [])
        editable_validation_target_files = sorted([path for path in validation_target_files if path in set(allowed_edit_files)])
        expected_new_files = _normalize_paths(repo_root, execution_contract.get("expected_new_files") or []) if repo_root else _dedupe_strings(execution_contract.get("expected_new_files") or [])
        forbidden_edit_files = _normalize_paths(repo_root, execution_contract.get("forbidden_edit_files") or []) if repo_root else _dedupe_strings(execution_contract.get("forbidden_edit_files") or [])
        required_modify_files = _normalize_paths(repo_root, [entry.get("path") for entry in list(patch_plan.get("modify_files") or []) if isinstance(entry, Mapping)]) if repo_root else _dedupe_strings([entry.get("path") for entry in list(patch_plan.get("modify_files") or []) if isinstance(entry, Mapping)])
        explicit_test_edit_request = _objective_requests_test_edits(
            str(patch_plan.get("objective") or job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or "")
        )
        must_not_change = _normalize_paths(
            repo_root,
            list((execution_contract.get("constraints") or {}).get("must_not_change") or []) + list(patch_plan.get("must_not_change") or []),
        ) if repo_root else _dedupe_strings(list((execution_contract.get("constraints") or {}).get("must_not_change") or []) + list(patch_plan.get("must_not_change") or []))
        validated_targets = _normalize_paths(repo_root, validated_artifact.get("target_files") or []) if repo_root else _dedupe_strings(validated_artifact.get("target_files") or [])

        permitted_scope = set(allowed_edit_files + expected_new_files)
        if policy["enforce_scope"] and not permitted_scope and changed_files:
            unexpected_files = sorted(changed_files)
        else:
            unexpected_files = sorted([path for path in changed_files if permitted_scope and path not in permitted_scope])
        forbidden_touched = sorted([path for path in changed_files if path in set(forbidden_edit_files)])
        must_not_change_touched = sorted([path for path in changed_files if path in set(must_not_change)])
        missing_required_files = sorted([path for path in required_modify_files if path not in set(changed_files)])
        changed_test_files = sorted([path for path in changed_files if _looks_like_test_path(path)])
        target_scope_mismatch = sorted([path for path in changed_files if validated_artifact_valid and validated_targets and path not in set(validated_targets)])
        allowed_files_only = not unexpected_files and not forbidden_touched and not must_not_change_touched and not target_scope_mismatch
        required_files_touched = not missing_required_files

        workspace_root = Path(workspace_path).resolve() if workspace_path else None
        expected_new_files_missing: List[str] = []
        expected_new_files_present = True
        if expected_new_files:
            for rel_path in expected_new_files:
                changed = rel_path in set(changed_files)
                exists_in_workspace = bool(workspace_root and (workspace_root / rel_path).exists())
                if not changed or not exists_in_workspace:
                    expected_new_files_missing.append(rel_path)
            expected_new_files_present = not expected_new_files_missing

        static_required = bool(policy["require_static_success"])
        runtime_required = bool(policy["require_runtime_success"])
        static_status = _status_from_checks(static_required, static_verification, valid=static_verification_valid)
        runtime_status = _status_from_checks(runtime_required, runtime_verification, valid=runtime_verification_valid)
        required_checks_present = (not static_required or static_verification_valid) and (not runtime_required or runtime_verification_valid)
        static_passed = (not static_required) or static_status == "passed"
        runtime_passed = (not runtime_required) or runtime_status == "passed"

        required_tests = [
            command
            for command in _dedupe_strings(list((execution_contract.get("constraints") or {}).get("required_tests") or []))
            if _looks_like_executable_command(command)
        ]
        if not required_tests:
            required_tests = [
                command
                for command in _dedupe_strings(list(patch_plan.get("required_tests") or []))
                if _looks_like_executable_command(command)
            ]
        observed_commands = [
            canonical_command_string(list(item.get("command") or []))
            for item in list(runtime_verification.get("executed_checks") or [])
            if isinstance(item, Mapping)
        ] if runtime_verification_valid else []
        required_test_commands = [_canonical_required_test(command) for command in required_tests]
        required_tests_executed = all(command in observed_commands for command in required_test_commands)
        required_tests_present = bool(required_tests)
        runtime_diag_codes = {str(item.get("reason_code") or "") for item in list(runtime_verification.get("diagnostics") or [])}
        runtime_evidence_available = runtime_verification_valid and str(runtime_status) in {"passed", "failed"} and "missing_runtime_profile" not in runtime_diag_codes
        claimed_impl_files = _normalize_paths(repo_root, verification_claim.get("implementation_files") or []) if repo_root else _dedupe_strings(verification_claim.get("implementation_files") or [])
        claimed_test_files = _normalize_paths(repo_root, verification_claim.get("changed_test_files") or []) if repo_root else _dedupe_strings(verification_claim.get("changed_test_files") or [])
        claimed_tests = [dict(item) for item in list(verification_claim.get("new_or_updated_tests") or []) if isinstance(item, Mapping)]
        claimed_test_paths = _normalize_paths(repo_root, [item.get("path") for item in claimed_tests]) if repo_root else _dedupe_strings([item.get("path") for item in claimed_tests])
        claim_impl_ok = all(path in set(changed_files) for path in claimed_impl_files)
        claim_test_file_ok = all(path in set(changed_test_files) for path in claimed_test_files)
        claim_test_paths_match_validation_targets = all(path in set(validation_target_files) for path in claimed_test_paths)
        claim_named_tests_ok = bool(workspace_root or claimed_tests == [])
        if claimed_tests and workspace_root is not None:
            claim_named_tests_ok = all(
                _named_test_present(
                    workspace_path=workspace_root,
                    rel_path=str(item.get("path") or ""),
                    test_name=str(item.get("name") or ""),
                )
                for item in claimed_tests
            )
        validation_targets_touched = not editable_validation_target_files or bool(set(changed_files).intersection(editable_validation_target_files))
        validation_claim_present = not editable_validation_target_files or bool(claimed_tests)
        verification_claim_supported = bool(verification_claim) and claim_impl_ok and claim_test_file_ok and claim_named_tests_ok and claim_test_paths_match_validation_targets and validation_claim_present
        objective_text = " ".join(
            [
                str(patch_plan.get("objective") or ""),
                str(job_contract.get("objective") or ""),
                str(job_contract.get("description") or ""),
                str(job_contract.get("title") or ""),
            ]
        )
        mentioned_exception_types = {item.lower() for item in _mentioned_exception_types(objective_text)}
        added_exception_types = _added_exception_types(str(validated_artifact.get("normalized_content") or ""))
        unexpected_added_exception_types = [
            name for name in added_exception_types
            if mentioned_exception_types and name.lower() not in mentioned_exception_types
        ]

        acceptance_criteria = _dedupe_strings(
            list((execution_contract.get("constraints") or {}).get("acceptance_criteria") or [])
            + list(patch_plan.get("acceptance_criteria") or [])
            + list(job_contract.get("acceptance_criteria") or [])
        )
        acceptance_assessment_details = _evaluate_acceptance_criteria(
            acceptance_criteria,
            allowed_files_only=allowed_files_only,
            required_files_touched=required_files_touched,
            expected_new_files_present=expected_new_files_present,
            static_passed=static_passed,
            runtime_passed=runtime_passed,
            runtime_evidence_present=runtime_evidence_available,
            required_tests_present=required_tests_present,
            required_tests_executed=required_tests_executed,
            verification_claim_supported=verification_claim_supported,
        )
        acceptance_criteria_supported = not acceptance_assessment_details["unsupported"]
        acceptance_criteria_satisfied = not acceptance_assessment_details["failed"] and acceptance_criteria_supported

        apply_status = str(apply_result.get("apply_status") or "missing")
        core_artifact_failures = _dedupe_strings(
            [
                "execution_contract" if not execution_contract_valid else "",
                "patch_plan" if not patch_plan_valid else "",
                "validated_artifact" if not validated_artifact_valid else "",
                "apply_result" if not apply_result_valid else "",
            ]
        )
        missing_required_evidence = _dedupe_strings(
            core_artifact_failures
            + (["static_verification"] if static_required and not static_verification_valid else [])
            + (["runtime_verification"] if runtime_required and not runtime_verification_valid else [])
        )

        checks = [
            _check("artifact_integrity", "passed" if not core_artifact_failures else "failed", ", ".join(core_artifact_failures) or "all required upstream artifacts validated"),
            _check("apply_clean", "passed" if apply_status == "applied_cleanly" else "failed", apply_status),
            _check("validated_artifact", "passed" if validated_artifact_valid else "failed", ", ".join(validated_targets) or "validated artifact missing or invalid"),
            _check(
                "allowed_edit_scope",
                "passed" if allowed_files_only else ("failed" if policy["enforce_scope"] else "skipped"),
                ", ".join(unexpected_files + forbidden_touched + must_not_change_touched + target_scope_mismatch),
            ),
            _check(
                "required_files_touched",
                "passed" if required_files_touched else ("failed" if policy["enforce_required_files_touched"] else "skipped"),
                ", ".join(missing_required_files),
            ),
            _check(
                "explicit_test_edits",
                "passed" if (not explicit_test_edit_request or bool(changed_test_files)) else "failed",
                ", ".join(changed_test_files) or "prompt explicitly required test updates but no test files changed",
            ),
            _check(
                "validation_targets_touched",
                "passed" if validation_targets_touched else ("failed" if editable_validation_target_files else "skipped"),
                ", ".join(editable_validation_target_files) or "n/a",
            ),
            _check(
                "validation_claim_present",
                "passed" if validation_claim_present else ("failed" if editable_validation_target_files else "skipped"),
                ", ".join(claimed_test_paths) or "editable validation targets existed but the model did not claim any concrete tests",
            ),
            _check(
                "expected_new_files_present",
                "passed" if expected_new_files_present else ("failed" if expected_new_files else "skipped"),
                ", ".join(expected_new_files_missing),
            ),
            _check("static_verification", "passed" if static_passed else ("failed" if static_required else "skipped"), static_status),
            _check("runtime_verification", "passed" if runtime_passed else ("failed" if runtime_required else "skipped"), runtime_status),
            _check("required_tests_executed", "passed" if required_tests_executed else ("failed" if required_tests else "skipped"), ", ".join(required_test_commands)),
            _check(
                "unexpected_exception_behavior",
                "failed" if unexpected_added_exception_types else ("passed" if added_exception_types else "skipped"),
                ", ".join(unexpected_added_exception_types or added_exception_types),
            ),
            _check(
                "verification_claim",
                "passed" if verification_claim_supported else ("failed" if verification_claim else "skipped"),
                "verification claim matched changed files and named tests" if verification_claim_supported else ("verification claim missing or could not be confirmed" if verification_claim else "n/a"),
            ),
            _check(
                "acceptance_criteria",
                "passed" if acceptance_criteria_satisfied else ("failed" if acceptance_assessment_details["failed"] else ("failed" if policy["fail_closed_on_missing_evidence"] and acceptance_assessment_details["unsupported"] else "skipped")),
                "; ".join(acceptance_assessment_details["failed"] + acceptance_assessment_details["unsupported"]),
            ),
        ]

        scope_assessment = {
            "allowed_files_only": allowed_files_only,
            "required_files_touched": required_files_touched,
            "unexpected_files_touched": unexpected_files,
            "forbidden_files_touched": forbidden_touched,
            "must_not_change_violations": must_not_change_touched,
            "validated_target_mismatches": target_scope_mismatch,
            "changed_test_files": changed_test_files,
            "validation_target_files": validation_target_files,
            "editable_validation_target_files": editable_validation_target_files,
            "validation_targets_touched": validation_targets_touched,
            "verification_claim_supported": verification_claim_supported,
            "verification_claim": verification_claim,
            "expected_new_files_present": expected_new_files_present,
            "missing_expected_new_files": expected_new_files_missing,
            "mentioned_exception_types": sorted(mentioned_exception_types),
            "added_exception_types": added_exception_types,
            "unexpected_added_exception_types": unexpected_added_exception_types,
        }
        acceptance_assessment = {
            "required_checks_present": required_checks_present,
            "static_passed": static_passed,
            "runtime_passed": runtime_passed,
            "static_required": static_required,
            "runtime_required": runtime_required,
            "required_tests_executed": required_tests_executed,
            "required_tests_present": required_tests_present,
            "acceptance_criteria_present": bool(acceptance_criteria),
            "acceptance_criteria_supported": acceptance_criteria_supported,
            "acceptance_criteria_satisfied": acceptance_criteria_satisfied,
            "failed_acceptance_criteria": acceptance_assessment_details["failed"],
            "unsupported_acceptance_criteria": acceptance_assessment_details["unsupported"],
            "criterion_evaluations": acceptance_assessment_details["evaluations"],
            "core_artifacts_valid": not core_artifact_failures,
            "mentioned_exception_types": sorted(mentioned_exception_types),
            "added_exception_types": added_exception_types,
            "unexpected_added_exception_types": unexpected_added_exception_types,
        }

        reason_codes: List[str] = []
        hard_violations: List[str] = []
        missing_evidence_reasons: List[str] = []

        if apply_status != "applied_cleanly":
            _append_reason(hard_violations, "apply_not_clean")
        if not validated_artifact_valid:
            raw_validity = validated_artifact.get("is_valid")
            if raw_validity is False:
                _append_reason(hard_violations, "validated_artifact_invalid")
            else:
                _append_reason(missing_evidence_reasons, "validated_artifact_missing_or_invalid")
        if policy["enforce_scope"] and not allowed_files_only:
            _append_reason(hard_violations, "scope_violation")
        if policy["enforce_required_files_touched"] and not required_files_touched:
            _append_reason(hard_violations, "required_files_not_touched")
        if explicit_test_edit_request and not changed_test_files:
            _append_reason(hard_violations, "explicit_test_edits_missing")
        if editable_validation_target_files and not validation_targets_touched:
            _append_reason(hard_violations, "validation_targets_not_touched")
        if editable_validation_target_files and not validation_claim_present:
            _append_reason(hard_violations, "validation_claim_missing_tests")
        if claimed_tests and not claim_test_paths_match_validation_targets:
            _append_reason(hard_violations, "validation_claim_paths_mismatch")
        if expected_new_files and not expected_new_files_present:
            _append_reason(hard_violations, "expected_new_files_missing")
        if static_required and static_status == "failed":
            diag_codes = {str(item.get("reason_code") or "") for item in list(static_verification.get("diagnostics") or [])}
            if "missing_static_profile" in diag_codes:
                _append_reason(missing_evidence_reasons, "missing_static_evidence")
            else:
                _append_reason(hard_violations, "static_verification_failed")
        if runtime_required and runtime_status == "failed":
            if "missing_runtime_profile" in runtime_diag_codes:
                _append_reason(missing_evidence_reasons, "missing_runtime_evidence")
            else:
                _append_reason(hard_violations, "runtime_verification_failed")
        if required_tests and not required_tests_executed:
            if runtime_verification_valid:
                _append_reason(hard_violations, "required_tests_missing")
            else:
                _append_reason(missing_evidence_reasons, "required_test_evidence_missing")
        if unexpected_added_exception_types:
            _append_reason(hard_violations, "unexpected_exception_behavior")
        if acceptance_assessment_details["failed"]:
            _append_reason(hard_violations, "acceptance_criteria_not_satisfied")
        if acceptance_assessment_details["unsupported"] and policy["fail_closed_on_missing_evidence"]:
            _append_reason(missing_evidence_reasons, "acceptance_criteria_unsupported")
        if patch_plan.get("should_escalate") is True:
            _append_reason(missing_evidence_reasons, "planner_requested_escalation")
        for artifact_name in missing_required_evidence:
            _append_reason(missing_evidence_reasons, f"missing_required_{artifact_name}")
        if not required_checks_present:
            _append_reason(missing_evidence_reasons, "missing_required_evidence")

        status = "satisfied"
        for code in hard_violations:
            _append_reason(reason_codes, code)
        if hard_violations:
            status = "violated"
        else:
            for code in missing_evidence_reasons:
                _append_reason(reason_codes, code)
            if missing_evidence_reasons:
                status = "inconclusive"
        if status == "satisfied":
            _append_reason(reason_codes, "deterministic_requirements_satisfied")

        summary_text = {
            "satisfied": "Deterministic scope checks and required verification evidence satisfied the Stage 7 execution contract.",
            "violated": "Deterministic contract checks found a concrete violation or failed required verification evidence.",
            "inconclusive": "Required verification evidence was missing, malformed, or insufficient to prove contract satisfaction.",
            "skipped": "Contract verification was skipped.",
        }[status]

        result = _final_result(
            status=status,
            workspace_path=workspace_path,
            checks=checks,
            reason_codes=reason_codes,
            summary_text=summary_text,
            scope_assessment=scope_assessment,
            acceptance_assessment=acceptance_assessment,
            diagnostics=diagnostics,
            run_dir=run_dir,
        )
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=job_id,
            run_id=run_id,
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
