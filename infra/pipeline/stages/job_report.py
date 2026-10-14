from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, save_text_atomic, utc_now_iso
from infra.pipeline.schemas import validate_job_report_result

STAGE_KEY = "script_16_job_report"



def _truncate(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"



def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out



def _load_artifact(path: Path) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "name": path.name,
        "path": str(path.resolve()),
        "exists": path.exists(),
        "malformed": False,
        "payload": {},
        "result": {},
        "error": None,
    }
    if not path.exists():
        return state
    try:
        payload = load_json(path)
    except Exception as exc:  # pragma: no cover - exercised via stage behavior
        state["malformed"] = True
        state["error"] = _truncate(exc)
        return state
    state["payload"] = payload
    state["result"] = dict(payload.get("result") or {})
    return state



def _run_dir_for(args_input: str, args_output: str) -> Path:
    input_path = Path(args_input)
    if input_path.exists() or input_path.parent.exists():
        return input_path.resolve().parent
    return Path(args_output).resolve().parent



def _load_input_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return dict(load_json(path) or {})
    except Exception:
        return {}



def _artifact_root_value(artifact_state: Mapping[str, Any], result_key: str) -> Dict[str, Any]:
    return dict(dict(artifact_state.get("result") or {}).get(result_key) or {})



def _load_context(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    return {
        "apply": _load_artifact(run_dir / "10_apply_result.json"),
        "static": _load_artifact(run_dir / "11_static_verification.json"),
        "runtime": _load_artifact(run_dir / "12_runtime_verification.json"),
        "contract": _load_artifact(run_dir / "13_contract_verification.json"),
        "repair": _load_artifact(run_dir / "14_repair_decision.json"),
        "finalization": _load_artifact(run_dir / "15_finalization.json"),
    }


def _response_preview(text: str, *, head_chars: int, tail_chars: int, max_chars: int) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    head = max(1, min(head_chars, max_chars))
    tail = max(1, min(tail_chars, max(1, max_chars - head - 7)))
    if head + tail + 7 > max_chars:
        tail = max(1, max_chars - head - 7)
    return normalized[:head].rstrip() + "\n...\n...\n" + normalized[-tail:].lstrip()


def _model_response_review(run_dir: Path, config: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    reporting = dict(config.get("reporting") or {})
    review_cfg = dict(reporting.get("model_response_review") or {})
    if not bool(review_cfg.get("enabled", False)):
        return None
    stage8_result = dict(_load_artifact(run_dir / "08_patch_artifact.json").get("result") or {})
    execution_contract_payload = _load_input_payload(run_dir / "07_execution_contract.json")
    execution_contract = dict(execution_contract_payload.get("execution_contract") or execution_contract_payload.get("result", {}).get("execution_contract") or {})
    stage9_result = dict(_load_artifact(run_dir / "09_validated_artifact.json").get("result") or {})
    apply_result = _artifact_root_value(_load_artifact(run_dir / "10_apply_result.json"), "apply_result")
    metadata = dict(stage8_result.get("generation_metadata") or {})
    raw_output_path = str(metadata.get("raw_output_path") or "").strip()
    if not raw_output_path:
        return None
    raw_path = Path(raw_output_path)
    if not raw_path.exists():
        return {
            "available": False,
            "reason": "raw_output_missing",
            "raw_output_path": raw_output_path,
        }
    try:
        raw_text = raw_path.read_text(encoding="utf-8")
    except Exception as exc:
        return {
            "available": False,
            "reason": f"raw_output_unreadable: {_truncate(exc)}",
            "raw_output_path": raw_output_path,
        }
    max_preview_chars = int(review_cfg.get("max_preview_chars") or 2400)
    preview_head_chars = int(review_cfg.get("preview_head_chars") or 1800)
    preview_tail_chars = int(review_cfg.get("preview_tail_chars") or 500)
    preview = _response_preview(
        raw_text,
        head_chars=preview_head_chars,
        tail_chars=preview_tail_chars,
        max_chars=max_preview_chars,
    )
    line_count = len(raw_text.splitlines()) if raw_text else 0
    generation_files = [
        str(item.get("path") or "").strip()
        for item in list(execution_contract.get("generation_files") or [])
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    ]
    allowed_edit_files = [str(item).strip() for item in list(execution_contract.get("allowed_edit_files") or []) if str(item).strip()]
    implementation_target_files = [str(item).strip() for item in list(execution_contract.get("implementation_target_files") or []) if str(item).strip()]
    validation_target_files = [str(item).strip() for item in list(execution_contract.get("validation_target_files") or []) if str(item).strip()]
    expected_new_files = [str(item).strip() for item in list(execution_contract.get("expected_new_files") or []) if str(item).strip()]
    return {
        "available": True,
        "generation_status": str(stage8_result.get("generation_status") or ""),
        "generation_failed_reason": str(stage8_result.get("generation_failed_reason") or stage8_result.get("generation_blocked_reason") or ""),
        "failure_class": str(metadata.get("failure_class") or ""),
        "failure_source": str(metadata.get("failure_source") or ""),
        "provider": str(metadata.get("provider") or ""),
        "requested_model": str(metadata.get("requested_model") or metadata.get("model") or ""),
        "selected_model": str(metadata.get("selected_model") or metadata.get("requested_model") or ""),
        "execution_mode": str(metadata.get("execution_mode") or ""),
        "execution_source": str(metadata.get("execution_source") or ""),
        "simulation_mode": bool(metadata.get("simulation_mode")),
        "output_format_received": str(metadata.get("output_format_received") or ""),
        "raw_output_path": raw_output_path,
        "raw_output_chars": len(raw_text),
        "raw_output_lines": line_count,
        "preview_truncated": len(preview) < len(raw_text.strip()),
        "preview_text": preview,
        "generation_files": generation_files,
        "allowed_edit_files": allowed_edit_files,
        "implementation_target_files": implementation_target_files,
        "validation_target_files": validation_target_files,
        "expected_new_files": expected_new_files,
        "verification_claim": dict(stage8_result.get("verification_claim") or {}),
        "validation_skipped": bool(stage9_result.get("validation_skipped")),
        "validation_blocked_reason": str(stage9_result.get("generation_blocked_reason") or ""),
        "apply_status": str(apply_result.get("apply_status") or ""),
        "changed_files": [str(item).strip() for item in list(apply_result.get("changed_files") or []) if str(item).strip()],
        "workspace_path": str(apply_result.get("workspace_path") or "").strip(),
        "apply_failure_detail": str(dict(apply_result.get("diagnostics") or {}).get("failure_detail") or ""),
    }


def _load_optional_stage_result(run_dir: Path, filename: str, result_key: str) -> Dict[str, Any]:
    artifact = _load_artifact(run_dir / filename)
    return _artifact_root_value(artifact, result_key)



def _artifact_status(block: Mapping[str, Any], *fields: str) -> str:
    for field in fields:
        value = block.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return "missing"



def _timeline_entry(stage_key: str, artifact_state: Mapping[str, Any], block: Mapping[str, Any], *fields: str) -> Dict[str, Any]:
    duration = artifact_state.get("payload", {}).get("duration_ms") if isinstance(artifact_state.get("payload"), Mapping) else None
    if not isinstance(duration, int):
        duration = None
    status = "malformed" if artifact_state.get("malformed") else (_artifact_status(block, *fields) if artifact_state.get("exists") else "missing")
    return {"stage": stage_key, "status": status, "duration_ms": duration}



def _provenance_groups(artifact_refs: Sequence[str]) -> Dict[str, List[str]]:
    groups = {
        "execution": [ref for ref in artifact_refs if ref == "10_apply_result.json"],
        "verification": [ref for ref in artifact_refs if ref in {"11_static_verification.json", "12_runtime_verification.json", "13_contract_verification.json"}],
        "decision": [ref for ref in artifact_refs if ref in {"14_repair_decision.json", "15_finalization.json"}],
    }
    return {key: value for key, value in groups.items() if value}



def _degraded_sections(context: Mapping[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    missing_optional_inputs: List[str] = []
    degraded_sections: List[Dict[str, Any]] = []

    for artifact_name, section_name in [
        ("10_apply_result.json", "execution_apply"),
        ("11_static_verification.json", "static_verification"),
        ("12_runtime_verification.json", "runtime_verification"),
        ("13_contract_verification.json", "contract_verification"),
        ("14_repair_decision.json", "repair_decision"),
    ]:
        artifact_state = dict(context.get(section_name.split("_")[0] if section_name.startswith("execution") else section_name.split("_")[0]) or {})
        # explicit mapping fallback for names that do not match the context keys directly
        if artifact_name == "10_apply_result.json":
            artifact_state = dict(context.get("apply") or {})
        elif artifact_name == "11_static_verification.json":
            artifact_state = dict(context.get("static") or {})
        elif artifact_name == "12_runtime_verification.json":
            artifact_state = dict(context.get("runtime") or {})
        elif artifact_name == "13_contract_verification.json":
            artifact_state = dict(context.get("contract") or {})
        elif artifact_name == "14_repair_decision.json":
            artifact_state = dict(context.get("repair") or {})

        if artifact_name in {"11_static_verification.json", "12_runtime_verification.json"} and not artifact_state.get("exists"):
            missing_optional_inputs.append(artifact_name)
            degraded_sections.append(
                {
                    "section": section_name,
                    "reason": "missing_optional_input",
                    "artifact": artifact_name,
                }
            )
            continue
        if not artifact_state.get("exists"):
            degraded_sections.append(
                {
                    "section": section_name,
                    "reason": "missing_expected_input",
                    "artifact": artifact_name,
                }
            )
            continue
        if artifact_state.get("malformed"):
            degraded_sections.append(
                {
                    "section": section_name,
                    "reason": "malformed_input",
                    "artifact": artifact_name,
                    "details": _truncate(artifact_state.get("error")),
                }
            )
    return missing_optional_inputs, degraded_sections



def _next_action(finalization: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    final_status = str(finalization.get("final_status") or "").strip().lower()
    if final_status != "needs_review":
        return None
    reason_codes = [str(item) for item in list(finalization.get("review_reason_codes") or []) if str(item).strip()]
    reason = ", ".join(reason_codes) if reason_codes else ", ".join(str(item) for item in list(finalization.get("approval_pending_summary") or []))
    return {
        "type": str(finalization.get("recommended_action") or "review_finalization_evidence"),
        "reason": reason or "operator review is required before the run can progress honestly",
        "recommended_first_artifact": finalization.get("recommended_first_artifact") or "14_repair_decision.json",
    }



def _summary_text(
    *,
    final_status: str,
    contract_passed: Optional[bool],
    static_status: str,
    runtime_status: str,
    contract_status: str,
    operator_action_required: bool,
    remediation_summary: Sequence[str],
) -> str:
    contract_label = "unknown"
    if contract_passed is True:
        contract_label = "pass"
    elif contract_passed is False:
        contract_label = "fail"
    remediation_label = ", ".join(str(item) for item in remediation_summary) if remediation_summary else "none"
    return (
        f"Final status {final_status}. Contract {contract_label}. "
        f"Verification static={static_status}, runtime={runtime_status}, contract={contract_status}. "
        f"Operator action required={'yes' if operator_action_required else 'no'}. "
        f"Remediation proposed/pending={remediation_label}."
    )


def _stage8_token_summary(stage8: Mapping[str, Any]) -> str:
    metadata = dict(stage8.get("generation_metadata") or {})
    input_tokens = metadata.get("usage_input_tokens")
    output_tokens = metadata.get("usage_output_tokens")
    source = ""
    if input_tokens is None and output_tokens is None:
        input_tokens = metadata.get("estimated_input_tokens")
        output_tokens = metadata.get("estimated_output_tokens")
        if input_tokens is not None or output_tokens is not None:
            source = " estimated"
    if input_tokens is None and output_tokens is None:
        return ""
    input_label = str(input_tokens) if input_tokens is not None else "unknown"
    output_label = str(output_tokens) if output_tokens is not None else "unknown"
    return f"input={input_label} output={output_label}{source}"


def _primary_blocker(
    *,
    stage7: Mapping[str, Any],
    apply_result: Mapping[str, Any],
    contract: Mapping[str, Any],
    repair: Mapping[str, Any],
    finalization: Mapping[str, Any],
) -> str:
    apply_diagnostics = dict(apply_result.get("diagnostics") or {})
    apply_reason = str(apply_diagnostics.get("failure_detail") or apply_diagnostics.get("failure_reason_code") or "").strip()
    if apply_reason and str(apply_result.get("apply_status") or "").strip().lower() != "applied_cleanly":
        return apply_reason

    contract_decision = dict(contract.get("decision") or {})
    contract_summary = str(contract_decision.get("summary") or "").strip()
    if contract_summary and str(contract.get("contract_verification_status") or "").strip().lower() not in {"satisfied", "passed"}:
        return contract_summary

    repair_codes = [str(item) for item in list(repair.get("blocking_reason_codes") or []) if str(item).strip()]
    if repair_codes:
        return ", ".join(repair_codes)

    unresolved_items = [str(item) for item in list(finalization.get("unresolved_items") or []) if str(item).strip()]
    if unresolved_items:
        return unresolved_items[0]

    blockers = [str(item) for item in list(stage7.get("unresolved_uncertainties") or []) if str(item).strip()]
    if blockers:
        return blockers[0]
    return ""


def _quick_read_blocker_line(blocker: str, *, final_status: str, apply_status: str) -> str:
    text = str(blocker or "").strip()
    if not text:
        return ""
    if "deterministic fallback used" in text.lower() and final_status == "accepted" and apply_status == "applied_cleanly":
        return f"Planning note: {text}"
    return f"Blocker: {text}"


def _compact_stage_summary(run_dir: Path, context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    stage1 = _load_optional_stage_result(run_dir, "01_job_contract.json", "job_contract")
    stage2 = _load_optional_stage_result(run_dir, "02_task_profile.json", "task_profile")
    stage3 = _load_optional_stage_result(run_dir, "03_search_plan.json", "search_plan")
    stage4 = _load_optional_stage_result(run_dir, "04_retrieval.json", "retrieval")
    stage5 = _load_optional_stage_result(run_dir, "05_context_ranked.json", "ranked_context")
    stage6 = _load_optional_stage_result(run_dir, "06_context_bundle.json", "context_bundle")
    stage7 = _load_optional_stage_result(run_dir, "07_patch_plan.json", "patch_plan")
    stage8_result = dict(_load_artifact(run_dir / "08_patch_artifact.json").get("result") or {})
    stage8 = dict(stage8_result.get("patch_artifact") or {})
    apply_result = _artifact_root_value(context.get("apply") or {}, "apply_result")
    static = _artifact_root_value(context.get("static") or {}, "static_verification")
    runtime = _artifact_root_value(context.get("runtime") or {}, "runtime_verification")
    contract = _artifact_root_value(context.get("contract") or {}, "contract_verification")
    repair = _artifact_root_value(context.get("repair") or {}, "repair_decision")
    finalization = _artifact_root_value(context.get("finalization") or {}, "finalization")

    selected_files = [str(item.get("path") or "") for item in list(stage5.get("selected_files") or []) if isinstance(item, Mapping)]
    implementation_targets = [str(item.get("path") or "") for item in list(stage5.get("implementation_targets") or []) if isinstance(item, Mapping)]
    validation_targets = [str(item.get("path") or "") for item in list(stage5.get("validation_targets") or []) if isinstance(item, Mapping)]
    bundle_files = [str(item.get("path") or "") for item in list(stage6.get("files") or []) if isinstance(item, Mapping)]
    modify_files = [str(item.get("path") or "") for item in list(stage7.get("modify_files") or []) if isinstance(item, Mapping)]
    changed_files = [str(item) for item in list(apply_result.get("changed_files") or []) if str(item).strip()]
    artifact_targets = [str(item) for item in list(stage8.get("target_files") or []) if str(item).strip()]
    token_summary = _stage8_token_summary(stage8_result)
    blocker = _primary_blocker(
        stage7=stage7,
        apply_result=apply_result,
        contract=contract,
        repair=repair,
        finalization=finalization,
    )

    stage7_label = "blocker"
    if str(blocker or "").strip().lower().startswith("model planning disabled by config"):
        stage7_label = "note"
    return [
        {"stage": "1", "title": "Intake", "summary": _truncate(stage1.get("objective") or stage1.get("description") or "")},
        {
            "stage": "2",
            "title": "Classification",
            "summary": _truncate(
                f"type={stage2.get('primary_task_type') or 'unknown'} archetype={stage2.get('task_archetype') or 'unknown'} families={','.join(list(stage2.get('task_families') or [])[:4])}"
            ),
        },
        {
            "stage": "3",
            "title": "Search Plan",
            "summary": _truncate(
                f"file_hints={','.join(list((stage3.get('extracted_terms') or {}).get('file_hints') or [])[:4]) or 'none'} subsystems={','.join(list(stage3.get('subsystem_hypotheses') or [])[:4]) or 'none'}"
            ),
        },
        {
            "stage": "4",
            "title": "Retrieval",
            "summary": _truncate(
                f"candidates={len(list(stage4.get('file_candidates') or []))} selected={','.join(selected_files[:4]) or 'none'} targets={','.join(implementation_targets[:3]) or 'none'} validation={','.join(validation_targets[:2]) or 'none'}"
            ),
        },
        {
            "stage": "5-6",
            "title": "Context",
            "summary": _truncate(
                f"targets={','.join(implementation_targets[:4]) or 'none'} validation={','.join(validation_targets[:2]) or 'none'} editable={len(list(stage6.get('editable_targets') or []))} bundle={','.join(bundle_files[:4]) or 'none'}"
            ),
        },
        {
            "stage": "7",
            "title": "Plan",
            "summary": _truncate(
                f"outcome={stage7.get('plan_outcome') or stage7.get('plan_status') or 'unknown'} modify={','.join(modify_files[:4]) or 'none'} {stage7_label}={blocker or 'none'}"
            ),
        },
        {
            "stage": "8-10",
            "title": "Patch",
            "summary": _truncate(
                f"artifact_targets={','.join(artifact_targets[:4]) or 'none'} changed={','.join(changed_files[:4]) or 'none'} apply={apply_result.get('apply_status') or 'skipped'} tokens={token_summary or 'unknown'}"
            ),
        },
        {
            "stage": "11-13",
            "title": "Verification",
            "summary": _truncate(
                f"static={static.get('static_verification_status') or 'missing'} runtime={runtime.get('runtime_verification_status') or 'missing'} contract={contract.get('contract_verification_status') or 'missing'}"
            ),
        },
        {
            "stage": "14-15",
            "title": "Decision",
            "summary": _truncate(
                f"retry={repair.get('retry_mode') or 'missing'} final={finalization.get('final_status') or 'missing'}"
            ),
        },
    ]



def _build_report(run_dir: Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    context = _load_context(run_dir)
    finalization_artifact = dict(context.get("finalization") or {})
    if not finalization_artifact.get("exists") or finalization_artifact.get("malformed"):
        raise ValueError("15_finalization.json must be present and well-formed before Stage 16 can report truthfully")

    apply_result = _artifact_root_value(context.get("apply") or {}, "apply_result")
    static = _artifact_root_value(context.get("static") or {}, "static_verification")
    runtime = _artifact_root_value(context.get("runtime") or {}, "runtime_verification")
    contract = _artifact_root_value(context.get("contract") or {}, "contract_verification")
    repair = _artifact_root_value(context.get("repair") or {}, "repair_decision")
    finalization = _artifact_root_value(finalization_artifact, "finalization")

    final_status = str(finalization.get("final_status") or "aborted")
    contract_passed = finalization.get("decision_basis", {}).get("contract_passed") if isinstance(finalization.get("decision_basis"), Mapping) else None
    static_status = _artifact_status(static, "static_verification_status")
    runtime_status = _artifact_status(runtime, "runtime_verification_status")
    contract_status = _artifact_status(contract, "contract_verification_status")
    remediation_summary = _dedupe_strings(list(finalization.get("applied_remediation_summary") or []) + [str(item.get("candidate") or item.get("action") or "") for item in list(repair.get("auto_remediation_candidates") or []) if isinstance(item, Mapping)])
    operator_action_needed = str(final_status).strip().lower() == "needs_review"
    operator_action_items = _dedupe_strings(list(finalization.get("approval_pending_summary") or []) + list(finalization.get("review_reason_codes") or []))

    artifact_refs = [
        artifact_name
        for artifact_name in [
            "10_apply_result.json",
            "11_static_verification.json",
            "12_runtime_verification.json",
            "13_contract_verification.json",
            "14_repair_decision.json",
            "15_finalization.json",
        ]
        if (run_dir / artifact_name).exists()
    ]
    artifact_ref_groups = _provenance_groups(artifact_refs)
    missing_optional_inputs, degraded_sections = _degraded_sections(context)

    residual_risks = _dedupe_strings(
        list(finalization.get("unresolved_items") or [])
        + [
            f"{item.get('section')}: {item.get('reason')}"
            for item in degraded_sections
            if isinstance(item, Mapping)
        ]
    )
    if not residual_risks and final_status != "accepted":
        residual_risks.append("non-accepted final state")

    verification_summary = [
        f"static={static_status}",
        f"runtime={runtime_status}",
        f"contract={contract_status}",
        f"contract_passed={contract_passed if contract_passed is not None else 'unknown'}",
    ]

    timeline = [
        _timeline_entry("script_10_patch_apply", dict(context.get("apply") or {}), apply_result, "apply_status"),
        _timeline_entry("script_11_verify_static", dict(context.get("static") or {}), static, "static_verification_status"),
        _timeline_entry("script_12_verify_runtime", dict(context.get("runtime") or {}), runtime, "runtime_verification_status"),
        _timeline_entry("script_13_contract_verify", dict(context.get("contract") or {}), contract, "contract_verification_status"),
        _timeline_entry("script_14_repair_loop", dict(context.get("repair") or {}), repair, "retry_mode"),
        _timeline_entry("script_15_finalize_job", finalization_artifact, finalization, "final_status"),
    ]

    summary = _summary_text(
        final_status=final_status,
        contract_passed=contract_passed if isinstance(contract_passed, bool) else None,
        static_status=static_status,
        runtime_status=runtime_status,
        contract_status=contract_status,
        operator_action_required=operator_action_needed,
        remediation_summary=remediation_summary,
    )
    compact_stage_summary = _compact_stage_summary(run_dir, context)
    model_response_review = _model_response_review(run_dir, config)
    stage5 = _load_optional_stage_result(run_dir, "05_context_ranked.json", "ranked_context")
    stage7 = _load_optional_stage_result(run_dir, "07_patch_plan.json", "patch_plan")
    stage8_result = dict(_load_artifact(run_dir / "08_patch_artifact.json").get("result") or {})
    selected_targets = [str(item.get("path") or "") for item in list(stage5.get("implementation_targets") or []) if isinstance(item, Mapping)]
    token_summary = _stage8_token_summary(stage8_result)
    blocker = _primary_blocker(
        stage7=stage7,
        apply_result=apply_result,
        contract=contract,
        repair=repair,
        finalization=finalization,
    )
    quick_read = _dedupe_strings(
        [
            f"Status: {final_status}",
            f"Selected targets: {', '.join(selected_targets[:4]) or 'none'}",
            f"Changed files: {', '.join([str(item) for item in list(apply_result.get('changed_files') or []) if str(item).strip()]) or 'none'}",
            f"Task: {stage2_type}" if (stage2_type := str(_load_optional_stage_result(run_dir, '02_task_profile.json', 'task_profile').get('task_archetype') or '').strip()) else "",
            f"Planned edits: {', '.join([str(item.get('path') or '') for item in list(_load_optional_stage_result(run_dir, '07_patch_plan.json', 'patch_plan').get('modify_files') or []) if isinstance(item, Mapping)]) or 'none'}",
            _quick_read_blocker_line(blocker, final_status=final_status, apply_status=str(apply_result.get("apply_status") or "")),
            f"Tokens: {token_summary}" if token_summary else "",
            (
                (
                    "Model response review: "
                    + str(
                        (
                            model_response_review.get("raw_output_path")
                            or "available"
                        )
                    )
                )
                if isinstance(model_response_review, Mapping)
                and bool(model_response_review.get("available"))
                and bool(dict(dict(config.get("reporting") or {}).get("model_response_review") or {}).get("include_in_quick_read", False))
                else ""
            ),
            (
                "Files provided to model: "
                + ", ".join(list(model_response_review.get("generation_files") or [])[:4])
                if isinstance(model_response_review, Mapping)
                and bool(model_response_review.get("available"))
                and bool(dict(dict(config.get("reporting") or {}).get("model_response_review") or {}).get("include_in_quick_read", False))
                and list(model_response_review.get("generation_files") or [])
                else ""
            ),
            f"Verification: static={static_status}, runtime={runtime_status}, contract={contract_status}",
            f"Next action: {str((_next_action(finalization) or {}).get('type') or 'none')}",
        ]
    )

    return {
        "summary": summary,
        "quick_read": quick_read,
        "compact_stage_summary": compact_stage_summary,
        "final_status": final_status,
        "changed_files": [str(item) for item in list(apply_result.get("changed_files") or [])],
        "verification_summary": verification_summary,
        "residual_risks": residual_risks,
        "timeline": timeline,
        "operator_action_needed": operator_action_needed,
        "operator_action_items": operator_action_items,
        "remediation_summary": remediation_summary,
        "approval_summary": [str(item) for item in list(finalization.get("approval_pending_summary") or [])],
        "artifact_refs": artifact_refs,
        "artifact_ref_groups": artifact_ref_groups,
        "missing_optional_inputs": missing_optional_inputs,
        "degraded_sections": degraded_sections,
        "next_action": _next_action(finalization),
        "model_response_review": model_response_review,
    }



def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Job Report",
        "",
        str(report.get("summary") or ""),
        "",
        f"- Final status: `{report.get('final_status')}`",
        f"- Operator action needed: `{report.get('operator_action_needed')}`",
    ]
    next_action = report.get("next_action")
    if isinstance(next_action, Mapping):
        lines.extend(
            [
                f"- Next action: `{next_action.get('type')}`",
                f"- Next action reason: {next_action.get('reason')}",
                f"- Recommended first artifact: `{next_action.get('recommended_first_artifact')}`",
            ]
        )
    if report.get("quick_read"):
        lines.extend(["", "## 30-Second Summary"])
        for item in list(report.get("quick_read") or []):
            lines.append(f"- {item}")
    if report.get("compact_stage_summary"):
        lines.extend(["", "## Stage Highlights"])
        for item in list(report.get("compact_stage_summary") or []):
            if not isinstance(item, Mapping):
                continue
            lines.append(f"- Stage {item.get('stage')}: {item.get('title')} :: {item.get('summary')}")
    if report.get("missing_optional_inputs"):
        lines.extend(["", "## Missing Optional Inputs"])
        for item in list(report.get("missing_optional_inputs") or []):
            lines.append(f"- `{item}`")
    if report.get("artifact_refs"):
        lines.extend(["", "## Artifact References"])
        for item in list(report.get("artifact_refs") or []):
            lines.append(f"- `{item}`")
    model_response_review = report.get("model_response_review")
    if isinstance(model_response_review, Mapping):
        lines.extend(["", "## Model Response Review"])
        if not bool(model_response_review.get("available")):
            lines.append(f"- Review unavailable: {model_response_review.get('reason') or 'unknown'}")
            if model_response_review.get("raw_output_path"):
                lines.append(f"- Raw output path: `{model_response_review.get('raw_output_path')}`")
        else:
            lines.extend(
                [
                    f"- Provider: `{model_response_review.get('provider')}`",
                    f"- Requested model: `{model_response_review.get('requested_model')}`",
                    f"- Selected model: `{model_response_review.get('selected_model')}`",
                    f"- Generation status: `{model_response_review.get('generation_status') or 'unknown'}`",
                    f"- Generation failure: `{model_response_review.get('generation_failed_reason') or 'none'}`",
                    f"- Failure class/source: `{model_response_review.get('failure_class') or 'none'}` / `{model_response_review.get('failure_source') or 'none'}`",
                    f"- Execution: `{model_response_review.get('execution_mode')}` / `{model_response_review.get('execution_source')}`",
                    f"- Output format: `{model_response_review.get('output_format_received')}`",
                    f"- Raw output path: `{model_response_review.get('raw_output_path')}`",
                    f"- Raw output size: `{model_response_review.get('raw_output_chars')}` chars, `{model_response_review.get('raw_output_lines')}` lines",
                    f"- Preview truncated: `{model_response_review.get('preview_truncated')}`",
                    f"- Files provided to model: `{', '.join(list(model_response_review.get('generation_files') or []) or ['none'])}`",
                    f"- Allowed edit files: `{', '.join(list(model_response_review.get('allowed_edit_files') or []) or ['none'])}`",
                    f"- Implementation targets: `{', '.join(list(model_response_review.get('implementation_target_files') or []) or ['none'])}`",
                    f"- Validation targets: `{', '.join(list(model_response_review.get('validation_target_files') or []) or ['none'])}`",
                    f"- Expected new files: `{', '.join(list(model_response_review.get('expected_new_files') or []) or ['none'])}`",
                    f"- Stage 9 validation skipped: `{model_response_review.get('validation_skipped')}`",
                    f"- Stage 9 blocked reason: `{model_response_review.get('validation_blocked_reason') or 'none'}`",
                    f"- Apply status: `{model_response_review.get('apply_status') or 'unknown'}`",
                    f"- Changed files: `{', '.join(list(model_response_review.get('changed_files') or []) or ['none'])}`",
                    f"- Apply failure detail: `{model_response_review.get('apply_failure_detail') or 'none'}`",
                    f"- Workspace path: `{model_response_review.get('workspace_path') or ''}`",
                ]
            )
            verification_claim = dict(model_response_review.get("verification_claim") or {})
            if verification_claim:
                lines.extend(
                    [
                        f"- Claimed implementation files: `{', '.join(list(verification_claim.get('implementation_files') or []) or ['none'])}`",
                        f"- Claimed changed test files: `{', '.join(list(verification_claim.get('changed_test_files') or []) or ['none'])}`",
                        f"- Claimed test names: `{', '.join([str(item.get('name') or '') for item in list(verification_claim.get('new_or_updated_tests') or []) if isinstance(item, Mapping)] or ['none'])}`",
                    ]
                )
            lines.extend(
                [
                    "",
                    "```text",
                    str(model_response_review.get("preview_text") or ""),
                    "```",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"



def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 16 job report")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        input_path = Path(args.input)
        output_path = Path(args.output)
        run_dir = _run_dir_for(args.input, args.output)
        payload = _load_input_payload(input_path)
        report = _build_report(run_dir, config)
        result = {"job_report": report}
        validate_job_report_result(result)
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=str(payload.get("job_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            input_payload=payload,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(output_path, envelope)
        save_text_atomic(run_dir / "16_job_report.md", _render_markdown(report))
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
