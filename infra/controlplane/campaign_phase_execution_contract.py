"""Campaign phase execution-binding and launch-contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import campaign_phase_gate_contract
from infra.controlplane import job_shape_context_contract
from infra.controlplane import job_shape_contract
from infra.controlplane import job_shape_relationship_contract


SCHEMA_VERSION = "campaign_phase_execution_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/campaign_phase_execution_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_campaign_phase_execution_v1.md")
UID = "PIS-20260409-121602Z-L1P4"

SUPPORTED_PHASE_JOB_KINDS: Tuple[str, ...] = ("single_turn", "build_verify_loop", "swarm_launch")
JOB_KIND_ALIASES = {"fanout": "swarm_launch"}
PHASE_REQUIRED_FIELDS: Tuple[str, ...] = ("phase_id", "job_shape", "state", "execution_binding")
EXECUTION_BINDING_REQUIRED_FIELDS: Tuple[str, ...] = (
    "binding_id",
    "job_kind",
    "instruction_refs",
    "durable_context_refs",
    "expected_evidence",
)
EXPECTED_EVIDENCE_REQUIRED_FIELDS: Tuple[str, ...] = ("summary", "artifact_refs", "next_decision")
NEXT_DECISION_REQUIRED_FIELDS: Tuple[str, ...] = ("decision_owner", "question")
FANOUT_REQUIRED_FIELDS: Tuple[str, ...] = ("mode", "child_job_kind")

JOB_KIND_TO_SHAPE = {
    "single_turn": "verification_loop_job",
    "build_verify_loop": "verification_loop_job",
    "swarm_launch": "fanout_job",
}
TERMINAL_PHASE_STATES = frozenset({"running", "blocked", "succeeded", "failed", "cancelled"})
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "phase_execution_binding_is_required",
    "phase_launch_requires_ready_or_partial_ready_gate_unlock",
    "binding_job_kind_must_match_declared_phase_job_shape",
    "campaign_phases_may_not_launch_campaign_or_recursive_children",
    "fanout_inside_a_phase_must_remain_supervised_under_existing_fanout_boundaries",
)


@dataclass(frozen=True)
class CampaignPhaseExecutionContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    supported_phase_job_kinds: Tuple[str, ...]
    execution_binding_required_fields: Tuple[str, ...]
    expected_evidence_required_fields: Tuple[str, ...]
    fail_closed_rules: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def campaign_phase_execution_contract_v1() -> CampaignPhaseExecutionContractV1:
    return CampaignPhaseExecutionContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        supported_phase_job_kinds=SUPPORTED_PHASE_JOB_KINDS,
        execution_binding_required_fields=EXECUTION_BINDING_REQUIRED_FIELDS,
        expected_evidence_required_fields=EXPECTED_EVIDENCE_REQUIRED_FIELDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_campaign_phase_execution_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_ref_list(value: Any, *, field_name: str) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        ref = _normalize_text(raw).strip("/")
        if not ref:
            continue
        if ref.startswith(".."):
            raise ValueError(f"{field_name} entries must stay within the repo root")
        if ref not in out:
            out.append(ref)
    return out


def _normalize_parent_binding(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "binding_type": _normalize_text(row.get("binding_type")).lower(),
        "binding_id": _normalize_text(row.get("binding_id")),
    }


def _normalize_next_decision(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "decision_owner": _normalize_text(row.get("decision_owner")),
        "question": _normalize_text(row.get("question")),
    }


def _normalize_expected_evidence(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "summary": _normalize_text(row.get("summary")),
        "artifact_refs": _normalize_ref_list(row.get("artifact_refs"), field_name="expected_evidence.artifact_refs"),
        "next_decision": _normalize_next_decision(row.get("next_decision")),
    }


def _normalize_fanout(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    child_job_kind = _normalize_job_kind(row.get("child_job_kind"))
    return {
        "mode": _normalize_text(row.get("mode")).lower(),
        "child_job_kind": child_job_kind,
        "child_job_shape": JOB_KIND_TO_SHAPE.get(child_job_kind, ""),
    }


def _normalize_job_kind(value: Any) -> str:
    normalized = _normalize_text(value).lower()
    normalized = JOB_KIND_ALIASES.get(normalized, normalized)
    return normalized


def normalize_execution_binding(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    job_kind = _normalize_job_kind(row.get("job_kind"))
    normalized = {
        "binding_id": _normalize_text(row.get("binding_id")),
        "job_kind": job_kind,
        "job_shape": JOB_KIND_TO_SHAPE.get(job_kind, ""),
        "instruction_refs": _normalize_ref_list(row.get("instruction_refs"), field_name="execution_binding.instruction_refs"),
        "durable_context_refs": _normalize_ref_list(
            row.get("durable_context_refs"), field_name="execution_binding.durable_context_refs"
        ),
        "expected_evidence": _normalize_expected_evidence(row.get("expected_evidence")),
    }
    if job_kind == "swarm_launch":
        normalized["fanout"] = _normalize_fanout(row.get("fanout"))
    return normalized


def canonical_phase_launch_json(normalized_contract: Mapping[str, Any]) -> str:
    return json.dumps(dict(normalized_contract), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def phase_launch_fingerprint(normalized_contract: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_phase_launch_json(normalized_contract).encode("utf-8")).hexdigest()


def _validate_phase_state(state: str) -> str | None:
    normalized = _normalize_text(state).lower()
    if not normalized:
        return "phase state is required"
    if normalized in TERMINAL_PHASE_STATES:
        return f"phase state must remain launchable before materialization; got {normalized}"
    if normalized != "declared":
        return f"phase state must be declared before launch; got {normalized}"
    return None


def _gate_allows_phase(
    raw_gate_decision: Mapping[str, Any],
    *,
    campaign_id: str,
    phase_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    gate_result = campaign_phase_gate_contract.validate_phase_gate_decision(raw_gate_decision)
    if not gate_result["ok"]:
        return gate_result, list(gate_result["errors"])

    normalized = gate_result["normalized_decision"]
    errors: List[Dict[str, str]] = []
    normalized_campaign_id = _normalize_text(campaign_id)
    normalized_phase_id = _normalize_text(phase_id)
    if normalized["campaign_id"] != normalized_campaign_id:
        errors.append(
            {
                "field": "campaign_id",
                "code": "gate_mismatch",
                "message": "gate decision campaign_id must match the launch request campaign_id",
            }
        )
    if normalized["phase_id"] != normalized_phase_id:
        errors.append(
            {
                "field": "phase_id",
                "code": "gate_mismatch",
                "message": "gate decision phase_id must match the launch request phase_id",
            }
        )
    verdict = str(normalized["verdict"])
    unlock = normalized["downstream_unlock"]
    if verdict not in {"ready", "partial_ready"}:
        errors.append(
            {
                "field": "verdict",
                "code": "gate_not_launchable",
                "message": "phase launch requires a ready or partial_ready gate decision",
            }
        )
    if not bool(unlock["allowed"]) or normalized_phase_id not in list(unlock["unlocked_phase_ids"]):
        errors.append(
            {
                "field": "downstream_unlock.unlocked_phase_ids",
                "code": "gate_not_launchable",
                "message": "phase launch requires an explicit unlock for this phase id",
            }
        )
    return gate_result, errors


def materialize_phase_launch_contract(
    *,
    campaign_id: str,
    parent_binding: Mapping[str, Any],
    phase: Mapping[str, Any],
    gate_decision: Mapping[str, Any],
) -> Dict[str, Any]:
    normalized_parent_binding = _normalize_parent_binding(parent_binding)
    raw_phase = dict(phase or {})
    errors: List[Dict[str, str]] = []

    normalized_phase = {
        "phase_id": _normalize_text(raw_phase.get("phase_id")),
        "job_shape": _normalize_text(raw_phase.get("job_shape")),
        "state": _normalize_text(raw_phase.get("state")).lower(),
        "execution_binding": normalize_execution_binding(raw_phase.get("execution_binding")),
    }

    if not isinstance(raw_phase.get("execution_binding"), Mapping):
        errors.append(
            {
                "field": "execution_binding",
                "code": "missing_field",
                "message": "execution_binding is required for phase launch",
            }
        )

    for field in PHASE_REQUIRED_FIELDS:
        value = normalized_phase.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value):
            errors.append({"field": field, "code": "missing_field", "message": f"{field} is required for phase launch"})

    if normalized_parent_binding["binding_type"] != "ledger_uid" or not normalized_parent_binding["binding_id"]:
        errors.append(
            {
                "field": "parent_binding",
                "code": "invalid_value",
                "message": "campaign phase launch requires a canonical ledger_uid parent binding",
            }
        )

    state_error = _validate_phase_state(normalized_phase["state"])
    if state_error:
        errors.append({"field": "state", "code": "invalid_value", "message": state_error})

    try:
        declared_job_shape = job_shape_contract.canonical_job_shape_name(normalized_phase["job_shape"])
    except KeyError:
        declared_job_shape = ""
        errors.append(
            {
                "field": "job_shape",
                "code": "invalid_value",
                "message": f"unknown canonical job shape: {normalized_phase['job_shape']}",
            }
        )

    binding = normalized_phase["execution_binding"]
    for field in EXECUTION_BINDING_REQUIRED_FIELDS:
        value = binding.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value) or (isinstance(value, list) and not value):
            errors.append(
                {
                    "field": f"execution_binding.{field}",
                    "code": "missing_field",
                    "message": f"execution_binding.{field} is required",
                }
            )

    job_kind = str(binding.get("job_kind") or "")
    if job_kind not in SUPPORTED_PHASE_JOB_KINDS:
        errors.append(
            {
                "field": "execution_binding.job_kind",
                "code": "invalid_value",
                "message": "execution_binding.job_kind must be one of: single_turn, build_verify_loop, swarm_launch",
            }
        )
    child_job_shape = str(binding.get("job_shape") or "")
    if declared_job_shape and child_job_shape and child_job_shape != declared_job_shape:
        errors.append(
            {
                "field": "execution_binding.job_kind",
                "code": "binding_shape_mismatch",
                "message": "execution_binding.job_kind must map to the declared phase job_shape",
            }
        )

    evidence = binding.get("expected_evidence") if isinstance(binding.get("expected_evidence"), Mapping) else {}
    for field in EXPECTED_EVIDENCE_REQUIRED_FIELDS:
        value = evidence.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value) or (isinstance(value, list) and not value):
            errors.append(
                {
                    "field": f"execution_binding.expected_evidence.{field}",
                    "code": "missing_field",
                    "message": f"execution_binding.expected_evidence.{field} is required",
                }
            )
    next_decision = evidence.get("next_decision") if isinstance(evidence.get("next_decision"), Mapping) else {}
    for field in NEXT_DECISION_REQUIRED_FIELDS:
        if not _normalize_text(next_decision.get(field)):
            errors.append(
                {
                    "field": f"execution_binding.expected_evidence.next_decision.{field}",
                    "code": "missing_field",
                    "message": f"execution_binding.expected_evidence.next_decision.{field} is required",
                }
            )

    gate_result, gate_errors = _gate_allows_phase(
        gate_decision,
        campaign_id=campaign_id,
        phase_id=normalized_phase["phase_id"],
    )
    errors.extend(gate_errors)

    relation: Mapping[str, str] = {}
    if child_job_shape:
        try:
            job_shape_relationship_contract.assert_legal_parent_child_relationship(
                "campaign_job",
                child_job_shape,
                allow_future=True,
            )
            relation = job_shape_relationship_contract.relationship_for_shapes("campaign_job", child_job_shape)
        except (KeyError, ValueError) as exc:
            errors.append({"field": "execution_binding.job_kind", "code": "invalid_value", "message": str(exc)})

    fanout = binding.get("fanout") if isinstance(binding.get("fanout"), Mapping) else {}
    if job_kind == "swarm_launch":
        for field in FANOUT_REQUIRED_FIELDS:
            if not _normalize_text(fanout.get(field)):
                errors.append(
                    {
                        "field": f"execution_binding.fanout.{field}",
                        "code": "missing_field",
                        "message": f"execution_binding.fanout.{field} is required for swarm_launch",
                    }
                )
        if str(fanout.get("mode") or "") != "supervised":
            errors.append(
                {
                    "field": "execution_binding.fanout.mode",
                    "code": "invalid_value",
                    "message": "phase-local fanout must declare mode=supervised",
                }
            )
        child_shape = str(fanout.get("child_job_shape") or "")
        try:
            job_shape_relationship_contract.assert_legal_parent_child_relationship("fanout_job", child_shape)
        except (KeyError, ValueError) as exc:
            errors.append(
                {
                    "field": "execution_binding.fanout.child_job_kind",
                    "code": "invalid_value",
                    "message": str(exc),
                }
            )

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "normalized_phase": normalized_phase,
            "gate_result": gate_result,
        }

    durable_delta_refs = binding["instruction_refs"] + [
        ref for ref in binding["durable_context_refs"] if ref not in binding["instruction_refs"]
    ]
    context = job_shape_context_contract.resolve_context_obligations_for_shape(
        child_job_shape,
        durable_delta_refs=durable_delta_refs,
    )
    launch_contract = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": _normalize_text(campaign_id),
        "phase_id": normalized_phase["phase_id"],
        "phase_binding_id": binding["binding_id"],
        "parent_binding": normalized_parent_binding,
        "gate_decision_id": gate_result["normalized_decision"]["decision_id"],
        "gate_verdict": gate_result["normalized_decision"]["verdict"],
        "launch_job_kind": job_kind,
        "launch_job_shape": child_job_shape,
        "required_context_bundle": context["required_context_bundle"],
        "effective_context_refs": context["effective_context_refs"],
        "durable_delta_refs": context["durable_delta_refs"],
        "integrator_owner": str(relation.get("integrator_owner") or ""),
        "reintegration_boundary": str(relation.get("reintegration_boundary") or ""),
        "expected_evidence": evidence,
        "launch_payload": {
            "job_kind": job_kind,
            "job_shape": child_job_shape,
            "binding": {
                "type": "campaign_phase_binding",
                "id": f"{_normalize_text(campaign_id)}:{normalized_phase['phase_id']}:{binding['binding_id']}",
            },
            "required_context_bundle": context["required_context_bundle"],
            "context_refs": context["effective_context_refs"],
            "expected_evidence": evidence,
        },
    }
    if job_kind == "swarm_launch":
        launch_contract["launch_payload"]["fanout"] = fanout

    fingerprint = phase_launch_fingerprint(launch_contract)
    launch_contract["launch_contract_id"] = f"phase_launch::{fingerprint[:16]}"
    launch_contract["launch_fingerprint"] = fingerprint
    return {
        "ok": True,
        "errors": [],
        "normalized_phase": normalized_phase,
        "gate_result": gate_result,
        "launch_contract": launch_contract,
    }


def validate_campaign_phase_execution_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_campaign_phase_execution_contract(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain campaign_phase_execution_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260409-121602Z-L1P4")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion campaign phase execution contract doc")
    if tuple(payload.get("supported_phase_job_kinds") or ()) != SUPPORTED_PHASE_JOB_KINDS:
        issues.append("supported_phase_job_kinds must match the runtime helper contract")
    if tuple(payload.get("execution_binding_required_fields") or ()) != EXECUTION_BINDING_REQUIRED_FIELDS:
        issues.append("execution_binding_required_fields drifted from the runtime helper contract")
    if tuple(payload.get("expected_evidence_required_fields") or ()) != EXPECTED_EVIDENCE_REQUIRED_FIELDS:
        issues.append("expected_evidence_required_fields drifted from the runtime helper contract")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules drifted from the runtime helper contract")

    job_kind_to_shape = payload.get("job_kind_to_shape")
    if not isinstance(job_kind_to_shape, dict):
        issues.append("job_kind_to_shape must be a mapping")
    elif tuple(job_kind_to_shape.keys()) != SUPPORTED_PHASE_JOB_KINDS:
        issues.append("job_kind_to_shape must list the supported phase job kinds in order")
    else:
        expected_mapping = tuple(JOB_KIND_TO_SHAPE[kind] for kind in SUPPORTED_PHASE_JOB_KINDS)
        actual_mapping = tuple(str(job_kind_to_shape.get(kind) or "") for kind in SUPPORTED_PHASE_JOB_KINDS)
        if actual_mapping != expected_mapping:
            issues.append("job_kind_to_shape drifted from the runtime helper contract")

    return tuple(issues)
