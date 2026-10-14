"""Shared first-release fanout master ownership contract helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


CONTRACT_ARTIFACT_REL = Path("governance/fanout_master_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_fanout_master_ownership_v1.md")
SCHEMA_VERSION = "fanout_master_contract_v1"
UID = "PIS-20260409-082100Z-A1F1"
MASTER_OWNER = "fanout_master_release_one"
SCHEDULER_TERMINAL_STAGE = "terminal_reporting"
LIFECYCLE_STAGES: Tuple[str, ...] = (
    "launch_planning",
    "child_execution",
    "collection",
    "integration",
    "final_verification",
    "terminal_reporting",
)
DURABLE_TRUTH_FIELDS: Tuple[str, ...] = (
    "fanout_stage",
    "child_roster",
    "child_statuses",
    "integration_disposition",
    "final_verdict",
    "operator_summary_state",
)
FORBIDDEN_CHILD_CLAIMS: Tuple[str, ...] = (
    "integrator_owner",
    "integration_disposition",
    "final_verdict",
    "operator_summary_state",
    "child_roster",
    "fanout_stage",
)
RUNTIME_PHASE_TO_STAGE: Mapping[str, str] = {
    "queued": "launch_planning",
    "planning": "launch_planning",
    "makers": "child_execution",
    "finishers": "collection",
    "integration": "integration",
    "completed": "final_verification",
    "failed": "final_verification",
    "cancelled": "final_verification",
    "timed_out": "final_verification",
    "interrupted": "final_verification",
}


@dataclass(frozen=True)
class FanoutMasterContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    master_owner: str
    lifecycle_stages: Tuple[str, ...]
    durable_truth_fields: Tuple[str, ...]
    forbidden_child_claims: Tuple[str, ...]
    scheduler_terminal_stage: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_fanout_master_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fanout_master_contract_v1() -> FanoutMasterContractV1:
    return FanoutMasterContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        master_owner=MASTER_OWNER,
        lifecycle_stages=LIFECYCLE_STAGES,
        durable_truth_fields=DURABLE_TRUTH_FIELDS,
        forbidden_child_claims=FORBIDDEN_CHILD_CLAIMS,
        scheduler_terminal_stage=SCHEDULER_TERMINAL_STAGE,
    )


def canonical_stage_for_runtime_phase(phase: str) -> str:
    normalized = str(phase or "").strip().lower()
    return RUNTIME_PHASE_TO_STAGE.get(normalized, "launch_planning")


def project_fanout_state(
    state: Mapping[str, Any],
    *,
    as_terminal_report: bool = False,
    integrated_result: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    runtime_phase = str(state.get("phase") or "").strip().lower()
    runtime_status = str(state.get("status") or "").strip().lower()
    canonical_stage = SCHEDULER_TERMINAL_STAGE if as_terminal_report else canonical_stage_for_runtime_phase(runtime_phase)
    worker_rows = [row for row in list(state.get("workers") or []) if isinstance(row, Mapping)]
    child_roster = [str(row.get("worker_id") or "").strip() for row in worker_rows if str(row.get("worker_id") or "").strip()]
    child_statuses = {
        str(row.get("worker_id") or "").strip(): str(row.get("status") or "").strip()
        for row in worker_rows
        if str(row.get("worker_id") or "").strip()
    }
    integrated = integrated_result if isinstance(integrated_result, Mapping) else {}
    return {
        "master_owner": MASTER_OWNER,
        "fanout_stage": canonical_stage,
        "runtime_phase": runtime_phase,
        "runtime_status": runtime_status,
        "durable_truth_fields": list(DURABLE_TRUTH_FIELDS),
        "child_roster": child_roster,
        "child_statuses": child_statuses,
        "integration_disposition": str(
            integrated.get("integration_disposition")
            or ((state.get("integration") if isinstance(state.get("integration"), Mapping) else {}) or {}).get("status")
            or ""
        ).strip(),
        "final_verdict": str(integrated.get("final_verdict") or runtime_status).strip(),
        "operator_summary_state": str(integrated.get("operator_summary_state") or runtime_status).strip(),
        "restart_reconciliation": dict(integrated.get("restart_reconciliation") or {}),
        "retry_boundaries": dict(integrated.get("retry_boundaries") or {}),
    }


def child_claim_violations(worker_rows: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Any], ...]:
    violations = []
    for row in worker_rows:
        worker_id = str(row.get("worker_id") or "").strip()
        claimed = []
        for key in FORBIDDEN_CHILD_CLAIMS:
            value = row.get(key)
            if value not in (None, "", [], {}, ()):
                claimed.append(key)
        if claimed:
            violations.append(
                {
                    "worker_id": worker_id,
                    "forbidden_claims": claimed,
                }
            )
    return tuple(violations)


def validate_fanout_master_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_fanout_master_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain fanout_master_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260409-082100Z-A1F1")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the fanout ownership contract doc")
    if str(payload.get("master_owner") or "") != MASTER_OWNER:
        issues.append("master_owner must remain fanout_master_release_one")
    if str(payload.get("scheduler_terminal_stage") or "") != SCHEDULER_TERMINAL_STAGE:
        issues.append("scheduler_terminal_stage must remain terminal_reporting")

    lifecycle = payload.get("lifecycle_stages")
    if not isinstance(lifecycle, dict):
        issues.append("lifecycle_stages must be a mapping")
    elif tuple(lifecycle.keys()) != LIFECYCLE_STAGES:
        issues.append("lifecycle_stages must list the canonical first-release fanout stages in order")

    durable_truth_fields = payload.get("durable_truth_fields")
    if tuple(durable_truth_fields or ()) != DURABLE_TRUTH_FIELDS:
        issues.append("durable_truth_fields must list the canonical parent-owned truth fields in order")

    mappings = payload.get("runtime_phase_mappings")
    if not isinstance(mappings, dict):
        issues.append("runtime_phase_mappings must be a mapping")
    else:
        for phase, stage in RUNTIME_PHASE_TO_STAGE.items():
            if str(mappings.get(phase) or "") != stage:
                issues.append(f"runtime phase {phase} must map to {stage}")

    forbidden = payload.get("forbidden_child_claims")
    if not isinstance(forbidden, dict):
        issues.append("forbidden_child_claims must be a mapping")
    elif tuple(forbidden.keys()) != FORBIDDEN_CHILD_CLAIMS:
        issues.append("forbidden_child_claims must list the canonical forbidden child claims in order")

    return tuple(issues)
