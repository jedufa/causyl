from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .core import PipelineError, load_json, save_json_atomic, sha256_file, sha256_text, utc_now_iso


class ArtifactRegistry:
    """Append-only immutable artifact registry for job folders."""

    def __init__(
        self,
        job_dir: str | os.PathLike[str],
        *,
        registry_name: str = "artifact_registry.json",
        schema_version: str = "3",
        contract_version: str = "1.2",
    ) -> None:
        self.job_dir = Path(job_dir).resolve()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.job_dir / registry_name
        self.schema_version = str(schema_version)
        self.contract_version = str(contract_version)
        if not self.registry_path.exists():
            save_json_atomic(self.registry_path, self._empty_registry())

    def _empty_registry(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "artifacts": [],
            "indexes": {
                "by_artifact_id": {},
                "by_run_id": {},
                "by_stage": {},
                "by_branch_id": {},
                "by_branch_stage": {},
                "by_role": {},
            },
        }

    def load(self) -> Dict[str, Any]:
        data = load_json(self.registry_path)
        if not isinstance(data.get("artifacts"), list) or not isinstance(data.get("indexes"), dict):
            raise PipelineError("artifact_registry_invalid", "artifact registry structure is invalid", exit_code=3)
        if str(data.get("schema_version") or "") != self.schema_version:
            raise PipelineError("artifact_registry_incompatible", "artifact registry schema_version mismatch", details={"expected": self.schema_version, "actual": data.get("schema_version")}, exit_code=3)
        if str(data.get("contract_version") or "") != self.contract_version:
            raise PipelineError("artifact_registry_incompatible", "artifact registry contract_version mismatch", details={"expected": self.contract_version, "actual": data.get("contract_version")}, exit_code=3)
        return data

    def _append_index(self, indexes: Dict[str, Any], bucket: str, key: str, position: int) -> None:
        if bucket == "by_artifact_id":
            indexes.setdefault(bucket, {})[key] = position
            return
        bucket_map = indexes.setdefault(bucket, {})
        values = list(bucket_map.get(key) or [])
        if position not in values:
            values.append(position)
        bucket_map[key] = values

    def _make_artifact_id(self, *, run_id: str, stage: str, role: str, rel_path: str, sha256: str) -> str:
        digest = sha256_text(f"{run_id}|{stage}|{role}|{rel_path}|{sha256}")[:20]
        return f"ART-{digest}"

    def register(
        self,
        *,
        stage: str,
        run_id: str,
        branch_id: str,
        file_path: str | os.PathLike[str],
        metadata: Mapping[str, Any],
        role: str = "stage_output",
        artifact_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        registry = self.load()
        target = Path(file_path).resolve()
        if not target.exists() or not target.is_file():
            raise PipelineError("artifact_missing", "Artifact path does not exist", details={"path": str(target)}, exit_code=3)
        sha = sha256_file(target)
        rel_path = target.relative_to(self.job_dir).as_posix() if str(target).startswith(str(self.job_dir)) else str(target)
        artifact_id = str(artifact_id or self._make_artifact_id(run_id=run_id, stage=stage, role=role, rel_path=rel_path, sha256=sha))

        for existing in registry["artifacts"]:
            if existing.get("artifact_id") == artifact_id:
                if existing.get("sha256") != sha or existing.get("file_path") != rel_path:
                    raise PipelineError("artifact_immutable_violation", "Artifact id already registered with different content", details={"artifact_id": artifact_id}, exit_code=6)
                return dict(existing)

        entry = {
            "artifact_id": artifact_id,
            "stage": str(stage),
            "role": str(role),
            "run_id": str(run_id),
            "branch_id": str(branch_id),
            "file_path": rel_path,
            "schema_version": str(metadata.get("schema_version") or ""),
            "contract_version": str(metadata.get("contract_version") or ""),
            "created_at": utc_now_iso(),
            "sha256": sha,
            "bytes": int(target.stat().st_size),
            "stage_version": str(metadata.get("stage_version") or ""),
            "tool_version": str(metadata.get("tool_version") or ""),
            "source_commit": str(metadata.get("source_commit") or ""),
        }
        position = len(registry["artifacts"])
        registry["artifacts"].append(entry)
        indexes = dict(registry.get("indexes") or {})
        self._append_index(indexes, "by_artifact_id", artifact_id, position)
        self._append_index(indexes, "by_run_id", str(run_id), position)
        self._append_index(indexes, "by_stage", str(stage), position)
        self._append_index(indexes, "by_branch_id", str(branch_id), position)
        self._append_index(indexes, "by_branch_stage", f"{branch_id}::{stage}", position)
        self._append_index(indexes, "by_role", str(role), position)
        registry["indexes"] = indexes
        save_json_atomic(self.registry_path, registry)
        return entry

    def lookup_by_artifact_id(self, artifact_id: str) -> Dict[str, Any]:
        registry = self.load()
        idx = registry.get("indexes", {}).get("by_artifact_id", {}).get(str(artifact_id))
        if idx is None:
            raise PipelineError("artifact_not_registered", "Artifact not found", details={"artifact_id": artifact_id}, exit_code=3)
        return dict(registry["artifacts"][int(idx)])

    def registry_by_artifact_id(self) -> Dict[str, Dict[str, Any]]:
        registry = self.load()
        out: Dict[str, Dict[str, Any]] = {}
        for entry in registry.get("artifacts", []):
            out[str(entry["artifact_id"])] = dict(entry)
        return out

    def _lookup_many(self, bucket: str, key: str) -> List[Dict[str, Any]]:
        registry = self.load()
        positions = list(registry.get("indexes", {}).get(bucket, {}).get(str(key)) or [])
        return [dict(registry["artifacts"][int(position)]) for position in positions]

    def lookup_by_run_id(self, run_id: str) -> List[Dict[str, Any]]:
        return self._lookup_many("by_run_id", run_id)


    def lookup_by_branch_stage_role(self, *, branch_id: str, stage: str, role: str) -> List[Dict[str, Any]]:
        entries = self._lookup_many("by_branch_stage", f"{branch_id}::{stage}")
        return [entry for entry in entries if str(entry.get("role") or "") == str(role)]

    def lookup_latest(self, *, run_id: str, stage: str, role: str = "stage_output") -> Dict[str, Any]:
        entries = [entry for entry in self.lookup_by_run_id(run_id) if entry.get("stage") == stage and entry.get("role") == role]
        if not entries:
            raise PipelineError("artifact_not_registered", "No artifact found for run/stage/role", details={"run_id": run_id, "stage": stage, "role": role}, exit_code=3)
        entries.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("artifact_id") or "")))
        return entries[-1]
