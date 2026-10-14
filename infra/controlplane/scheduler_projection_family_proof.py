"""Cross-surface proof helpers for scheduler projection parity in the plane."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from infra.bin import scheduler_live_watch as _scheduler_live_watch
from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.webui import server_routes_and_main as _routes


def collect_scheduler_projection_surface_case(
    *,
    api_list_row: Mapping[str, Any],
    api_detail_row: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    job_id = str(api_list_row.get("job_id") or "").strip()
    detail_row = dict(api_detail_row or api_list_row)
    mobile_summary = _routes._plane_mobile_scheduler_summary_payload([dict(api_list_row)])
    mobile_rows = {
        str(row.get("job_id") or "").strip(): dict(row)
        for row in list(mobile_summary.get("projected_rows") or [])
        if str(row.get("job_id") or "").strip()
    }
    watch_truth = _scheduler_live_watch._projection_truth_for_watch(dict(api_list_row))
    mobile_row = dict(mobile_rows.get(job_id) or {})

    api_list_status = str(((api_list_row.get("scheduler_projection_v1") or {}).get("projected_status") or "")).strip()
    api_detail_status = str(((detail_row.get("scheduler_projection_v1") or {}).get("projected_status") or "")).strip()
    watch_status = str(watch_truth.get("status") or "").strip()
    mobile_status = str(mobile_row.get("projected_status") or "").strip()
    statuses = {
        "api_list": api_list_status,
        "api_detail": api_detail_status,
        "watch": watch_status,
        "next_job_summary": mobile_status,
    }
    degraded = {
        "api_list": bool(api_list_row.get("scheduler_truth_degraded")),
        "api_detail": bool(detail_row.get("scheduler_truth_degraded")),
        "watch": bool(watch_truth.get("degraded")),
        "next_job_summary": bool(
            mobile_row.get("durable_conflict")
            or str(mobile_row.get("projected_status") or "").strip() == "ambiguous"
        ),
    }
    return {
        "job_id": job_id,
        "display_title": str(api_list_row.get("display_title") or job_id),
        "statuses": statuses,
        "degraded": degraded,
        "reasons": {
            "api_list": str(((api_list_row.get("scheduler_projection_v1") or {}).get("projection_reason") or "")).strip(),
            "api_detail": str(((detail_row.get("scheduler_projection_v1") or {}).get("projection_reason") or "")).strip(),
            "watch": str(watch_truth.get("reason") or "").strip(),
            "next_job_summary": str(mobile_row.get("projection_reason") or "").strip(),
        },
        "parity_ok": len({value for value in statuses.values() if value}) == 1,
        "degraded_parity_ok": len(set(degraded.values())) == 1,
    }


def collect_scheduler_projection_surface_proof(*, repo_root: Path, now_utc: str | None = None) -> Dict[str, Any]:
    rows = list(_scheduled_jobs.list_jobs(repo_root=repo_root, now_utc=now_utc))
    mobile_summary = _routes._plane_mobile_scheduler_summary_payload(rows)
    cases = []
    mismatches = []
    degraded_mismatches = []

    for api_list_row in rows:
        job_id = str(api_list_row.get("job_id") or "").strip()
        if not job_id:
            continue
        spec = _scheduled_jobs.load_job(job_id, repo_root=repo_root)
        state = _scheduled_jobs.load_state(job_id, repo_root=repo_root)
        api_detail_row = _scheduled_jobs.job_summary(
            spec,
            state=state,
            repo_root=repo_root,
            now=None if not now_utc else _scheduled_jobs._parse_utc(now_utc),
        )
        case = collect_scheduler_projection_surface_case(api_list_row=api_list_row, api_detail_row=api_detail_row)
        if not case["parity_ok"]:
            mismatches.append({"job_id": job_id, "statuses": dict(case["statuses"])})
        if not case["degraded_parity_ok"]:
            degraded_mismatches.append({"job_id": job_id, "degraded": dict(case["degraded"])})
        cases.append(case)

    next_projection = mobile_summary.get("next_projection") if isinstance(mobile_summary.get("next_projection"), Mapping) else {}
    next_job = mobile_summary.get("next_job") if isinstance(mobile_summary.get("next_job"), Mapping) else {}
    return {
        "schema_version": "scheduler_projection_surface_proof_v1",
        "surfaces": ["watch", "api_list", "api_detail", "next_job_summary"],
        "case_count": len(cases),
        "all_cases_match": not mismatches,
        "all_degraded_flags_match": not degraded_mismatches,
        "mismatches": mismatches,
        "degraded_mismatches": degraded_mismatches,
        "cases": cases,
        "summary": {
            "total_jobs": int(mobile_summary.get("total_jobs") or 0),
            "enabled_jobs": int(mobile_summary.get("enabled_jobs") or 0),
            "running_jobs": int(mobile_summary.get("running_jobs") or 0),
            "blocked_jobs": int(mobile_summary.get("blocked_jobs") or 0),
            "next_job_id": str(next_job.get("job_id") or "").strip(),
            "next_candidate_bucket": str(next_projection.get("next_candidate_bucket") or "").strip(),
            "next_due_utc": str(next_projection.get("next_due_utc") or "").strip(),
            "next_reason": str(next_projection.get("projection_reason") or next_projection.get("due_reason") or "").strip(),
        },
    }
