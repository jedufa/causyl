from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .core import load_json, save_json_atomic
from .execution_tools import build_default_execution_tool_registry
from .job_state import JobStateStore

EXECUTION_ATTEMPT_SCAFFOLD_FILE = "08_execution_attempt_scaffold.json"


def build_execution_attempt_scaffold(*, job_dir: str | Path, run_id: str, config: Mapping[str, Any]) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    state_store = JobStateStore(job_path)
    state = state_store.load()
    run = dict(state.get("runs", {}).get(run_id) or {})
    review = dict(run.get("review") or {})
    execution_contract_payload = {}
    execution_contract_path = job_path / "runs" / str(run_id) / "07_execution_contract.json"
    if execution_contract_path.exists():
        execution_contract_payload = load_json(execution_contract_path)
    execution_contract = dict(execution_contract_payload.get("execution_contract") or {})
    registry = build_default_execution_tool_registry()
    tool_chain = registry.list_specs()
    execution_mode = str(execution_contract.get("execution_mode") or "review_only")
    generation_applicable = bool(execution_contract.get("generation_applicable", False))
    if execution_mode == "patch_generation" and generation_applicable:
        pending_tools = [entry["name"] for entry in tool_chain]
    else:
        pending_tools = []
    return {
        "job_id": str(state.get("job_id") or job_path.name),
        "run_id": str(run_id),
        "stage": "execution_attempt_scaffold",
        "attempt": {
            "attempt_index": int(run.get("attempt_index") or 0),
            "branch_id": str(run.get("branch_id") or ""),
            "parent_run_id": run.get("parent_run_id"),
            "status": str(run.get("status") or "UNKNOWN"),
            "retry_context": dict(run.get("retry_context") or {}),
            "review_status": str(review.get("status") or "PENDING"),
        },
        "execution_mode": str(execution_contract.get("execution_mode") or "review_only"),
        "generation_applicable": bool(execution_contract.get("generation_applicable", False)),
        "generation_blocked_reason": execution_contract.get("generation_blocked_reason"),
        "source_execution_contract_artifact": "07_execution_contract.json" if execution_contract_payload else None,
        "tool_chain": tool_chain,
        "planned_tool_sequence": pending_tools,
        "pending_attempts": [
            {"tool": name, "status": "pending", "attempt_number": 0, "last_error": None}
            for name in pending_tools
        ],
    }


def persist_execution_attempt_scaffold(*, job_dir: str | Path, run_id: str, config: Mapping[str, Any]) -> Dict[str, Any]:
    payload = build_execution_attempt_scaffold(job_dir=job_dir, run_id=run_id, config=config)
    target = Path(job_dir).resolve() / "runs" / str(run_id) / EXECUTION_ATTEMPT_SCAFFOLD_FILE
    save_json_atomic(target, payload)
    return {"file_path": str(target), "payload": payload}
