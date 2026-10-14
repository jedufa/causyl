"""Declarative workflow-definition contract for the first intent_family_v2 family."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple


CONTRACT_ARTIFACT_REL = Path("governance/intent_family_v2_workflow_definition_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_intent_family_v2_workflow_definition_v1.md")
SCHEMA_VERSION = "intent_family_v2_workflow_definition_contract_v1"
UID = "PIS-20260409-195200Z-A7V1"
WORKFLOW_KIND = "intent_family_v2"

STAGE_ORDER: Tuple[str, ...] = (
    "intent",
    "parent",
    "children",
    "implement",
    "intent_validation",
    "closeout",
)

NODE_KINDS: Tuple[str, ...] = (
    "source_intent",
    "family_parent",
    "execution_child",
    "intent_validation_suite",
    "intent_family_closeout",
)

STAGE_TO_NODE_KINDS: Mapping[str, Tuple[str, ...]] = {
    "intent": ("source_intent",),
    "parent": ("family_parent",),
    "children": ("execution_child",),
    "implement": ("execution_child",),
    "intent_validation": ("intent_validation_suite",),
    "closeout": ("intent_family_closeout",),
}

STAGE_DEPENDENCIES: Mapping[str, Tuple[str, ...]] = {
    "intent": (),
    "parent": ("intent",),
    "children": ("parent",),
    "implement": ("children",),
    "intent_validation": ("implement",),
    "closeout": ("intent_validation",),
}

FINAL_OWNER_STAGE = "closeout"
FINAL_OWNER_NODE_KIND = "intent_family_closeout"
VALIDATION_CHILD_KIND = "intent_validation_suite"
REQUIRED_FINAL_CHILD_KIND = "intent_family_closeout"
FORBIDDEN_SCHEMA_CAPABILITIES: Tuple[str, ...] = (
    "retry_policy",
    "recovery_policy",
    "projection_policy",
    "runtime_logic",
    "business_logic",
    "prompt_defined_execution_rules",
)
PROMOTABLE_TARGETS_BY_NODE_KIND: Mapping[str, Tuple[str, ...]] = {
    "source_intent": (),
    "family_parent": (),
    "execution_child": (),
    "intent_validation_suite": (),
    "intent_family_closeout": ("family_parent", "source_intent"),
}


@dataclass(frozen=True)
class IntentFamilyV2WorkflowContractV1:
    version: str
    uid: str
    workflow_kind: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    stage_order: Tuple[str, ...]
    node_kinds: Tuple[str, ...]
    required_validation_child_kind: str
    required_final_child_kind: str
    final_owner_stage: str
    final_owner_node_kind: str
    forbidden_schema_capabilities: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_intent_family_v2_workflow_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def intent_family_v2_workflow_contract_v1() -> IntentFamilyV2WorkflowContractV1:
    return IntentFamilyV2WorkflowContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        workflow_kind=WORKFLOW_KIND,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        stage_order=STAGE_ORDER,
        node_kinds=NODE_KINDS,
        required_validation_child_kind=VALIDATION_CHILD_KIND,
        required_final_child_kind=REQUIRED_FINAL_CHILD_KIND,
        final_owner_stage=FINAL_OWNER_STAGE,
        final_owner_node_kind=FINAL_OWNER_NODE_KIND,
        forbidden_schema_capabilities=FORBIDDEN_SCHEMA_CAPABILITIES,
    )


def canonical_stage_name(name: str) -> str:
    value = str(name or "").strip()
    if value not in STAGE_ORDER:
        raise KeyError(f"unknown intent_family_v2 stage: {value}")
    return value


def stage_node_kinds(stage_name: str) -> Tuple[str, ...]:
    return STAGE_TO_NODE_KINDS[canonical_stage_name(stage_name)]


def stage_dependencies(stage_name: str) -> Tuple[str, ...]:
    return STAGE_DEPENDENCIES[canonical_stage_name(stage_name)]


def final_owner_for_stage(stage_name: str) -> str:
    return FINAL_OWNER_NODE_KIND if canonical_stage_name(stage_name) == FINAL_OWNER_STAGE else ""


def node_kind_may_promote_target(node_kind: str, target_kind: str) -> bool:
    return str(target_kind or "").strip() in PROMOTABLE_TARGETS_BY_NODE_KIND.get(str(node_kind or "").strip(), ())


def validate_promotion(stage_name: str, acting_node_kind: str, target_kind: str) -> None:
    stage = canonical_stage_name(stage_name)
    node_kind = str(acting_node_kind or "").strip()
    target = str(target_kind or "").strip()
    if stage != FINAL_OWNER_STAGE:
        raise ValueError(f"{stage} may not promote {target}; only {FINAL_OWNER_STAGE} may promote parent or source intent state")
    if node_kind != FINAL_OWNER_NODE_KIND:
        raise ValueError(
            f"{node_kind or '(empty)'} may not promote {target}; only {FINAL_OWNER_NODE_KIND} may own closeout promotion"
        )
    if not node_kind_may_promote_target(node_kind, target):
        raise ValueError(f"{node_kind} may not promote {target}")


def validate_intent_family_v2_workflow_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_intent_family_v2_workflow_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain intent_family_v2_workflow_definition_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260409-195200Z-A7V1")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion docs contract")
    if str(payload.get("workflow_kind") or "") != WORKFLOW_KIND:
        issues.append("workflow_kind must remain intent_family_v2")

    forbidden = tuple(payload.get("forbidden_schema_capabilities") or [])
    if forbidden != FORBIDDEN_SCHEMA_CAPABILITIES:
        issues.append("forbidden_schema_capabilities must match the canonical allowlist exactly")

    shape_policy = payload.get("workflow_shape_policy") if isinstance(payload.get("workflow_shape_policy"), dict) else {}
    if str(shape_policy.get("single_final_stage") or "") != FINAL_OWNER_STAGE:
        issues.append("workflow_shape_policy.single_final_stage must remain closeout")
    if str(shape_policy.get("single_final_node_kind") or "") != FINAL_OWNER_NODE_KIND:
        issues.append("workflow_shape_policy.single_final_node_kind must remain intent_family_closeout")
    if str(shape_policy.get("required_final_owner") or "") != FINAL_OWNER_STAGE:
        issues.append("workflow_shape_policy.required_final_owner must remain closeout")

    node_kinds = payload.get("node_kinds")
    if not isinstance(node_kinds, dict):
        issues.append("node_kinds must be a mapping")
    elif tuple(node_kinds.keys()) != NODE_KINDS:
        issues.append("node_kinds must list every canonical node kind in order")

    stages = payload.get("stages")
    if not isinstance(stages, list):
        return ("stages must be a list in canonical order",)
    stage_ids = tuple(str(item.get("stage_id") or "") for item in stages if isinstance(item, dict))
    if stage_ids != STAGE_ORDER:
        issues.append("stages must list the canonical stage order exactly")
    for index, stage_name in enumerate(STAGE_ORDER, start=1):
        entry = stages[index - 1] if len(stages) >= index and isinstance(stages[index - 1], dict) else {}
        if int(entry.get("sequence_index") or 0) != index:
            issues.append(f"{stage_name} sequence_index must remain {index}")
        allowed_node_kinds = tuple(entry.get("allowed_node_kinds") or [])
        if allowed_node_kinds != STAGE_TO_NODE_KINDS[stage_name]:
            issues.append(f"{stage_name} allowed_node_kinds drifted from the runtime helper contract")
        depends_on_stages = tuple(entry.get("depends_on_stages") or [])
        if depends_on_stages != STAGE_DEPENDENCIES[stage_name]:
            issues.append(f"{stage_name} depends_on_stages drifted from the runtime helper contract")

    family_tail = payload.get("family_tail_contract") if isinstance(payload.get("family_tail_contract"), dict) else {}
    if str(family_tail.get("required_validation_child_kind") or "") != VALIDATION_CHILD_KIND:
        issues.append("family_tail_contract.required_validation_child_kind must remain intent_validation_suite")
    if str(family_tail.get("required_final_child_kind") or "") != REQUIRED_FINAL_CHILD_KIND:
        issues.append("family_tail_contract.required_final_child_kind must remain intent_family_closeout")
    if tuple(family_tail.get("tail_stage_order") or []) != ("intent_validation", "closeout"):
        issues.append("family_tail_contract.tail_stage_order must remain intent_validation then closeout")

    promotion_law = payload.get("promotion_law") if isinstance(payload.get("promotion_law"), dict) else {}
    if str(promotion_law.get("parent_and_intent_owner_stage") or "") != FINAL_OWNER_STAGE:
        issues.append("promotion_law.parent_and_intent_owner_stage must remain closeout")
    if str(promotion_law.get("parent_and_intent_owner_node_kind") or "") != FINAL_OWNER_NODE_KIND:
        issues.append("promotion_law.parent_and_intent_owner_node_kind must remain intent_family_closeout")
    if bool(promotion_law.get("validation_may_promote_parent")):
        issues.append("promotion_law.validation_may_promote_parent must remain false")
    if bool(promotion_law.get("validation_may_promote_source_intent")):
        issues.append("promotion_law.validation_may_promote_source_intent must remain false")

    return tuple(issues)
