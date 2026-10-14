from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class JobSchedule(TypedDict, total=False):
    mode: str
    run_at: str
    timezone: str
    every_minutes: int


class JobRuntime(TypedDict, total=False):
    model: str
    fallback_models: List[str]
    reasoning_effort: str
    agent_tech: str


class JobSpec(TypedDict, total=False):
    job_id: str
    job_shape: str
    job_shape_source: str
    job_shape_compatibility_mode: bool
    job_shape_migration_status: str
    job_shape_migration_reason: str
    enabled: bool
    role: str
    binding: Dict[str, str]
    prompt: str
    prompt_ref: str
    schedule: JobSchedule
    expected_output: Dict[str, str]
    policy: Dict[str, Any]
    runtime: JobRuntime


class JobReceipt(TypedDict, total=False):
    job_id: str
    job_shape: str
    job_shape_source: str
    job_shape_compatibility_mode: bool
    job_shape_migration_status: str
    job_shape_migration_reason: str
    run_id: str
    status: str
    assistant: Dict[str, Any]
    dispatched_message: str
