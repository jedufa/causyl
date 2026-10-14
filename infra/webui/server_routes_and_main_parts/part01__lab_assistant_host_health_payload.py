"""WebUI server module (extracted from server.py to keep things maintainable).

Last modified: 2026-03-31 Europe/Vienna
"""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
from infra.webui.server_deprecated_index import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_data_precedence import *  # noqa: F403,F401
from infra.webui.server_tail_parsers import *  # noqa: F403,F401
from infra.webui.server_timeline import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
from infra.webui.server_changesets import *  # noqa: F403,F401
from infra.webui.server_exports import *  # noqa: F403,F401
from infra.webui.server_archives import *  # noqa: F403,F401
from infra.webui.server_command_submission import *  # noqa: F403,F401
from infra.webui.server_branch_planning import *  # noqa: F403,F401
from infra.webui.server_run_summaries import *  # noqa: F403,F401
from infra.webui.server_http_core import *  # noqa: F403,F401
from tools.benchmarks import agent_benchmark as _agent_benchmark  # noqa: E402
from infra.webui import server_agents as _agents  # noqa: E402
from infra.webui import server_agents_orchestrator as _agent_orch  # noqa: E402
from infra.webui import server_agents_swarm as _agent_swarm  # noqa: E402
from infra.webui import server_route_dispatch as _route_dispatch  # noqa: E402
from infra.webui import server_lab_assistant_delegation_policy as _lab_delegate_policy  # noqa: E402
from infra.webui import server_lab_assistant_execution_proof as _lab_execution_proof  # noqa: E402
from infra.webui import server_lab_assistant_execution_truth_ownership as _lab_execution_truth_ownership  # noqa: E402
from infra.webui import server_mobile_cockpit as _mobile_cockpit  # noqa: E402
from infra.webui import server_execution_queue as _execution_queue  # noqa: E402
from infra.webui import server_lab_assistant_bootstrap as _lab_bootstrap  # noqa: E402
from infra.webui import server_lab_assistant_host as _lab_host  # noqa: E402
from infra.webui import server_lab_assistant_self_check as _lab_self_check  # noqa: E402
from infra.webui import server_lab_assistant_operator_snapshot as _lab_operator_snapshot  # noqa: E402
from infra.webui import server_lab_assistant_web_operations as _lab_web_ops  # noqa: E402
from infra.webui import server_lab_assistant_recovery as _lab_recovery  # noqa: E402
from infra.webui import server_lab_assistant_state as _lab_state  # noqa: E402
from infra.webui import server_lab_assistant_task_register as _lab_task_register  # noqa: E402
from infra.webui import server_lab_assistant_status_diagnostics as _lab_status_diag  # noqa: E402
from infra.webui import server_control_action_audit as _control_action_audit  # noqa: E402
from infra.webui import server_experiment_control_policy_admin as _experiment_control_policy_admin  # noqa: E402
from infra.webui import server_assistant_probe as _assistant_probe  # noqa: E402
from infra.webui import server_assistant_bridge as _assistant_bridge  # noqa: E402
from infra.webui import server_ledger_actions as _ledger_actions  # noqa: E402
from infra.controlplane import scheduled_jobs as _scheduled_jobs  # noqa: E402
from infra.controlplane import scheduler_projection_contract as _scheduler_projection_contract  # noqa: E402
from infra.controlplane.sched import surfaces as _sched_v2_surfaces  # noqa: E402
from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger  # noqa: E402
from infra.controlplane import backend_api_boundary as _backend_api_boundary  # noqa: E402
from infra.controlplane import full_framework_scenarios as _full_framework_scenarios  # noqa: E402
from infra.controlplane import split_topology_contract as _split_topology_contract  # noqa: E402
from infra.webui.ledger import ledger_schema as _ledger_schema  # noqa: E402
from infra.webui.server_codex_runtime import CodexRuntimeError  # noqa: E402
from tools.git.support import commit_journal as _commit_journal  # noqa: E402
from tools import runtime_policy as _runtime_policy  # noqa: E402
from experiments_shared import refresh_experiment_control_policy_cache as _refresh_experiment_control_policy_cache  # noqa: E402
import infra.controlplane.verification_workflow_launch as _verification_workflow_launch  # noqa: E402
import queue

# NOTE: _ServerState is intentionally prefixed with an underscore, so it is
# not imported by the star-import above (Python excludes _names unless
# server_http_core defines __all__). We still need it at runtime.
from infra.webui.server_http_core import _ServerState  # noqa: E402

_PIS_UID_RE = re.compile(r"\bPIS-\d{8}-\d{6}Z-[A-Z0-9]{4}\b")
_MOBILE_CANCEL_RACE_LABEL = "Stop raced"
_MOBILE_SESSION_LOCK = threading.Lock()
_MOBILE_SESSION_MAX_TURNS = 20000
_MOBILE_SESSIONS: Dict[str, List[Dict[str, str]]] = {}
_MOBILE_SESSION_CAPS: Dict[str, int] = {}
_MOBILE_SESSIONS_PATH: Optional[Path] = None
_MOBILE_SESSIONS_LOADED = False
_MOBILE_ACTIVE_STREAMS_LOCK = threading.Lock()
_MOBILE_ACTIVE_STREAMS: Dict[str, Dict[str, Any]] = {}
_MOBILE_CANCEL_WAIT_S = 1.5
_MOBILE_BOOTSTRAP_PROMPT_CACHE: Dict[str, str] = {}
_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY: Dict[str, str] = {}
_STARTUP_RECOVERY_GUARD = threading.Lock()
_STARTUP_RECOVERY_THREAD: Optional[threading.Thread] = None
_QUIET_POLLING_LOGS = str(os.environ.get("EIEL_WEBUI_LOG_QUIET_POLLING", "1")).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_QUIET_POLLING_PATHS = {
    "/api/health",
    "/api/runs",
    "/api/system/runs",
    "/api/system/processes",
    "/api/changesets",
}
_MOBILE_AGENT_SPECS: Dict[str, Dict[str, str]] = {
    "p": {"thread_id": "mpl", "role": "generalist", "prompt_file": "role__plane_mobile.md"},
    "a": {"thread_id": "mbl", "role": "generalist", "prompt_file": "role__generalist_mobile.md"},
    "a2": {"thread_id": "mb2", "role": "generalist", "prompt_file": "role__generalist_mobile.md"},
    "a3": {"thread_id": "mb3", "role": "generalist", "prompt_file": "role__generalist_mobile.md"},
    "o": {"thread_id": "mor", "role": "generalist", "prompt_file": "role__orchestrator_mobile.md"},
    "b": {"thread_id": "mbu", "role": "builder", "prompt_file": "role__builder_mobile.md"},
    "v": {"thread_id": "mvr", "role": "assurer", "prompt_file": "role__verifier_mobile.md"},
}
_SWARM_TRIGGER_RE = re.compile(r"^\s*swarm it\b[:\s-]*(?P<objective>.*)$", re.IGNORECASE)
_BENCHMARK_TRIGGER_RE = re.compile(r"^\s*(?:benchmark it|start the benchmark)\b[:\s-]*(?P<objective>.*)$", re.IGNORECASE)


def _scheduler_projection_contract_payload() -> Dict[str, Any]:
    contract = _scheduler_projection_contract.scheduler_projection_contract_v1()
    return {
        "version": contract.version,
        "uid": contract.uid,
        "canonical_artifact_rel": contract.canonical_artifact_rel.as_posix(),
        "canonical_doc_rel": contract.canonical_doc_rel.as_posix(),
        "statuses": list(contract.statuses),
        "consumer_surfaces": list(contract.consumer_surfaces),
        "next_candidate_buckets": list(contract.next_candidate_buckets),
    }


