"""Compiled execution shadow integration helpers."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from infra.controlplane import compiled_execution_contract as _contract
from infra.controlplane import compiled_prompt_renderer as _renderer


def build_compiled_execution_shadow_bundle(
    ir_result: Mapping[str, Any],
    manifest_result: Mapping[str, Any],
    *,
    roles: list[str],
    execution_mode: str = "shadow_compile",
) -> Dict[str, Any]:
    contract = _contract.compiled_execution_contract_v1()
    ir_ok = bool(ir_result.get("ok"))
    manifest_ok = bool(manifest_result.get("ok"))
    metadata = (ir_result.get("normalized_ir") or {}).get("metadata") if isinstance((ir_result.get("normalized_ir") or {}).get("metadata"), dict) else {}
    prompts: Dict[str, Any] = {}
    if ir_ok and manifest_ok:
        for role in roles:
            prompts[role] = _renderer.render_compiled_prompt(
                role=role,
                normalized_ir=ir_result["normalized_ir"],
                manifest=manifest_result["manifest"],
            )
    job_kind = str((ir_result.get("normalized_ir") or {}).get("metadata", {}).get("job_kind") or "")
    return {
        "enabled": True,
        "contract_version": contract.version,
        "contract_doc": contract.canonical_doc_rel.as_posix(),
        "rollout_mode": str(execution_mode or _contract.compiled_execution_rollout_mode_for_task_class(job_kind)),
        "execution_mode": str(execution_mode or "shadow_compile"),
        "dispatch_mode": "compiled_prompt" if str(execution_mode or "").strip().lower() in {"soft_gate", "hard_gate"} else "legacy_message",
        "job_shape": str(metadata.get("job_shape") or ""),
        "job_shape_source": str(metadata.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(metadata.get("job_shape_compatibility_mode")),
        "ir": dict(ir_result),
        "manifest": dict(manifest_result),
        "prompts": prompts,
    }
