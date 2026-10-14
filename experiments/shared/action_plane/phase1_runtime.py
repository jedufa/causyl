"""Concrete phase-1 deterministic action helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import uuid4

from infra.controlplane import scheduled_jobs as sched
from infra.pipeline.core import PipelineError
from infra.pipeline.diff_utils import (
    extract_target_files,
    normalize_diff_text,
    repair_unified_diff_bare_hunks,
    repair_unified_diff_hunk_headers,
)
from infra.pipeline.patch_ops import classify_apply_failure, preflight_validated_patch
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui.ledger import ledger_storage

from .outcomes import classify_action_outcome


FALLBACK_EVENT_SCHEMA_PATH = Path("governance/contracts/fallback_event_schema_v1.json")
IMPROVEMENT_OPPORTUNITY_SCHEMA_PATH = Path("governance/contracts/improvement_opportunity_schema_v1.json")
FALLBACK_EVENT_LOG_PATH = Path("runtime/state/action_plane/fallback_events.jsonl")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def _run(cmd: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=str(cwd), text=True, capture_output=True, check=False)


def _first_diff_start(text: str) -> int:
    markers = ("\ndiff --git ", "\n--- ", "\n*** Begin Patch", "diff --git ", "--- ", "*** Begin Patch")
    starts: List[int] = []
    for marker in markers:
        idx = text.find(marker)
        if idx >= 0:
            starts.append(idx + (1 if marker.startswith("\n") else 0))
    return min(starts) if starts else -1


def _strip_patch_wrapper(text: str) -> tuple[str, List[str]]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    idx = _first_diff_start(normalized)
    repairs: List[str] = []
    if idx > 0:
        normalized = normalized[idx:]
        repairs.append("metadata_wrapper_stripped")
    if normalized.startswith("*** Begin Patch"):
        raise PipelineError("unsupported_patch_wrapper", "apply_patch grammar input is not valid for git-based patch ingestion", exit_code=11)
    return normalized, repairs


def _drop_duplicate_diff_headers(text: str) -> tuple[str, List[str]]:
    lines = text.splitlines()
    out: List[str] = []
    repairs: List[str] = []
    previous = ""
    for line in lines:
        if line.startswith("diff --git ") and line == previous:
            repairs.append("duplicate_adjacent_diff_header_removed")
            continue
        out.append(line)
        previous = line
    rebuilt = "\n".join(out)
    if rebuilt and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt or text, repairs


def normalize_patch_for_phase1(patch_text: str) -> Dict[str, Any]:
    repaired, repairs = _strip_patch_wrapper(patch_text)
    repaired2, repairs2 = _drop_duplicate_diff_headers(repaired)
    repaired3 = repair_unified_diff_hunk_headers(repaired2)
    if repaired3 != repaired2:
        repairs2.append("hunk_header_counts_repaired")
    repaired4 = repair_unified_diff_bare_hunks(repaired3)
    if repaired4 != repaired3:
        repairs2.append("bare_hunk_headers_repaired")
    normalized = normalize_diff_text(repaired4)
    return {
        "patch_text": normalized,
        "repairs_applied": repairs + repairs2,
    }


def _validate_commands(commands: Sequence[str], *, repo_root: Path) -> tuple[List[Dict[str, Any]], str]:
    rows: List[Dict[str, Any]] = []
    combined_logs: List[str] = []
    for command in commands:
        text = str(command or "").strip()
        if not text:
            continue
        result = subprocess.run(text, cwd=str(repo_root), shell=True, text=True, capture_output=True, check=False)
        row = {
            "command": text,
            "returncode": int(result.returncode),
            "passed": result.returncode == 0,
        }
        rows.append(row)
        combined_logs.append((result.stdout or "").strip())
        combined_logs.append((result.stderr or "").strip())
        if result.returncode != 0:
            break
    return rows, "\n".join(part for part in combined_logs if part).strip()


def _checklist_complete(checklist: Mapping[str, Any], required_keys: Sequence[str]) -> bool:
    for key in required_keys:
        if not bool(checklist.get(key)):
            return False
    return True


def execute_patch_ingest_repair_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    patch_path = str(request.get("patch_path") or "").strip()
    patch_text = str(request.get("patch_text") or "")
    if patch_path and not patch_text:
        patch_text = (root / patch_path).read_text(encoding="utf-8")
    if not patch_text.strip():
        raise ValueError("patch_ingest_repair_v1 requires patch_text or patch_path")

    normalized = normalize_patch_for_phase1(patch_text)
    normalized_patch = str(normalized.get("patch_text") or "")
    repair_classes = list(normalized.get("repairs_applied") or [])
    target_files = [str(x).strip() for x in list(request.get("expected_target_files") or []) if str(x).strip()]
    checklist = {
        "patch_contract_check_performed": False,
        "repo_preflight_performed": False,
        "failure_class_recorded": False,
        "deterministic_repairs_exhausted": False,
        "narrow_validation_contract_prepared": False,
        "escalation_reason_recorded": False,
    }
    proof_gaps: List[str] = []
    evidence_artifacts: List[str] = [str(x).strip() for x in list(request.get("evidence_artifacts") or []) if str(x).strip()]
    validation_commands = [str(x).strip() for x in list(request.get("validation_commands") or []) if str(x).strip()]
    validation_rows: List[Dict[str, Any]] = []
    git_check_log = ""
    apply_log = ""
    failure_reason_code = ""

    try:
        if not target_files:
            target_files = extract_target_files(normalized_patch)
        preflight_validated_patch(repo_root=str(root), patch_text=normalized_patch, expected_target_files=target_files)
        checklist["patch_contract_check_performed"] = True
    except Exception as exc:
        proof_gaps.append("patch_contract_invalid")
        checklist["failure_class_recorded"] = True
        checklist["deterministic_repairs_exhausted"] = True
        failure_reason_code = getattr(exc, "code", "patch_contract_invalid")
        return {
            "action_id": "patch_ingest_repair_v1",
            "ok": False,
            "failure_reason_code": str(failure_reason_code),
            "proof_gaps": proof_gaps,
            "repair_classes_applied": repair_classes,
            "pre_escalation_checklist": checklist,
            "fallback_allowed": False,
            "outcome": classify_action_outcome(
                {
                    "requested_outcome": "failed_with_known_gap",
                    "proof_gaps": proof_gaps,
                    "validation_commands_executed": [],
                    "evidence_artifacts": evidence_artifacts,
                    "verifier_verdict": str(request.get("verifier_verdict") or ""),
                }
            ),
        }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".diff", delete=False) as tmp:
        tmp.write(normalized_patch)
        tmp.flush()
        patch_file = Path(tmp.name)

    try:
        check_result = _run(["git", "apply", "--check", str(patch_file)], cwd=root)
        git_check_log = ((check_result.stdout or "") + "\n" + (check_result.stderr or "")).strip()
        checklist["repo_preflight_performed"] = True
        apply_status = "conflict"
        if check_result.returncode == 0:
            apply_result = _run(["git", "apply", str(patch_file)], cwd=root)
            apply_log = ((apply_result.stdout or "") + "\n" + (apply_result.stderr or "")).strip()
            if apply_result.returncode == 0:
                apply_status = "applied_cleanly"
            else:
                failure = classify_apply_failure(
                    logs=apply_log,
                    patch_text=normalized_patch,
                    changed_files=[],
                    repo_root=str(root),
                    expected_target_files=target_files,
                )
                apply_status = str(failure.get("apply_status") or "conflict")
                failure_reason_code = str(failure.get("failure_reason_code") or "git_apply_failed")
        else:
            failure = classify_apply_failure(
                logs=git_check_log,
                patch_text=normalized_patch,
                changed_files=[],
                repo_root=str(root),
                expected_target_files=target_files,
            )
            apply_status = str(failure.get("apply_status") or "conflict")
            failure_reason_code = str(failure.get("failure_reason_code") or "git_apply_failed")

        if apply_status != "applied_cleanly":
            checklist["failure_class_recorded"] = True
            checklist["deterministic_repairs_exhausted"] = True
            proof_gaps.append(str(failure_reason_code or "git_apply_failed"))
        else:
            if validation_commands:
                checklist["narrow_validation_contract_prepared"] = True
                validation_rows, validation_log = _validate_commands(validation_commands, repo_root=root)
                if validation_log:
                    evidence_artifacts.append("validation_log:captured")
                if validation_rows and not all(bool(row.get("passed")) for row in validation_rows):
                    proof_gaps.append("validation_failed")
            requested_outcome = "success_deterministic" if not repair_classes else "success_after_deterministic_recovery"
            outcome = classify_action_outcome(
                {
                    "requested_outcome": requested_outcome,
                    "validation_commands_executed": [row["command"] for row in validation_rows] or validation_commands,
                    "evidence_artifacts": evidence_artifacts or ["git_apply"],
                    "verifier_verdict": str(request.get("verifier_verdict") or ""),
                    "proof_gaps": proof_gaps,
                }
            )
            return {
                "action_id": "patch_ingest_repair_v1",
                "ok": outcome.get("outcome") in {"success_deterministic", "success_after_deterministic_recovery"},
                "target_files": target_files,
                "repair_classes_applied": repair_classes,
                "git_apply_check_log": git_check_log,
                "git_apply_log": apply_log,
                "validation_results": validation_rows,
                "pre_escalation_checklist": checklist,
                "fallback_allowed": False,
                "outcome": outcome,
            }
    finally:
        patch_file.unlink(missing_ok=True)

    escalation_reason = str(request.get("escalation_reason") or "").strip()
    if escalation_reason:
        checklist["escalation_reason_recorded"] = True
    if not checklist["narrow_validation_contract_prepared"] and validation_commands:
        checklist["narrow_validation_contract_prepared"] = True
    checklist_required = (
        "patch_contract_check_performed",
        "repo_preflight_performed",
        "failure_class_recorded",
        "deterministic_repairs_exhausted",
        "narrow_validation_contract_prepared",
        "escalation_reason_recorded",
    )
    fallback_allowed = bool(request.get("allow_fallback")) and _checklist_complete(checklist, checklist_required)
    outcome = classify_action_outcome(
        {
            "requested_outcome": "blocked_requires_review" if not fallback_allowed else "failed_with_known_gap",
            "validation_commands_executed": validation_commands,
            "evidence_artifacts": evidence_artifacts,
            "verifier_verdict": str(request.get("verifier_verdict") or ""),
            "proof_gaps": proof_gaps,
        }
    )
    return {
        "action_id": "patch_ingest_repair_v1",
        "ok": False,
        "target_files": target_files,
        "repair_classes_applied": repair_classes,
        "failure_reason_code": str(failure_reason_code or "git_apply_failed"),
        "git_apply_check_log": git_check_log,
        "git_apply_log": apply_log,
        "pre_escalation_checklist": checklist,
        "fallback_allowed": fallback_allowed,
        "outcome": outcome,
    }


def _ledger_for(root: Path) -> ChangeLedger:
    return ChangeLedger(repo_root=root)


def _ledger_item_for_uid(uid: str, *, repo_root: Path) -> Dict[str, Any]:
    return _ledger_for(repo_root).read_item(uid)


def assess_pis_proof_gap_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    uid = str(request.get("target_pis_uid") or "").strip()
    pis_markdown = str(request.get("pis_markdown") or "")
    meta: Dict[str, Any] = {}
    if uid and not pis_markdown:
        item = _ledger_item_for_uid(uid, repo_root=root)
        pis_markdown = str(item.get("pis_markdown") or "")
        meta = dict(item.get("meta") or {})
    if not pis_markdown.strip():
        raise ValueError("pis_assess_proof_gap_v1 requires target_pis_uid or pis_markdown")

    evidence_artifacts = [str(x).strip() for x in list(request.get("evidence_artifacts") or []) if str(x).strip()]
    validation_commands = [str(x).strip() for x in list(request.get("validation_commands_executed") or []) if str(x).strip()]
    verifier_verdict = str(request.get("verifier_verdict") or "").strip().upper()
    proof_gaps: List[str] = []

    if not evidence_artifacts:
        proof_gaps.append("missing_evidence_artifacts")
    if not validation_commands:
        proof_gaps.append("missing_validation_commands")
    if verifier_verdict != "PASS":
        proof_gaps.append("missing_explicit_pass")
    if not str(meta.get("status") or "").strip():
        proof_gaps.append("pis_meta_status_unknown")
    if "acceptance" not in pis_markdown.lower():
        proof_gaps.append("acceptance_targets_not_explicit")

    checklist = {
        "evidence_gathered": bool(evidence_artifacts),
        "proof_gap_class_recorded": bool(proof_gaps),
        "deterministic_assess_rules_exhausted": True,
        "bounded_ambiguity_reason_recorded": bool(str(request.get("bounded_ambiguity_reason") or "").strip()),
    }
    fallback_allowed = bool(request.get("allow_fallback")) and _checklist_complete(
        checklist,
        (
            "evidence_gathered",
            "proof_gap_class_recorded",
            "deterministic_assess_rules_exhausted",
            "bounded_ambiguity_reason_recorded",
        ),
    )
    requested_outcome = "success_deterministic" if not proof_gaps else "proof_gap_present"
    outcome = classify_action_outcome(
        {
            "requested_outcome": requested_outcome,
            "validation_commands_executed": validation_commands,
            "evidence_artifacts": evidence_artifacts,
            "verifier_verdict": verifier_verdict,
            "proof_gaps": proof_gaps,
        }
    )
    return {
        "action_id": "pis_assess_proof_gap_v1",
        "target_pis_uid": uid,
        "proof_class": "closeable" if not proof_gaps else "proof_gap_present",
        "proof_gaps": proof_gaps,
        "closeable": outcome.get("outcome") == "success_deterministic",
        "pre_escalation_checklist": checklist,
        "fallback_allowed": fallback_allowed,
        "outcome": outcome,
    }


def inspect_job_bounded_recovery_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    job_id = str(request.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_inspect_bounded_recovery_v1 requires job_id")

    job_spec = dict(request.get("job_spec") or {})
    job_state = dict(request.get("job_state") or {})
    if not job_spec:
        spec_path = sched._job_path(job_id, repo_root=root)  # type: ignore[attr-defined]
        job_spec = ledger_storage.read_json(spec_path) or {}
    if not job_state:
        state_path = sched._state_path(job_id, repo_root=root)  # type: ignore[attr-defined]
        job_state = ledger_storage.read_json(state_path) or {}

    receipts = list(request.get("receipts") or [])
    if not receipts:
        receipts = sched.list_receipts(job_id, repo_root=root, limit=5)
    watchdog_summary = dict(request.get("watchdog_summary") or {})

    state_class = "unknown"
    if str(job_state.get("active_run_id") or "").strip():
        state_class = "running"
    else:
        last_status = str(job_state.get("last_status") or "").strip().lower()
        if last_status in {"blocked", "error", "failed", "interrupted"}:
            state_class = "blocked" if last_status == "blocked" else "failed"
        elif last_status in {"ok", "success"}:
            state_class = "completed"
        elif last_status:
            state_class = "degraded"

    receipt_status = ""
    if receipts:
        receipt_status = str((receipts[0] or {}).get("status") or (receipts[0] or {}).get("last_status") or "").strip().lower()
    watchdog_status = str(watchdog_summary.get("health") or watchdog_summary.get("status") or "").strip().lower()
    disagreement = bool(receipt_status and watchdog_status and receipt_status != watchdog_status)

    recovery_taken = ""
    recovery_suggested = ""
    if state_class in {"blocked", "failed"} and bool(request.get("allow_recovery")) and str(request.get("authority_scope") or "") == "job_only":
        recovery_suggested = "job_only_rerun_safe"
    elif state_class in {"blocked", "failed"}:
        recovery_suggested = "review_then_rerun"
    else:
        recovery_taken = "reinspect_only"

    checklist = {
        "job_evidence_gathered": bool(job_spec or job_state or receipts),
        "receipt_watchdog_disagreement_resolved_or_recorded": not disagreement or bool(str(request.get("disagreement_note") or "").strip()),
        "deterministic_bounded_recovery_attempted_or_inapplicable": bool(recovery_taken or recovery_suggested),
        "escalation_reason_recorded": bool(str(request.get("escalation_reason") or "").strip()),
    }
    fallback_allowed = bool(request.get("allow_fallback")) and _checklist_complete(
        checklist,
        (
            "job_evidence_gathered",
            "receipt_watchdog_disagreement_resolved_or_recorded",
            "deterministic_bounded_recovery_attempted_or_inapplicable",
            "escalation_reason_recorded",
        ),
    )
    proof_gaps = ["receipt_watchdog_disagreement"] if disagreement else []
    outcome = classify_action_outcome(
        {
            "requested_outcome": "partial_success" if state_class in {"running", "completed"} else "blocked_requires_review",
            "validation_commands_executed": [],
            "evidence_artifacts": ["job_spec", "job_state"] + (["receipt"] if receipts else []) + (["watchdog"] if watchdog_summary else []),
            "verifier_verdict": "",
            "proof_gaps": proof_gaps,
        }
    )
    return {
        "action_id": "job_inspect_bounded_recovery_v1",
        "job_id": job_id,
        "state_class": state_class,
        "receipt_status": receipt_status,
        "watchdog_status": watchdog_status,
        "disagreement_recorded": disagreement,
        "recovery_taken": recovery_taken,
        "recovery_suggested": recovery_suggested,
        "pre_escalation_checklist": checklist,
        "fallback_allowed": fallback_allowed,
        "outcome": outcome,
    }


def load_fallback_event_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _read_json(_repo_root(repo_root) / FALLBACK_EVENT_SCHEMA_PATH)


def load_improvement_opportunity_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _read_json(_repo_root(repo_root) / IMPROVEMENT_OPPORTUNITY_SCHEMA_PATH)


def _fallback_event_log_path(*, repo_root: Optional[Path] = None) -> Path:
    path = (_repo_root(repo_root) / FALLBACK_EVENT_LOG_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_recorded_fallback_events_v1(
    *,
    repo_root: Optional[Path] = None,
    action_family: str = "",
    failure_class: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    path = _fallback_event_log_path(repo_root=repo_root)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    family_filter = str(action_family or "").strip()
    failure_filter = str(failure_class or "").strip()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if family_filter and str(row.get("action_family") or "").strip() != family_filter:
            continue
        if failure_filter and str(row.get("failure_class") or "").strip() != failure_filter:
            continue
        rows.append(dict(row))
    return rows[-max(1, int(limit or 50)) :]


def validate_fallback_event_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_fallback_event_schema(repo_root=repo_root)
    required = [str(x).strip() for x in list(schema.get("required_fields") or []) if str(x).strip()]
    missing = [key for key in required if not payload.get(key)]
    return {"ok": not missing, "missing_fields": missing}


def validate_improvement_opportunity_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_improvement_opportunity_schema(repo_root=repo_root)
    required = [str(x).strip() for x in list(schema.get("required_fields") or []) if str(x).strip()]
    missing = [key for key in required if payload.get(key) in {None, ""}]
    return {"ok": not missing, "missing_fields": missing}


def record_fallback_event_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    validation = validate_fallback_event_payload(payload, repo_root=repo_root)
    if not validation.get("ok"):
        raise ValueError(f"invalid fallback event payload: missing {validation.get('missing_fields')}")
    row = dict(payload)
    row.setdefault("fallback_event_id", f"fallback::{uuid4().hex[:12]}")
    row.setdefault("recorded_utc", _utc_now_iso())
    path = _fallback_event_log_path(repo_root=repo_root)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def _stable_solution_paths(events: Iterable[Mapping[str, Any]]) -> bool:
    values = {
        str(event.get("solution_path_signature") or "").strip()
        for event in events
        if str(event.get("solution_path_signature") or "").strip()
    }
    return len(values) == 1 and bool(values)


def synthesize_improvement_opportunity_v1(
    events: Sequence[Mapping[str, Any]],
    *,
    repo_root: Optional[Path] = None,
    min_observations: int = 2,
) -> Dict[str, Any]:
    rows = [dict(event) for event in events]
    if not rows:
        raise ValueError("at least one fallback event is required")
    for row in rows:
        validation = validate_fallback_event_payload(row, repo_root=repo_root)
        if not validation.get("ok"):
            raise ValueError(f"invalid fallback event payload: missing {validation.get('missing_fields')}")
    # Synthesis also serves read-only operator views. Recording belongs to the
    # explicit ingestion call, never to a projection of existing evidence.

    action_family = str(rows[0].get("action_family") or "")
    failure_class = str(rows[0].get("failure_class") or "")
    same_failure_class = all(str(row.get("failure_class") or "") == failure_class for row in rows)
    stable_solution = _stable_solution_paths(rows)
    safe_authority = all(bool(row.get("bounded_authority_safe")) for row in rows)
    validator_path = all(bool(row.get("validator_path_clear")) for row in rows)
    observations = len(rows)
    future_cost = sum(float(row.get("expected_future_codex_cost") or 0.0) for row in rows)
    future_friction = sum(float(row.get("operator_friction_cost") or 0.0) for row in rows)
    build_cost = max(float(row.get("estimated_build_cost") or 0.0) for row in rows)
    maintenance_cost = max(float(row.get("estimated_maintenance_cost") or 0.0) for row in rows)
    absorb_economic = (future_cost + future_friction) > (build_cost + maintenance_cost)

    rejection_reason = ""
    promote = True
    if observations < int(min_observations):
        promote = False
        rejection_reason = "insufficient_recurrence"
    elif not same_failure_class:
        promote = False
        rejection_reason = "unstable_failure_class"
    elif not stable_solution:
        promote = False
        rejection_reason = "solution_path_not_repeatable"
    elif not safe_authority:
        promote = False
        rejection_reason = "unsafe_authority_boundary"
    elif not validator_path:
        promote = False
        rejection_reason = "validator_path_unclear"
    elif not absorb_economic:
        promote = False
        rejection_reason = "bad_cost_benefit"

    opportunity = {
        "schema_version": "improvement_opportunity_schema_v1",
        "action_family": action_family,
        "failure_class": failure_class,
        "observations": observations,
        "stable_failure_class": same_failure_class,
        "stable_solution_path": stable_solution,
        "bounded_authority_safe": safe_authority,
        "validator_path_clear": validator_path,
        "expected_future_codex_cost": future_cost,
        "operator_friction_cost": future_friction,
        "estimated_build_cost": build_cost,
        "estimated_maintenance_cost": maintenance_cost,
        "economic_absorption_positive": absorb_economic,
        "decision": "promote_opportunity" if promote else "reject_opportunity",
        "rejection_reason": rejection_reason,
        "rank_score": round((future_cost + future_friction) - (build_cost + maintenance_cost), 3),
        "governed_follow_on_kind": "draft_idea_note" if promote else "record_only",
    }
    validation = validate_improvement_opportunity_payload(opportunity, repo_root=repo_root)
    if not validation.get("ok"):
        raise ValueError(f"invalid improvement opportunity payload: missing {validation.get('missing_fields')}")
    return opportunity


def build_phase1_regression_report(results: Mapping[str, Any]) -> Dict[str, Any]:
    patch_result = dict(results.get("patch_ingest_repair") or {})
    pis_result = dict(results.get("pis_assess") or {})
    job_result = dict(results.get("job_inspect") or {})
    fallback_result = dict(results.get("fallback_gate") or {})
    checks = {
        "patch_success_requires_pass": str(dict(patch_result.get("outcome") or {}).get("outcome") or "").startswith("success_")
        and str(dict(patch_result.get("outcome") or {}).get("verifier_verdict") or "").upper() == "PASS",
        "pis_success_requires_pass": str(dict(pis_result.get("outcome") or {}).get("outcome") or "").startswith("success_")
        and str(dict(pis_result.get("outcome") or {}).get("verifier_verdict") or "").upper() == "PASS",
        "fallback_gate_governed": str(fallback_result.get("governed_follow_on_kind") or "") in {"draft_idea_note", "record_only"},
        "job_inspect_not_registry_truth": bool(job_result.get("state_class")),
    }
    return {
        "schema_version": "action_plane_phase1_regression_report_v1",
        "checks": checks,
        "ok": all(bool(v) for v in checks.values()),
        "results": {
            "patch_ingest_repair": patch_result,
            "pis_assess": pis_result,
            "job_inspect": job_result,
            "fallback_gate": fallback_result,
        },
    }
