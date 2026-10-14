"""Admin helpers for job budget allocation and durable ledger inspection."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List

from infra.controlplane import job_budget_cost_ledger_contract as _contract
from infra.controlplane import job_budget_cost_ledger_store as _store


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record_budget_allocation(
    *,
    allocation_uid: str,
    job_uid: str,
    amount_minor_units: int,
    unit: str,
    allocation_reason_code: str,
    recorded_at_utc: str = "",
) -> Dict[str, Any]:
    normalized_job_uid = _contract._validate_path_safe_identifier(job_uid, field_name="job_uid")
    recorded_at = str(recorded_at_utc or "").strip() or _utc_iso()
    persisted = _store.append_allocation_record_once(
        {
            "schema_version": _contract.RECORD_SCHEMA_VERSION,
            "allocation_uid": str(allocation_uid or "").strip(),
            "job_uid": normalized_job_uid,
            "recorded_at_utc": recorded_at,
            "amount_minor_units": amount_minor_units,
            "unit": unit,
            "allocation_reason_code": allocation_reason_code,
        },
        repo_root=_repo_root(),
    )
    inspection = inspect_job_budget_ledger(job_uid=normalized_job_uid)
    return {
        "ok": True,
        "job_uid": normalized_job_uid,
        "record": dict(persisted["record"]),
        "deduped": bool(persisted.get("deduped")),
        "semantics": "idempotent_by_allocation_uid",
        "inspection": inspection,
    }


def inspect_job_budget_ledger(*, job_uid: str) -> Dict[str, Any]:
    normalized_job_uid = _contract._validate_path_safe_identifier(job_uid, field_name="job_uid")
    repo_root = _repo_root()
    allocations = _store.read_allocation_records(normalized_job_uid, repo_root=repo_root)
    cost_events = _store.read_cost_event_records(normalized_job_uid, repo_root=repo_root)
    recovery_outcomes = _store.read_recovery_outcome_records(normalized_job_uid, repo_root=repo_root)
    if not allocations and not cost_events and not recovery_outcomes:
        raise FileNotFoundError(f"job budget ledger not found: {normalized_job_uid}")

    snapshot = _store.replay_job_budget_state(normalized_job_uid, repo_root=repo_root)
    return {
        "ok": True,
        "job_uid": normalized_job_uid,
        "snapshot": {
            **dict(snapshot),
            "allocation_count": len(allocations),
            "cost_event_count": len(cost_events),
            "recovery_outcome_count": len(recovery_outcomes),
        },
        "allocation_records": allocations,
        "cost_event_records": cost_events,
        "recovery_outcome_records": recovery_outcomes,
        "last_durable_event": _store.last_durable_event(normalized_job_uid, repo_root=repo_root),
        "recovery_follow_up": _store.recovery_follow_up_state(normalized_job_uid, repo_root=repo_root),
    }


def list_budget_jobs(*, limit: int = 200) -> Dict[str, Any]:
    repo_root = _repo_root()
    jobs_root = _store.ledger_root(repo_root=repo_root) / "jobs"
    rows: List[Dict[str, Any]] = []
    if not jobs_root.exists():
        return {"ok": True, "jobs": rows}

    for job_dir in sorted((path for path in jobs_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        job_uid = job_dir.name
        try:
            inspection = inspect_job_budget_ledger(job_uid=job_uid)
            snapshot = dict(inspection["snapshot"])
            last_event = inspection.get("last_durable_event")
            rows.append(
                {
                    "job_uid": job_uid,
                    "snapshot": snapshot,
                    "last_durable_event": last_event,
                    "recovery_follow_up": inspection.get("recovery_follow_up"),
                    "has_budget_activity": bool(
                        snapshot.get("allocation_count")
                        or snapshot.get("cost_event_count")
                        or snapshot.get("recovery_outcome_count")
                    ),
                    "last_activity_utc": str((last_event or {}).get("recorded_at_utc") or ""),
                }
            )
        except FileNotFoundError:
            continue
        except Exception as exc:
            rows.append(
                {
                    "job_uid": job_uid,
                    "error": str(exc),
                    "has_budget_activity": True,
                    "last_activity_utc": "",
                }
            )

    rows.sort(key=lambda item: (str(item.get("last_activity_utc") or ""), str(item.get("job_uid") or "")), reverse=True)
    limited = rows[: max(1, int(limit))]
    return {"ok": True, "jobs": limited, "total_jobs": len(rows)}
