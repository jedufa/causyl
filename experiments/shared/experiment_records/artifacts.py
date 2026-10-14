"""Deterministic artifact-record helpers for experiment wrapper folders."""

from __future__ import annotations

import os
from typing import Dict, List

from .hashing import files_list_hash, sha256_file


def build_artifact_manifest(wrapper_dir: str, job_id: str, job_spec_hash: str) -> Dict:
    """Build a deterministic artifact manifest for a wrapper directory."""
    files: List[Dict] = []
    for root, _dirs, filenames in os.walk(wrapper_dir):
        for name in filenames:
            abs_path = os.path.join(root, name)
            rel_path = os.path.relpath(abs_path, wrapper_dir).replace(os.sep, "/")
            st = os.stat(abs_path)
            files.append(
                {
                    "path": rel_path,
                    "sha256": sha256_file(abs_path),
                    "bytes": int(st.st_size),
                }
            )

    files.sort(key=lambda d: d["path"])
    manifest_hash = files_list_hash(files)
    return {
        "schema_version": "v1",
        "job_id": job_id,
        "job_spec_hash": job_spec_hash,
        "manifest_hash": manifest_hash,
        "files": files,
    }
