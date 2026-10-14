"""Shared manifest-document operations for explicit job lists."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "PyYAML is required for experiments/shared/job_manifests/ops.py. "
        "Install it (pip install pyyaml) in your venv."
    ) from e


Manifest = Dict[str, Any]
JobDef = Dict[str, Any]


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def load_manifest_document(path: str) -> Manifest:
    """Load a manifest document from YAML or JSON."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        doc = yaml.safe_load(_read_text(path)) or {}
    elif suffix == ".json":
        doc = json.loads(_read_text(path))
    else:
        raise ValueError(f"Unsupported manifest extension: {p.suffix} (expected .yaml/.yml/.json)")

    if not isinstance(doc, dict):
        raise ValueError("Manifest must parse into a mapping/dict.")
    return doc


def dump_manifest_yaml(manifest: Manifest) -> str:
    """Dump manifest to YAML with stable formatting."""
    return yaml.safe_dump(
        manifest,
        sort_keys=False,
        default_flow_style=False,
        width=120,
        allow_unicode=True,
    )


def write_manifest(path: str, manifest: Manifest) -> None:
    """Write a manifest as YAML or JSON based on file extension."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        _write_text(path, dump_manifest_yaml(manifest))
    elif suffix == ".json":
        _write_text(path, json.dumps(manifest, indent=2, sort_keys=True))
    else:
        raise ValueError(f"Unsupported manifest extension: {p.suffix} (expected .yaml/.yml/.json)")


def canonical_json(obj: Any) -> str:
    """Serialize to canonical JSON for manifest hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha1_hex(text: str) -> str:
    h = hashlib.sha1()
    h.update(text.encode("utf-8", errors="strict"))
    return h.hexdigest()


def compute_job_fingerprint(identity: Dict[str, Any]) -> str:
    """Compute a deterministic fingerprint from a canonical job-identity payload."""
    return "sha1:" + sha1_hex(canonical_json(identity))


def get_job_fingerprint(job: JobDef) -> str:
    """Get an explicit or best-effort deterministic job fingerprint."""
    try:
        meta = job.get("meta") or {}
        fp = str(meta.get("fingerprint") or "").strip()
        if fp:
            return fp
    except Exception:
        pass

    try:
        ident = {
            "job_id": str(job.get("job_id", "")),
            "module": str(job.get("module", "")),
            "args": job.get("args") or {},
        }
        return compute_job_fingerprint(ident)
    except Exception:
        return ""


def append_jobs(
    manifest: Manifest,
    new_jobs: List[JobDef],
    *,
    dedupe_policy: str = "fingerprint",
) -> Tuple[int, int]:
    """Append jobs to an existing manifest with dedupe."""
    if "jobs" not in manifest or manifest.get("jobs") is None:
        manifest["jobs"] = []
    if not isinstance(manifest["jobs"], list):
        raise ValueError("manifest.jobs must be a list")

    existing_jobs: List[JobDef] = list(manifest["jobs"])
    existing_job_ids = {str(j.get("job_id", "")) for j in existing_jobs}
    existing_fps = {get_job_fingerprint(j) for j in existing_jobs if get_job_fingerprint(j)}

    appended = 0
    skipped = 0

    for job in new_jobs:
        job_id = str(job.get("job_id", ""))
        if not job_id:
            raise ValueError("New job missing job_id")
        if dedupe_policy == "job_id":
            if job_id in existing_job_ids:
                skipped += 1
                continue
        else:
            fingerprint = get_job_fingerprint(job)
            if fingerprint and fingerprint in existing_fps:
                skipped += 1
                continue

        if job_id in existing_job_ids:
            existing = next((x for x in existing_jobs if str(x.get("job_id", "")) == job_id), None)
            if existing is not None:
                fp_old = get_job_fingerprint(existing)
                fp_new = get_job_fingerprint(job)
                if fp_old and fp_new and fp_old != fp_new:
                    raise ValueError(
                        f"Job ID collision with different fingerprints: job_id={job_id} old={fp_old} new={fp_new}"
                    )
            raise ValueError(f"Job ID already exists in manifest: {job_id}")

        manifest["jobs"].append(job)
        existing_job_ids.add(job_id)
        fingerprint = get_job_fingerprint(job)
        if fingerprint:
            existing_fps.add(fingerprint)
        appended += 1

    return appended, skipped
