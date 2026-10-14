"""Persistence helpers for compiled execution artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from infra.controlplane import compiled_prompt_renderer as _compiled_prompt_renderer


def compiled_artifacts_dir(*, runtime_root: Path, job_id: str, run_id: str) -> Path:
    out = (runtime_root / "compiled_artifacts" / job_id / run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def persist_compiled_artifacts(
    *,
    runtime_root: Path,
    job_id: str,
    run_id: str,
    compiled_execution: Mapping[str, Any],
    loop: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    artifact_dir = compiled_artifacts_dir(runtime_root=runtime_root, job_id=job_id, run_id=run_id)
    shadow_path = (artifact_dir / "compiled_execution_shadow.json").resolve()
    shadow_path.write_text(json.dumps(dict(compiled_execution), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    paths = {"compiled_execution_shadow": str(shadow_path)}
    ir_result = compiled_execution.get("ir") if isinstance(compiled_execution.get("ir"), dict) else {}
    if ir_result:
        ir_path = (artifact_dir / "normalized_execution_ir.json").resolve()
        ir_path.write_text(
            json.dumps(dict(ir_result.get("normalized_ir") or {}), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        paths["normalized_execution_ir"] = str(ir_path)
        ir_validation_path = (artifact_dir / "ir_validation.json").resolve()
        ir_validation_path.write_text(json.dumps(dict(ir_result), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        paths["ir_validation"] = str(ir_validation_path)
    manifest_result = compiled_execution.get("manifest") if isinstance(compiled_execution.get("manifest"), dict) else {}
    if manifest_result:
        manifest_path = (artifact_dir / "context_manifest.json").resolve()
        manifest_path.write_text(json.dumps(dict(manifest_result), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        paths["context_manifest"] = str(manifest_path)
    prompts = compiled_execution.get("prompts") if isinstance(compiled_execution.get("prompts"), dict) else {}
    prompt_paths: Dict[str, str] = {}
    prompt_metadata_paths: Dict[str, str] = {}
    prompt_contract_paths: Dict[str, str] = {}
    for role, artifact in prompts.items():
        if not isinstance(artifact, dict):
            continue
        artifact_for_validation = dict(artifact)
        artifact_for_validation.setdefault("role", str(role))
        artifact_for_validation.setdefault("canonical_role", str(role))
        validation = _compiled_prompt_renderer.validate_compiled_prompt_artifact(artifact_for_validation)
        if not bool(validation.get("ok")):
            errors = ", ".join([str(item) for item in list(validation.get("errors") or []) if str(item).strip()])
            raise ValueError(f"compiled prompt artifact invalid for {role}: {errors or 'unknown validation failure'}")
        path = (artifact_dir / f"compiled_prompt__{role}.txt").resolve()
        path.write_text(str(artifact.get("prompt_text") or ""), encoding="utf-8")
        prompt_paths[str(role)] = str(path)
        metadata_path = (artifact_dir / f"compiled_prompt_metadata__{role}.json").resolve()
        metadata_path.write_text(json.dumps(dict(artifact), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        prompt_metadata_paths[str(role)] = str(metadata_path)
        contract_path = (artifact_dir / f"compiled_prompt_contract__{role}.json").resolve()
        contract_path.write_text(
            json.dumps(dict(artifact.get("contract") or {}), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        prompt_contract_paths[str(role)] = str(contract_path)
    if prompt_paths:
        paths["compiled_prompts"] = prompt_paths
    if prompt_metadata_paths:
        paths["compiled_prompt_metadata"] = prompt_metadata_paths
    if prompt_contract_paths:
        paths["compiled_prompt_contracts"] = prompt_contract_paths
    if isinstance(loop, dict):
        classification_rows = []
        retry_rows = []
        for row in list(loop.get("iterations") or []):
            if not isinstance(row, dict):
                continue
            classification_rows.append(
                {
                    "iteration": row.get("iteration"),
                    "builder": ((row.get("builder") if isinstance(row.get("builder"), dict) else {}).get("meta") or {}).get("compiled_output_classification"),
                    "verifier": ((row.get("verifier") if isinstance(row.get("verifier"), dict) else {}).get("meta") or {}).get("compiled_output_classification"),
                }
            )
            retry_meta = ((row.get("verifier") if isinstance(row.get("verifier"), dict) else {}).get("meta") or {}).get("compiled_retry_disposition")
            if retry_meta:
                retry_rows.append({"iteration": row.get("iteration"), "retry_disposition": retry_meta})
        class_path = (artifact_dir / "compiled_classifications.json").resolve()
        class_path.write_text(json.dumps({"iterations": classification_rows}, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        paths["compiled_classifications"] = str(class_path)
        retry_path = (artifact_dir / "compiled_retry_decisions.json").resolve()
        retry_path.write_text(json.dumps({"iterations": retry_rows}, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        paths["compiled_retry_decisions"] = str(retry_path)
    return {
        "artifact_dir": str(artifact_dir),
        "paths": paths,
    }
