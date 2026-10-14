"""tools.joblib.hashing

Hash helpers for Phase 0 contract v1.

- job_spec_hash: sha256(canonical job_spec.json bytes)
- file sha256: sha256(file bytes)

Note: The Phase 0 docs include an ambiguous comment about whether
job_spec_hash is included when hashing job_spec.json. The implementation
here uses the standard, practical approach:

1) set job_spec_hash to "" (empty string)
2) compute sha256 over canonical JSON bytes
3) set job_spec_hash to the computed digest

This makes job_spec_hash deterministic without requiring an impossible
self-referential fixed point.

If you *must* interpret Phase 0 differently, update HASHING_RULES_v1.md
and docs/specs/schemas/job_spec_v1.json in a v1.1 contract.
"""

from __future__ import annotations

import hashlib
from typing import Any

from tools.joblib.canonical_json import dumps_canonical


def sha256_bytes(data: bytes) -> str:
    """Compute sha256 hex digest for bytes."""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: str, chunk_bytes: int = 1024 * 1024) -> str:
    """Compute sha256 hex digest for a file.

    Parameters
    ----------
    path:
        File path.
    chunk_bytes:
        Chunk size for streaming reads.

    Returns
    -------
    str
        64-hex sha256 digest.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def job_spec_hash(job_spec_obj: Any) -> str:
    """Compute sha256 of canonical job_spec.json.

    The caller should ensure job_spec_hash field is set to "" before hashing.
    """
    canonical = dumps_canonical(job_spec_obj).encode("utf-8")
    return sha256_bytes(canonical)


def files_list_hash(files_list_obj: Any) -> str:
    """Compute sha256 of canonical JSON for a sorted files list."""
    canonical = dumps_canonical(files_list_obj).encode("utf-8")
    return sha256_bytes(canonical)
