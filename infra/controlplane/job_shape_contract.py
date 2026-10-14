"""Canonical job-shape vocabulary helpers backed by a durable repo contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple


CONTRACT_ARTIFACT_REL = Path("governance/canonical_job_shape_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_canonical_job_shape_vocabulary_v1.md")
SCHEMA_VERSION = "canonical_job_shape_contract_v1"
UID = "PIS-20260406-053811Z-8C0B"

CANONICAL_JOB_SHAPES: Tuple[str, ...] = (
    "verification_loop_job",
    "fanout_job",
    "campaign_job",
    "experiment_run_job",
    "delegation_tree_job",
)

CURRENT_JOB_SHAPES: Tuple[str, ...] = (
    "verification_loop_job",
    "fanout_job",
    "experiment_run_job",
)

FUTURE_FACING_JOB_SHAPES: Tuple[str, ...] = (
    "campaign_job",
    "delegation_tree_job",
)

CURRENT_REPO_MAPPINGS: Mapping[str, Tuple[str, ...]] = {
    "verification_loop_job": ("scheduled_jobs.job_kind=build_verify_loop",),
    "fanout_job": ("scheduled_jobs.job_kind=swarm_launch",),
    "campaign_job": (),
    "experiment_run_job": ("scheduled_jobs.job_kind=project_runner",),
    "delegation_tree_job": (),
}


@dataclass(frozen=True)
class JobShapeContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    allowlisted_shape_names: Tuple[str, ...]
    current_shape_names: Tuple[str, ...]
    future_facing_shape_names: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_job_shape_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_job_shape_contract_v1() -> JobShapeContractV1:
    return JobShapeContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        allowlisted_shape_names=CANONICAL_JOB_SHAPES,
        current_shape_names=CURRENT_JOB_SHAPES,
        future_facing_shape_names=FUTURE_FACING_JOB_SHAPES,
    )


def canonical_job_shape_name(name: str) -> str:
    value = str(name or "").strip()
    if value not in CANONICAL_JOB_SHAPES:
        raise KeyError(f"unknown canonical job shape: {value}")
    return value


def is_future_facing_job_shape(name: str) -> bool:
    return canonical_job_shape_name(name) in FUTURE_FACING_JOB_SHAPES


def current_repo_mappings_for_shape(name: str) -> Tuple[str, ...]:
    return CURRENT_REPO_MAPPINGS[canonical_job_shape_name(name)]


def validate_job_shape_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_job_shape_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain canonical_job_shape_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260406-053811Z-8C0B")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion docs contract")

    allowlisted = tuple(payload.get("allowlisted_shape_names") or [])
    if allowlisted != CANONICAL_JOB_SHAPES:
        issues.append("allowlisted_shape_names must match the canonical v1 job-shape order exactly")
    if str(payload.get("alias_policy") or "") != "reject_noncanonical_names":
        issues.append("alias_policy must fail closed on noncanonical names")

    shapes = payload.get("shapes")
    if not isinstance(shapes, dict):
        issues.append("shapes must be a mapping keyed by canonical job-shape name")
        return tuple(issues)

    if tuple(shapes.keys()) != CANONICAL_JOB_SHAPES:
        issues.append("shape definitions must exist for every canonical job shape in order")

    for name in CANONICAL_JOB_SHAPES:
        entry = shapes.get(name)
        if not isinstance(entry, dict):
            issues.append(f"{name} entry must be an object")
            continue
        if bool(entry.get("future_facing")) != (name in FUTURE_FACING_JOB_SHAPES):
            issues.append(f"{name} future_facing flag drifted from the runtime helper contract")
        if bool(entry.get("recursive")) != (name == "delegation_tree_job"):
            issues.append(f"{name} recursive flag drifted from the runtime helper contract")
        mappings = tuple(
            f"{item.get('surface')}={item.get('value')}"
            for item in (entry.get("current_repo_mappings") or [])
            if isinstance(item, dict)
        )
        if mappings != CURRENT_REPO_MAPPINGS[name]:
            issues.append(f"{name} current_repo_mappings drifted from the runtime helper contract")

    return tuple(issues)
