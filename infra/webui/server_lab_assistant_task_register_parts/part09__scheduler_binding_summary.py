from typing import Any, Dict, List, Set


_SCHEDULER_LIKE_SURFACES = {"scheduler", "healer", "swarm"}
_LIVE_SCHEDULER_RUN_STATES = {"requested", "active", "running", "waiting"}
_RUNTIME_SCHEDULER_RUN_STATES = _LIVE_SCHEDULER_RUN_STATES | {"stale"}
_LIVE_SCHEDULER_REFERENCE_STATUSES = {"execution_ref_active", "execution_ref_unresolved"}


def _binding_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _binding_job_ids(task: Dict[str, Any]) -> List[str]:
    job_ids: List[str] = []
    seen: Set[str] = set()
    refs = [dict(row) for row in list(dict(task or {}).get("delegation_refs") or []) if isinstance(row, dict)]
    for ref in refs:
        surface = _binding_text(ref.get("surface")).lower()
        if surface not in _SCHEDULER_LIKE_SURFACES:
            continue
        for key in ("subsystem_id", "delegation_request_id"):
            value = _binding_text(ref.get(key))
            if value and value not in seen:
                seen.add(value)
                job_ids.append(value)
    owner = dict(dict(task or {}).get("execution_surface_ownership") or {})
    if _binding_text(owner.get("selected_surface")).lower() in _SCHEDULER_LIKE_SURFACES:
        execution_id = _binding_text(owner.get("execution_id"))
        if execution_id and execution_id not in seen:
            seen.add(execution_id)
            job_ids.append(execution_id)
    task_runs = [dict(row) for row in list(dict(task or {}).get("task_runs") or []) if isinstance(row, dict)]
    for run in task_runs:
        if _binding_text(run.get("surface")).lower() not in _SCHEDULER_LIKE_SURFACES:
            continue
        candidate = _binding_text(run.get("execution_id")) or _binding_text(run.get("run_id"))
        if candidate and candidate not in seen:
            seen.add(candidate)
            job_ids.append(candidate)
    return job_ids


def scheduler_binding_summary(task: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(task or {})
    refs = [dict(item) for item in list(row.get("delegation_refs") or []) if isinstance(item, dict)]
    scheduler_surfaces: Set[str] = set()
    non_scheduler_surfaces: Set[str] = set()
    binding_sources: Set[str] = set()
    live_binding_sources: Set[str] = set()
    runtime_binding_sources: Set[str] = set()
    for ref in refs:
        surface = _binding_text(ref.get("surface")).lower()
        if not surface:
            continue
        if surface in _SCHEDULER_LIKE_SURFACES:
            scheduler_surfaces.add(surface)
            binding_sources.add("delegation_refs")
            live_binding_sources.add("delegation_refs")
        else:
            non_scheduler_surfaces.add(surface)
    owner = dict(row.get("execution_surface_ownership") or {})
    owner_surface = _binding_text(owner.get("selected_surface")).lower()
    if owner_surface in _SCHEDULER_LIKE_SURFACES:
        scheduler_surfaces.add(owner_surface)
        binding_sources.add("execution_surface_ownership")
        runtime_binding_sources.add("execution_surface_ownership")
        if _binding_text(owner.get("reference_status")).lower() in _LIVE_SCHEDULER_REFERENCE_STATUSES:
            live_binding_sources.add("execution_surface_ownership")
    elif owner_surface:
        non_scheduler_surfaces.add(owner_surface)
    task_runs = [dict(item) for item in list(row.get("task_runs") or []) if isinstance(item, dict)]
    for run in task_runs:
        run_surface = _binding_text(run.get("surface")).lower()
        if not run_surface:
            continue
        if run_surface in _SCHEDULER_LIKE_SURFACES:
            scheduler_surfaces.add(run_surface)
            binding_sources.add("task_runs")
            run_state = _binding_text(run.get("run_state")).lower()
            if run_state in _RUNTIME_SCHEDULER_RUN_STATES:
                runtime_binding_sources.add("task_runs")
            if run_state in _LIVE_SCHEDULER_RUN_STATES:
                live_binding_sources.add("task_runs")
        else:
            non_scheduler_surfaces.add(run_surface)
    if "delegation_refs" in binding_sources and not runtime_binding_sources and not non_scheduler_surfaces:
        runtime_binding_sources.add("delegation_refs")
    historical_task_run_provenance_only = (
        binding_sources == {"task_runs"} and not runtime_binding_sources and not live_binding_sources
    )
    job_ids = _binding_job_ids(row)
    return {
        "has_scheduler_binding": bool(scheduler_surfaces),
        "has_live_scheduler_binding": bool(live_binding_sources),
        "has_runtime_scheduler_binding": bool(runtime_binding_sources),
        "has_historical_scheduler_provenance_only": (
            ("delegation_refs" in binding_sources and not runtime_binding_sources and bool(non_scheduler_surfaces))
            or historical_task_run_provenance_only
        ),
        "has_only_scheduler_surfaces": bool(scheduler_surfaces) and not non_scheduler_surfaces,
        "scheduler_surfaces": sorted(scheduler_surfaces),
        "non_scheduler_surfaces": sorted(non_scheduler_surfaces),
        "binding_sources": sorted(binding_sources),
        "live_binding_sources": sorted(live_binding_sources),
        "runtime_binding_sources": sorted(runtime_binding_sources),
        "job_ids": job_ids,
    }
