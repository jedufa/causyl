#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from infra.webui.ledger import ledger_storage
from infra.controlplane import server_maintenance as maint
from tools import runtime_policy as _runtime_policy


RECEIPT_SCHEMA_VERSION = "scheduler_pipeline_healer_receipt_v1"
DEFAULT_REPEAT_FAILURE_THRESHOLD = 3
DEFAULT_IDLE_MINUTES = 60
DEFAULT_MAX_REPAIRS_PER_JOB = 2
DEFAULT_MAX_REPAIRS_GLOBAL = 10
DEFAULT_RECENT_LEDGER_STALE_MINUTES = 24 * 60
DEFAULT_MAX_LEDGER_RECONCILES_PER_RUN = 4

REPAIRABLE_CONTRACT_FAILURE_REASONS = {
    "missing_changed_files_section",
    "missing_verdict_header",
    "verdict_not_on_first_line",
}


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _utc_iso(ts: Optional[dt.datetime] = None) -> str:
    return (ts or _utc_now()).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_name(raw: str) -> str:
    return sched._safe_name(raw)


def _root(repo_root: Path) -> Path:
    path = (repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "pipeline_healer").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _reports_dir(repo_root: Path) -> Path:
    path = (_root(repo_root) / "reports").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _receipts_dir(repo_root: Path) -> Path:
    path = (_root(repo_root) / "receipts").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _latest_report_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "latest_report.json").resolve()


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_jobs(repo_root: Path) -> List[Dict[str, Any]]:
    return sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())


def _load_rows_by_job(repo_root: Path) -> Dict[str, Dict[str, Any]]:
    rows = _load_jobs(repo_root)
    return {str(row.get("job_id") or ""): row for row in rows}


