"""Canonical job-shape parent-child legality helpers backed by a durable contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from infra.controlplane import job_shape_contract


CONTRACT_ARTIFACT_REL = Path("governance/job_shape_relationship_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_job_shape_parent_child_relationship_matrix_v1.md")
CANONICAL_SHAPE_CONTRACT_REL = Path("governance/canonical_job_shape_contract_v1.json")
SCHEMA_VERSION = "job_shape_relationship_contract_v1"
UID = "PIS-20260406-053812Z-C277"

LEGALITY_STATUSES: Tuple[str, ...] = ("allowed", "future_only", "forbidden")
CURRENT_JOB_KIND_TO_SHAPE: Mapping[str, str] = {
    "build_verify_loop": "verification_loop_job",
    "swarm_launch": "fanout_job",
    "project_runner": "experiment_run_job",
}


@dataclass(frozen=True)
class JobShapeRelationshipContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    canonical_shape_contract_rel: Path
    shape_names: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_job_shape_relationship_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def _relationship_matrix_cached(repo_root_key: str) -> Dict[str, Dict[str, Dict[str, str]]]:
    repo_root = Path(repo_root_key)
    payload = load_job_shape_relationship_contract(repo_root=repo_root)
    shapes = payload.get("shapes") if isinstance(payload.get("shapes"), dict) else {}
    matrix: Dict[str, Dict[str, Dict[str, str]]] = {}
    for parent_shape in job_shape_contract.CANONICAL_JOB_SHAPES:
        parent_entry = shapes.get(parent_shape) if isinstance(shapes, dict) else None
        children = parent_entry.get("children") if isinstance(parent_entry, dict) else {}
        row: Dict[str, Dict[str, str]] = {}
        for child_shape in job_shape_contract.CANONICAL_JOB_SHAPES:
            child_entry = children.get(child_shape) if isinstance(children, dict) else {}
            row[child_shape] = {
                "legality": str(child_entry.get("legality") or ""),
                "integrator_owner": str(child_entry.get("integrator_owner") or ""),
                "reintegration_boundary": str(child_entry.get("reintegration_boundary") or ""),
            }
        matrix[parent_shape] = row
    return matrix


def _relationship_matrix(*, repo_root: Path | None = None) -> Dict[str, Dict[str, Dict[str, str]]]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    return _relationship_matrix_cached(str(root.resolve()))


def job_shape_relationship_contract_v1() -> JobShapeRelationshipContractV1:
    return JobShapeRelationshipContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        canonical_shape_contract_rel=CANONICAL_SHAPE_CONTRACT_REL,
        shape_names=job_shape_contract.CANONICAL_JOB_SHAPES,
    )


def relationship_for_shapes(parent_shape: str, child_shape: str, *, repo_root: Path | None = None) -> Mapping[str, str]:
    canonical_parent = job_shape_contract.canonical_job_shape_name(parent_shape)
    canonical_child = job_shape_contract.canonical_job_shape_name(child_shape)
    return dict(_relationship_matrix(repo_root=repo_root)[canonical_parent][canonical_child])


def legal_child_shapes_for_parent(
    parent_shape: str,
    *,
    include_future: bool = False,
    repo_root: Path | None = None,
) -> Tuple[str, ...]:
    canonical_parent = job_shape_contract.canonical_job_shape_name(parent_shape)
    allowed_statuses = {"allowed", "future_only"} if include_future else {"allowed"}
    matrix = _relationship_matrix(repo_root=repo_root)
    return tuple(
        child_shape
        for child_shape in job_shape_contract.CANONICAL_JOB_SHAPES
        if matrix[canonical_parent][child_shape]["legality"] in allowed_statuses
    )


def parent_shape_for_job_kind(job_kind: str) -> str:
    normalized = str(job_kind or "").strip()
    if normalized not in CURRENT_JOB_KIND_TO_SHAPE:
        raise KeyError(f"unknown current job kind for relationship lookup: {normalized}")
    return CURRENT_JOB_KIND_TO_SHAPE[normalized]


def legal_child_shapes_for_job_kind(
    job_kind: str,
    *,
    include_future: bool = False,
    repo_root: Path | None = None,
) -> Tuple[str, ...]:
    return legal_child_shapes_for_parent(
        parent_shape_for_job_kind(job_kind),
        include_future=include_future,
        repo_root=repo_root,
    )


def assert_legal_parent_child_relationship(
    parent_shape: str,
    child_shape: str,
    *,
    allow_future: bool = False,
    repo_root: Path | None = None,
) -> None:
    relation = relationship_for_shapes(parent_shape, child_shape, repo_root=repo_root)
    legality = relation["legality"]
    if legality == "allowed":
        return
    if legality == "future_only" and not allow_future:
        raise ValueError(
            "future-only parent-child relationship is not launchable in v1: "
            f"{job_shape_contract.canonical_job_shape_name(parent_shape)} -> {job_shape_contract.canonical_job_shape_name(child_shape)}"
        )
    if legality == "future_only":
        return
    raise KeyError(
        "illegal parent-child job-shape relationship in v1: "
        f"{job_shape_contract.canonical_job_shape_name(parent_shape)} -> {job_shape_contract.canonical_job_shape_name(child_shape)}"
    )


def validate_job_shape_relationship_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_job_shape_relationship_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain job_shape_relationship_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260406-053812Z-C277")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion relationship docs contract")
    if str(payload.get("canonical_shape_contract_rel") or "") != CANONICAL_SHAPE_CONTRACT_REL.as_posix():
        issues.append("canonical_shape_contract_rel must point at the canonical job-shape vocabulary contract")

    definitions = payload.get("legality_status_definitions")
    if not isinstance(definitions, dict):
        issues.append("legality_status_definitions must be a mapping")
    elif tuple(definitions.keys()) != LEGALITY_STATUSES:
        issues.append("legality_status_definitions must list the canonical legality states in order")

    shapes = payload.get("shapes")
    if not isinstance(shapes, dict):
        return ("shapes must be a mapping keyed by canonical job-shape name",)
    if tuple(shapes.keys()) != job_shape_contract.CANONICAL_JOB_SHAPES:
        issues.append("shape definitions must exist for every canonical job shape in order")

    for parent_shape in job_shape_contract.CANONICAL_JOB_SHAPES:
        entry = shapes.get(parent_shape)
        if not isinstance(entry, dict):
            issues.append(f"{parent_shape} entry must be an object")
            continue
        children = entry.get("children")
        if not isinstance(children, dict):
            issues.append(f"{parent_shape} children must be a mapping")
            continue
        if tuple(children.keys()) != job_shape_contract.CANONICAL_JOB_SHAPES:
            issues.append(f"{parent_shape} children must list every canonical child shape in order")
        for child_shape in job_shape_contract.CANONICAL_JOB_SHAPES:
            child_entry = children.get(child_shape)
            if not isinstance(child_entry, dict):
                issues.append(f"{parent_shape} -> {child_shape} entry must be an object")
                continue
            legality = str(child_entry.get("legality") or "")
            if legality not in LEGALITY_STATUSES:
                issues.append(
                    f"{parent_shape} -> {child_shape} legality must be one of: {', '.join(LEGALITY_STATUSES)}"
                )
            if not str(child_entry.get("integrator_owner") or ""):
                issues.append(f"{parent_shape} -> {child_shape} integrator_owner must be non-empty")
            if not str(child_entry.get("reintegration_boundary") or ""):
                issues.append(f"{parent_shape} -> {child_shape} reintegration_boundary must be non-empty")

    return tuple(issues)
