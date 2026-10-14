"""Shared in-memory result types for manifest-driven execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class JobResult:
    """In-memory result for one job execution."""

    job_id: str
    job_spec_hash: str
    status: str
    exit_code: int
    failure_code: str
    wall_time_sec: float
    run_folder_rel: str
    wrapper_dir_abs: str
    run_record_rel: str
    run_meta_rel: str
    report_path_rel: str
    artifact_manifest_rel: str
