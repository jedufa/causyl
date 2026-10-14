"""Canonical job-shape context bundle and delta-loading helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import context_bundles
from infra.controlplane import job_shape_contract


CONTRACT_ARTIFACT_REL = Path("governance/job_shape_context_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_job_shape_context_bundle_policy_v1.md")
CANONICAL_SHAPE_CONTRACT_REL = Path("governance/canonical_job_shape_contract_v1.json")
BUNDLE_REGISTRY_DOC_REL = Path("docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context_bundles.md")
SCHEMA_VERSION = "job_shape_context_contract_v1"
UID = "PIS-20260406-053812Z-733A"
TRANSCRIPT_SUMMARY_POLICY = "ignored_for_context_resolution"
REENTRY_PRECEDENCE = "durable_contract_then_durable_delta_then_transcript_summary"

JOB_KIND_TO_SHAPE: Mapping[str, str] = {
    "build_verify_loop": "verification_loop_job",
    "swarm_launch": "fanout_job",
    "project_runner": "experiment_run_job",
}

DELTA_REF_KIND_DEFINITIONS: Mapping[str, str] = {
    "binding_context_ref": "A durable context ref attached from the bound work item or scheduler binding.",
    "task_context_ref": "A task-specific durable ref attached for the current objective.",
    "verification_target_ref": "A durable ref naming the exact code, test, or proof target under repair.",
    "child_contract_ref": "A durable ref for child launch contracts or fanout child definitions.",
    "experiment_contract_ref": "A durable ref to the experiment contract or compatible run contract.",
    "run_input_ref": "A durable ref naming run-specific registries, profiles, or operator-approved inputs.",
}

SHAPE_CONTEXT_SPECS: Mapping[str, Mapping[str, Any]] = {
    "verification_loop_job": {
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs_allowed": True,
        "allowed_durable_delta_ref_kinds": (
            "binding_context_ref",
            "task_context_ref",
            "verification_target_ref",
        ),
        "forbidden_context_refs": ("full_repo_dump", "legacy_transcript_blob"),
        "forbidden_context_ref_prefixes": ("governance/agents/sessions/",),
        "transcript_summary_policy": TRANSCRIPT_SUMMARY_POLICY,
        "reentry_precedence": REENTRY_PRECEDENCE,
    },
    "fanout_job": {
        "required_context_bundle": "lab_arch_gov_v1",
        "additional_context_refs_allowed": True,
        "allowed_durable_delta_ref_kinds": (
            "binding_context_ref",
            "task_context_ref",
            "child_contract_ref",
        ),
        "forbidden_context_refs": ("full_repo_dump", "legacy_transcript_blob"),
        "forbidden_context_ref_prefixes": ("governance/agents/sessions/",),
        "transcript_summary_policy": TRANSCRIPT_SUMMARY_POLICY,
        "reentry_precedence": REENTRY_PRECEDENCE,
    },
    "campaign_job": {
        "required_context_bundle": "lab_arch_gov_v1",
        "additional_context_refs_allowed": False,
        "allowed_durable_delta_ref_kinds": (),
        "forbidden_context_refs": ("full_repo_dump", "legacy_transcript_blob"),
        "forbidden_context_ref_prefixes": ("governance/agents/sessions/",),
        "transcript_summary_policy": TRANSCRIPT_SUMMARY_POLICY,
        "reentry_precedence": REENTRY_PRECEDENCE,
    },
    "experiment_run_job": {
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs_allowed": True,
        "allowed_durable_delta_ref_kinds": (
            "binding_context_ref",
            "experiment_contract_ref",
            "run_input_ref",
        ),
        "forbidden_context_refs": ("full_repo_dump", "legacy_transcript_blob"),
        "forbidden_context_ref_prefixes": ("governance/agents/sessions/",),
        "transcript_summary_policy": TRANSCRIPT_SUMMARY_POLICY,
        "reentry_precedence": REENTRY_PRECEDENCE,
    },
    "delegation_tree_job": {
        "required_context_bundle": "lab_arch_gov_v1",
        "additional_context_refs_allowed": False,
        "allowed_durable_delta_ref_kinds": (),
        "forbidden_context_refs": ("full_repo_dump", "legacy_transcript_blob"),
        "forbidden_context_ref_prefixes": ("governance/agents/sessions/",),
        "transcript_summary_policy": TRANSCRIPT_SUMMARY_POLICY,
        "reentry_precedence": REENTRY_PRECEDENCE,
    },
}


@dataclass(frozen=True)
class JobShapeContextContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    canonical_shape_contract_rel: Path
    bundle_registry_doc_rel: Path
    shape_names: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_ref_list(values: Sequence[str] | None) -> List[str]:
    out: List[str] = []
    for raw in list(values or ()):
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _is_forbidden_ref(ref: str, *, forbidden_refs: Sequence[str], forbidden_prefixes: Sequence[str]) -> bool:
    if ref in forbidden_refs:
        return True
    return any(ref.startswith(prefix) for prefix in forbidden_prefixes)


def load_job_shape_context_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def job_shape_context_contract_v1() -> JobShapeContextContractV1:
    return JobShapeContextContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        canonical_shape_contract_rel=CANONICAL_SHAPE_CONTRACT_REL,
        bundle_registry_doc_rel=BUNDLE_REGISTRY_DOC_REL,
        shape_names=job_shape_contract.CANONICAL_JOB_SHAPES,
    )


def context_policy_for_shape(name: str) -> Mapping[str, Any]:
    canonical_name = job_shape_contract.canonical_job_shape_name(name)
    return SHAPE_CONTEXT_SPECS[canonical_name]


def shape_for_job_kind(job_kind: str) -> str:
    normalized = str(job_kind or "").strip()
    if normalized not in JOB_KIND_TO_SHAPE:
        raise KeyError(f"unknown current job kind for context policy lookup: {normalized}")
    return JOB_KIND_TO_SHAPE[normalized]


def context_policy_for_job_kind(job_kind: str) -> Mapping[str, Any]:
    return context_policy_for_shape(shape_for_job_kind(job_kind))


def resolve_context_obligations_for_shape(
    name: str,
    *,
    durable_delta_refs: Sequence[str] | None = None,
    transcript_summary_refs: Sequence[str] | None = None,
) -> Dict[str, Any]:
    policy = dict(context_policy_for_shape(name))
    bundle_id = str(policy["required_context_bundle"])
    if not bundle_id:
        raise ValueError(f"missing required context bundle for {name}")

    durable_refs = _normalize_ref_list(durable_delta_refs)
    transcript_refs = _normalize_ref_list(transcript_summary_refs)
    if durable_refs and not bool(policy["additional_context_refs_allowed"]):
        raise ValueError(f"{name} forbids durable delta context refs in v1")

    forbidden_refs = tuple(policy["forbidden_context_refs"])
    forbidden_prefixes = tuple(policy["forbidden_context_ref_prefixes"])
    rejected_durable_refs = [
        ref for ref in durable_refs if _is_forbidden_ref(ref, forbidden_refs=forbidden_refs, forbidden_prefixes=forbidden_prefixes)
    ]
    if rejected_durable_refs:
        raise ValueError(f"forbidden context refs requested for {name}: {', '.join(rejected_durable_refs)}")

    ignored_transcript_refs = list(transcript_refs)
    omitted_transcript_refs = [
        {
            "path": ref,
            "reason": "forbidden_transcript_ref"
            if _is_forbidden_ref(ref, forbidden_refs=forbidden_refs, forbidden_prefixes=forbidden_prefixes)
            else "transcript_summary_ignored",
        }
        for ref in transcript_refs
    ]

    effective_context_refs: List[str] = []
    for ref in context_bundles.bundle_refs(bundle_id) + durable_refs:
        if ref not in effective_context_refs:
            effective_context_refs.append(ref)

    return {
        "job_shape": job_shape_contract.canonical_job_shape_name(name),
        "required_context_bundle": bundle_id,
        "allowed_durable_delta_ref_kinds": list(policy["allowed_durable_delta_ref_kinds"]),
        "additional_context_refs_allowed": bool(policy["additional_context_refs_allowed"]),
        "forbidden_context_refs": list(forbidden_refs),
        "forbidden_context_ref_prefixes": list(forbidden_prefixes),
        "transcript_summary_policy": str(policy["transcript_summary_policy"]),
        "reentry_precedence": str(policy["reentry_precedence"]),
        "effective_context_refs": effective_context_refs,
        "durable_delta_refs": durable_refs,
        "ignored_transcript_summary_refs": ignored_transcript_refs,
        "omitted_transcript_summary_refs": omitted_transcript_refs,
    }


def resolve_context_obligations_for_job_kind(
    job_kind: str,
    *,
    durable_delta_refs: Sequence[str] | None = None,
    transcript_summary_refs: Sequence[str] | None = None,
) -> Dict[str, Any]:
    return resolve_context_obligations_for_shape(
        shape_for_job_kind(job_kind),
        durable_delta_refs=durable_delta_refs,
        transcript_summary_refs=transcript_summary_refs,
    )


def validate_job_shape_context_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_job_shape_context_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain job_shape_context_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260406-053812Z-733A")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion job-shape context docs contract")
    if str(payload.get("canonical_shape_contract_rel") or "") != CANONICAL_SHAPE_CONTRACT_REL.as_posix():
        issues.append("canonical_shape_contract_rel must point at the canonical job-shape vocabulary contract")
    if str(payload.get("bundle_registry_doc_rel") or "") != BUNDLE_REGISTRY_DOC_REL.as_posix():
        issues.append("bundle_registry_doc_rel must point at the bundle registry document")

    definitions = payload.get("delta_ref_kind_definitions")
    if not isinstance(definitions, dict):
        issues.append("delta_ref_kind_definitions must be a mapping")
    elif tuple(definitions.keys()) != tuple(DELTA_REF_KIND_DEFINITIONS.keys()):
        issues.append("delta_ref_kind_definitions must list the canonical delta ref kinds in order")

    shapes = payload.get("shapes")
    if not isinstance(shapes, dict):
        return ("shapes must be a mapping keyed by canonical job-shape name",)
    if tuple(shapes.keys()) != job_shape_contract.CANONICAL_JOB_SHAPES:
        issues.append("shape definitions must exist for every canonical job shape in order")

    bundle_ids = set(context_bundles.available_bundle_ids())
    for shape_name in job_shape_contract.CANONICAL_JOB_SHAPES:
        entry = shapes.get(shape_name)
        if not isinstance(entry, dict):
            issues.append(f"{shape_name} entry must be an object")
            continue
        expected = SHAPE_CONTEXT_SPECS[shape_name]
        bundle_id = str(entry.get("required_context_bundle") or "")
        if not bundle_id:
            issues.append(f"{shape_name} required_context_bundle must be non-empty")
        elif bundle_id not in bundle_ids:
            issues.append(f"{shape_name} required_context_bundle must be a known bundle id")
        if bool(entry.get("additional_context_refs_allowed")) != bool(expected["additional_context_refs_allowed"]):
            issues.append(f"{shape_name} additional_context_refs_allowed drifted from the runtime helper contract")
        if tuple(entry.get("allowed_durable_delta_ref_kinds") or []) != tuple(expected["allowed_durable_delta_ref_kinds"]):
            issues.append(f"{shape_name} allowed_durable_delta_ref_kinds drifted from the runtime helper contract")
        if tuple(entry.get("forbidden_context_refs") or []) != tuple(expected["forbidden_context_refs"]):
            issues.append(f"{shape_name} forbidden_context_refs drifted from the runtime helper contract")
        if tuple(entry.get("forbidden_context_ref_prefixes") or []) != tuple(expected["forbidden_context_ref_prefixes"]):
            issues.append(f"{shape_name} forbidden_context_ref_prefixes drifted from the runtime helper contract")
        if str(entry.get("transcript_summary_policy") or "") != TRANSCRIPT_SUMMARY_POLICY:
            issues.append(f"{shape_name} transcript_summary_policy must fail closed on transcript summary refs")
        if str(entry.get("reentry_precedence") or "") != REENTRY_PRECEDENCE:
            issues.append(f"{shape_name} reentry_precedence drifted from the durable context loading contract")
        if not bool(entry.get("additional_context_refs_allowed")) and tuple(entry.get("allowed_durable_delta_ref_kinds") or ()):
            issues.append(f"{shape_name} cannot declare allowed durable delta ref kinds when additional refs are disallowed")

    return tuple(issues)
