"""Deterministic hashing helpers for experiment records and artifact inventories."""

from __future__ import annotations

import hashlib
from typing import Any

from .canonical_json import dumps_canonical


def sha256_bytes(data: bytes) -> str:
    """Compute sha256 hex digest for bytes."""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: str, chunk_bytes: int = 1024 * 1024) -> str:
    """Compute sha256 hex digest for a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def job_spec_hash(job_spec_obj: Any) -> str:
    """Compute sha256 of canonical job_spec.json."""
    canonical = dumps_canonical(job_spec_obj).encode("utf-8")
    return sha256_bytes(canonical)


def files_list_hash(files_list_obj: Any) -> str:
    """Compute sha256 of canonical JSON for a sorted files list."""
    canonical = dumps_canonical(files_list_obj).encode("utf-8")
    return sha256_bytes(canonical)
