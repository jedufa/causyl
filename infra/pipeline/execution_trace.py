from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .core import load_json, save_json_atomic, utc_now_iso
from .job_state import JobStateStore
from .run_summary import load_stage_envelopes

EXECUTION_TRACE_FILE = "execution_trace.json"


def _safe_load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def build_execution_trace(*, job_dir: str | Path, run_id: str, config: Mapping[str, Any], status: Optional[str] = None) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    state_store = JobStateStore(job_path)
    state = state_store.load()
    run = dict(state.get("runs", {}).get(run_id) or {})
    stage_envelopes = load_stage_envelopes(job_path, run_id)
    stage_trace: List[Dict[str, Any]] = []
    for stage_key, envelope in stage_envelopes.items():
        result = dict(envelope.get("result") or {})
        decision_trace = list(result.get("decision_trace") or [])
        stage_trace.append(
            {
                "stage": stage_key,
                "status": str(envelope.get("status") or "success"),
                "duration_ms": int(envelope.get("duration_ms") or 0),
                "decision_trace": decision_trace,
                "decision_count": len(decision_trace),
            }
        )
    execution_contract = _safe_load_json(job_path / "runs" / str(run_id) / "07_execution_contract.json")
    attempt_scaffold = _safe_load_json(job_path / "runs" / str(run_id) / "08_execution_attempt_scaffold.json")
    return {
        "job_id": str(state.get("job_id") or job_path.name),
        "run_id": str(run_id),
        "stage": "execution_trace",
        "status": status or str(run.get("status") or state.get("status") or "UNKNOWN"),
        "generated_at": utc_now_iso(),
        "attempt": {
            "attempt_index": int(run.get("attempt_index") or 0),
            "retry_context": dict(run.get("retry_context") or {}),
            "branch_id": str(run.get("branch_id") or ""),
        },
        "stage_trace": stage_trace,
        "execution_contract_summary": {
            "execution_mode": execution_contract.get("execution_contract", {}).get("execution_mode"),
            "generation_applicable": execution_contract.get("execution_contract", {}).get("generation_applicable"),
            "allowed_edit_files": list(execution_contract.get("execution_contract", {}).get("allowed_edit_files") or []),
        },
        "retry_scaffold_summary": {
            "planned_tool_sequence": list(attempt_scaffold.get("planned_tool_sequence") or []),
            "generation_blocked_reason": attempt_scaffold.get("generation_blocked_reason"),
        },
    }


def persist_execution_trace(*, job_dir: str | Path, run_id: str, config: Mapping[str, Any], status: Optional[str] = None) -> Dict[str, Any]:
    payload = build_execution_trace(job_dir=job_dir, run_id=run_id, config=config, status=status)
    target = Path(job_dir).resolve() / "runs" / str(run_id) / EXECUTION_TRACE_FILE
    save_json_atomic(target, payload)
    return {"file_path": str(target), "payload": payload}