def _build_schedules_jobs_payload(*, repo_root: Optional[Path] = None, timeout_s: float = 8.0) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    env = dict(os.environ)
    repo_txt = str(root)
    env["PYTHONPATH"] = repo_txt + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    script = (
        "import json\n"
        "from pathlib import Path\n"
        "from infra.controlplane import scheduled_jobs as s\n"
        f"root = Path({repo_txt!r})\n"
        "payload = {\n"
        "  'schema_version': 'scheduled_jobs_list_v1',\n"
        "  'utc': s._utc_iso(),\n"
        "  'jobs': s.list_jobs(repo_root=root, include_context_resolution=False, include_reconciliation=False),\n"
        "  'events': s.list_execution_events(repo_root=root, limit=50),\n"
        "}\n"
        "print(json.dumps(payload, sort_keys=True, separators=(',', ':')))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_txt,
        env=env,
        capture_output=True,
        text=True,
        timeout=max(1.0, float(timeout_s)),
        check=False,
    )
    if proc.returncode != 0:
        stderr = str(proc.stderr or "").strip()
        raise RuntimeError(f"scheduler snapshot helper failed rc={proc.returncode}: {stderr or 'no stderr'}")
    payload = json.loads(str(proc.stdout or "").strip() or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("scheduler snapshot helper returned non-object JSON")
    payload["scheduler_projection_contract"] = _scheduler_projection_contract_payload()
    return payload


def _build_schedules_jobs_payload_inprocess(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    payload = {
        "schema_version": "scheduled_jobs_list_v1",
        "utc": _scheduled_jobs._utc_iso(),
        "jobs": _scheduled_jobs.list_jobs(
            repo_root=root,
            include_context_resolution=False,
            include_reconciliation=False,
        ),
        "events": _scheduled_jobs.list_execution_events(repo_root=root, limit=50),
    }
    payload["scheduler_projection_contract"] = _scheduler_projection_contract_payload()
    return payload


def _scheduled_jobs_autostart_enabled() -> bool:
    return str(os.environ.get("EIEL_SCHEDULED_JOBS_AUTOSTART", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _scheduler_liveness_payload_or_disabled(*, repo_root: Path) -> Dict[str, Any]:
    if not _scheduled_jobs_autostart_enabled():
        return {
            "health": "disabled",
            "reason": "scheduled_jobs_autostart_disabled",
            "restart_attempted": False,
        }
    return _scheduled_jobs.ensure_scheduler_service_liveness(repo_root=repo_root)


def _attach_inprocess_scheduler_status(payload: Dict[str, Any], *, include_scheduler_v2: bool = True) -> Dict[str, Any]:
    out = dict(payload or {})
    try:
        scheduler_health = _scheduler_liveness_payload_or_disabled(repo_root=_repo_root_from_webui())
    except Exception as exc:
        scheduler_health = {
            "health": "degraded",
            "reason": f"self_heal_probe_failed:{type(exc).__name__}",
            "error": str(exc),
            "restart_attempted": False,
        }
    try:
        watchdog_health = _scheduled_jobs.ensure_watchdog_service_liveness(repo_root=_repo_root_from_webui())
    except Exception as exc:
        watchdog_health = {
            "health": "degraded",
            "reason": f"self_heal_probe_failed:{type(exc).__name__}",
            "error": str(exc),
            "restart_attempted": False,
        }
    try:
        healer_health = _scheduled_jobs.ensure_healer_service_liveness(repo_root=_repo_root_from_webui())
    except Exception as exc:
        healer_health = {
            "health": "degraded",
            "reason": f"self_heal_probe_failed:{type(exc).__name__}",
            "error": str(exc),
            "restart_attempted": False,
        }
    out["scheduler"] = _scheduled_jobs.scheduler_service_status()
    out["scheduler_health"] = scheduler_health
    out["watchdog_health"] = watchdog_health
    out["healer_health"] = healer_health
    out["watchdog"] = _scheduled_jobs.watchdog_service_status()
    out["healer"] = _scheduled_jobs.healer_service_status()
    if include_scheduler_v2:
        try:
            out["scheduler_v2"] = _sched_v2_surfaces.build_internal_surface_bundle(repo_root=_repo_root_from_webui())
        except Exception as exc:
            out["scheduler_v2"] = {
                "schema_version": _sched_v2_surfaces.SURFACE_BUNDLE_SCHEMA_VERSION,
                "status": "blocked",
                "error": f"{type(exc).__name__}: {exc}",
                "surfaces": {"api": [], "watch": [], "web": [], "mobile": []},
                "projection_equivalent": False,
            }
    return out


def _attach_observation_integrity(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    reasons = []
    if bool(out.get("jobs_unavailable")):
        reasons.append("jobs_unavailable")
    if bool(out.get("snapshot_stale")):
        reasons.append("snapshot_stale")
    if bool(out.get("snapshot_pending")):
        reasons.append("snapshot_pending")
    if bool(out.get("snapshot_refreshing")):
        reasons.append("snapshot_refreshing")
    status = "degraded" if reasons else "fresh"
    observation = {
        "schema_version": "scheduled_jobs_observation_integrity_v1",
        "status": status,
        "advisory_only": bool(reasons),
        "reasons": reasons,
        "summary": str(out.get("summary") or "").strip(),
        "last_error": str(out.get("snapshot_last_error") or "").strip(),
    }
    out["observation_integrity"] = observation
    scheduler_v2 = out.get("scheduler_v2") if isinstance(out.get("scheduler_v2"), dict) else None
    if scheduler_v2 is not None:
        scheduler_v2 = dict(scheduler_v2)
        scheduler_v2["observation_advisory"] = dict(observation)
        out["scheduler_v2"] = scheduler_v2
    return out


def _scheduler_mutation_observation_guard(
    *,
    action_id: str,
    job_id: str,
    observation_evidence: Optional[Mapping[str, Any]] = None,
    allow_stale_observation: bool = False,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    payload = _schedules_jobs_payload(repo_root=root)
    observation = dict(payload.get("observation_integrity") or {})
    current_job = next(
        (
            dict(row)
            for row in list(payload.get("jobs") or [])
            if str((row or {}).get("job_id") or "").strip() == str(job_id or "").strip()
        ),
        {},
    )
    evidence = dict(observation_evidence or {})
    evidence_reasons = [str(item).strip() for item in list(evidence.get("reasons") or []) if str(item).strip()]
    evidence_status = str(evidence.get("observation_status") or "").strip().lower()
    evidence_age_s = evidence.get("view_age_s")
    try:
        evidence_age_s = float(evidence_age_s) if evidence_age_s not in (None, "") else None
    except Exception:
        evidence_age_s = None
    evidence_scheduler_status = str(
        evidence.get("scheduler_status")
        or evidence.get("projected_status")
        or evidence.get("job_status")
        or ""
    ).strip()
    current_scheduler_status = str(
        current_job.get("scheduler_status")
        or dict(current_job.get("scheduler_projection_v1") or {}).get("projected_status")
        or ""
    ).strip()

    stale_reasons: List[str] = []
    if str(observation.get("status") or "").strip().lower() == "degraded":
        stale_reasons.extend(str(item).strip() for item in list(observation.get("reasons") or []) if str(item).strip())
    if evidence_status == "degraded":
        stale_reasons.append("delayed_observation_status")
    if evidence_age_s is not None and evidence_age_s > 300.0:
        stale_reasons.append("delayed_observation_window_exceeded")
    if evidence_reasons:
        stale_reasons.extend(f"evidence:{item}" for item in evidence_reasons)
    override_requested = bool(allow_stale_observation)
    has_override_evidence = bool(
        evidence_status
        or evidence_reasons
        or evidence_age_s is not None
        or evidence_scheduler_status
    )
    if override_requested and not has_override_evidence:
        stale_reasons.append("stale_override_missing_evidence")

    contradiction = bool(
        evidence_scheduler_status
        and current_scheduler_status
        and evidence_scheduler_status != current_scheduler_status
    )
    if contradiction:
        stale_reasons.append("stale_action_contradicts_live_truth")

    deduped_reasons: List[str] = []
    seen: set[str] = set()
    for raw in stale_reasons:
        item = str(raw or "").strip()
        if item and item not in seen:
            seen.add(item)
            deduped_reasons.append(item)

    blocked = bool(deduped_reasons) and not bool(allow_stale_observation)
    reason_code = ""
    if override_requested and "stale_override_missing_evidence" in deduped_reasons:
        blocked = True
        reason_code = "stale_override_missing_evidence"
    elif override_requested and bool(deduped_reasons) and str(action_id or "").strip() in {"job.trigger", "job.toggle", "job.postpone"}:
        blocked = True
        reason_code = "stale_override_forbidden_for_action"
    elif blocked:
        if "stale_action_contradicts_live_truth" in deduped_reasons:
            reason_code = "stale_action_contradicts_live_truth"
        elif "stale_override_missing_evidence" in deduped_reasons:
            reason_code = "stale_override_missing_evidence"
        elif "delayed_observation_window_exceeded" in deduped_reasons:
            reason_code = "delayed_observation_window_exceeded"
        else:
            reason_code = "stale_observation_requires_ack"

    return {
        "schema_version": "scheduled_job_mutation_observation_guard_v1",
        "action_id": str(action_id or "").strip(),
        "job_id": str(job_id or "").strip(),
        "allowed": not blocked,
        "reason_code": reason_code,
        "allow_stale_observation": bool(allow_stale_observation),
        "supports_unsupervised_operation": not bool(deduped_reasons),
        "supported_only_if_noticed_within_minutes": 0 if deduped_reasons else None,
        "current_observation": observation,
        "observation_evidence": evidence,
        "current_scheduler_status": current_scheduler_status,
        "evidence_scheduler_status": evidence_scheduler_status,
        "stale_reasons": deduped_reasons,
        "contradiction": contradiction,
    }


def _schedules_jobs_payload(
    *,
    repo_root: Optional[Path] = None,
    wait_timeout_s: float = 8.0,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        payload = _attach_inprocess_scheduler_status(
            _build_schedules_jobs_payload_inprocess(repo_root=root),
            include_scheduler_v2=True,
        )
        payload["snapshot_cached"] = False
        payload["snapshot_age_s"] = 0.0
        payload["snapshot_refreshing"] = False
        payload["snapshot_stale"] = False
        payload["snapshot_pending"] = False
        return _attach_observation_integrity(payload)
    except Exception as exc:
        inprocess_error = f"{type(exc).__name__}: {exc}"
        try:
            payload = _attach_inprocess_scheduler_status(
                _build_schedules_jobs_payload(repo_root=root, timeout_s=wait_timeout_s),
                include_scheduler_v2=True,
            )
            payload["snapshot_cached"] = False
            payload["snapshot_age_s"] = 0.0
            payload["snapshot_refreshing"] = False
            payload["snapshot_stale"] = False
            payload["snapshot_pending"] = False
            payload["snapshot_last_error"] = inprocess_error
            payload["summary"] = "Jobs view served from subprocess fallback after in-process listing failure."
            return _attach_observation_integrity(payload)
        except Exception as fallback_exc:
            payload = _attach_inprocess_scheduler_status(
                {
                    "schema_version": "scheduled_jobs_list_v1",
                    "utc": _now_utc_iso(),
                    "jobs": [],
                    "events": [],
                    "scheduler_projection_contract": _scheduler_projection_contract_payload(),
                },
                include_scheduler_v2=True,
            )
            payload["snapshot_cached"] = False
            payload["snapshot_age_s"] = None
            payload["snapshot_refreshing"] = False
            payload["snapshot_stale"] = True
            payload["snapshot_pending"] = False
            payload["jobs_unavailable"] = True
            payload["snapshot_last_error"] = (
                f"{inprocess_error}; fallback_failed:{type(fallback_exc).__name__}: {fallback_exc}"
            )
            payload["summary"] = "Jobs view is temporarily unavailable; scheduler service status is still reported from the plane."
            return _attach_observation_integrity(payload)


def _lab_assistant_host_health_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the current ADD9 host health, degrading honestly on bootstrap failure."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        return _lab_host.host_health_snapshot(root)
    except Exception as e:
        return {
            "schema_version": "lab_assistant_host_health_v1",
            "utc": _now_utc_iso(),
            "status": "blocked",
            "mode": "blocked_storage_missing",
            "blocked": True,
            "summary": f"ADD9 host baseline bootstrap failed: {e}",
            "reasons": [f"bootstrap_error:{type(e).__name__}"],
            "install_id": "",
            "last_boot_id": "",
            "last_boot_utc": "",
            "last_shutdown_clean": False,
            "restore_receipt_present": False,
            "surfaces": [],
        }


def _bootstrap_lab_assistant_host(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Initialize the ADD9 host baseline for the current server process."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        receipt = _lab_host.bootstrap_host_baseline(root)
        _launch_startup_recovery(root)
        _lab_host.register_clean_shutdown(root)
        return receipt
    except Exception as e:
        return {
            "schema_version": "lab_assistant_host_boot_receipt_v1",
            "boot_id": "",
            "booted_utc": _now_utc_iso(),
            "install_id": "",
            "local_host_id": "",
            "mode": "blocked_storage_missing",
            "status": "blocked",
            "blocked": True,
            "summary": f"ADD9 host baseline bootstrap failed: {e}",
            "reasons": [f"bootstrap_error:{type(e).__name__}"],
            "surfaces": [],
            "restore_receipt_present": False,
            "restore_receipt_path": "",
        }


def _launch_startup_recovery(repo_root: Path) -> None:
    global _STARTUP_RECOVERY_THREAD
    root = Path(repo_root).resolve()

    def _runner() -> None:
        try:
            _control_action_audit.reconcile_after_restart(root, writer="server_startup.control_audit")
            _lab_recovery.reconcile_startup(root, writer="server_startup.recovery")
        except Exception as exc:
            _lab_recovery.record_recovery_failure(
                root,
                writer="server_startup.recovery",
                error=exc,
                phase="startup_reconcile",
            )

    with _STARTUP_RECOVERY_GUARD:
        thread = _STARTUP_RECOVERY_THREAD
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=_runner,
            name="lab-startup-recovery",
            daemon=True,
        )
        _STARTUP_RECOVERY_THREAD = thread
        thread.start()


def _lab_assistant_state_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the 2848 assistant-state snapshot or an explicit blocked payload."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        return _lab_state.read_assistant_state_snapshot(root, initialize_if_missing=False)
    except Exception as e:
        return {
            "schema_version": "lab_assistant_state_snapshot_v1",
            "utc": _now_utc_iso(),
            "ok": False,
            "error": str(e),
            "assistant_state": None,
            "host_health": _lab_assistant_host_health_payload(repo_root=root),
            "precedence_samples": {},
        }


def _lab_assistant_task_register_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the BD05 task-register snapshot or an explicit blocked payload."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        return _lab_task_register.read_task_register_snapshot(root, initialize_if_missing=False)
    except Exception as e:
        return {
            "schema_version": "lab_assistant_task_register_snapshot_v1",
            "utc": _now_utc_iso(),
            "ok": False,
            "error": str(e),
            "task_register": None,
            "summary_cache": None,
            "assistant_state": _lab_assistant_state_payload(repo_root=root).get("assistant_state"),
            "host_health": _lab_assistant_host_health_payload(repo_root=root),
        }


def _lab_assistant_diagnostic_snapshot_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    missing_snapshot = _missing_lab_assistant_operator_snapshot(root)
    if missing_snapshot is not None:
        return missing_snapshot
    try:
        return _lab_operator_snapshot.build_operator_snapshot(root)
    except Exception as e:
        return {
            "schema_version": _lab_operator_snapshot.SNAPSHOT_SCHEMA_VERSION,
            "generated_utc": _now_utc_iso(),
            "error": str(e),
            "freshness": {"stale": True, "stale_sources": ["operator_snapshot_error"]},
            "operator_summary": {"truth_conflicts": []},
        }


def _missing_lab_assistant_operator_snapshot(repo_root: Path) -> Optional[Dict[str, Any]]:
    root = Path(repo_root).resolve()
    lab_runtime_root = root / "runtime" / "state" / "lab_assistant"
    assistant_state_path = root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_state.json"
    task_register_path = root / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
    if lab_runtime_root.exists():
        return None
    if assistant_state_path.exists() or task_register_path.exists():
        return None
    return {
        "schema_version": _lab_operator_snapshot.SNAPSHOT_SCHEMA_VERSION,
        "generated_utc": _now_utc_iso(),
        "operator_state": "idle",
        "assistant_state": None,
        "task_register": None,
        "active_tasks": [],
        "completed_tasks": [],
        "residue_tasks": [],
        "open_loops": [],
        "delegated_work": [],
        "freshness": {"stale": True, "stale_sources": ["canonical_state_missing"]},
        "operator_summary": {"truth_conflicts": []},
        "error": "lab assistant canonical state is not initialized",
    }


def _attach_diagnostic_metadata(
    payload: Dict[str, Any],
    *,
    surface_id: str,
    snapshot_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    diagnostic_payload = dict(payload)
    snapshot = snapshot_payload if snapshot_payload is not None else _lab_assistant_diagnostic_snapshot_payload()
    diagnostic_payload["diagnostic_boundary"] = _lab_status_diag.diagnostic_boundary_snapshot()
    diagnostic_payload["diagnostic_surface"] = _lab_status_diag.diagnostic_surface(surface_id)
    diagnostic_payload["diagnostic_mismatch"] = _lab_status_diag.diagnostic_mismatch(
        surface_id,
        diagnostic_payload=payload,
        snapshot_payload=snapshot,
    )
    return diagnostic_payload


def _lab_assistant_delegation_policy_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the 01A2 answer-vs-delegate policy snapshot."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        return _lab_delegate_policy.delegation_policy_snapshot(repo_root=root)
    except Exception as e:
        return {
            "schema_version": _lab_delegate_policy.POLICY_SCHEMA_VERSION,
            "utc": _now_utc_iso(),
            "error": str(e),
            "allowed_surfaces": [],
            "supported_classifications": [],
            "next_decision_kinds": [],
            "notes": [],
        }


def _lab_assistant_operator_snapshot_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the 2DB7 operator-visible assistant truth projection."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    missing_snapshot = _missing_lab_assistant_operator_snapshot(root)
    if missing_snapshot is not None:
        return missing_snapshot
    snapshot = _lab_operator_snapshot.build_operator_snapshot(root)
    if _operator_snapshot_needs_self_heal(snapshot):
        _self_heal_operator_snapshot(root)
        snapshot = _lab_operator_snapshot.build_operator_snapshot(root)
    return snapshot


def _lab_assistant_resource_concurrency_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the durable shared-resource concurrency projection for admin/operator surfaces."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    return _resource_claim_ledger.resource_concurrency_snapshot(
        repo_root=root,
        runtime_policy=_scheduled_jobs.current_runtime_resource_policy(repo_root=root),
    )


def _operator_snapshot_needs_self_heal(snapshot: Optional[Dict[str, Any]]) -> bool:
    conflicts = list((snapshot or {}).get("truth_conflicts") or [])
    return any(str(row.get("kind") or "").strip().lower() == "stale_task_register_with_open_work" for row in conflicts if isinstance(row, dict))


def _self_heal_operator_snapshot(repo_root: Path) -> None:
    writer = "operator_snapshot.self_heal"
    _lab_recovery.reconcile_startup(repo_root, writer=f"{writer}.recovery")
    _lab_task_register.rebuild_summary_cache(repo_root, writer=f"{writer}.summary_cache")


def _lab_assistant_delegation_proof_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the latest derived G7B1 delegated execution proof report."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    return _lab_execution_proof.read_latest_report(root)


def _lab_assistant_execution_truth_ownership_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the M9R2 execution truth ownership table."""
    _ = Path(repo_root or _repo_root_from_webui()).resolve()
    return _lab_execution_truth_ownership.ownership_snapshot()


def _lab_assistant_backend_api_boundary_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the L8Q1 backend API boundary contract."""
    _ = Path(repo_root or _repo_root_from_webui()).resolve()
    return _backend_api_boundary.boundary_snapshot()


def _lab_assistant_split_topology_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the ABCD split-topology contract snapshot."""
    _ = Path(repo_root or _repo_root_from_webui()).resolve()
    return _split_topology_contract.split_topology_snapshot()


def _lab_assistant_full_framework_scenarios_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the V4N8 full-framework scenario catalog."""
    _ = Path(repo_root or _repo_root_from_webui()).resolve()
    return _full_framework_scenarios.full_framework_scenarios_snapshot()


def _backend_boundary_http_status(result: Dict[str, Any]) -> HTTPStatus:
    status = str((result or {}).get("status") or "").strip().lower()
    reason = str((result or {}).get("reason_code") or "").strip().lower()
    if status in {"accepted", "duplicate_suppressed"}:
        return HTTPStatus.OK
    if status == "ambiguous_timeout":
        return HTTPStatus.GATEWAY_TIMEOUT
    if status == "backend_unavailable":
        return HTTPStatus.SERVICE_UNAVAILABLE
    if status == "rejected":
        if reason == "idempotency_conflict":
            return HTTPStatus.CONFLICT
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.BAD_REQUEST


def _process_boundary_mutation(
    *,
    command_id: str,
    payload: Dict[str, Any],
    request_id: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    return _backend_api_boundary.process_command_request(
        {
            "request_id": str(request_id or "").strip(),
            "command_id": str(command_id or "").strip(),
            "idempotency_key": str(idempotency_key or "").strip(),
            "payload": dict(payload or {}),
        },
        repo_root=_repo_root_from_webui(),
        writer=f"webui.route.{command_id}",
    )


def _boundary_ambiguous_timeout_result(
    *,
    command_id: str,
    request_id: str,
    idempotency_key: str,
    accepted_result: Dict[str, Any],
) -> Dict[str, Any]:
    out = _backend_api_boundary.command_result(
        request_id=request_id,
        command_id=command_id,
        status=_backend_api_boundary.COMMAND_STATUS_AMBIGUOUS_TIMEOUT,
        reason_code=_backend_api_boundary.REASON_AMBIGUOUS_TIMEOUT,
        summary="Transport timeout after boundary acceptance; command outcome is uncertain to the caller.",
        extra={
            "retry_owner": "caller_retry_same_idempotency_key",
            "idempotency_key": str(idempotency_key or "").strip(),
            "request_identity_hash": str((accepted_result or {}).get("request_identity_hash") or "").strip(),
            "idempotency_keys": list((accepted_result or {}).get("idempotency_keys") or []),
        },
    )
    out["ok"] = False
    return out


def _lab_assistant_self_check_payload(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return D2K3 self-check snapshot, fail-closed on unexpected errors."""
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        return _lab_self_check.refresh_default_checks_if_needed(root)
    except Exception as e:
        return {
            "schema_version": "lab_assistant_self_check_v1",
            "utc": _now_utc_iso(),
            "required_checks": [],
            "checks": {},
            "mode": "self_check_unknown",
            "status": "degraded",
            "reasons": [f"error:{type(e).__name__}"],
        }


def _mobile_sessions_startup_max_bytes() -> int:
    """Return the largest mobile session cache file we will load during startup."""
    raw = str(os.environ.get("EIEL_MOBILE_SESSIONS_STARTUP_MAX_BYTES", str(32 * 1024 * 1024)) or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = 32 * 1024 * 1024
    return max(1 * 1024 * 1024, min(value, 512 * 1024 * 1024))


def _mobile_history_window_rows() -> int:
    """Bound UI/mobile bootstrap history windows to keep giant transcripts usable."""
    raw = str(os.environ.get("EIEL_MOBILE_HISTORY_WINDOW_ROWS", "400") or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = 400
    return max(50, min(value, 4000))


def _mobile_sessions_import_legacy_enabled() -> bool:
    """Return whether startup may import mobile session caches from legacy locations.

    Legacy import stays enabled by default so older mobile session caches are
    reconciled into the canonical runtime path during startup. Operators may
    still disable it explicitly via EIEL_MOBILE_SESSIONS_IMPORT_LEGACY=0.
    """
    raw = str(os.environ.get("EIEL_MOBILE_SESSIONS_IMPORT_LEGACY", "1") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

def _mobile_session_file_loadable_at_startup(p: Path) -> bool:
    """Keep derived mobile cache files from blocking server bind when they balloon."""
    try:
        if not p.exists() or (not p.is_file()):
            return False
        size_bytes = int(p.stat().st_size)
    except Exception:
        return False
    limit_bytes = _mobile_sessions_startup_max_bytes()
    if size_bytes <= limit_bytes:
        return True
    try:
        sys.stderr.write(
            "[webui] WARNING: skipping oversized mobile session cache during startup: "
            f"{p} size={size_bytes} limit={limit_bytes}\n"
        )
    except Exception:
        pass
    return False


def _mobile_is_cancel_race_label(label: str) -> bool:
    return str(label or "").strip().lower() == _MOBILE_CANCEL_RACE_LABEL.lower()


def _extract_swarm_objective(text: str) -> str:
    match = _SWARM_TRIGGER_RE.match(str(text or ""))
    if match is None:
        return ""
    return str(match.group("objective") or "").strip()


def _extract_benchmark_objective(text: str) -> str:
    match = _BENCHMARK_TRIGGER_RE.match(str(text or ""))
    if match is None:
        return ""
    return str(match.group("objective") or "").strip()


def _bound_chat_history_for_objective(*, binding: Dict[str, str], tail: int = 80) -> List[Dict[str, str]]:
    """Return the most relevant chat history available for objective reuse."""
    normalized = _coerce_agents_binding(binding)
    if str(normalized.get("type") or "").strip().lower() == "task_id":
        tid = _coerce_mobile_task_id(normalized.get("id"))
        try:
            agent_rows = _agents.session_history_rows(role=_mobile_role_for_thread_id(tid), binding=normalized, tail=tail)
        except Exception:
            agent_rows = []
        if agent_rows:
            return agent_rows
        return _mobile_effective_history(tid, tail=tail)
    try:
        return _agents.session_history_rows(role="generalist", binding=normalized, tail=tail)
    except Exception:
        return []


def _latest_reusable_user_objective(history: List[Dict[str, str]]) -> str:
    """Pick the latest concrete user task and skip swarm/benchmark trigger commands."""
    for row in reversed(history):
        if str(row.get("role") or "").strip().lower() != "user":
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        if _BENCHMARK_TRIGGER_RE.match(content) or _SWARM_TRIGGER_RE.match(content):
            continue
        return content
    return ""


def _resolve_bound_benchmark_objective(*, binding: Dict[str, str], explicit_objective: str) -> str:
    explicit = str(explicit_objective or "").strip()
    if explicit:
        return explicit
    return _latest_reusable_user_objective(_bound_chat_history_for_objective(binding=binding, tail=80))


def _bound_benchmark_missing_objective_error() -> str:
    """Keep the API failure text plain when a bound chat has no reusable task."""
    return "No earlier user task was found for this binding. Provide `objective` to start the benchmark."


def _bound_benchmark_missing_objective_chat_text() -> str:
    """Tell chat users how to recover without describing server internals."""
    return "No earlier user task was found for this chat. Say `Benchmark it: <objective>` to provide one directly."


def _mobile_effective_history_status(*, status: str, label: str) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {"", "ok", "complete"} and _mobile_is_cancel_race_label(label):
        return "interrupted"
    return normalized_status


def _coerce_agents_binding(raw_binding: Any, *, raw_thread_id: Any = None) -> Dict[str, str]:
    """Accept missing/legacy binding payloads and coerce to a valid task binding."""
    if isinstance(raw_binding, dict):
        return raw_binding  # validated later by server_agents
    tid = str(raw_thread_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{3}", tid or ""):
        tid = "mbl"
    return {"type": "task_id", "id": tid}


def _coerce_mobile_task_id(raw_thread_id: Any) -> str:
    tid = str(raw_thread_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{3}", tid or ""):
        tid = "mbl"
    return tid


def _coerce_mobile_agent_key(raw_agent_key: Any) -> str:
    key = str(raw_agent_key or "a").strip().lower()
    if key not in _MOBILE_AGENT_SPECS:
        raise ValueError("agent_key must be one of: p, a, a2, a3, o, b, v")
    return key


def _mobile_agent_spec(agent_key: str) -> Dict[str, str]:
    key = _coerce_mobile_agent_key(agent_key)
    spec = _MOBILE_AGENT_SPECS.get(key) or _MOBILE_AGENT_SPECS["a"]
    return dict(spec)


def _mobile_known_thread_ids() -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for spec in _MOBILE_AGENT_SPECS.values():
        tid = str(spec.get("thread_id") or "").strip().lower()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def _mobile_agent_default_header_title(agent_key: str) -> str:
    key = _coerce_mobile_agent_key(agent_key)
    if key == "p":
        return "PLANE"
    if key in {"a", "a2", "a3"}:
        return "AGT"
    if key == "o":
        return "ORCH"
    if key == "b":
        return "BLD"
    if key == "v":
        return "VER"
    return "AGT"


def _mobile_agent_supports_topic_title(agent_key: str) -> bool:
    return _coerce_mobile_agent_key(agent_key) in {"a", "a2", "a3"}


def _mobile_topic_title_from_session(*, role: str, tid: str) -> str:
    try:
        session = _agents.read_session(
            role=role,
            binding={"type": "task_id", "id": str(tid)},
            tail=120,
            inbox_tail=0,
        )
    except Exception:
        return ""
    rows = session.get("session") if isinstance(session.get("session"), list) else []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if str(row.get("sender") or "").strip().lower() != "assistant":
            continue
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        normalized = _agents._normalize_chat_header_title(str(meta.get("chat_header_title") or ""))  # type: ignore[attr-defined]
        if normalized:
            return str(normalized)
    return ""


def _mobile_tab_topic_identity(*, agent_key: str, tid: str, role: str) -> Dict[str, str]:
    default_title = _mobile_agent_default_header_title(agent_key)
    if not _mobile_agent_supports_topic_title(agent_key):
        return {
            "header_title": default_title,
            "header_title_source": "fixed_tab",
            "default_header_title": default_title,
        }
    topic_title = _mobile_topic_title_from_session(role=role, tid=tid)
    if topic_title:
        return {
            "header_title": topic_title,
            "header_title_source": "agent_marker",
            "default_header_title": default_title,
        }
    return {
        "header_title": default_title,
        "header_title_source": "default_tab",
        "default_header_title": default_title,
    }


def _mobile_shell_navigation_payload(*, agent_key: str, tid: str, role: str) -> Dict[str, Any]:
    binding = {"type": "task_id", "id": str(tid)}
    context = {
        "binding": binding,
        "agent_key": str(agent_key),
        "thread_id": str(tid),
        "role": str(role),
    }
    return {
        "context": context,
        "surfaces": {
            "chat": {
                "path": "/mobile",
                "query": {"agent_key": str(agent_key), "thread_id": str(tid)},
                "context_passthrough": context,
            },
            "backlog": {
                "path": "/mobile/backlog",
                "query": {"agent_key": str(agent_key), "thread_id": str(tid)},
                "context_passthrough": context,
            },
            "execution": {
                "path": "/mobile/backlog",
                "query": {"agent_key": str(agent_key), "thread_id": str(tid), "pane": "execution_queue"},
                "context_passthrough": context,
            },
            "audit": {
                "path": "/agents",
                "query": {"agent_key": str(agent_key), "thread_id": str(tid)},
                "context_passthrough": context,
            },
        },
    }


def _mobile_assistant_shell_payload(*, selected_agent_key: str, selected_tid: str) -> Dict[str, Any]:
    visible_keys = ("a", "a2", "a3", "o", "b", "v")
    selected_key = _coerce_mobile_agent_key(selected_agent_key)
    if selected_key not in visible_keys:
        selected_key = "a"
    selected_spec = _mobile_agent_spec(selected_key)
    selected_tid_norm = _coerce_mobile_task_id(selected_tid or selected_spec.get("thread_id") or "mbl")
    selected_role = str(selected_spec.get("role") or "generalist").strip().lower() or "generalist"
    tabs: List[Dict[str, Any]] = []
    for key in visible_keys:
        spec = _mobile_agent_spec(key)
        role = str(spec.get("role") or "generalist").strip().lower() or "generalist"
        tid = _coerce_mobile_task_id(spec.get("thread_id") or "mbl")
        if key == selected_key:
            tid = selected_tid_norm
            role = selected_role
        topic = _mobile_tab_topic_identity(agent_key=key, tid=tid, role=role)
        tabs.append(
            {
                "agent_key": key,
                "tab_label": str(key).upper(),
                "thread_id": tid,
                "role": role,
                "selected": bool(key == selected_key),
                "topic": topic,
                "binding": {"type": "task_id", "id": tid},
            }
        )
    snapshot = _lab_assistant_operator_snapshot_payload()
    identity = snapshot.get("assistant_identity") if isinstance(snapshot.get("assistant_identity"), dict) else {}
    backend_projection = snapshot.get("backend_projection") if isinstance(snapshot.get("backend_projection"), dict) else {}
    upstream_truth = {
        "operator_state": str(snapshot.get("operator_state") or "unknown").strip().lower() or "unknown",
        "backend_condition": str(backend_projection.get("state") or "unknown").strip().lower() or "unknown",
        "summary": str(snapshot.get("summary") or "").strip(),
    }
    return {
        "schema_version": "mobile_assistant_shell_v1",
        "utc": _now_utc_iso(),
        "assistant_identity": {
            "assistant_id": str(identity.get("assistant_id") or "").strip(),
            "assistant_label": str(identity.get("assistant_label") or _lab_state.default_assistant_label()).strip()
            or _lab_state.default_assistant_label(),
        },
        "one_face_shell": {
            "assistant_label": str(identity.get("assistant_label") or _lab_state.default_assistant_label()).strip()
            or _lab_state.default_assistant_label(),
            "tab_chips": [tab["tab_label"] for tab in tabs],
            "selected_agent_key": selected_key,
            "selected_thread_id": selected_tid_norm,
        },
        "upstream_truth": upstream_truth,
        "tabs": tabs,
        "navigation": _mobile_shell_navigation_payload(agent_key=selected_key, tid=selected_tid_norm, role=selected_role),
    }


def _default_mobile_chat_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "mobile.chat_turn_timeout_s",
        3600,
        env_names=("EIEL_MOBILE_CHAT_TIMEOUT_S", "EIEL_CHAT_TURN_TIMEOUT_S", "EIEL_CHAT_TIMEOUT_S"),
        minimum=30,
        maximum=6 * 60 * 60,
    )


def _default_mobile_chat_idle_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "mobile.chat_idle_timeout_s",
        600,
        env_names=("EIEL_CHAT_IDLE_TIMEOUT_S",),
        minimum=30,
        maximum=6 * 60 * 60,
    )


def _default_mobile_workflow_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "mobile.workflow_timeout_s",
        1800,
        env_names=("EIEL_MOBILE_WORKFLOW_TIMEOUT_S",),
        minimum=30,
        maximum=6 * 60 * 60,
    )


def _default_mobile_chat_model() -> str:
    return _runtime_policy.get_runtime_policy_text(
        "mobile.chat_model",
        "gpt-5.4",
        env_names=("EIEL_MOBILE_CHAT_MODEL", "EIEL_MOBILE_MODEL"),
    )


def _default_mobile_chat_reasoning_effort(*, model: str = "") -> str:
    raw = _runtime_policy.get_runtime_policy_text(
        "mobile.chat_reasoning_effort",
        "medium",
        env_names=("EIEL_MOBILE_CHAT_REASONING_EFFORT", "EIEL_MOBILE_REASONING_EFFORT"),
    )
    try:
        return _scheduled_jobs._normalize_reasoning_effort(
            raw,
            model=str(model or _default_mobile_chat_model() or "").strip(),
            field_name="mobile.chat_reasoning_effort",
        ) or "medium"
    except ValueError:
        return "medium"


def _default_mobile_chat_fallback_models() -> List[str]:
    raw = _runtime_policy.get_runtime_policy_text(
        "mobile.chat_fallback_models",
        "gpt-5.4-mini,gpt-5.2-codex,gpt-5.2,gpt-5.1-codex-max,gpt-5.1-codex-mini",
        env_names=("EIEL_MOBILE_CHAT_FALLBACK_MODELS", "EIEL_MOBILE_FALLBACK_MODELS"),
    )
    out: List[str] = []
    for part in str(raw or "").split(","):
        txt = str(part or "").strip()
        if txt and txt not in out:
            out.append(txt)
    return out


def _mobile_runtime_policy_payload() -> Dict[str, Any]:
    return {
        "chat_turn_timeout_s": int(_default_mobile_chat_timeout_s()),
        "chat_idle_timeout_s": int(_default_mobile_chat_idle_timeout_s()),
        "workflow_timeout_s": int(_default_mobile_workflow_timeout_s()),
        "chat_model": str(_default_mobile_chat_model() or "").strip(),
        "chat_reasoning_effort": str(_default_mobile_chat_reasoning_effort() or "").strip(),
        "chat_fallback_models": list(_default_mobile_chat_fallback_models()),
    }


_PLANE_MOBILE_QUEUE_RE = re.compile(
    r"\b(how many|what(?:'s| is)?|show|tell me)\b.*\b(queue|queued|jobs?)\b|\bjobs?\b.*\b(queue|queued)\b",
    re.IGNORECASE,
)
_PLANE_MOBILE_NEXT_JOB_RE = re.compile(
    r"\b(next|what(?:'s| is)? next)\b.*\b(scheduled job|job to execute|job to run|job)\b",
    re.IGNORECASE,
)
_PLANE_MOBILE_HEALTH_RE = re.compile(
    r"^(?:ping|test)\s*$|\b(plane is responding|server is online|server ok|all is ok|healthy)\b",
    re.IGNORECASE,
)
_PLANE_MOBILE_SCHEDULE_JOB_RE = re.compile(
    r"^\s*(?:create|schedule)\s+(?:a\s+)?(?:pipeline\s+|scheduled\s+)?job\b",
    re.IGNORECASE,
)
_PLANE_MOBILE_VERIFICATION_WORKFLOW_RE = re.compile(
    r"^\s*(?:launch|create|schedule)\s+(?:a\s+)?verification\s+workflow\b",
    re.IGNORECASE,
)
_PLANE_ROUTE_DIRECT_STATUS = "direct_status_answer"
_PLANE_ROUTE_SCHEDULE_JOB = "typed_mutation_schedule_job"
_PLANE_ROUTE_VERIFICATION_WORKFLOW = "typed_mutation_verification_workflow"
_PLANE_ROUTE_BACKEND_SEND_TURN = "backend_boundary_send_turn"


def _plane_mobile_extract_json_block(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("missing JSON payload")
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("expected a JSON object after the command")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return dict(payload)


def _plane_mobile_schedule_job_mutation(prompt: str) -> Optional[Dict[str, Any]]:
    text = str(prompt or "").strip()
    if not _PLANE_MOBILE_SCHEDULE_JOB_RE.match(text):
        return None
    try:
        payload = _plane_mobile_extract_json_block(text)
        run_now = bool(payload.pop("run_now", False))
        if not isinstance(payload.get("binding"), dict):
            binding_type = str(payload.pop("binding_type", "") or "").strip()
            binding_id = str(payload.pop("binding_id", "") or "").strip()
            if binding_type or binding_id:
                payload["binding"] = {"type": binding_type or "ledger_uid", "id": binding_id}
        if not isinstance(payload.get("schedule"), dict):
            schedule_mode = str(payload.pop("schedule_mode", "") or "").strip()
            if schedule_mode:
                schedule: Dict[str, Any] = {"mode": schedule_mode}
                for src_key, dst_key in (
                    ("run_at", "run_at"),
                    ("run_at_utc", "run_at"),
                    ("time_of_day", "time_of_day"),
                    ("timezone", "timezone"),
                    ("every_minutes", "every_minutes"),
                    ("start_at_utc", "start_at_utc"),
                ):
                    if src_key in payload:
                        schedule[dst_key] = payload.pop(src_key)
                payload["schedule"] = schedule
        if not isinstance(payload.get("runtime"), dict):
            runtime: Dict[str, Any] = {}
            for src_key, dst_key in (
                ("executor_agent_tech", "executor_agent_tech"),
                ("executor", "executor_agent_tech"),
                ("pipeline_executor_mode", "pipeline_executor_mode"),
                ("pipeline_mode", "pipeline_executor_mode"),
                ("model", "model"),
            ):
                if src_key in payload:
                    runtime[dst_key] = payload.pop(src_key)
            if runtime:
                payload["runtime"] = runtime
        if "max_iterations" in payload and not isinstance(payload.get("loop"), dict):
            payload["loop"] = {"max_iterations": int(payload.pop("max_iterations") or 0)}
        if str(payload.get("job_kind") or "").strip().lower() == "build_verify_loop":
            payload.setdefault("loop", {"max_iterations": 4})
        saved = _scheduled_jobs.save_job(payload)
        job_id = str(saved.get("job_id") or "").strip()
        display = str(saved.get("display_title") or job_id or "scheduled job").strip()
        status_bits = [
            f"Created scheduled job `{job_id}` ({display}).",
            f"kind={str(saved.get('job_kind') or '').strip() or 'single_turn'}",
        ]
        runtime = saved.get("runtime") if isinstance(saved.get("runtime"), dict) else {}
        schedule = saved.get("schedule") if isinstance(saved.get("schedule"), dict) else {}
        executor = str(runtime.get("executor_agent_tech") or "codex").strip() or "codex"
        model = str(runtime.get("model") or "").strip()
        pipeline_mode = str(runtime.get("pipeline_executor_mode") or "").strip()
        status_bits.append(f"executor={executor}")
        if pipeline_mode:
            status_bits.append(f"pipeline_mode={pipeline_mode}")
        if model:
            status_bits.append(f"model={model}")
        if str(schedule.get("mode") or "").strip():
            status_bits.append(f"schedule={str(schedule.get('mode') or '').strip()}")
        run_payload: Dict[str, Any] = {}
        if run_now:
            run_payload = _scheduled_jobs.request_run_now(job_id, request_source="plane_mobile_prompt")
            if bool(run_payload.get("ok")) and bool(run_payload.get("queued")):
                status_bits.append("run_now=queued")
            else:
                status_bits.append(
                    f"run_now_failed={str(run_payload.get('reason') or 'unknown').strip() or 'unknown'}"
                )
        return {
            "route": _PLANE_ROUTE_SCHEDULE_JOB,
            "reply": " ".join(status_bits),
            "applied": True,
            "backend": "scheduler_mutation",
            "job_id": job_id,
            "saved_job": dict(saved),
            "run_now": run_payload,
        }
    except Exception as exc:
        return {
            "route": _PLANE_ROUTE_SCHEDULE_JOB,
            "reply": (
                "To create a scheduled job from the plane, send `create job` followed by a JSON object with "
                "`job_id`, `job_kind`, `binding`, `schedule`, `prompt`, and `runtime`. "
                f"Error: {str(exc).strip() or 'invalid payload'}"
            ),
            "applied": False,
            "backend": "scheduler_mutation",
            "job_id": "",
            "saved_job": {},
            "run_now": {},
        }


def _plane_mobile_verification_workflow_mutation(prompt: str) -> Optional[Dict[str, Any]]:
    text = str(prompt or "").strip()
    if not _PLANE_MOBILE_VERIFICATION_WORKFLOW_RE.match(text):
        return None
    try:
        payload = _plane_mobile_extract_json_block(text)
        run_now = bool(payload.pop("run_now", False))
        launch = _verification_workflow_launch.create_scheduler_backed_verification_launch(
            payload,
            repo_root=_repo_root_from_webui(),
            run_now=run_now,
        )
        durable_request = dict(launch.get("durable_request") or {})
        scheduled_job = dict(launch.get("scheduled_job") or {})
        run_payload = dict(launch.get("run_now") or {})
        request_id = str(durable_request.get("request_id") or "").strip()
        request_fingerprint = str(durable_request.get("request_fingerprint") or "").strip()
        job_id = str(scheduled_job.get("job_id") or "").strip()
        status_bits = [
            f"Queued verification workflow `{request_id or 'request'}`.",
            f"fingerprint={request_fingerprint or 'unknown'}",
        ]
        if job_id:
            status_bits.append(f"scheduled_job={job_id}")
        if run_now:
            if bool(run_payload.get('ok')) and bool(run_payload.get('queued')):
                status_bits.append("run_now=queued")
            else:
                status_bits.append(
                    f"run_now_failed={str(run_payload.get('reason') or 'unknown').strip() or 'unknown'}"
                )
        return {
            "route": _PLANE_ROUTE_VERIFICATION_WORKFLOW,
            "reply": " ".join(status_bits),
            "applied": True,
            "backend": "verification_workflow_mutation",
            "request_id": request_id,
            "request_fingerprint": request_fingerprint,
            "job_id": job_id,
            "launch_result": launch,
            "run_now": run_payload,
        }
    except Exception as exc:
        return {
            "route": _PLANE_ROUTE_VERIFICATION_WORKFLOW,
            "reply": (
                "To launch a verification workflow from the plane, send `launch verification workflow` "
                "followed by a JSON object with `request_id`, `verification_goal`, "
                "`requested_output_mode`, `selected_block_summary`, and `phase_b_anchor_payload`. "
                f"Error: {str(exc).strip() or 'invalid payload'}"
            ),
            "applied": False,
            "backend": "verification_workflow_mutation",
            "request_id": "",
            "request_fingerprint": "",
            "job_id": "",
            "launch_result": {},
            "run_now": {},
        }


def _plane_mobile_schedule_job_reply(prompt: str) -> Optional[str]:
    mutation = _plane_mobile_schedule_job_mutation(prompt)
    if mutation is None:
        return None
    return str(mutation.get("reply") or "")


def _plane_mobile_route_prompt(prompt: str) -> Dict[str, Any]:
    direct_reply = _plane_mobile_direct_reply(prompt)
    if direct_reply is not None:
        return {
            "kind": "direct_reply",
            "route": _PLANE_ROUTE_DIRECT_STATUS,
            "reply": direct_reply,
            "applied": False,
            "backend": "direct_status",
        }
    mutation = _plane_mobile_schedule_job_mutation(prompt)
    if mutation is not None:
        return {
            "kind": "mutation",
            "route": _PLANE_ROUTE_SCHEDULE_JOB,
            "reply": str(mutation.get("reply") or ""),
            "applied": bool(mutation.get("applied")),
            "backend": str(mutation.get("backend") or "scheduler_mutation").strip() or "scheduler_mutation",
        }
    mutation = _plane_mobile_verification_workflow_mutation(prompt)
    if mutation is not None:
        return {
            "kind": "mutation",
            "route": _PLANE_ROUTE_VERIFICATION_WORKFLOW,
            "reply": str(mutation.get("reply") or ""),
            "applied": bool(mutation.get("applied")),
            "backend": str(mutation.get("backend") or "verification_workflow_mutation").strip()
            or "verification_workflow_mutation",
        }
    return {
        "kind": "codex",
        "route": _PLANE_ROUTE_BACKEND_SEND_TURN,
        "reply": "",
        "applied": False,
        "backend": "codex",
    }


def _plane_mobile_direct_reply(prompt: str) -> Optional[str]:
    text = str(prompt or "").strip()
    if not text:
        return None
    wants_health = bool(_PLANE_MOBILE_HEALTH_RE.search(text))
    wants_queue = bool(_PLANE_MOBILE_QUEUE_RE.search(text))
    wants_next = bool(_PLANE_MOBILE_NEXT_JOB_RE.search(text))
    if wants_health and not wants_queue and not wants_next:
        snapshot = _lab_assistant_operator_snapshot_payload()
        operator_state = str(snapshot.get("operator_state") or "unknown").strip().lower() or "unknown"
        active_task_count = int(len(snapshot.get("active_tasks") or []))
        open_loop_count = int(len(snapshot.get("open_loops") or []))
        backend_projection = snapshot.get("backend_projection") if isinstance(snapshot.get("backend_projection"), dict) else {}
        backend_state = str(backend_projection.get("state") or "unknown").strip().lower() or "unknown"
        recovery_status = str(backend_projection.get("recovery_status") or "unknown").strip().lower() or "unknown"
        return (
            "Plane is responding.\n\n"
            "Current durable state says:\n"
            f"- assistant: `{operator_state}`\n"
            f"- active tasks: `{active_task_count}`\n"
            f"- open loops: `{open_loop_count}`\n"
            f"- backend: `{backend_state}`\n"
            f"- reload status: `{recovery_status}`"
        )
    if not wants_queue and not wants_next:
        return None

    jobs = list(_scheduled_jobs.list_jobs())
    summary = _plane_mobile_scheduler_summary_payload(jobs)
    total_jobs = int(summary.get("total_jobs") or 0)
    enabled_count = int(summary.get("enabled_jobs") or 0)
    running_count = int(summary.get("running_jobs") or 0)
    blocked_count = int(summary.get("blocked_jobs") or 0)
    next_projection = summary.get("next_projection") if isinstance(summary.get("next_projection"), dict) else None
    next_job = summary.get("next_job") if isinstance(summary.get("next_job"), dict) else None
    next_due_utc = "" if next_projection is None else str(next_projection.get("next_due_utc") or "").strip()
    next_bucket = "" if next_projection is None else str(next_projection.get("next_candidate_bucket") or "").strip()

    if wants_next and not wants_queue:
        if next_projection is None or next_job is None:
            return (
                f"There is no next scheduled job to execute right now. "
                f"The scheduler inbox has {total_jobs} job specs, but 0 enabled jobs."
            )
        display = str(next_job.get("display_title") or next_job.get("job_id") or "scheduled job").strip()
        run_at = next_due_utc or str(((next_job.get("schedule") or {}).get("run_at") or "")).strip()
        if next_bucket == "runnable_now":
            return (
                f"The next scheduled job is `{str(next_job.get('job_id') or '').strip()}`"
                f" ({display}). It is runnable now ({str(next_projection.get('due_reason') or '').strip()})."
            )
        return (
            f"The next scheduled job is `{str(next_job.get('job_id') or '').strip()}`"
            f" ({display}). It is set to run at {run_at}."
        )

    if next_projection is None or next_job is None:
        return (
            f"There are {total_jobs} job specs in the scheduler inbox. "
            f"{enabled_count} are enabled, {running_count} are running, and {blocked_count} are blocked. "
            "No enabled job is currently queued to run next."
        )

    display = str(next_job.get("display_title") or next_job.get("job_id") or "scheduled job").strip()
    run_at = next_due_utc or str(((next_job.get("schedule") or {}).get("run_at") or "")).strip()
    return (
        f"There are {total_jobs} job specs in the scheduler inbox. "
        f"{enabled_count} are enabled, {running_count} are running, and {blocked_count} are blocked. "
        f"The next enabled job would be `{str(next_job.get('job_id') or '').strip()}` ({display}) at {run_at}."
    )


def _plane_mobile_scheduler_summary_payload(jobs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [dict(row) for row in jobs]
    enabled_jobs = [row for row in rows if bool(row.get("enabled"))]
    projected_rows = [_plane_mobile_scheduler_projection(row) for row in rows]
    running_jobs = [row for row in projected_rows if str(row.get("projected_status") or "").strip().lower() == "active"]
    blocked_jobs = [row for row in projected_rows if str(row.get("projected_status") or "").strip().lower() == "blocked"]
    next_projection = _scheduler_projection_contract.select_next_scheduler_job(enabled_jobs)
    next_job = None if next_projection is None else next_projection.get("source_row")
    return {
        "schema_version": "plane_mobile_scheduler_summary_v1",
        "total_jobs": len(rows),
        "enabled_jobs": len(enabled_jobs),
        "running_jobs": len(running_jobs),
        "blocked_jobs": len(blocked_jobs),
        "projected_rows": projected_rows,
        "next_projection": None if next_projection is None else dict(next_projection),
        "next_job": None if not isinstance(next_job, dict) else dict(next_job),
    }


def _plane_mobile_scheduler_projection(row: Mapping[str, Any]) -> Dict[str, Any]:
    existing = row.get("scheduler_projection_v1")
    if isinstance(existing, dict) and str(existing.get("projected_status") or "").strip():
        return dict(existing)
    return _scheduler_projection_contract.project_scheduler_job_row(row)


def _refresh_live_runtime_snapshot() -> Dict[str, Any]:
    """Apply live policy-backed server settings without a process restart."""
    getattr(_runtime_policy.load_runtime_policy, "cache_clear", lambda: None)()
    refresh_inventory = _runtime_policy.runtime_refresh_boundary_inventory()
    agents_state = _agents.refresh_live_runtime()
    orchestrator_state = _agent_orch.current_runtime_defaults()
    control_policy_refresh = _refresh_experiment_control_policy_cache()
    ok = bool(control_policy_refresh.get("ok", True))
    message = "Live runtime settings refreshed for future work. In-flight turns/jobs keep prior settings."
    if not ok:
        message = "Live runtime refresh kept the last known-good experiment control policy for some cached projects. Future work keeps using the retained snapshot until a later refresh succeeds."
    return {
        "schema_version": "admin_server_runtime_refresh_v1",
        "utc": _now_utc_iso(),
        "ok": ok,
        "agents": agents_state,
        "orchestrator": orchestrator_state,
        "scheduler_runtime_policy": _scheduled_jobs.current_runtime_resource_policy(),
        "experiment_control_policy_refresh": control_policy_refresh,
        "mobile_runtime_policy": _mobile_runtime_policy_payload(),
        "message": message,
        "scope": {
            "applies_to": "future_work_started_after_refresh",
            "does_not_apply_to": "in_flight_turns_or_jobs",
            "live_refreshable": [
                "runtime_policy.properties",
                "prompt_files",
                "agent_config",
                "orchestrator_defaults",
                "experiment_control_action_policy",
            ],
            "future_turn_only_keys": list(refresh_inventory.get("future_turn_only_keys") or []),
            "future_job_only_keys": list(refresh_inventory.get("future_job_only_keys") or []),
            "restart_required_keys": list(refresh_inventory.get("restart_required_keys") or []),
            "env_override_keys": [
                str(row.get("key") or "")
                for row in list(refresh_inventory.get("env_overrides_active") or [])
                if str(row.get("key") or "").strip()
            ],
            "deploy_gated": [
                "server_code",
                "android_apk",
                "in_flight_job_state",
                "runtime_deployment",
            ],
            "code_reload": "not_performed",
            "source_of_truth": "runtime_policy_inventory",
        },
        "runtime_refresh_boundary": refresh_inventory,
    }


def _admin_control_action_actor(handler: Any, cfg: Any, qs: Mapping[str, List[str]], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    provided = str(_admin_extract_token(handler, dict(qs)) or "").strip()
    required_admin = str(getattr(cfg, "admin_token", "") or "").strip()
    if required_admin and provided == required_admin:
        return {"kind": "admin_token", "token_id": "", "issuer": "", "session_id": ""}
    if provided:
        try:
            session = _assistant_bridge.validate_control_pane_access_token(root, provided)
        except Exception:
            session = {}
        if session:
            token_id = str(session.get("token_id") or "").strip()
            issuer = ""
            if token_id:
                try:
                    config = _assistant_bridge._load_config(root)  # type: ignore[attr-defined]
                    token_row = _assistant_bridge._token_row_by_id(  # type: ignore[attr-defined]
                        _assistant_bridge._load_token_rows(root, config),  # type: ignore[attr-defined]
                        token_id,
                    )
                    issuer = str(token_row.get("issuer") or "").strip()
                except Exception:
                    issuer = ""
            return {
                "kind": "control_session",
                "token_id": token_id,
                "issuer": issuer,
                "session_id": str(session.get("session_id") or "").strip(),
            }
    return {
        "kind": "admin_guard",
        "token_id": "",
        "issuer": "",
        "session_id": "",
    }

def _mobile_sessions_primary_path() -> Path:
    """Stable mobile session storage path outside runs folders."""
    return (_webui_runtime_root() / "mobile_chat_sessions.json").resolve()


def _mobile_bootstrap_prompt_path(agent_key: str) -> Path:
    repo_root = _repo_root_from_webui()
    spec = _mobile_agent_spec(agent_key)
    return (Path(repo_root) / "governance" / "agents" / "prompts" / str(spec["prompt_file"])).resolve()


def _mobile_bootstrap_prompt_text(agent_key: str) -> str:
    """Load mobile bootstrap prompt from governance prompt file.

    Falls back to legacy inline prompt if file is missing/unreadable.
    """
    global _MOBILE_BOOTSTRAP_PROMPT_CACHE, _MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY
    fallback = (
        "You are Codex. Answer concisely and directly. "
        "Use simple Markdown when it improves readability. "
        "Use fenced code blocks for code and commands."
    )
    key = _coerce_mobile_agent_key(agent_key)
    p = _mobile_bootstrap_prompt_path(key)
    try:
        rendered = _agents.render_prompt_file(p)
        cache_key = str(rendered.get("cache_key") or "")
        cached = _MOBILE_BOOTSTRAP_PROMPT_CACHE.get(key)
        cached_key = _MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY.get(key)
        if (cached is not None) and cache_key and (cached_key == cache_key):
            return cached
        txt = str(rendered.get("text") or "").strip()
        if not txt:
            return fallback
        _MOBILE_BOOTSTRAP_PROMPT_CACHE[key] = txt
        _MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY[key] = cache_key
        return txt
    except Exception:
        return fallback


_MOBILE_APK_CHANNEL_META: Dict[str, Dict[str, str]] = {
    "debug": {
        "package_name": "com.eiel.chat.debug",
        "apk_name": "causyl-debug.apk",
        "apk_prefix": "causyl-debug",
        "legacy_apk_name": "app-debug.apk",
        "base_version_name": "0.1.0",
    },
    "release": {
        "package_name": "com.eiel.chat",
        "apk_name": "causyl-release.apk",
        "apk_prefix": "causyl-release",
        "legacy_apk_name": "app-release.apk",
        "base_version_name": "0.1.0",
    },
}


def _mobile_apk_candidate_paths(repo_root: Path, channel: str) -> List[Path]:
    rr = Path(repo_root).resolve()
    safe_channel = str(channel or "").strip().lower()
    if safe_channel not in _MOBILE_APK_CHANNEL_META:
        return []
    meta = _MOBILE_APK_CHANNEL_META[safe_channel]
    apk_name = meta["apk_name"]
    apk_prefix = meta["apk_prefix"]
    legacy_apk_name = meta["legacy_apk_name"]
    artifact_dir = (rr / "runtime" / "artifacts" / "mobile_apk" / safe_channel).resolve()
    versioned = sorted(
        artifact_dir.glob(f"{apk_prefix}-*.apk"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [
        *[path.resolve() for path in versioned],
        (artifact_dir / apk_name).resolve(),
        (artifact_dir / legacy_apk_name).resolve(),
        (rr / "mobile" / "android" / "app" / "build" / "outputs" / "apk" / safe_channel / legacy_apk_name).resolve(),
    ]
