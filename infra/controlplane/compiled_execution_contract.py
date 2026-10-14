"""Compiled execution v1 operational contract helpers.

This module is intentionally small. It gives later X7K2 children one importable
place for rollout semantics and truth-boundary authority instead of scattering
those decisions across prompt code and receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Tuple


CONTRACT_DOC_REL = Path("docs/contracts/contract_compiled_execution_layer_operational_boundary.md")

ROLLOUT_LEGACY = "legacy"
ROLLOUT_SHADOW_COMPILE = "shadow_compile"
ROLLOUT_SOFT_GATE = "soft_gate"
ROLLOUT_HARD_GATE = "hard_gate"

ROLLOUT_ORDER: Tuple[str, ...] = (
    ROLLOUT_LEGACY,
    ROLLOUT_SHADOW_COMPILE,
    ROLLOUT_SOFT_GATE,
    ROLLOUT_HARD_GATE,
)

TRUTH_BOUNDARY_AUTHORITIES: Mapping[str, str] = MappingProxyType(
    {
        "source_task_intent": "structured_execution_ir",
        "resolved_context_manifest": "resolved_context_manifest_artifact",
        "compiled_prompt": "compiled_prompt_artifact",
        "raw_model_output": "raw_model_output_artifact",
        "proof_artifacts": "validation_and_proof_artifacts",
        "execution_outcome": "substrate_native_receipt_or_result",
        "run_status": "existing_execution_records_and_receipts",
    }
)

DEFAULT_TASK_CLASS_ROLLOUTS: Mapping[str, str] = MappingProxyType(
    {
        "single_turn": ROLLOUT_SHADOW_COMPILE,
        "build_verify_loop": ROLLOUT_SHADOW_COMPILE,
        "scheduled_single_turn": ROLLOUT_SHADOW_COMPILE,
        "scheduled_build_verify_loop": ROLLOUT_SHADOW_COMPILE,
    }
)

HARD_GATE_ALLOWED_TASK_CLASSES: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledExecutionContractV1:
    version: str
    canonical_doc_rel: Path
    rollout_order: Tuple[str, ...]
    truth_boundary_authorities: Mapping[str, str]
    default_task_class_rollouts: Mapping[str, str]
    hard_gate_allowed_task_classes: Tuple[str, ...]


def compiled_execution_contract_v1() -> CompiledExecutionContractV1:
    return CompiledExecutionContractV1(
        version="compiled_execution_contract_v1",
        canonical_doc_rel=CONTRACT_DOC_REL,
        rollout_order=ROLLOUT_ORDER,
        truth_boundary_authorities=TRUTH_BOUNDARY_AUTHORITIES,
        default_task_class_rollouts=DEFAULT_TASK_CLASS_ROLLOUTS,
        hard_gate_allowed_task_classes=HARD_GATE_ALLOWED_TASK_CLASSES,
    )


def compiled_execution_rollout_mode_for_task_class(
    task_class: str,
    *,
    allow_hard_gate: bool = False,
) -> str:
    task_key = str(task_class or "").strip()
    mode = DEFAULT_TASK_CLASS_ROLLOUTS.get(task_key, ROLLOUT_LEGACY)
    if mode == ROLLOUT_HARD_GATE and not allow_hard_gate:
        return ROLLOUT_SOFT_GATE
    if mode == ROLLOUT_HARD_GATE and task_key not in HARD_GATE_ALLOWED_TASK_CLASSES:
        return ROLLOUT_SOFT_GATE
    return mode


def truth_boundary_authority(boundary: str) -> str:
    key = str(boundary or "").strip()
    authority = TRUTH_BOUNDARY_AUTHORITIES.get(key)
    if authority is None:
        raise KeyError(f"unknown truth boundary: {key}")
    return authority


def validate_compiled_execution_contract_v1() -> Tuple[str, ...]:
    issues = []
    contract = compiled_execution_contract_v1()
    if contract.rollout_order != (
        ROLLOUT_LEGACY,
        ROLLOUT_SHADOW_COMPILE,
        ROLLOUT_SOFT_GATE,
        ROLLOUT_HARD_GATE,
    ):
        issues.append("rollout order must remain legacy -> shadow_compile -> soft_gate -> hard_gate")
    required_boundaries = {
        "source_task_intent",
        "resolved_context_manifest",
        "compiled_prompt",
        "raw_model_output",
        "proof_artifacts",
        "execution_outcome",
        "run_status",
    }
    if set(contract.truth_boundary_authorities) != required_boundaries:
        issues.append("truth boundaries must match the v1 operational contract exactly")
    for task_class, mode in contract.default_task_class_rollouts.items():
        if mode not in contract.rollout_order:
            issues.append(f"task class {task_class} maps to unknown rollout mode {mode}")
        if mode == ROLLOUT_HARD_GATE and task_class not in contract.hard_gate_allowed_task_classes:
            issues.append(f"task class {task_class} cannot default to hard_gate before proof exists")
    return tuple(issues)
