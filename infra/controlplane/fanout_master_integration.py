"""Deterministic master-side integration and classification for fanout runs."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import fanout_master_contract as _master_contract
from infra.controlplane import job_shape_lifecycle_contract as _job_shape_lifecycle_contract


SCHEMA_VERSION = "fanout_integrated_result_v1"
_FAILED_WORKER_STATUSES = frozenset({"failed_with_evidence", "split_needed"})
_BLOCKED_WORKER_STATUSES = frozenset({"blocked_need_master_decision"})
_PARTIAL_WORKER_STATUSES = frozenset({"delivered_with_known_gaps"})
_SUCCESS_WORKER_STATUSES = frozenset({"delivered"})


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(values: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(values, list):
        return out
    for raw in values:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _worker_changed_files(worker: Mapping[str, Any]) -> List[str]:
    return _normalize_text_list(worker.get("changed_files"))


def _maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _infer_worker_status(worker: Mapping[str, Any]) -> str:
    raw_status = _normalize_text(worker.get("status")).lower()
    if raw_status:
        return raw_status
    if _worker_changed_files(worker) or _normalize_text(worker.get("summary_path")) or list(worker.get("local_checks") or []):
        return "delivered"
    return ""


def _overlapping_changed_files(child_results: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_path: Dict[str, List[str]] = {}
    for row in child_results:
        child_id = _normalize_text(row.get("child_id"))
        decision = _normalize_text(row.get("decision"))
        if not child_id or decision not in {"keep", "keep_partial"}:
            continue
        for path in _normalize_text_list(row.get("changed_files")):
            by_path.setdefault(path, [])
            if child_id not in by_path[path]:
                by_path[path].append(child_id)

    conflicts: List[Dict[str, Any]] = []
    for path, child_ids in sorted(by_path.items()):
        if len(child_ids) < 2:
            continue
        conflicts.append(
            {
                "kind": "overlapping_changed_files",
                "paths": [path],
                "child_ids": sorted(child_ids),
                "summary": f"multiple kept children changed {path}",
            }
        )
    return conflicts


def _duplicate_child_receipts(worker_rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, int] = {}
    for row in worker_rows:
        child_id = _normalize_text(row.get("worker_id"))
        if not child_id:
            continue
        seen[child_id] = seen.get(child_id, 0) + 1

    conflicts: List[Dict[str, Any]] = []
    for child_id, count in sorted(seen.items()):
        if count < 2:
            continue
        conflicts.append(
            {
                "kind": "duplicate_child_receipts",
                "paths": [],
                "child_ids": [child_id],
                "summary": f"duplicate durable receipts detected for {child_id}",
                "receipt_count": count,
            }
        )
    return conflicts


def _retry_policy(swarm: Mapping[str, Any]) -> Dict[str, int]:
    policy = swarm.get("retry_policy") if isinstance(swarm.get("retry_policy"), Mapping) else {}
    child_limit = _maybe_int(policy.get("child_relaunch_limit"))
    master_limit = _maybe_int(policy.get("master_rerun_limit"))
    return {
        "child_relaunch_limit": max(0, child_limit or 0),
        "master_rerun_limit": max(0, master_limit or 0),
    }


def integrate_swarm_result(*, state: Mapping[str, Any], swarm: Mapping[str, Any]) -> Dict[str, Any]:
    success = swarm.get("success_contract") if isinstance(swarm.get("success_contract"), Mapping) else {}
    evidence = swarm.get("evidence_requirements") if isinstance(swarm.get("evidence_requirements"), Mapping) else {}
    write_policy = swarm.get("write_policy") if isinstance(swarm.get("write_policy"), Mapping) else {}
    launch_options = swarm.get("launch_options") if isinstance(swarm.get("launch_options"), Mapping) else {}
    retry_policy = _retry_policy(swarm)

    worker_rows = [row for row in list(state.get("workers") or []) if isinstance(row, Mapping)]
    accepted_statuses = [_normalize_text(item).lower() for item in list(success.get("accepted_statuses") or []) if _normalize_text(item)]
    if not accepted_statuses:
        accepted_statuses = ["finished"]
    runtime_status = _normalize_text(state.get("status")).lower()
    runtime_phase = _normalize_text(state.get("phase")).lower()
    integration = state.get("integration") if isinstance(state.get("integration"), Mapping) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), Mapping) else {}
    token_usage = state.get("token_usage") if isinstance(state.get("token_usage"), Mapping) else {}

    changed_files: List[str] = []
    for path in _normalize_text_list(integration.get("changed_files")):
        if path not in changed_files:
            changed_files.append(path)
    for worker in worker_rows:
        for path in _worker_changed_files(worker):
            if path not in changed_files:
                changed_files.append(path)

    summary_path = _normalize_text(artifacts.get("summary_path"))
    worker_summary_paths = []
    for worker in worker_rows:
        path = _normalize_text(worker.get("summary_path"))
        if path and path not in worker_summary_paths:
            worker_summary_paths.append(path)

    child_results: List[Dict[str, Any]] = []
    semantically_failed_workers: List[str] = []
    semantically_partial_workers: List[str] = []
    unusable_child_ids: List[str] = []
    worker_statuses: List[str] = []
    packet_errors: List[Dict[str, Any]] = []

    for index, worker in enumerate(worker_rows, start=1):
        child_id = _normalize_text(worker.get("worker_id")) or f"worker_{index}"
        status = _infer_worker_status(worker)
        worker_statuses.append(status)
        changed = _worker_changed_files(worker)
        known_gaps = _normalize_text_list(worker.get("known_gaps"))
        finisher_attempt = worker.get("finisher_attempt") if isinstance(worker.get("finisher_attempt"), Mapping) else {}
        finisher_status = _normalize_text(finisher_attempt.get("status")).lower()

        decision = "reject"
        classification = "unusable"
        reasons: List[str] = []

        if _normalize_text(worker.get("worker_id")) == "":
            reasons.append("missing_worker_id")
        if status in _FAILED_WORKER_STATUSES:
            classification = "rerun"
            decision = "rerun"
            semantically_failed_workers.append(child_id)
            reasons.append(f"worker_status:{status}")
        elif status in _BLOCKED_WORKER_STATUSES:
            classification = "blocked"
            decision = "master_review"
            reasons.append(f"worker_status:{status}")
        elif status in _PARTIAL_WORKER_STATUSES or known_gaps:
            classification = "partial"
            decision = "keep_partial"
            semantically_partial_workers.append(child_id)
            if status:
                reasons.append(f"worker_status:{status}")
            for gap in known_gaps:
                reasons.append(f"known_gap:{gap}")
        elif finisher_status and finisher_status not in {"delivered", "delivered_with_known_gaps"}:
            classification = "partial"
            decision = "keep_partial"
            semantically_partial_workers.append(child_id)
            reasons.append(f"finisher_status:{finisher_status}")
        elif status in _SUCCESS_WORKER_STATUSES:
            classification = "accepted"
            decision = "keep"
            reasons.append(f"worker_status:{status}")
        else:
            reasons.append(f"worker_status:{status or 'missing'}")

        if classification == "unusable":
            unusable_child_ids.append(child_id)
            packet_errors.append({"child_id": child_id, "reason": "; ".join(reasons)})

        child_results.append(
            {
                "child_id": child_id,
                "status": status,
                "classification": classification,
                "decision": decision,
                "changed_files": changed,
                "summary_path": _normalize_text(worker.get("summary_path")),
                "known_gaps": known_gaps,
                "reasons": reasons,
            }
        )

    ownership_drift = list(_master_contract.child_claim_violations(worker_rows))
    conflicts: List[Dict[str, Any]] = []
    if ownership_drift:
        for row in ownership_drift:
            child_id = _normalize_text(row.get("worker_id")) or "unknown_worker"
            claims = _normalize_text_list(row.get("forbidden_claims"))
            conflicts.append(
                {
                    "kind": "child_claimed_master_truth",
                    "child_ids": [child_id],
                    "paths": [],
                    "summary": f"{child_id} claimed master-owned fields: {', '.join(claims)}",
                    "claims": claims,
                }
            )

    conflicts.extend(_duplicate_child_receipts(worker_rows))
    conflicts.extend(_overlapping_changed_files(child_results))

    allowed_paths = [path.strip("/") for path in _normalize_text_list(write_policy.get("allowed_paths"))]
    off_target_paths = [
        path for path in changed_files if allowed_paths and not any(path == allowed or path.startswith(f"{allowed}/") for allowed in allowed_paths)
    ]

    master_blockers: List[Dict[str, Any]] = []
    if runtime_status not in accepted_statuses:
        master_blockers.append({"code": "runtime_status_not_accepted", "summary": f"swarm finished with status={runtime_status or 'unknown'}"})
    if bool(success.get("require_changed_files", True)) and not changed_files:
        master_blockers.append({"code": "changed_files_missing", "summary": "swarm produced no changed files"})
    if bool(evidence.get("require_summary_path", True)) and not summary_path:
        master_blockers.append({"code": "summary_path_missing", "summary": "swarm summary artifact is missing"})
    if bool(evidence.get("require_worker_artifacts", False)) and not worker_summary_paths:
        master_blockers.append({"code": "worker_artifacts_missing", "summary": "swarm worker summary artifacts are missing"})
    if allowed_paths and off_target_paths:
        master_blockers.append({"code": "off_target_scope", "summary": "swarm changed files outside allowed_paths"})
    max_changed_files = int(write_policy.get("max_changed_files") or 0)
    if max_changed_files and len(changed_files) > max_changed_files:
        master_blockers.append(
            {
                "code": "too_many_changed_files",
                "summary": f"swarm changed {len(changed_files)} files which exceeds max_changed_files={max_changed_files}",
            }
        )
    total_tokens = int(token_usage.get("total_tokens") or 0)
    max_total_tokens = int(launch_options.get("max_total_tokens") or 0)
    if max_total_tokens and total_tokens > max_total_tokens:
        master_blockers.append(
            {
                "code": "token_budget_exceeded",
                "summary": f"swarm used {total_tokens} total tokens which exceeds max_total_tokens={max_total_tokens}",
            }
        )

    keep_child_ids = [row["child_id"] for row in child_results if row["decision"] in {"keep", "keep_partial"}]
    partial_child_ids = [row["child_id"] for row in child_results if row["decision"] == "keep_partial"]
    rerun_child_ids = [row["child_id"] for row in child_results if row["decision"] == "rerun"]
    rejected_child_ids = [
        row["child_id"] for row in child_results if row["decision"] not in {"keep", "keep_partial", "rerun"}
    ]

    decision_rationale = [row["summary"] for row in conflicts]
    decision_rationale.extend(row["summary"] for row in master_blockers)
    decision_rationale.extend(f"unusable child packet from {row['child_id']}: {row['reason']}" for row in packet_errors)

    if conflicts:
        classification = "conflicting_outputs"
        integration_disposition = "conflict_blocked"
        final_verdict = "blocked_requires_review"
        operator_summary_state = "blocked_requires_review"
        recommended_action = "reject_conflicted_children_and_rerun"
        evaluation_status = "ownership_drift" if ownership_drift else "conflicting_outputs"
        rerun_child_ids = sorted(set(rerun_child_ids + keep_child_ids))
        keep_child_ids = []
        partial_child_ids = []
        rejected_child_ids = sorted(set(rejected_child_ids))
    elif master_blockers:
        classification = "master_blocked_outcome"
        integration_disposition = "master_blocked"
        recommended_action = "downgrade_parent_and_review"
        blocker_codes = {row["code"] for row in master_blockers}
        if blocker_codes <= {"changed_files_missing", "summary_path_missing", "worker_artifacts_missing"}:
            final_verdict = "proof_gap_present"
            operator_summary_state = "proof_gap_present"
            evaluation_status = "proof_gap"
        elif "token_budget_exceeded" in blocker_codes:
            final_verdict = "blocked_requires_review"
            operator_summary_state = "blocked_requires_review"
            evaluation_status = "token_budget_exceeded"
        elif blocker_codes & {"off_target_scope", "too_many_changed_files"}:
            final_verdict = "blocked_requires_review"
            operator_summary_state = "blocked_requires_review"
            evaluation_status = "off_target_scope"
        else:
            final_verdict = "blocked_requires_review"
            operator_summary_state = "blocked_requires_review"
            evaluation_status = "semantic_failure"
    elif unusable_child_ids and not keep_child_ids:
        classification = "unusable_child_packets"
        integration_disposition = "rerun_required"
        final_verdict = "proof_gap_present"
        operator_summary_state = "proof_gap_present"
        recommended_action = "rerun_unusable_children"
        evaluation_status = "proof_gap"
        rerun_child_ids = sorted(set(rerun_child_ids + unusable_child_ids))
    elif partial_child_ids or rerun_child_ids or unusable_child_ids:
        classification = "partial_success"
        integration_disposition = "integrated_partial"
        final_verdict = "partial_success"
        operator_summary_state = "partial_success"
        recommended_action = "keep_clean_results_and_rerun_failed_children"
        evaluation_status = "semantic_partial"
        rerun_child_ids = sorted(set(rerun_child_ids + unusable_child_ids))
    else:
        classification = "complete_success"
        integration_disposition = "integrated_clean"
        final_verdict = "success_deterministic"
        operator_summary_state = "ok"
        recommended_action = "keep_all_results"
        evaluation_status = "ok"
        if not decision_rationale:
            decision_rationale.append("all child results were integrated cleanly")

    child_attempts: Dict[str, int] = {}
    for worker in worker_rows:
        child_id = _normalize_text(worker.get("worker_id"))
        if not child_id:
            continue
        child_attempts[child_id] = max(child_attempts.get(child_id, 1), _maybe_int(worker.get("attempt_count")) or 1)

    child_relaunch_limit = int(retry_policy.get("child_relaunch_limit") or 0)
    master_rerun_limit = int(retry_policy.get("master_rerun_limit") or 0)
    child_exhausted_ids = sorted(
        child_id
        for child_id in rerun_child_ids
        if child_attempts.get(child_id, 1) > child_relaunch_limit
    )
    child_eligible_ids = [child_id for child_id in rerun_child_ids if child_id not in child_exhausted_ids]
    master_attempts_used = max(1, _maybe_int(state.get("master_attempt_count")) or _maybe_int(state.get("attempt_count")) or 1)
    master_rerun_exhausted = master_attempts_used > master_rerun_limit

    if conflicts:
        child_relaunch_state = "blocked_by_conflict"
    elif child_eligible_ids:
        child_relaunch_state = "allowed"
    elif child_exhausted_ids:
        child_relaunch_state = "exhausted"
    else:
        child_relaunch_state = "not_needed"

    if classification in {"complete_success"}:
        master_rerun_state = "not_needed"
        master_rerun_reason = "fanout integrated cleanly"
    elif runtime_status == "interrupted":
        master_rerun_state = "allowed" if not master_rerun_exhausted else "exhausted"
        master_rerun_reason = "fanout stopped interrupted and must reenter from durable parent truth"
    elif conflicts:
        master_rerun_state = "blocked"
        master_rerun_reason = "conflicting durable child receipts require master review before relaunch"
    elif master_rerun_exhausted:
        master_rerun_state = "exhausted"
        master_rerun_reason = "master rerun limit already consumed"
    else:
        master_rerun_state = "blocked"
        master_rerun_reason = "current outcome requires explicit operator or master review"

    duplicate_child_ids = sorted(
        {
            child_id
            for row in conflicts
            if str(row.get("kind") or "") == "duplicate_child_receipts"
            for child_id in list(row.get("child_ids") or [])
            if _normalize_text(child_id)
        }
    )
    completed_child_ids = sorted(
        row["child_id"] for row in child_results if row["classification"] in {"accepted", "partial", "blocked", "rerun"}
    )
    pending_child_ids = sorted(
        row["child_id"] for row in child_results if row["classification"] == "unusable"
    )
    if runtime_status == "interrupted":
        reconciliation_status = "interrupted_reentry_required"
        reconciliation_summary = "fanout stopped interrupted; future work must reenter from durable parent truth"
    elif duplicate_child_ids:
        reconciliation_status = "blocked_on_conflicting_receipts"
        reconciliation_summary = "fanout reconciliation found conflicting durable child receipts"
    elif classification == "partial_success":
        reconciliation_status = "partial_collection_reconciled"
        reconciliation_summary = "fanout reconciled partial durable child output and preserved rerun boundaries"
    else:
        reconciliation_status = "terminal_state_reconciled"
        reconciliation_summary = "fanout terminal view was derived from durable parent and child records"

    return {
        "schema_version": SCHEMA_VERSION,
        "master_contract_uid": _master_contract.UID,
        "master_owner": _master_contract.MASTER_OWNER,
        "runtime_phase": runtime_phase,
        "runtime_status": runtime_status,
        "child_roster": [row["child_id"] for row in child_results],
        "child_statuses": {row["child_id"]: row["status"] for row in child_results},
        "child_results": child_results,
        "classification": classification,
        "integration_disposition": integration_disposition,
        "final_verdict": final_verdict,
        "operator_summary_state": operator_summary_state,
        "recommended_action": recommended_action,
        "evaluation_status": evaluation_status,
        "decision_rationale": decision_rationale,
        "restart_reconciliation": {
            "status": reconciliation_status,
            "summary": reconciliation_summary,
            "source_of_truth": "durable_parent_and_child_records",
            "recovery_class": _job_shape_lifecycle_contract.recovery_class_for_shape("fanout_job"),
            "runtime_status": runtime_status,
            "runtime_phase": runtime_phase,
            "completed_child_ids": completed_child_ids,
            "pending_child_ids": pending_child_ids,
            "rerun_candidate_child_ids": list(rerun_child_ids),
            "duplicate_child_ids": duplicate_child_ids,
            "child_receipt_counts": {child_id: count for child_id, count in sorted({
                child_id: sum(1 for worker in worker_rows if _normalize_text(worker.get("worker_id")) == child_id)
                for child_id in [row["child_id"] for row in child_results]
            }.items())},
        },
        "retry_boundaries": {
            "policy": {
                "child_relaunch_limit": child_relaunch_limit,
                "master_rerun_limit": master_rerun_limit,
            },
            "child_relaunch": {
                "state": child_relaunch_state,
                "eligible_child_ids": child_eligible_ids,
                "exhausted_child_ids": child_exhausted_ids,
                "attempt_count_by_child": {child_id: child_attempts.get(child_id, 1) for child_id in sorted(set(rerun_child_ids))},
            },
            "master_rerun": {
                "state": master_rerun_state,
                "attempts_used": master_attempts_used,
                "reason": master_rerun_reason,
            },
        },
        "keep_child_ids": keep_child_ids,
        "partial_child_ids": partial_child_ids,
        "rerun_child_ids": rerun_child_ids,
        "rejected_child_ids": rejected_child_ids,
        "unusable_child_ids": unusable_child_ids,
        "conflicts": conflicts,
        "master_blockers": master_blockers,
        "ownership_drift": ownership_drift,
        "semantically_failed_workers": sorted(set(semantically_failed_workers)),
        "semantically_partial_workers": sorted(set(semantically_partial_workers)),
        "off_target_paths": off_target_paths,
        "packet_errors": packet_errors,
        "evidence_summary": {
            "changed_files": changed_files,
            "summary_path": summary_path,
            "worker_summary_paths": worker_summary_paths,
            "worker_statuses": worker_statuses,
            "token_usage": {
                "input_tokens": int(token_usage.get("input_tokens") or 0),
                "output_tokens": int(token_usage.get("output_tokens") or 0),
                "total_tokens": total_tokens,
            },
        },
    }
