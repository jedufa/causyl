from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class TaskRunRecord(TypedDict, total=False):
    run_id: str
    state: str
    execution_surface: str
    execution_ref: str
    reference_status: str
    attention_state: str
    evidence_refs: List[str]


class TaskRow(TypedDict, total=False):
    task_id: str
    kind: str
    title: str
    status: str
    owner: str
    source: str
    ledger_uid: str
    intake_path: str
    next_expected_action: str
    task_runs: List[TaskRunRecord]
    scope_contract: Dict[str, Any]
    acceptance_contract: Dict[str, Any]
    decomposition_contract: Dict[str, Any]


class TaskRegisterSnapshot(TypedDict, total=False):
    schema_version: str
    register_id: str
    assistant_id: str
    write_generation: int
    active_tasks: List[TaskRow]
    completed_tasks: List[TaskRow]
    residue_tasks: List[TaskRow]
    open_loops: List[Dict[str, Any]]
    recent_constraints: List[Dict[str, Any]]
