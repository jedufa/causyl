from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from tools import runtime_policy as _runtime_policy

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.explicit_pass_chain_compiler import ExplicitPassChainCompileError
from infra.controlplane.intent_family_v2_compiler import IntentFamilyV2CompileError

from .ownership import load_ownership, ownership_receipt
from .runtime import run_shadow_tick


SURFACE_BUNDLE_SCHEMA_VERSION = "sched_v2_surface_bundle_v1"
WIRING_CONTRACT_REL = Path("infra/controlplane/sched/production_wiring_contract.json")
PARENT_UID_POLICY_KEY = "scheduler.v2.parent_uid"
PENDING_DECOMPOSITION_CODES = frozenset(
    {
        "missing_source_intent_uid",
        "missing_children",
        "missing_execution_children",
        "invalid_validation_tail",
        "invalid_closeout_tail",
    }
)


def _repo_root(repo_root: Path | None = None) -> Path:
    return Path(repo_root or ".").resolve()


def load_production_wiring_contract(*, repo_root: Path | None = None) -> dict[str, Any]:
    root = _repo_root(repo_root)
    path = (root / WIRING_CONTRACT_REL).resolve()
    return json.loads(path.read_text(encoding="utf-8"))


def _projection_rows_from_shadow(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), Mapping) else {}
    admitted = [row for row in list(evaluation.get("admitted") or []) if isinstance(row, Mapping)]
    projection = evaluation.get("projection") if isinstance(evaluation.get("projection"), Mapping) else {}
    nodes = projection.get("nodes") if isinstance(projection.get("nodes"), list) else projection.get("children")
    nodes = nodes if isinstance(nodes, list) else []
    node_by_child_uid = {
        str(row.get("child_uid") or row.get("node_id") or "").strip(): row
        for row in nodes
        if isinstance(row, Mapping)
    }
    rows = []
    for row in admitted:
        child_uid = str(row.get("child_uid") or "").strip()
        node = node_by_child_uid.get(child_uid, {})
        rows.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "dispatch_job_id": str(row.get("dispatch_job_id") or row.get("job_id") or ""),
                "child_uid": child_uid,
                "title": str(node.get("title") or child_uid),
                "node_kind": str(node.get("node_kind") or ""),
                "stage": str(node.get("stage") or ""),
                "fanout_role": str(node.get("fanout_role") or ""),
                "decision": str(row.get("decision") or ""),
                "reason": str(row.get("reason") or ""),
                "gate": str(row.get("gate") or ""),
                "unmet_dependencies": list(row.get("unmet_dependencies") or []),
            }
        )
    return tuple(rows)


def _is_pending_decomposition(exc: IntentFamilyV2CompileError | ExplicitPassChainCompileError) -> bool:
    diagnostics = getattr(exc, "diagnostics", ())
    codes = {
        str(row.get("code") or "").strip()
        for row in diagnostics
        if isinstance(row, Mapping) and str(row.get("code") or "").strip()
    }
    return bool(codes) and codes.issubset(PENDING_DECOMPOSITION_CODES) and "missing_children" in codes


def _pending_decomposition_rows(*, repo_root: Path, parent_uid: str, run_at_utc: str) -> tuple[Mapping[str, Any], ...]:
    rows = []
    jobs_root = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox"
    for path in sorted(jobs_root.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(job, Mapping):
            continue
        binding = job.get("binding") if isinstance(job.get("binding"), Mapping) else {}
        if str(binding.get("id") or "").strip().upper() != str(parent_uid or "").strip().upper():
            continue
        job_id = str(job.get("job_id") or path.stem).strip()
        try:
            state = sched.load_state(job_id, repo_root=repo_root)
        except Exception:
            state = {}
        active_run_id = str(state.get("active_run_id") or "").strip()
        run_now_id = str(state.get("run_now_request_run_id") or "").strip()
        if active_run_id:
            decision = "active"
            reason = "active_run_present"
        elif run_now_id:
            decision = "admit"
            reason = "run_now_requested"
        elif not bool(job.get("enabled", True)):
            decision = "blocked"
            reason = "disabled"
        else:
            decision = "admit"
            reason = "waiting_for_decomposition"
        rows.append(
            {
                "job_id": job_id,
                "child_uid": str(parent_uid or "").strip(),
                "title": str(job.get("display_title") or parent_uid),
                "node_kind": "family_parent_decomposition",
                "decision": decision,
                "reason": reason,
                "gate": "parent_decomposition",
                "unmet_dependencies": [],
            }
        )
    return tuple(rows)


def build_internal_surface_bundle(
    *,
    repo_root: Path | None = None,
    parent_uid: str = "",
    run_at_utc: str = "2026-04-10T00:00:00Z",
    mode: str | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    ownership = load_ownership(mode=mode)
    wiring = load_production_wiring_contract(repo_root=root)
    shadow_payload: Mapping[str, Any] = {}
    projection_rows: tuple[Mapping[str, Any], ...] = ()
    status = "ready_without_family"
    error = ""
    normalized_parent_uid = str(
        parent_uid or _runtime_policy.get_runtime_policy_text(PARENT_UID_POLICY_KEY, "")
    ).strip()
    if normalized_parent_uid:
        try:
            shadow_payload = run_shadow_tick(
                parent_uid=normalized_parent_uid,
                run_at_utc=run_at_utc,
                repo_root=root,
                mode=ownership.mode,
            )
            projection_rows = _projection_rows_from_shadow(shadow_payload)
            status = "ready"
        except (IntentFamilyV2CompileError, ExplicitPassChainCompileError) as exc:
            if _is_pending_decomposition(exc):
                projection_rows = _pending_decomposition_rows(
                    repo_root=root,
                    parent_uid=normalized_parent_uid,
                    run_at_utc=run_at_utc,
                )
                status = "pending_decomposition"
                error = ""
            else:
                status = "blocked"
                error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            status = "blocked"
            error = f"{type(exc).__name__}: {exc}"
    surfaces = {
        "api": list(projection_rows),
        "watch": list(projection_rows),
        "web": list(projection_rows),
        "mobile": list(projection_rows),
    }
    return {
        "schema_version": SURFACE_BUNDLE_SCHEMA_VERSION,
        "status": status,
        "error": error,
        "parent_uid": normalized_parent_uid,
        "ownership": dict(ownership_receipt(ownership, parent_uid=normalized_parent_uid)),
        "process": {
            "public_ports": [],
            "api": wiring["internal_wiring"]["api"],
            "public_port_policy": wiring["internal_wiring"]["public_ports"],
            "preferred_runtime": "in_process_existing_plane_server",
            "separate_process_policy": "localhost_or_unix_socket_only_if_later_required",
        },
        "consumers": {
            "watch": wiring["internal_wiring"]["watch"],
            "web": wiring["internal_wiring"]["web"],
            "mobile": wiring["internal_wiring"]["mobile"],
            "watchdog": wiring["internal_wiring"]["watchdog"],
            "healer": wiring["internal_wiring"]["healer"],
        },
        "surfaces": surfaces,
        "projection_equivalent": surfaces["api"] == surfaces["watch"] == surfaces["web"] == surfaces["mobile"],
        "shadow_payload": dict(shadow_payload),
    }
