"""tools.joblib.artifacts

Artifact manifest builder for wrapper run folders.

The artifact manifest is a deterministic inventory of all files inside the
wrapper folder, with sha256 hashes and byte sizes.

Rules (v1):
- include all files that exist in wrapper folder (recursively)
- store paths relative to wrapper folder
- sort by path lexicographically
- manifest_hash = sha256(canonical JSON of the sorted files list)
"""

from __future__ import annotations

import os
from typing import Dict, List

from tools.joblib.hashing import sha256_file, files_list_hash


def build_artifact_manifest(wrapper_dir: str, job_id: str, job_spec_hash: str) -> Dict:
    """Build artifact_manifest.json content.

    Parameters
    ----------
    wrapper_dir:
        Absolute path to the wrapper run folder.
    job_id:
        Job identifier.
    job_spec_hash:
        job_spec_hash for this job.

    Returns
    -------
    dict
        Artifact manifest object matching docs/specs/schemas/artifact_manifest_v1.json.
    """
    files: List[Dict] = []

    for root, _dirs, filenames in os.walk(wrapper_dir):
        for name in filenames:
            abs_path = os.path.join(root, name)
            rel_path = os.path.relpath(abs_path, wrapper_dir)
            rel_path = rel_path.replace(os.sep, "/")

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
