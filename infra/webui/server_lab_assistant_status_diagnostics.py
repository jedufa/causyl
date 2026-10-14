"""Legacy status endpoint and diagnostic boundary contract (G4K7).

Defines snapshot-first precedence for operator status reads and retains legacy
diagnostic endpoints with explicit mismatch handling guidance.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple


STATUS_DIAGNOSTIC_SCHEMA_VERSION = "lab_assistant_status_diagnostic_boundary_v1"
STATUS_DIAGNOSTIC_MISMATCH_SCHEMA_VERSION = "lab_assistant_status_diagnostic_mismatch_v1"
CANONICAL_SNAPSHOT_API_PATH = "/api/admin/lab_assistant/snapshot"

LEGACY_DIAGNOSTIC_ENDPOINTS: List[Dict[str, Any]] = [
    {
        "surface_id": "assistant_state",
        "api_path": "/api/admin/lab_assistant/state",
        "label": "Assistant runtime state (2848)",
        "owner": "assistant_state",
        "status_role": "diagnostic",
        "retained": True,
    },
    {
        "surface_id": "task_register",
        "api_path": "/api/admin/lab_assistant/task_register",
        "label": "Task register snapshot (BD05)",
        "owner": "bd05_task_register",
        "status_role": "diagnostic",
        "retained": True,
    },
    {
        "surface_id": "delegation_proof",
        "api_path": "/api/admin/lab_assistant/delegation_proof",
        "label": "Delegated execution proof report (G7B1)",
        "owner": "delegation_proof",
        "status_role": "diagnostic",
        "retained": True,
    },
    {
        "surface_id": "execution_truth_ownership",
        "api_path": "/api/admin/lab_assistant/execution_truth_ownership",
        "label": "Execution truth ownership boundary (M9R2)",
        "owner": "execution_truth_ownership",
        "status_role": "diagnostic",
        "retained": True,
    },
    {
        "surface_id": "backend_api_boundary",
        "api_path": "/api/admin/lab_assistant/backend_api_boundary",
        "label": "Backend API boundary contract (L8Q1)",
        "owner": "backend_api_boundary",
        "status_role": "diagnostic",
        "retained": True,
    },
    {
        "surface_id": "host_health",
        "api_path": "/api/admin/lab_assistant/health",
        "label": "Host health snapshot (ADD9)",
        "owner": "host_health",
        "status_role": "diagnostic",
        "retained": True,
    },
    {
        "surface_id": "self_check",
        "api_path": "/api/admin/lab_assistant/self_check",
        "label": "Self-check snapshot (D2K3)",
        "owner": "self_check",
        "status_role": "diagnostic",
        "retained": True,
    },
]

DEFAULT_READ_ORDER = [
    "operator_snapshot",
    "task_register",
    "assistant_state",
    "delegation_proof",
    "execution_truth_ownership",
    "backend_api_boundary",
    "host_health",
    "self_check",
]

FALLBACK_READ_ORDER = [
    "task_register",
    "assistant_state",
    "host_health",
    "delegation_proof",
    "execution_truth_ownership",
]

_DIAGNOSTIC_COMPARISON_FIELDS: Dict[str, List[Dict[str, Tuple[str, ...]]]] = {
    "host_health": [
        {"diagnostic_path": ("status",), "snapshot_path": ("host_health", "status")},
        {"diagnostic_path": ("mode",), "snapshot_path": ("host_health", "mode")},
        {"diagnostic_path": ("blocked",), "snapshot_path": ("host_health", "blocked")},
    ],
    "assistant_state": [
        {"diagnostic_path": ("assistant_state", "assistant_id"), "snapshot_path": ("assistant_state", "assistant_id")},
        {"diagnostic_path": ("assistant_state", "current_state"), "snapshot_path": ("assistant_state", "current_state")},
        {"diagnostic_path": ("assistant_state", "write_generation"), "snapshot_path": ("assistant_state", "write_generation")},
    ],
    "task_register": [
        {"diagnostic_path": ("task_register", "register_id"), "snapshot_path": ("task_register", "register_id")},
        {"diagnostic_path": ("task_register", "write_generation"), "snapshot_path": ("task_register", "write_generation")},
        {"diagnostic_path": ("task_register", "summary_stale"), "snapshot_path": ("task_register", "summary_stale")},
    ],
    "delegation_proof": [
        {"diagnostic_path": ("schema_version",), "snapshot_path": ("delegation_proof", "schema_version")},
        {"diagnostic_path": ("summary", "overall_status"), "snapshot_path": ("delegation_proof", "summary", "overall_status")},
        {"diagnostic_path": ("freshness", "stale"), "snapshot_path": ("delegation_proof", "freshness", "stale")},
    ],
    "execution_truth_ownership": [
        {"diagnostic_path": ("schema_version",), "snapshot_path": ("execution_truth_ownership", "schema_version")},
        {"diagnostic_path": ("uid",), "snapshot_path": ("execution_truth_ownership", "uid")},
    ],
}


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _extract_path(source: Any, path: Iterable[str]) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        except Exception:
            return str(value)
    return _clean_text(value)


def diagnostic_boundary_snapshot(*, now_utc: str | None = None) -> Dict[str, Any]:
    now = now_utc or _utc_now_iso()
    return {
        "schema_version": STATUS_DIAGNOSTIC_SCHEMA_VERSION,
        "generated_utc": str(now),
        "canonical_snapshot": {
            "surface_id": "operator_snapshot",
            "api_path": CANONICAL_SNAPSHOT_API_PATH,
            "label": "Operator snapshot (2DB7)",
            "status_role": "canonical",
        },
        "legacy_endpoints": [dict(row) for row in LEGACY_DIAGNOSTIC_ENDPOINTS],
        "default_read_order": list(DEFAULT_READ_ORDER),
        "fallback_read_order": list(FALLBACK_READ_ORDER),
        "mismatch_handling": {
            "rule": "snapshot_first",
            "when_snapshot_fresh": "Treat snapshot as canonical. If a diagnostic disagrees without snapshot conflicts, record a degraded mismatch and investigate.",
            "when_snapshot_stale_or_missing": "Use diagnostics as evidence but keep degraded/unknown status visible; do not collapse into success.",
            "conflict_surface": "operator_snapshot.status_contract.truth_conflicts",
        },
        "retention": {
            "legacy_endpoints_retained": True,
            "deprecation_status": "not_deprecated",
            "rationale": "Backward-compatible diagnostics for existing tooling; not a primary operator status source.",
        },
    }


def diagnostic_surface(surface_id: str) -> Dict[str, Any]:
    sid = str(surface_id or "").strip()
    for row in LEGACY_DIAGNOSTIC_ENDPOINTS:
        if row.get("surface_id") == sid:
            return dict(row)
    return {
        "surface_id": sid or "unknown",
        "api_path": "",
        "label": "Unknown diagnostic surface",
        "owner": "unknown",
        "status_role": "diagnostic",
        "retained": True,
    }


def diagnostic_mismatch(
    surface_id: str,
    *,
    diagnostic_payload: Dict[str, Any],
    snapshot_payload: Optional[Dict[str, Any]] = None,
    now_utc: Optional[str] = None,
) -> Dict[str, Any]:
    sid = _clean_text(surface_id) or "unknown"
    now = now_utc or _utc_now_iso()
    snapshot = dict(snapshot_payload or {})
    snapshot_available = bool(snapshot)
    snapshot_fresh = snapshot_available and not bool(dict(snapshot.get("freshness") or {}).get("stale"))
    truth_conflicts = list(dict(snapshot.get("operator_summary") or {}).get("truth_conflicts") or [])
    conflict_present = bool(truth_conflicts)

    comparison_fields = list(_DIAGNOSTIC_COMPARISON_FIELDS.get(sid) or [])
    if not comparison_fields:
        return {
            "schema_version": STATUS_DIAGNOSTIC_MISMATCH_SCHEMA_VERSION,
            "evaluated_utc": now,
            "surface_id": sid,
            "state": "no_snapshot_projection",
            "mismatch": False,
            "snapshot_available": snapshot_available,
            "snapshot_fresh": snapshot_fresh,
            "truth_conflicts_present": conflict_present,
            "canonical_snapshot": {
                "surface_id": "operator_snapshot",
                "api_path": CANONICAL_SNAPSHOT_API_PATH,
            },
            "summary": "No canonical snapshot projection is defined for this diagnostic surface.",
            "fields_compared": [],
            "differences": [],
        }

    differences: List[Dict[str, Any]] = []
    fields_compared: List[Dict[str, Any]] = []
    for row in comparison_fields:
        diagnostic_path = tuple(row.get("diagnostic_path") or ())
        snapshot_path = tuple(row.get("snapshot_path") or ())
        diag_value = _extract_path(diagnostic_payload, diagnostic_path)
        snap_value = _extract_path(snapshot, snapshot_path)
        fields_compared.append(
            {
                "diagnostic_path": ".".join(diagnostic_path),
                "snapshot_path": ".".join(snapshot_path),
            }
        )
        diag_norm = _normalize_value(diag_value)
        snap_norm = _normalize_value(snap_value)
        if diag_norm is None and snap_norm is None:
            continue
        if diag_norm != snap_norm:
            differences.append(
                {
                    "field": ".".join(snapshot_path) or ".".join(diagnostic_path),
                    "diagnostic_value": diag_value,
                    "snapshot_value": snap_value,
                }
            )

    mismatch = bool(differences)
    if not snapshot_available:
        state = "snapshot_unavailable"
        summary = "Canonical snapshot is unavailable; mismatch cannot be evaluated."
    elif not mismatch:
        state = "match"
        summary = "Diagnostic surface matches the canonical snapshot fields."
    elif snapshot_fresh and not conflict_present:
        state = "degraded_mismatch"
        summary = "Diagnostic surface disagrees with fresh snapshot without conflict markers; treat as degraded mismatch."
    elif snapshot_fresh and conflict_present:
        state = "mismatch_conflicts_present"
        summary = "Diagnostic surface disagrees with snapshot, but snapshot already marks conflicts."
    else:
        state = "mismatch_snapshot_stale"
        summary = "Diagnostic surface disagrees with a stale snapshot; treat as advisory until snapshot recovers."

    return {
        "schema_version": STATUS_DIAGNOSTIC_MISMATCH_SCHEMA_VERSION,
        "evaluated_utc": now,
        "surface_id": sid,
        "state": state,
        "mismatch": mismatch,
        "snapshot_available": snapshot_available,
        "snapshot_fresh": snapshot_fresh,
        "truth_conflicts_present": conflict_present,
        "canonical_snapshot": {
            "surface_id": "operator_snapshot",
            "api_path": CANONICAL_SNAPSHOT_API_PATH,
        },
        "summary": summary,
        "fields_compared": fields_compared,
        "differences": differences,
    }