def _dependents_index(rows: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for job_id, row in rows.items():
        for dep in list(row.get("depends_on") or []):
            out.setdefault(str(dep), []).append(job_id)
    return out


def _last_error_signature(job_id: str, *, repo_root: Path, threshold: int) -> Dict[str, Any]:
    receipts = sched.list_receipts(job_id, repo_root=repo_root, limit=max(threshold + 2, 6))
    if not receipts:
        return {"count": 0, "signature": "", "error": ""}
    latest_error = str(receipts[0].get("error") or "").strip()
    if not latest_error:
        return {"count": 0, "signature": "", "error": ""}
    signature = sched._sha256_text(latest_error)
    count = 0
    for row in receipts:
        err = str(row.get("error") or "").strip()
        if not err or sched._sha256_text(err) != signature:
            break
        count += 1
    return {"count": count, "signature": signature, "error": latest_error}


def _latest_receipt(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    receipts = sched.list_receipts(job_id, repo_root=repo_root, limit=1)
    if not receipts:
        return {}
    latest = receipts[0]
    run_id = str(latest.get("run_id") or "").strip()
    if not run_id:
        return latest if isinstance(latest, dict) else {}
    return sched.load_receipt(job_id, run_id, repo_root=repo_root)


def _is_repairable_contract_failure_reason(error: str) -> bool:
    normalized = str(error or "").strip()
    if normalized in REPAIRABLE_CONTRACT_FAILURE_REASONS:
        return True
    prefix = "invalid PASS_PENDING_ACTIONS contract: "
    if not normalized.startswith(prefix):
        return False
    detail = normalized[len(prefix) :].strip()
    repairable_pending_action_errors = {
        "missing_stop_rule_block",
        "missing_stop_rule_one_repair_turn",
        "missing_stop_rule_no_scope_widening",
        "missing_stop_rule_fail_honestly",
    }
    return detail in repairable_pending_action_errors


def _receipt_stage_content(receipt: Dict[str, Any], stage: str) -> str:
    direct_key = "last_builder_message" if stage == "builder" else "last_verifier_message"
    direct = str(receipt.get(direct_key) or "").strip()
    if direct:
        return direct
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    if not iterations:
        return ""
    last = iterations[-1] if isinstance(iterations[-1], dict) else {}
    stage_row = last.get(stage) if isinstance(last.get(stage), dict) else {}
    return str(stage_row.get("content") or "").strip()


def _is_empty_proof_gap_runtime_receipt(receipt: Dict[str, Any]) -> bool:
    if str(receipt.get("status") or "").strip().lower() != "proof_gap":
        return False
    error = str(receipt.get("error") or "").strip().lower()
    if "validation commands run" not in error:
        return False
    builder = _receipt_stage_content(receipt, "builder")
    verifier = _receipt_stage_content(receipt, "verifier")
    return not builder and not verifier


def _empty_proof_gap_signature(job_id: str, *, repo_root: Path, threshold: int) -> Dict[str, Any]:
    receipts = sched.list_receipts(job_id, repo_root=repo_root, limit=max(threshold + 2, 6))
    if not receipts:
        return {"count": 0, "error": "", "run_id": ""}
    latest = receipts[0]
    if not _is_empty_proof_gap_runtime_receipt(latest):
        return {"count": 0, "error": "", "run_id": ""}
    count = 0
    for row in receipts:
        if not _is_empty_proof_gap_runtime_receipt(row):
            break
        count += 1
    return {
        "count": count,
        "error": str(latest.get("error") or ""),
        "run_id": str(latest.get("run_id") or ""),
    }


def _is_verified_commit_failure_receipt(receipt: Dict[str, Any]) -> bool:
    if str(receipt.get("status") or "").strip().lower() != "error":
        return False
    error = str(receipt.get("error") or "").strip().lower()
    if not error.startswith("post-success git commit/tag failed"):
        return False
    if not isinstance(receipt.get("git_commit_tag"), dict):
        return False
    excerpt = sched._verifier_commit_excerpt(receipt)
    return bool(excerpt)


def _verified_commit_failure_signature(job_id: str, *, repo_root: Path, threshold: int) -> Dict[str, Any]:
    receipts = sched.list_receipts(job_id, repo_root=repo_root, limit=max(threshold + 2, 6))
    if not receipts:
        return {"count": 0, "error": "", "run_id": ""}
    latest = _latest_receipt(job_id, repo_root=repo_root)
    if not latest or not _is_verified_commit_failure_receipt(latest):
        return {"count": 0, "error": "", "run_id": ""}
    count = 0
    for row in receipts:
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            break
        full = sched.load_receipt(job_id, run_id, repo_root=repo_root)
        if not _is_verified_commit_failure_receipt(full):
            break
        count += 1
    return {
        "count": count,
        "error": str(latest.get("error") or ""),
        "run_id": str(latest.get("run_id") or ""),
    }


def _verifier_failure_excerpt(receipt: Dict[str, Any]) -> str:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    if not iterations:
        return ""
    last = iterations[-1] if isinstance(iterations[-1], dict) else {}
    verifier = last.get("verifier") if isinstance(last.get("verifier"), dict) else {}
    content = str(verifier.get("content") or "").strip()
    if not content:
        return ""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[:4])[:600]


def _repeated_identical_verifier_failure_signature(job_id: str, *, repo_root: Path, threshold: int) -> Dict[str, Any]:
    receipts = sched.list_receipts(job_id, repo_root=repo_root, limit=max(threshold + 2, 6))
    if not receipts:
        return {"count": 0, "signature": "", "excerpt": "", "run_id": ""}
    latest = _latest_receipt(job_id, repo_root=repo_root)
    if str(latest.get("status") or "").strip().lower() != "failed_verification":
        return {"count": 0, "signature": "", "excerpt": "", "run_id": ""}
    excerpt = _verifier_failure_excerpt(latest)
    if not excerpt or not excerpt.upper().startswith("VERDICT: FAIL"):
        return {"count": 0, "signature": "", "excerpt": "", "run_id": ""}
    signature = sched._sha256_text(excerpt)
    count = 0
    for row in receipts:
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            break
        full = sched.load_receipt(job_id, run_id, repo_root=repo_root)
        if str(full.get("status") or "").strip().lower() != "failed_verification":
            break
        candidate = _verifier_failure_excerpt(full)
        if not candidate or sched._sha256_text(candidate) != signature:
            break
        count += 1
    return {
        "count": count,
        "signature": signature,
        "excerpt": excerpt,
        "run_id": str(latest.get("run_id") or ""),
    }


def _latest_success_utc(rows: Dict[str, Dict[str, Any]]) -> str:
    winners: List[str] = []
    for row in rows.values():
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        if str(state.get("last_status") or "").strip().lower() == "ok":
            txt = str(state.get("last_finished_utc") or state.get("updated_utc") or "").strip()
            if txt:
                winners.append(txt)
    return sorted(winners)[-1] if winners else ""


def _find_ledger_item_dir_by_uid(repo_root: Path, *, uid: str) -> Path | None:
    ledger_root = (repo_root / "governance" / "ledger" / "items").resolve()
    if not ledger_root.exists():
        return None
    return ledger_storage.item_dir_for_uid(ledger_root, uid)


def _ledger_meta_for_uid(repo_root: Path, *, uid: str) -> Dict[str, Any]:
    item_dir = _find_ledger_item_dir_by_uid(repo_root, uid=uid)
    if item_dir is None:
        return {}
    meta_path = (item_dir / "meta.json").resolve()
    if not meta_path.exists():
        return {}
    return sched._read_json(meta_path) or {}


def _status_reconcile_job_id(uid: str) -> str:
    suffix = str(uid or "").rsplit("-", 1)[-1].lower()
    m = __import__("re").match(r"^PIS-(\d{8})-", str(uid or ""))
    date_part = m.group(1) if m else "job"
    return f"verify_{suffix}_{date_part}_status_reconcile"


def _reconcile_prompt(*, uid: str, title: str, pis_rel: str, source_job_id: str) -> str:
    return (
        f"Verify whether ledger item `{uid}` has actually earned a stronger ledger status based on repo evidence, tests, and prior implementation receipts.\n\n"
        "Instructions:\n"
        "1. Read the bound PIS and inspect only the files needed to judge the implementation honestly.\n"
        "2. Review the latest successful scheduled-job receipt/report for this bound item when useful.\n"
        "3. If the implementation is fully supported, update ledger truth to `verified` and append durable events/evidence.\n"
        "4. If confidence is only partial, keep or set the honest non-verified status and state the exact remaining gap.\n"
        "5. Run the narrowest real validation commands that support the verdict.\n"
        "6. Include a parseable `Validation Commands Run` section in the builder output.\n\n"
        "Do not claim verification from scheduler success alone; judge the bound item itself.\n\n"
        "Ledger binding:\n"
        f"- ledger_uid: {uid}\n"
        f"- title: {title}\n"
        f"- pis_path: {pis_rel}\n"
        f"- source_job_id: {source_job_id}\n"
    )


def _build_finding(
    *,
    finding_type: str,
    repair_class: str,
    severity: str,
    confidence: str,
    job_id: str,
    row: Dict[str, Any],
    evidence: Dict[str, Any],
    summary: str,
) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    return {
        "finding_type": finding_type,
        "repair_class": repair_class,
        "severity": severity,
        "confidence": confidence,
        "job_id": job_id,
        "summary": summary,
        "evidence": {
            "job_id": job_id,
            "last_status": str(state.get("last_status") or ""),
            "due_reason": str(due.get("reason") or ""),
            "last_slot_key": str(state.get("last_slot_key") or ""),
            **dict(evidence or {}),
        },
    }


def _finding_sort_key(finding: Dict[str, Any]) -> tuple[int, str]:
    finding_type = str(finding.get("finding_type") or "")
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    if finding_type == "stale_ledger_status_after_success":
        return (0, str(evidence.get("last_finished_utc") or ""))
    return (1, str(evidence.get("last_finished_utc") or ""))


def detect_findings(
    *,
    repo_root: Path,
    repeat_failure_threshold: int,
    idle_minutes: int,
) -> Dict[str, Any]:
    rows = _load_rows_by_job(repo_root)
    dependents = _dependents_index(rows)
    pause_manifest = maint.load_pause_manifest(repo_root=repo_root)
    paused_job_ids = {str(item or "").strip() for item in list(pause_manifest.get("paused_job_ids") or []) if str(item or "").strip()}
    findings: List[Dict[str, Any]] = []
    now = _utc_now()
    latest_success = _latest_success_utc(rows)
    latest_success_age_minutes = None
    if latest_success:
        latest_dt = sched._parse_utc(latest_success)
        latest_success_age_minutes = int(max(0.0, (now - latest_dt).total_seconds()) // 60)

    for job_id, row in rows.items():
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        schedule = row.get("schedule") if isinstance(row.get("schedule"), dict) else {}
        active_run_id = str(state.get("active_run_id") or "").strip()
        active_claim_present = bool(state.get("active_claim_present"))
        active_claim_stale = bool(state.get("active_claim_stale"))
        active_claim_reason = str(state.get("active_claim_reason") or "").strip()
        blocked_dependents = [dep for dep in dependents.get(job_id, []) if str(((rows.get(dep) or {}).get("due") or {}).get("reason") or "").startswith(f"dependency_not_ok:{job_id}")]
        if active_run_id and (not active_claim_present or active_claim_stale):
            findings.append(
                _build_finding(
                    finding_type="stranded_active_run",
                    repair_class="active_run_recovery",
                    severity="high",
                    confidence="high",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "active_run_id": active_run_id,
                        "active_claim_present": active_claim_present,
                        "active_claim_stale": active_claim_stale,
                        "active_claim_reason": active_claim_reason,
                    },
                    summary="Job is marked running but its active claim is missing or stale.",
                )
            )
        if str(schedule.get("mode") or "").strip().lower() == "once":
            run_at = str(schedule.get("run_at") or "").strip()
            if (
                run_at
                and str(state.get("last_slot_key") or "").strip() == run_at
                and str(state.get("last_status") or "").strip().lower() != "ok"
            ):
                findings.append(
                    _build_finding(
                        finding_type="once_slot_consumed_on_failure",
                        repair_class="state_repair",
                        severity="high",
                        confidence="high",
                        job_id=job_id,
                        row=row,
                        evidence={"blocked_dependents": blocked_dependents},
                        summary="Once job consumed its slot despite a non-ok terminal status.",
                    )
                )
        if (not bool(row.get("enabled"))) and blocked_dependents and job_id in paused_job_ids:
            findings.append(
                _build_finding(
                    finding_type="paused_chain_head_with_blocked_dependents",
                    repair_class="dependency_unblock",
                    severity="medium",
                    confidence="high",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "blocked_dependents": blocked_dependents,
                        "pause_manifest_path": str(pause_manifest.get("manifest_path") or ""),
                    },
                    summary="Chain head was disabled by server pause/resume machinery and is blocking downstream jobs.",
                )
            )
        elif (not bool(row.get("enabled"))) and blocked_dependents:
            findings.append(
                _build_finding(
                    finding_type="disabled_chain_head_with_blocked_dependents",
                    repair_class="dependency_unblock",
                    severity="medium",
                    confidence="high",
                    job_id=job_id,
                    row=row,
                    evidence={"blocked_dependents": blocked_dependents},
                    summary="Disabled chain head is blocking downstream jobs.",
                )
            )
        failure_sig = _last_error_signature(job_id, repo_root=repo_root, threshold=repeat_failure_threshold)
        empty_proof_gap_sig = _empty_proof_gap_signature(job_id, repo_root=repo_root, threshold=repeat_failure_threshold)
        commit_failure_sig = _verified_commit_failure_signature(job_id, repo_root=repo_root, threshold=repeat_failure_threshold)
        verifier_fail_sig = _repeated_identical_verifier_failure_signature(job_id, repo_root=repo_root, threshold=repeat_failure_threshold)
        if int(empty_proof_gap_sig.get("count") or 0) >= int(repeat_failure_threshold):
            findings.append(
                _build_finding(
                    finding_type="repeated_empty_proof_gap_runtime_failure",
                    repair_class="runtime_route_repair",
                    severity="medium",
                    confidence="high",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "repeat_count": int(empty_proof_gap_sig.get("count") or 0),
                        "latest_error": str(empty_proof_gap_sig.get("error") or ""),
                        "latest_run_id": str(empty_proof_gap_sig.get("run_id") or ""),
                    },
                    summary="Job is repeatedly ending in proof_gap with empty builder/verifier output, which points to a runtime-path failure rather than a real validation miss.",
                )
            )
        elif int(verifier_fail_sig.get("count") or 0) >= int(repeat_failure_threshold):
            findings.append(
                _build_finding(
                    finding_type="repeated_identical_verifier_failure",
                    repair_class="semantic_retry_tuning",
                    severity="medium",
                    confidence="medium",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "repeat_count": int(verifier_fail_sig.get("count") or 0),
                        "verifier_signature_hash": str(verifier_fail_sig.get("signature") or ""),
                        "latest_excerpt": str(verifier_fail_sig.get("excerpt") or ""),
                        "latest_run_id": str(verifier_fail_sig.get("run_id") or ""),
                    },
                    summary="Job is repeating the same explicit verifier failure, which suggests the current implementation route is stuck rather than making progress.",
                )
            )
        elif int(failure_sig.get("count") or 0) >= int(repeat_failure_threshold) and _is_repairable_contract_failure_reason(
            str(failure_sig.get("error") or "")
        ):
            findings.append(
                _build_finding(
                    finding_type="repeated_contract_shape_failure",
                    repair_class="semantic_retry_tuning",
                    severity="medium",
                    confidence="high",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "repeat_count": int(failure_sig.get("count") or 0),
                        "error_signature_hash": str(failure_sig.get("signature") or ""),
                        "latest_error": str(failure_sig.get("error") or ""),
                    },
                    summary="Job is repeating the same repairable contract-shape failure, which points to workflow hygiene drift rather than new product information.",
                )
            )
        elif int(commit_failure_sig.get("count") or 0) >= int(repeat_failure_threshold):
            findings.append(
                _build_finding(
                    finding_type="repeated_verified_commit_failure",
                    repair_class="post_success_commit_repair",
                    severity="medium",
                    confidence="high",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "repeat_count": int(commit_failure_sig.get("count") or 0),
                        "latest_error": str(commit_failure_sig.get("error") or ""),
                        "latest_run_id": str(commit_failure_sig.get("run_id") or ""),
                    },
                    summary="Job is repeatedly failing only at the post-success git commit/tag step after verification already passed.",
                )
            )
        elif int(failure_sig.get("count") or 0) >= int(repeat_failure_threshold):
            findings.append(
                _build_finding(
                    finding_type="repeated_runtime_signature_failure",
                    repair_class="runtime_route_repair",
                    severity="medium",
                    confidence="medium",
                    job_id=job_id,
                    row=row,
                    evidence={
                        "repeat_count": int(failure_sig.get("count") or 0),
                        "error_signature_hash": str(failure_sig.get("signature") or ""),
                        "latest_error": str(failure_sig.get("error") or ""),
                    },
                    summary="Job is repeating the same runtime failure signature.",
                )
            )
        binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
        binding_type = str(binding.get("type") or "").strip()
        binding_id = str(binding.get("id") or "").strip()
        last_status = str(state.get("last_status") or "").strip().lower()
        if binding_type == "ledger_uid" and binding_id and last_status == "ok" and not active_run_id:
            meta = _ledger_meta_for_uid(repo_root, uid=binding_id)
            ledger_status = str(meta.get("status") or "").strip().lower()
            evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
            changesets = meta.get("changesets") if isinstance(meta.get("changesets"), list) else []
            last_finished_utc = str(state.get("last_finished_utc") or "").strip()
            recent_success = False
            if last_finished_utc:
                try:
                    age_minutes = int(max(0.0, (now - sched._parse_utc(last_finished_utc)).total_seconds()) // 60)
                    recent_success = age_minutes <= DEFAULT_RECENT_LEDGER_STALE_MINUTES
                except Exception:
                    recent_success = False
            if ledger_status in {"draft", "in_progress", "blocked"} and recent_success:
                findings.append(
                    _build_finding(
                        finding_type="stale_ledger_status_after_success",
                        repair_class="ledger_truth_reconcile",
                        severity="medium",
                        confidence="high",
                        job_id=job_id,
                        row=row,
                        evidence={
                            "ledger_uid": binding_id,
                            "ledger_status": ledger_status,
                            "last_finished_utc": last_finished_utc,
                        },
                        summary="Job finished ok but the bound ledger item still has a stale non-verified status.",
                    )
                )
            elif ledger_status == "verified" and recent_success and evidence and not changesets:
                findings.append(
                    _build_finding(
                        finding_type="incomplete_verified_ledger_after_success",
                        repair_class="ledger_truth_reconcile",
                        severity="high",
                        confidence="high",
                        job_id=job_id,
                        row=row,
                        evidence={
                            "ledger_uid": binding_id,
                            "ledger_status": ledger_status,
                            "last_finished_utc": last_finished_utc,
                            "missing_reasons": ["ledger_changesets_missing"],
                        },
                        summary="Job finished ok but the bound ledger item is verified without required changesets.",
                    )
                )
        if blocked_dependents:
            root_cause = ""
            if any(f["job_id"] == job_id and f["finding_type"] == "once_slot_consumed_on_failure" for f in findings):
                root_cause = "once_slot_consumed_on_failure"
            elif any(f["job_id"] == job_id and f["finding_type"] == "paused_chain_head_with_blocked_dependents" for f in findings):
                root_cause = "paused_chain_head_with_blocked_dependents"
            elif not bool(row.get("enabled")):
                root_cause = "disabled_chain_head_with_blocked_dependents"
            elif any(f["job_id"] == job_id and f["finding_type"] == "repeated_empty_proof_gap_runtime_failure" for f in findings):
                root_cause = "repeated_empty_proof_gap_runtime_failure"
            elif any(f["job_id"] == job_id and f["finding_type"] == "repeated_identical_verifier_failure" for f in findings):
                root_cause = "repeated_identical_verifier_failure"
            elif any(f["job_id"] == job_id and f["finding_type"] == "repeated_verified_commit_failure" for f in findings):
                root_cause = "repeated_verified_commit_failure"
            elif int(failure_sig.get("count") or 0) >= int(repeat_failure_threshold):
                root_cause = "repeated_runtime_signature_failure"
            if root_cause:
                findings.append(
                    _build_finding(
                        finding_type="dependency_chain_deadlock",
                        repair_class="dependency_unblock" if root_cause not in {"repeated_runtime_signature_failure", "repeated_empty_proof_gap_runtime_failure", "repeated_verified_commit_failure", "repeated_identical_verifier_failure"} else ("post_success_commit_repair" if root_cause == "repeated_verified_commit_failure" else ("semantic_retry_tuning" if root_cause == "repeated_identical_verifier_failure" else "runtime_route_repair")),
                        severity="high",
                        confidence="high",
                        job_id=job_id,
                        row=row,
                        evidence={"blocked_dependents": blocked_dependents, "dependency_head": job_id, "root_cause": root_cause},
                        summary="Blocked dependents trace back to a deadlocked chain head.",
                    )
                )

    productive_due = [
        row for row in rows.values()
        if bool(((row.get("due") or {}).get("eligible"))) and str(((row.get("state") or {}).get("last_status") or "")).strip().lower() not in {"running"}
    ]
    active_jobs = [
        row for row in rows.values()
        if str((((row.get("state") or {}).get("active_run_id")) or "")).strip()
    ]
    heartbeat = {
        "last_successful_chain_advancement_utc": latest_success,
        "last_successful_chain_advancement_age_minutes": latest_success_age_minutes,
        "active_job_count": len(active_jobs),
        "due_eligible_count": len(productive_due),
        "oldest_blocked_chain_head": next(
            (
                finding["job_id"]
                for finding in findings
                if finding["finding_type"] == "dependency_chain_deadlock"
            ),
            "",
        ),
    }
    if (
        latest_success_age_minutes is not None
        and latest_success_age_minutes >= int(idle_minutes)
        and not active_jobs
        and not productive_due
        and findings
    ):
        heartbeat["stuck_threshold_exceeded"] = True
    else:
        heartbeat["stuck_threshold_exceeded"] = False
    return {"rows": rows, "findings": findings, "heartbeat": heartbeat}


def _runtime_healer_profile() -> Dict[str, Any]:
    codex_primary = _runtime_policy.get_runtime_policy_text(
        "scheduler.controller.default_model",
        "gpt-5.4",
    )
    codex_fallbacks = _runtime_policy.get_runtime_policy_csv(
        "scheduler.controller.default_fallback_models",
        "gpt-5.2-codex",
    )
    codex_models: List[str] = []
    for model_name in [codex_primary, *codex_fallbacks]:
        candidate = str(model_name or "").strip()
        if candidate and candidate not in codex_models:
            codex_models.append(candidate)
    fallback_models = ["claude-haiku-4-5", "grok-code-fast-1", *codex_models]
    tech_model_sequence = [
        {"agent_tech": "gemini", "model": "gemini-2.5-pro"},
        {"agent_tech": "claude", "model": "claude-haiku-4-5"},
        {"agent_tech": "xai", "model": "grok-code-fast-1"},
    ]
    tech_model_sequence.extend({"agent_tech": "codex", "model": model_name} for model_name in codex_models)
    return {
        "agent_tech": "gemini",
        "model": "gemini-2.5-pro",
        "fallback_on_quota": True,
        "fallback_agent_techs": ["claude", "xai", "codex"],
        "fallback_models": fallback_models,
        "tech_model_sequence": tech_model_sequence,
    }

def _read_prior_healer_runs(repo_root: Path, *, limit: int = 100) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(_receipts_dir(repo_root).glob("*.json"), key=lambda p: p.name, reverse=True):
        obj = sched._read_json(path)
        if obj:
            rows.append(obj)
        if len(rows) >= int(limit):
            break
    return rows


def _count_recent_repairs(
    receipts: List[Dict[str, Any]],
    *,
    now: dt.datetime,
    job_id: str,
    hours: int = 1,
) -> int:
    count = 0
    for row in receipts:
        started = str(row.get("generated_utc") or "").strip()
        if not started:
            continue
        try:
            ts = sched._parse_utc(started)
        except Exception:
            continue
        if (now - ts).total_seconds() > hours * 3600:
            continue
        for action in list(row.get("actions") or []):
            if isinstance(action, dict) and str(action.get("job_id") or "") == job_id and str(action.get("decision") or "") == "applied":
                count += 1
    return count


def _count_recent_global_repairs(receipts: List[Dict[str, Any]], *, now: dt.datetime, days: int = 1) -> int:
    count = 0
    for row in receipts:
        started = str(row.get("generated_utc") or "").strip()
        if not started:
            continue
        try:
            ts = sched._parse_utc(started)
        except Exception:
            continue
        if (now - ts).total_seconds() > days * 86400:
            continue
        for action in list(row.get("actions") or []):
            if isinstance(action, dict) and str(action.get("decision") or "") == "applied":
                count += 1
    return count


def _apply_clear_last_slot_key(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    state = sched.load_state(job_id, repo_root=repo_root)
    before = str(state.get("last_slot_key") or "")
    updated = dict(state)
    updated.pop("last_slot_key", None)
    sched._write_state(job_id, updated, repo_root=repo_root)
    return {"before": before, "after": str(updated.get("last_slot_key") or "")}


def _apply_active_run_recovery(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    spec = sched.load_job(job_id, repo_root=repo_root)
    state = sched.load_state(job_id, repo_root=repo_root)
    before_run_id = str(state.get("active_run_id") or "")
    before_status = str(state.get("last_status") or "")
    claim_reason = str(
        (
            sched.job_summary(spec, state=state, repo_root=repo_root)
            .get("state", {})
            .get("active_claim_reason")
            or state.get("active_claim_reason")
            or ""
        )
    )
    updated = sched._apply_active_run_recovery(
        job_id=job_id,
        state=state,
        recovery={
            "recover": True,
            "recovery_reason": "recovery_assistant_stranded_active_run",
            "claim_reason": claim_reason or "missing",
        },
        trigger="pipeline_healer",
        repo_root=repo_root,
    )
    return {
        "before_active_run_id": before_run_id,
        "after_active_run_id": str(updated.get("active_run_id") or ""),
        "before_last_status": before_status,
        "after_last_status": str(updated.get("last_status") or ""),
        "claim_reason": claim_reason or "missing",
    }


def _apply_enable_job(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    before = bool(sched.load_job(job_id, repo_root=repo_root).get("enabled"))
    saved = sched.set_job_enabled(job_id, True, repo_root=repo_root)
    return {"before": before, "after": bool(saved.get("enabled"))}


def _apply_verified_commit_repair(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    spec = sched.load_job(job_id, repo_root=repo_root)
    state = sched.load_state(job_id, repo_root=repo_root)
    run_id = str(state.get("last_run_id") or "").strip()
    if not run_id:
        return {"ok": False, "reason": "missing_last_run_id"}
    receipt = sched.load_receipt(job_id, run_id, repo_root=repo_root)
    if not receipt:
        return {"ok": False, "reason": "missing_receipt", "run_id": run_id}
    if not _is_verified_commit_failure_receipt(receipt):
        return {"ok": False, "reason": "latest_receipt_not_verified_commit_failure", "run_id": run_id}
    root = Path(repo_root).resolve()
    should_attempt_commit = (root / ".git").exists() and bool(sched._git_status_porcelain(repo_root=root))
    if not should_attempt_commit:
        return {"ok": False, "reason": "commit_not_attemptable", "run_id": run_id}
    if not isinstance(receipt.get("verification_commit_fragment"), dict):
        receipt["verification_commit_fragment"] = sched._record_post_success_verification_fragment(spec=spec, receipt=receipt, repo_root=repo_root)
    git_hook = sched._run_post_success_commit_tag_with_retries(
        repo_root=repo_root,
        retries=int((spec.get("policy") or {}).get("post_success_commit_tag_retries") or 0),
        backoff_seconds=int((spec.get("policy") or {}).get("post_success_commit_tag_backoff_seconds") or 60),
    )
    receipt["git_commit_tag"] = git_hook
    if not bool(git_hook.get("ok")):
        receipt_path = (sched.receipts_dir(repo_root) / sched._safe_name(job_id) / f"{sched._safe_name(run_id)}.json").resolve()
        report_path = (sched.reports_dir(repo_root) / sched._safe_name(job_id) / f"{sched._safe_name(run_id)}.md").resolve()
        sched._write_json_atomic(receipt_path, receipt)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(sched._job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")
        return {"ok": False, "reason": "commit_retry_failed", "run_id": run_id, "git_commit_tag": git_hook}

    receipt["status"] = "ok"
    receipt["error"] = ""
    receipt["job_retry"] = {
        "policy": {
            "job_transient_retries": int(((spec.get("policy") or {}).get("job_transient_retries")) or 0),
            "job_transient_retry_backoff_seconds": int(((spec.get("policy") or {}).get("job_transient_retry_backoff_seconds")) or 30),
        },
        "attempt_count": int(receipt.get("job_attempt") or 1),
        "retries_used": max(0, int(receipt.get("job_attempt") or 1) - 1),
        "succeeded_after_retry": bool(int(receipt.get("job_attempt") or 1) > 1),
        "history": list(((receipt.get("job_retry") or {}).get("history")) or []),
        "final_failure_class": "none",
        "final_failure_reason": "",
    }
    receipt_path = (sched.receipts_dir(repo_root) / sched._safe_name(job_id) / f"{sched._safe_name(run_id)}.json").resolve()
    report_path = (sched.reports_dir(repo_root) / sched._safe_name(job_id) / f"{sched._safe_name(run_id)}.md").resolve()
    sched._write_json_atomic(receipt_path, receipt)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(sched._job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")
    updated = {
        **state,
        "last_status": "ok",
        "last_receipt_path": str(receipt_path),
        "last_report_path": str(report_path),
    }
    slot_key = str(receipt.get("slot_key") or "").strip()
    schedule_mode = str(((spec.get("schedule") or {}).get("mode") or "")).strip().lower()
    if slot_key and schedule_mode == "once":
        updated["last_slot_key"] = slot_key
    sched._write_state(job_id, updated, repo_root=repo_root)
    sched._append_event(
        repo_root=repo_root,
        job_id=job_id,
        run_id=run_id,
        event="healed_post_success_commit",
        trigger="pipeline_healer",
        status="ok",
        details={"receipt_path": str(receipt_path), "report_path": str(report_path), "slot_key": slot_key},
    )
    return {"ok": True, "reason": "commit_retried_to_success", "run_id": run_id, "git_commit_tag": git_hook}


def _apply_runtime_profile(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    spec = sched.load_job(job_id, repo_root=repo_root)
    before = dict(spec.get("runtime") or {})
    before_loop = dict(spec.get("loop") or {})
    target = _runtime_healer_profile()
    changed = False
    spec["runtime"] = target
    if before != target:
        changed = True
    loop = dict(spec.get("loop") or {})
    if loop:
        builder_runtime = dict(loop.get("builder_runtime") or {})
        verifier_runtime = dict(loop.get("verifier_runtime") or {})
        if builder_runtime != target:
            loop["builder_runtime"] = dict(target)
            changed = True
        if verifier_runtime != target:
            loop["verifier_runtime"] = dict(target)
            changed = True
        spec["loop"] = loop
    if not changed:
        return {
            "before": before,
            "after": before,
            "before_loop": before_loop,
            "after_loop": before_loop,
            "changed": False,
        }
    saved = sched.save_job(spec, repo_root=repo_root)
    return {
        "before": before,
        "after": dict(saved.get("runtime") or {}),
        "before_loop": before_loop,
        "after_loop": dict(saved.get("loop") or {}),
        "changed": True,
    }


def _apply_semantic_retry_tuning(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    spec = sched.load_job(job_id, repo_root=repo_root)
    if sched._normalize_job_kind(spec.get("job_kind")) != "build_verify_loop":
        return {"changed": False, "reason": "not_build_verify_loop"}
    before_runtime = dict(spec.get("runtime") or {})
    before_loop = dict(spec.get("loop") or {})
    loop = dict(spec.get("loop") or {})
    current_iterations = int(loop.get("max_iterations") or 3)
    target_runtime = _runtime_healer_profile()
    changed = False
    builder_runtime = dict(loop.get("builder_runtime") or {})
    if builder_runtime != target_runtime:
        loop["builder_runtime"] = dict(target_runtime)
        changed = True
    if before_runtime != target_runtime:
        spec["runtime"] = dict(target_runtime)
        changed = True
    target_iterations = max(current_iterations, min(8, current_iterations + 1))
    if target_iterations != current_iterations:
        loop["max_iterations"] = target_iterations
        changed = True
    spec["loop"] = loop
    if not changed:
        return {
            "changed": False,
            "reason": "semantic_retry_profile_already_matches_target",
            "before": before_runtime,
            "after": before_runtime,
            "before_loop": before_loop,
            "after_loop": before_loop,
        }
    saved = sched.save_job(spec, repo_root=repo_root)
    return {
        "changed": True,
        "before": before_runtime,
        "after": dict(saved.get("runtime") or {}),
        "before_loop": before_loop,
        "after_loop": dict(saved.get("loop") or {}),
        "target_max_iterations": target_iterations,
    }


def _apply_kick(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    payload = sched.execute_job(job_id, repo_root=repo_root, trigger="pipeline_healer", force=False)
    return {
        "ok": bool(payload.get("ok")),
        "skipped": bool(payload.get("skipped")),
        "reason": str(payload.get("reason") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "receipt_path": str(payload.get("receipt_path") or ""),
    }


def _apply_ledger_truth_reconcile(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    row = _load_rows_by_job(repo_root).get(job_id) or {}
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    uid = str(binding.get("id") or "").strip()
    if str(binding.get("type") or "").strip() != "ledger_uid" or not uid:
        return {"ok": False, "reason": "job_not_bound_to_ledger_uid"}
    item_dir = _find_ledger_item_dir_by_uid(repo_root, uid=uid)
    if item_dir is None:
        return {"ok": False, "reason": "ledger_item_not_found", "ledger_uid": uid}
    meta = _ledger_meta_for_uid(repo_root, uid=uid)
    title = str(meta.get("title") or uid).strip() or uid
    pis_rel = str((item_dir / "pis.md").resolve().relative_to(repo_root)).replace("\\", "/")
    reconcile_job_id = _status_reconcile_job_id(uid)
    try:
        existing = sched.load_job(reconcile_job_id, repo_root=repo_root)
    except FileNotFoundError:
        existing = {}
    if existing:
        return {"ok": True, "reason": "reconcile_job_exists", "reconcile_job_id": reconcile_job_id}
    now_utc = _utc_iso()
    primary = {
        "agent_tech": "deepseek",
        "model": "deepseek-reasoner",
        "fallback_models": ["gpt-5.4"],
        "fallback_on_quota": True,
        "reasoning_effort": "medium",
    }
    verifier = {
        "agent_tech": "openai",
        "model": "gpt-5.4",
        "fallback_models": ["deepseek-reasoner"],
        "fallback_on_quota": True,
        "reasoning_effort": "medium",
    }
    spec = {
        "job_id": reconcile_job_id,
        "job_kind": "build_verify_loop",
        "enabled": True,
        "binding": {"type": "ledger_uid", "id": uid},
        "display_title": f"Verify {uid.split('-')[-1].lower()} ledger status reconciliation",
        "prompt": _reconcile_prompt(uid=uid, title=title, pis_rel=pis_rel, source_job_id=job_id),
        "context_refs": [pis_rel],
        "expected_output": {"kind": "report", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
        "policy": {
            "max_runtime_seconds": 1800,
            "repo_change_mode": "strict",
            "require_clean_worktree": False,
            "require_commit_journal": True,
            "require_validation_evidence": True,
            "post_success_commit_tag": True,
            "agent_turn_retries": 0,
            "job_transient_retries": 0,
        },
        "runtime": dict(primary),
        "required_context_bundle": "lab_core_v1",
        "loop": {
            "builder_role": "builder",
            "verifier_role": "verifier",
            "max_iterations": 4,
            "pass_marker": "VERDICT: PASS",
            "builder_runtime": dict(primary),
            "verifier_runtime": dict(verifier),
        },
        "prompt_meta": {
            "generated_by_pipeline_healer": True,
            "ledger_truth_reconcile_for_job_id": job_id,
            "ledger_truth_reconcile_for_uid": uid,
        },
    }
    saved = sched.save_job(spec, repo_root=repo_root)
    return {"ok": True, "reason": "reconcile_job_created", "reconcile_job_id": str(saved.get("job_id") or reconcile_job_id)}


def _bind_healer_actions(
    *,
    repo_root: Path,
    run_id: str,
    receipt_path: Path,
    report_path: Path,
    actions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    try:
        from infra.webui import server_lab_assistant_delegation_bindings as bindings
    except Exception as exc:
        return [{"ok": False, "error": str(exc), "reason": "binding_import_failed"}]
    results: List[Dict[str, Any]] = []
    for action in actions:
        job_id = str(action.get("job_id") or "").strip()
        decision = str(action.get("decision") or "").strip().lower()
        if not job_id or decision != "applied":
            continue
        try:
            spec = sched.load_job(job_id, repo_root=repo_root)
        except Exception as exc:
            results.append({"job_id": job_id, "ok": False, "error": str(exc), "reason": "job_load_failed"})
            continue
        binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
        if not binding:
            results.append({"job_id": job_id, "ok": False, "skipped": True, "reason": "missing_binding"})
            continue
        summary = f"Pipeline healer action for {job_id}"
        requested_action = f"heal scheduled job {job_id}"
        launch_result = {
            "run_id": run_id,
            "ref_id": f"{job_id}:{run_id}",
            "status": decision,
            "status_refs": [str(receipt_path)],
            "artifact_refs": [str(report_path)],
            "reference_status": "execution_ref_active",
            "reference_status_reason": "pipeline_healer_action",
        }
        input_payload = {
            "job_id": job_id,
            "finding_type": str(action.get("finding_type") or ""),
            "decision": decision,
            "details": dict(action.get("details") or {}) if isinstance(action.get("details"), dict) else action.get("details"),
        }
        result = bindings.bind_surface_launch(
            repo_root,
            surface="healer",
            binding=binding,
            summary=summary,
            requested_action=requested_action,
            launch_result=launch_result,
            input_payload=input_payload,
            request_id=f"healer:{run_id}:{job_id}",
            idempotency_key=f"healer:{run_id}:{job_id}",
            correlation_id=run_id,
            launch_cause="pipeline_healer",
            writer="pipeline_healer.bind",
        )
        results.append(
            {
                "job_id": job_id,
                "ok": bool(result.get("ok")),
                "error": str(result.get("error") or ""),
                "request_id": str(result.get("request_id") or ""),
            }
        )
    return results


def run_healer(
    *,
    repo_root: Path,
    apply: bool,
    repeat_failure_threshold: int,
    idle_minutes: int,
    allow_enable_heads: bool,
    allow_runtime_repair: bool,
    allow_kick: bool,
    max_repairs_per_job: int,
    max_repairs_global: int,
    allow_reconcile_jobs: bool = False,
) -> Dict[str, Any]:
    detection = detect_findings(
        repo_root=repo_root,
        repeat_failure_threshold=repeat_failure_threshold,
        idle_minutes=idle_minutes,
    )
    rows = detection["rows"]
    findings = list(detection["findings"])
    findings.sort(key=_finding_sort_key, reverse=True)
    heartbeat = dict(detection["heartbeat"])
    now = _utc_now()
    run_id = f"ph_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
    prior_receipts = _read_prior_healer_runs(repo_root)
    actions: List[Dict[str, Any]] = []
    repairs_applied = 0
    heads_repaired: List[str] = []
    ledger_reconciles_applied = 0

    for finding in findings:
        job_id = str(finding.get("job_id") or "")
        finding_type = str(finding.get("finding_type") or "")
        job_repairs = _count_recent_repairs(prior_receipts, now=now, job_id=job_id)
        global_repairs = _count_recent_global_repairs(prior_receipts, now=now)
        guard_block = ""
        if job_repairs >= int(max_repairs_per_job):
            guard_block = "job_hourly_repair_cap"
        elif global_repairs >= int(max_repairs_global):
            guard_block = "global_daily_repair_cap"
        if finding_type in {"stale_ledger_status_after_success", "incomplete_verified_ledger_after_success"}:
            if ledger_reconciles_applied >= DEFAULT_MAX_LEDGER_RECONCILES_PER_RUN:
                guard_block = "ledger_reconcile_run_cap"
            elif guard_block == "global_daily_repair_cap":
                guard_block = ""
        decision = "deferred"
        detail: Dict[str, Any] = {}
        if guard_block:
            decision = "deferred"
            detail["reason"] = guard_block
        elif finding_type == "once_slot_consumed_on_failure":
            if apply:
                detail = _apply_clear_last_slot_key(job_id, repo_root=repo_root)
                decision = "applied"
                heads_repaired.append(job_id)
            else:
                detail["reason"] = "dry_run"
        elif finding_type == "stranded_active_run":
            if apply:
                detail = _apply_active_run_recovery(job_id, repo_root=repo_root)
                decision = "applied"
                heads_repaired.append(job_id)
            else:
                detail["reason"] = "dry_run"
        elif finding_type == "paused_chain_head_with_blocked_dependents":
            if apply:
                detail = _apply_enable_job(job_id, repo_root=repo_root)
                decision = "applied"
                heads_repaired.append(job_id)
            else:
                detail["reason"] = "dry_run"
        elif finding_type == "disabled_chain_head_with_blocked_dependents":
            if not allow_enable_heads:
                detail["reason"] = "operator_required_enable_gate"
            elif apply:
                detail = _apply_enable_job(job_id, repo_root=repo_root)
                decision = "applied"
                heads_repaired.append(job_id)
            else:
                detail["reason"] = "dry_run"
        elif finding_type in {"repeated_runtime_signature_failure", "repeated_empty_proof_gap_runtime_failure"}:
            if not allow_runtime_repair:
                detail["reason"] = "operator_required_runtime_gate"
            elif apply:
                detail = _apply_runtime_profile(job_id, repo_root=repo_root)
                if bool(detail.get("changed")):
                    decision = "applied"
                    heads_repaired.append(job_id)
                else:
                    decision = "deferred"
                    detail["reason"] = "runtime_profile_already_matches_target"
            else:
                detail["reason"] = "dry_run"
        elif finding_type in {"repeated_identical_verifier_failure", "repeated_contract_shape_failure"}:
            if not allow_runtime_repair:
                detail["reason"] = "operator_required_runtime_gate"
            elif apply:
                detail = _apply_semantic_retry_tuning(job_id, repo_root=repo_root)
                if bool(detail.get("changed")):
                    decision = "applied"
                    heads_repaired.append(job_id)
                else:
                    decision = "deferred"
            else:
                detail["reason"] = "dry_run"
        elif finding_type == "repeated_verified_commit_failure":
            if apply:
                detail = _apply_verified_commit_repair(job_id, repo_root=repo_root)
                if bool(detail.get("ok")):
                    decision = "applied"
                    heads_repaired.append(job_id)
                else:
                    decision = "deferred"
            else:
                detail["reason"] = "dry_run"
        elif finding_type in {"stale_ledger_status_after_success", "incomplete_verified_ledger_after_success"}:
            if not allow_reconcile_jobs:
                detail["reason"] = "operator_required_reconcile_gate"
            elif apply:
                detail = _apply_ledger_truth_reconcile(job_id, repo_root=repo_root)
                if bool(detail.get("ok")):
                    decision = "applied"
                    ledger_reconciles_applied += 1
                    reconcile_job_id = str(detail.get("reconcile_job_id") or "").strip()
                    if reconcile_job_id:
                        heads_repaired.append(reconcile_job_id)
                else:
                    decision = "deferred"
            else:
                detail["reason"] = "dry_run"
        else:
            detail["reason"] = "reported_only"
        actions.append(
            {
                "job_id": job_id,
                "finding_type": finding_type,
                "repair_class": str(finding.get("repair_class") or ""),
                "decision": decision,
                "details": detail,
            }
        )
        if decision == "applied":
            repairs_applied += 1

    kick_actions: List[Dict[str, Any]] = []
    if allow_kick:
        active_jobs = heartbeat.get("active_job_count") or 0
        if int(active_jobs) == 0:
            for job_id in list(dict.fromkeys(heads_repaired)):
                row = sched.job_summary(sched.load_job(job_id, repo_root=repo_root), state=sched.load_state(job_id, repo_root=repo_root), repo_root=repo_root)
                due = row.get("due") if isinstance(row.get("due"), dict) else {}
                if bool(due.get("eligible")):
                    if apply:
                        detail = _apply_kick(job_id, repo_root=repo_root)
                        kick_actions.append({"job_id": job_id, "decision": "applied", "details": detail})
                    else:
                        kick_actions.append({"job_id": job_id, "decision": "deferred", "details": {"reason": "dry_run"}})
                    break

    report = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_utc": _utc_iso(now),
        "apply_mode": bool(apply),
        "guardrails": {
            "allow_enable_heads": bool(allow_enable_heads),
            "allow_runtime_repair": bool(allow_runtime_repair),
            "allow_kick": bool(allow_kick),
            "allow_reconcile_jobs": bool(allow_reconcile_jobs),
            "max_repairs_per_job_per_hour": int(max_repairs_per_job),
            "max_repairs_global_per_day": int(max_repairs_global),
        },
        "heartbeat": heartbeat,
        "findings": findings,
        "actions": actions,
        "kick_actions": kick_actions,
        "summary": {
            "finding_count": len(findings),
            "repairs_applied": repairs_applied,
            "repairs_deferred": sum(1 for row in actions if str(row.get("decision") or "") != "applied"),
            "deadlock_count": sum(1 for row in findings if str(row.get("finding_type") or "") == "dependency_chain_deadlock"),
        },
    }
    receipt_path = (_receipts_dir(repo_root) / f"{_safe_name(run_id)}.json").resolve()
    report_path = (_reports_dir(repo_root) / f"{_safe_name(run_id)}.md").resolve()
    report["report_path"] = str(report_path)
    report["receipt_path"] = str(receipt_path)
    _write_json(receipt_path, report)
    _write_json(_latest_report_path(repo_root), report)
    report_path.write_text(_to_markdown(report), encoding="utf-8")
    report["surface_bindings"] = _bind_healer_actions(
        repo_root=repo_root,
        run_id=run_id,
        receipt_path=receipt_path,
        report_path=report_path,
        actions=actions,
    )
    return report


def _to_markdown(row: Dict[str, Any]) -> str:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    heartbeat = row.get("heartbeat") if isinstance(row.get("heartbeat"), dict) else {}
    lines: List[str] = []
    lines.append("# Scheduler Pipeline Healer")
    lines.append("")
    lines.append(f"- generated_utc: {str(row.get('generated_utc') or '')}")
    lines.append(f"- apply_mode: {str(bool(row.get('apply_mode'))).lower()}")
    lines.append(f"- findings: {int(summary.get('finding_count') or 0)}")
    lines.append(f"- repairs_applied: {int(summary.get('repairs_applied') or 0)}")
    lines.append(f"- repairs_deferred: {int(summary.get('repairs_deferred') or 0)}")
    lines.append(f"- deadlock_count: {int(summary.get('deadlock_count') or 0)}")
    lines.append("")
    lines.append("## Pipeline Heartbeat")
    lines.append(f"- last_successful_chain_advancement_utc: {str(heartbeat.get('last_successful_chain_advancement_utc') or '')}")
    lines.append(f"- last_successful_chain_advancement_age_minutes: {str(heartbeat.get('last_successful_chain_advancement_age_minutes') or '')}")
    lines.append(f"- active_job_count: {int(heartbeat.get('active_job_count') or 0)}")
    lines.append(f"- due_eligible_count: {int(heartbeat.get('due_eligible_count') or 0)}")
    lines.append(f"- oldest_blocked_chain_head: {str(heartbeat.get('oldest_blocked_chain_head') or '')}")
    lines.append("")
    lines.append("## Findings")
    findings = row.get("findings") if isinstance(row.get("findings"), list) else []
    if not findings:
        lines.append("- none")
    else:
        for finding in findings:
            lines.append(
                f"- {finding.get('finding_type')}: job_id={finding.get('job_id')} "
                f"repair_class={finding.get('repair_class')} confidence={finding.get('confidence')} "
                f"summary={finding.get('summary')}"
            )
    lines.append("")
    lines.append("## Repairs Applied")
    applied = [row for row in list(row.get("actions") or []) if str(row.get("decision") or "") == "applied"]
    if not applied:
        lines.append("- none")
    else:
        for action in applied:
            lines.append(f"- {action.get('finding_type')}: job_id={action.get('job_id')} details={json.dumps(action.get('details') or {}, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Repairs Deferred")
    deferred = [row for row in list(row.get("actions") or []) if str(row.get("decision") or "") != "applied"]
    if not deferred:
        lines.append("- none")
    else:
        for action in deferred:
            lines.append(f"- {action.get('finding_type')}: job_id={action.get('job_id')} details={json.dumps(action.get('details') or {}, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Next Safe Actions")
    if not deferred:
        lines.append("- monitor the next scheduler cycle and confirm productive jobs advance")
    else:
        lines.append("- rerun with --apply after reviewing deferred repairs")
        lines.append("- add --allow-enable-heads to auto-enable blocked chain heads when appropriate")
        lines.append("- add --allow-runtime-repair to switch repeated runtime-failure loops onto the known-good ladder")
    return "\n".join(lines).strip() + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Detect and safely repair deadlocked scheduled-job pipelines.")
        parser.add_argument("--repo-root", default=str(repo_root))
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--repeat-failure-threshold", type=int, default=DEFAULT_REPEAT_FAILURE_THRESHOLD)
        parser.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MINUTES)
        parser.add_argument("--allow-enable-heads", action="store_true")
        parser.add_argument("--allow-runtime-repair", action="store_true")
        parser.add_argument("--allow-kick", action="store_true")
        parser.add_argument("--max-repairs-per-job-per-hour", type=int, default=DEFAULT_MAX_REPAIRS_PER_JOB)
        parser.add_argument("--max-repairs-global-per-day", type=int, default=DEFAULT_MAX_REPAIRS_GLOBAL)
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        if sched_backend.selected_backend(args.backend or None) == "v2":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "scheduler_backend": "v2",
                        "skipped": True,
                        "reason": "v2_uses_watchdog_decision_surface; legacy pipeline healer is disabled unless --backend legacy is explicit",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        report = run_healer(
            repo_root=Path(args.repo_root).resolve(),
            apply=bool(args.apply),
            repeat_failure_threshold=max(1, int(args.repeat_failure_threshold)),
            idle_minutes=max(1, int(args.idle_minutes)),
            allow_enable_heads=bool(args.allow_enable_heads),
            allow_runtime_repair=bool(args.allow_runtime_repair),
            allow_kick=bool(args.allow_kick),
            max_repairs_per_job=max(1, int(args.max_repairs_per_job_per_hour)),
            max_repairs_global=max(1, int(args.max_repairs_global_per_day)),
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
