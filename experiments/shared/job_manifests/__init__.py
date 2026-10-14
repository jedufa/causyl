"""Shared helpers for explicit experiment job manifests."""

from .io import load_manifest, repo_root_from_manifest_file, resolve_output_root, utc_now_iso
from .job_spec import build_job_spec, render_cli_args
from .ops import (
    append_jobs,
    canonical_json,
    compute_job_fingerprint,
    dump_manifest_yaml,
    get_job_fingerprint,
    load_manifest_document,
    write_manifest,
)
from .types import JobResult

__all__ = [
    "JobResult",
    "append_jobs",
    "build_job_spec",
    "canonical_json",
    "compute_job_fingerprint",
    "dump_manifest_yaml",
    "get_job_fingerprint",
    "load_manifest",
    "load_manifest_document",
    "render_cli_args",
    "repo_root_from_manifest_file",
    "resolve_output_root",
    "utc_now_iso",
    "write_manifest",
]
