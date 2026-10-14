#!/usr/bin/env python3
"""Show the next best Intent Sheets to work on."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger


EXCLUDED_STATUSES = {"verified", "done", "completed", "deprecated", "superseded", "deferred"}
TERMINAL_DEPENDENCY_STATUSES = {"verified", "done", "completed"}
PATH_AREA_PREFIXES = (
    ("experiments/shared/action_plane/", "action_plane"),
    ("infra/webui/", "operator_webui"),
    ("infra/mobile/android/", "operator_mobile"),
    ("experiments/shared/experiment_scaffold.py", "experiment_scaffold"),
    ("experiments/shared/experiment_scaffold_projection.py", "experiment_scaffold"),
    ("experiments/shared/experiment_records/", "experiment_records"),
    ("infra/controlplane/scheduled_jobs_parts/", "scheduler_runtime"),
    ("infra/controlplane/shared_resource_claim_ledger.py", "scheduler_runtime"),
    ("infra/controlplane/compiled_", "scheduler_runtime"),
    ("experiments/shared/shared_resource_contract.py", "scheduler_runtime"),
    ("experiments/shared/project_runner_contract.py", "scheduler_runtime"),
    ("tools/runtime_policy.py", "scheduler_runtime"),
    ("config/runtime_policy.properties", "scheduler_runtime"),
    ("governance/ledger/", "ledger_governance"),
    ("governance/agents/runtime/scheduled_jobs/", "scheduler_runtime"),
)


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _norm_status(value: Any) -> str:
    return _norm_text(value).lower()


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _slug_texts(row: Dict[str, Any]) -> str:
    parts = [
        _norm_text(row.get("title")).lower(),
        _norm_text(row.get("slug")).lower(),
        _norm_text(row.get("portfolio_group")).lower(),
        _norm_text(row.get("intent_kind")).lower(),
        _norm_text(row.get("naming_class")).lower(),
    ]
    tags = row.get("tags")
    if isinstance(tags, list):
        parts.extend([_norm_text(item).lower() for item in tags])
    return " ".join([part for part in parts if part])


def _path_areas(path: str) -> Set[str]:
    text = _norm_text(path).replace("\\", "/").lstrip("./")
    out: Set[str] = set()
    for prefix, area in PATH_AREA_PREFIXES:
        if text.startswith(prefix):
            out.add(area)
    lowered = text.lower()
    if lowered.startswith("tests/"):
        if "action_plane" in lowered:
            out.add("action_plane")
        if "webui" in lowered or "mobile" in lowered:
            out.add("operator_webui")
        if any(token in lowered for token in ("scheduled_jobs", "shared_resource", "runtime_policy", "project_runner")):
            out.add("scheduler_runtime")
        if "experiment_scaffold" in lowered:
            out.add("experiment_scaffold")
        if "experiment_records" in lowered:
            out.add("experiment_records")
    if "memory" in lowered or "continuation" in lowered:
        out.add("research_memory")
    return out


def _predicted_scope_areas(row: Dict[str, Any]) -> Tuple[Set[str], str]:
    areas: Set[str] = set()
    text = _slug_texts(row)
    portfolio = _norm_text(row.get("portfolio_group")).lower()
    intent_kind = _norm_text(row.get("intent_kind")).lower()

    if "scaffold" in text or intent_kind in {"bootstrap_automation"} or portfolio == "operationalization":
        areas.add("experiment_scaffold")
    if portfolio in {"runtime_and_executor_authority", "job_and_orchestration_substrate", "execution_shapes", "compiler_hardening", "job_substrate"}:
        areas.add("scheduler_runtime")
    if any(token in text for token in ("scheduler", "fanout", "queue", "workflow", "runtime policy", "resource concurrency", "shared resource", "executor", "verifier", "build-verify")):
        areas.add("scheduler_runtime")
    if portfolio == "operator_surfaces" or intent_kind in {"surface", "operator_surface"}:
        areas.add("operator_webui")
        if "android" in text or "mobile" in text:
            areas.add("operator_mobile")
    if any(token in text for token in ("action", "action plane", "control action", "tool access", "actuation")):
        areas.add("action_plane")
    if portfolio in {"knowledge_and_memory", "epistemic_governance"} or intent_kind in {"memory_contract", "model_or_registry", "evidence_contract"}:
        areas.add("research_memory")
    if any(token in text for token in ("continuation", "memory", "knowledge", "evidence pack", "literature", "registry")):
        areas.add("research_memory")
    if portfolio == "experiment_substrate" or any(token in text for token in ("experiment scaffold", "run lifecycle", "experiment artifact", "checkpoint", "replay", "reproducibility")):
        areas.add("experiment_records")
    if portfolio in {"human_governance", "institutional_scale", "mission_and_doctrine"}:
        areas.add("ledger_governance")
    if any(token in text for token in ("north star", "north-star")):
        areas.add("ledger_governance")
    confidence = "high" if areas else "none"
    return areas, confidence


def _sort_key(row: Dict[str, Any]) -> tuple:
    prio = row.get("execution_priority")
    prio_num = int(prio) if str(prio or "").isdigit() else 10**9
    seq = int(row.get("sequence_index")) if str(row.get("sequence_index") or "").isdigit() else 10**9
    order = int(row.get("recommended_order_in_wave")) if str(row.get("recommended_order_in_wave") or "").isdigit() else 10**9
    return (
        prio_num,
        0 if row.get("critical_path") else 1,
        str(row.get("recommended_wave") or ""),
        order,
        seq,
        str(row.get("uid") or ""),
    )


def _integration_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("integration_meta")
    return value if isinstance(value, dict) else {}


def _sheet_id_for_row(row: Dict[str, Any]) -> str:
    return _norm_text(row.get("sheet_id") or _integration_meta(row).get("sheet_id")).upper()


def _all_sheet_rows(*, repo_root: Path, project_id: str = "") -> List[Dict[str, Any]]:
    meta_by_uid: Dict[str, Dict[str, Any]] = {}
    for path in (repo_root / "governance" / "ledger" / "intent_sheets").rglob("meta.json"):
        meta = _load_json(path)
        uid = _norm_text(meta.get("uid")).upper()
        if uid:
            meta_by_uid[uid] = meta
    ledger = IntentSheetLedger(repo_root=repo_root)
    rows = ledger.list_sheets(limit=5000)
    out: List[Dict[str, Any]] = []
    for row in rows:
        uid = _norm_text(row.get("uid")).upper()
        merged = dict(meta_by_uid.get(uid, {}))
        merged.update(dict(row))
        if _norm_status(merged.get("status")) in EXCLUDED_STATUSES:
            continue
        if project_id and _norm_text(merged.get("project_id")) != project_id:
            continue
        out.append(merged)
    return out


def _all_sheet_metadata(*, repo_root: Path, project_id: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in (repo_root / "governance" / "ledger" / "intent_sheets").rglob("meta.json"):
        meta = _load_json(path)
        if project_id and _norm_text(meta.get("project_id")) != project_id:
            continue
        if _norm_text(meta.get("uid")):
            out.append(meta)
    return out


def _sheet_indexes(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_uid: Dict[str, Dict[str, Any]] = {}
    by_sheet_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        uid = _norm_text(row.get("uid")).upper()
        if uid:
            by_uid[uid] = row
        sheet_id = _sheet_id_for_row(row)
        if sheet_id:
            by_sheet_id[sheet_id] = row
    return by_uid, by_sheet_id


def _resolve_dependency_row(
    dep: Dict[str, Any],
    *,
    by_uid: Dict[str, Dict[str, Any]],
    by_sheet_id: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    dep_id = _norm_text(dep.get("id")).upper()
    if not dep_id:
        return None
    if dep_id in by_uid:
        return by_uid[dep_id]
    return by_sheet_id.get(dep_id)


def _hard_dependency_blockers(
    row: Dict[str, Any],
    *,
    by_uid: Dict[str, Dict[str, Any]],
    by_sheet_id: Dict[str, Dict[str, Any]],
) -> List[str]:
    blockers: List[str] = []
    for raw in list(row.get("depends_on") or []):
        dep = raw if isinstance(raw, dict) else {"id": raw}
        if _norm_text(dep.get("kind")).lower() != "hard":
            continue
        target = _resolve_dependency_row(dep, by_uid=by_uid, by_sheet_id=by_sheet_id)
        dep_label = _norm_text(dep.get("id")).upper() or "<unknown>"
        if target is None:
            blockers.append(dep_label)
            continue
        if _norm_status(target.get("status")) not in TERMINAL_DEPENDENCY_STATUSES:
            blockers.append(_norm_text(target.get("uid")).upper() or dep_label)
    return blockers


def _job_spec_for_state(job_id: str, *, repo_root: Path, run_id: str = "") -> Dict[str, Any]:
    if job_id and run_id:
        archive = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "archive" / job_id / f"{run_id}__spec.json"
        if archive.exists():
            return _load_json(archive)
    inbox = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox" / f"{job_id}.json"
    if inbox.exists():
        return _load_json(inbox)
    return {}


def _job_parent_uid(spec: Dict[str, Any]) -> str:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    child_contract = prompt_meta.get("execution_family_contract") if isinstance(prompt_meta.get("execution_family_contract"), dict) else {}
    closeout_contract = prompt_meta.get("execution_family_closeout") if isinstance(prompt_meta.get("execution_family_closeout"), dict) else {}
    if child_contract:
        return _norm_text(child_contract.get("parent_uid")).upper()
    if closeout_contract:
        return _norm_text(closeout_contract.get("parent_uid")).upper()
    if isinstance(prompt_meta.get("queue_child_implementation_jobs"), dict):
        binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
        if _norm_text(binding.get("type")) == "ledger_uid":
            return _norm_text(binding.get("id")).upper()
    return ""


def _job_source_intent_uid(spec: Dict[str, Any], *, repo_root: Path) -> str:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    for key in ("execution_family_closeout", "execution_family_contract"):
        value = prompt_meta.get(key)
        if isinstance(value, dict):
            uid = _norm_text(value.get("source_intent_uid")).upper()
            if uid:
                return uid
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    if _norm_text(binding.get("type")) == "ledger_uid":
        uid = _norm_text(binding.get("id")).upper()
        if uid.startswith("PIS-"):
            for meta_path in (repo_root / "governance" / "ledger" / "patch_instruction_sheets").rglob(f"{uid}__*/meta.json"):
                meta = _load_json(meta_path)
                for raw in list(meta.get("tags") or []):
                    tag = _norm_text(raw).lower()
                    if tag.startswith("source-intent:"):
                        return tag.split("source-intent:", 1)[-1].upper()
    return ""


def _family_specs_by_parent(*, repo_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    roots = [
        repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox",
        repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "archive",
    ]
    seen: Set[Tuple[str, str]] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            data = _load_json(path)
            job_id = _norm_text(data.get("job_id") or path.stem)
            parent_uid = _job_parent_uid(data)
            if not job_id or not parent_uid:
                continue
            key = (parent_uid, job_id)
            if key in seen:
                continue
            seen.add(key)
            out.setdefault(parent_uid, []).append(data)
    return out


def _family_observed_repo_paths(parent_uid: str, *, repo_root: Path, family_specs: Dict[str, List[Dict[str, Any]]]) -> Set[str]:
    out: Set[str] = set()
    for spec in list(family_specs.get(parent_uid, [])):
        job_id = _norm_text(spec.get("job_id"))
        if not job_id:
            continue
        state_path = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state" / f"{job_id}.json"
        state = _load_json(state_path)
        receipt_path = Path(_norm_text(state.get("last_receipt_path")))
        if not receipt_path.exists():
            continue
        receipt = _load_json(receipt_path)
        repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
        for raw in list(repo_residue.get("declared_changed_files") or []):
            path = _norm_text(raw).replace("\\", "/").lstrip("./")
            if path:
                out.add(path)
    return out


def _active_families(*, repo_root: Path, family_specs: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    state_root = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state"
    for path in sorted(state_root.glob("*.json")):
        state = _load_json(path)
        active_run_id = _norm_text(state.get("active_run_id"))
        if not active_run_id:
            continue
        job_id = _norm_text(state.get("job_id") or path.stem)
        spec = _job_spec_for_state(job_id, repo_root=repo_root, run_id=active_run_id)
        parent_uid = _job_parent_uid(spec)
        if not parent_uid:
            continue
        source_intent_uid = _job_source_intent_uid(spec, repo_root=repo_root)
        observed_paths = _family_observed_repo_paths(parent_uid, repo_root=repo_root, family_specs=family_specs)
        out.append(
            {
                "job_id": job_id,
                "parent_uid": parent_uid,
                "source_intent_uid": source_intent_uid,
                "observed_paths": observed_paths,
                "observed_areas": sorted({area for path in observed_paths for area in _path_areas(path)}),
                "unknown_scope": not bool(observed_paths),
            }
        )
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in out:
        deduped.setdefault(str(row["parent_uid"]), row)
    return list(deduped.values())


def _candidate_family_scope(row: Dict[str, Any], *, repo_root: Path, family_specs: Dict[str, List[Dict[str, Any]]]) -> Set[str]:
    out: Set[str] = set()
    for parent_uid in [_norm_text(item).upper() for item in list(row.get("derived_pis_uids") or []) if _norm_text(item)]:
        out.update(_family_observed_repo_paths(parent_uid, repo_root=repo_root, family_specs=family_specs))
    return out


def _candidate_scope_signature(row: Dict[str, Any], *, repo_root: Path, family_specs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    candidate_scope = _candidate_family_scope(row, repo_root=repo_root, family_specs=family_specs)
    if candidate_scope:
        return {
            "scope_paths": set(candidate_scope),
            "scope_areas": {area for path in candidate_scope for area in _path_areas(path)},
            "scope_confidence": "observed",
        }
    inferred_areas, inferred_confidence = _predicted_scope_areas(row)
    return {
        "scope_paths": set(),
        "scope_areas": set(inferred_areas),
        "scope_confidence": str(inferred_confidence or "none"),
    }


def _started_parent_uids(row: Dict[str, Any]) -> List[str]:
    return [_norm_text(item).upper() for item in list(row.get("derived_pis_uids") or []) if _norm_text(item)]


def _parent_family_is_verified(parent_uid: str, *, repo_root: Path) -> bool:
    if not parent_uid.startswith("PIS-"):
        return False
    parent_status = _norm_status(sched._ledger_status_for_uid(parent_uid, repo_root=repo_root))
    if parent_status != "verified":
        return False
    child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=repo_root)
    child_uids = [_norm_text(row.get("uid")).upper() for row in child_rows if _norm_text(row.get("uid"))]
    if not child_uids:
        for meta_path in (repo_root / "governance" / "ledger" / "patch_instruction_sheets").rglob("meta.json"):
            meta = _load_json(meta_path)
            if _norm_text(meta.get("parent_uid")).upper() == parent_uid:
                child_uid = _norm_text(meta.get("uid")).upper()
                if child_uid:
                    child_uids.append(child_uid)
    if not child_uids:
        return False
    return {_norm_status(sched._ledger_status_for_uid(child_uid, repo_root=repo_root)) for child_uid in child_uids} == {"verified"}


def _row_has_verified_family_completion(row: Dict[str, Any], *, repo_root: Path) -> bool:
    started_parents = _started_parent_uids(row)
    if not started_parents:
        return False
    return any(_parent_family_is_verified(parent_uid, repo_root=repo_root) for parent_uid in started_parents)


def _parallel_safety(
    row: Dict[str, Any],
    *,
    repo_root: Path,
    by_uid: Dict[str, Dict[str, Any]],
    by_sheet_id: Dict[str, Dict[str, Any]],
    family_specs: Dict[str, List[Dict[str, Any]]],
    active_families: List[Dict[str, Any]],
) -> Dict[str, Any]:
    uid = _norm_text(row.get("uid")).upper()
    blockers = _hard_dependency_blockers(row, by_uid=by_uid, by_sheet_id=by_sheet_id)
    if blockers:
        return {"safe": False, "reason": "hard_dependencies_unfinished", "blockers": blockers}
    started_parents = _started_parent_uids(row)
    active_parent_uids = {_norm_text(item.get("parent_uid")).upper() for item in active_families if _norm_text(item.get("parent_uid"))}
    if started_parents:
        in_flight = [parent_uid for parent_uid in started_parents if parent_uid in active_parent_uids]
        if in_flight:
            return {"safe": False, "reason": "already_in_flight", "blockers": in_flight}
        return {"safe": True, "reason": "resume_existing_family", "blockers": started_parents}
    if not active_families:
        return {"safe": True, "reason": "no_active_families", "blockers": []}
    candidate_scope = _candidate_family_scope(row, repo_root=repo_root, family_specs=family_specs)
    if candidate_scope:
        candidate_areas = sorted({area for path in candidate_scope for area in _path_areas(path)})
    else:
        inferred_areas, inferred_confidence = _predicted_scope_areas(row)
        candidate_areas = sorted(inferred_areas)
        if inferred_confidence == "none":
            return {"safe": False, "reason": "unknown_candidate_scope", "blockers": []}
    for family in active_families:
        if uid and uid == _norm_text(family.get("source_intent_uid")).upper():
            return {"safe": False, "reason": "already_in_flight", "blockers": [_norm_text(family.get("parent_uid")).upper()]}
        family_active_areas = {_norm_text(item) for item in list(family.get("observed_areas") or []) if _norm_text(item)}
        if bool(family.get("unknown_scope")):
            source_uid = _norm_text(family.get("source_intent_uid")).upper()
            source_row = by_uid.get(source_uid) if source_uid else None
            if isinstance(source_row, dict):
                inferred_active_areas, inferred_confidence = _predicted_scope_areas(source_row)
                if inferred_confidence != "none":
                    family_active_areas = set(inferred_active_areas)
            if not family_active_areas:
                return {"safe": False, "reason": "unknown_active_scope", "blockers": [_norm_text(family.get("parent_uid")).upper()]}
        if candidate_scope:
            overlap = sorted(candidate_scope.intersection(set(family.get("observed_paths") or set())))
            if overlap:
                return {"safe": False, "reason": "repo_scope_overlap", "blockers": overlap[:8]}
        else:
            overlap_areas = sorted(set(candidate_areas).intersection(family_active_areas))
            if overlap_areas:
                return {"safe": False, "reason": "predicted_area_overlap", "blockers": overlap_areas[:8]}
    if candidate_scope:
        return {"safe": True, "reason": "disjoint_from_active_families", "blockers": candidate_areas[:8]}
    return {"safe": True, "reason": "predicted_area_disjoint", "blockers": candidate_areas[:8]}


def _pairwise_parallel_safe(
    left: Dict[str, Any],
    right: Dict[str, Any],
    *,
    repo_root: Path,
    family_specs: Dict[str, List[Dict[str, Any]]],
) -> bool:
    left_uid = _norm_text(left.get("uid")).upper()
    right_uid = _norm_text(right.get("uid")).upper()
    if not left_uid or not right_uid or left_uid == right_uid:
        return False
    left_sig = _candidate_scope_signature(left, repo_root=repo_root, family_specs=family_specs)
    right_sig = _candidate_scope_signature(right, repo_root=repo_root, family_specs=family_specs)
    left_paths = set(left_sig.get("scope_paths") or set())
    right_paths = set(right_sig.get("scope_paths") or set())
    if left_paths and right_paths:
        return not bool(left_paths.intersection(right_paths))
    left_areas = set(left_sig.get("scope_areas") or set())
    right_areas = set(right_sig.get("scope_areas") or set())
    if left_areas and right_areas:
        return not bool(left_areas.intersection(right_areas))
    return False


def _maximal_pairwise_safe_rows(
    rows: List[Dict[str, Any]],
    *,
    limit: int,
    repo_root: Path,
    family_specs: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for row in rows:
        if len(selected) >= limit:
            break
        safety = row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}
        if not bool(safety.get("safe")):
            continue
        if str(safety.get("reason") or "").strip() == "resume_existing_family":
            continue
        if all(_pairwise_parallel_safe(row, existing, repo_root=repo_root, family_specs=family_specs) for existing in selected):
            selected.append(row)
    return selected


def _human_safety_reason(payload: Dict[str, Any]) -> str:
    reason = _norm_text(payload.get("reason"))
    blockers = [str(item) for item in list(payload.get("blockers") or []) if _norm_text(item)]
    if reason == "hard_dependencies_unfinished":
        return f"unfinished hard deps: {', '.join(blockers[:3])}" if blockers else "unfinished hard dependencies"
    if reason == "already_in_flight":
        return f"already in flight via {', '.join(blockers[:2])}" if blockers else "already in flight"
    if reason == "resume_existing_family":
        return f"resume existing family via {', '.join(blockers[:2])}" if blockers else "resume existing family"
    if reason == "repo_scope_overlap":
        return f"repo overlap: {', '.join(blockers[:3])}" if blockers else "repo scope overlap"
    if reason == "predicted_area_overlap":
        return f"predicted area overlap: {', '.join(blockers[:3])}" if blockers else "predicted area overlap"
    if reason == "unknown_active_scope":
        return f"active family scope unknown: {', '.join(blockers[:2])}" if blockers else "active family scope unknown"
    if reason == "unknown_candidate_scope":
        return "candidate scope unknown"
    if reason == "no_active_families":
        return "no active families"
    if reason == "disjoint_from_active_families":
        return f"disjoint from active families: {', '.join(blockers[:3])}" if blockers else "disjoint from active families"
    if reason == "predicted_area_disjoint":
        return f"predicted disjoint areas: {', '.join(blockers[:3])}" if blockers else "predicted disjoint areas"
    return reason or "unknown"


def next_sheets(*, repo_root: Path, limit: int = 10, project_id: str = "", safe_only: bool = False, set_safe_only: bool = False) -> List[Dict[str, Any]]:
    rows = _all_sheet_rows(repo_root=repo_root, project_id=project_id)
    all_meta_rows = _all_sheet_metadata(repo_root=repo_root, project_id=project_id)
    by_uid, by_sheet_id = _sheet_indexes(all_meta_rows)
    family_specs = _family_specs_by_parent(repo_root=repo_root)
    active_families = _active_families(repo_root=repo_root, family_specs=family_specs)
    out: List[Dict[str, Any]] = []
    for row in rows:
        if _row_has_verified_family_completion(row, repo_root=repo_root):
            continue
        cur = dict(row)
        cur["parallel_safety"] = _parallel_safety(
            cur,
            repo_root=repo_root,
            by_uid=by_uid,
            by_sheet_id=by_sheet_id,
            family_specs=family_specs,
            active_families=active_families,
        )
        if safe_only and not bool((cur.get("parallel_safety") if isinstance(cur.get("parallel_safety"), dict) else {}).get("safe")):
            continue
        out.append(cur)
    out.sort(key=_sort_key)
    if set_safe_only:
        return _maximal_pairwise_safe_rows(
            out,
            limit=max(1, min(int(limit or 10), 200)),
            repo_root=repo_root,
            family_specs=family_specs,
        )
    return out[: max(1, min(int(limit or 10), 200))]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Show the next best Intent Sheets to execute.")
    ap.add_argument("--limit", type=int, default=10, help="Number of sheets to show (default: 10).")
    ap.add_argument("--project-id", default="", help="Optional project filter.")
    ap.add_argument("--safe-only", action="store_true", help="Show only items that are currently parallel-safe to start.")
    ap.add_argument("--set-safe-only", action="store_true", help="Show a priority-ordered set that is mutually safe to start in parallel.")
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args(argv)

    rows = next_sheets(
        repo_root=REPO_ROOT,
        limit=int(args.limit),
        project_id=str(args.project_id or "").strip(),
        safe_only=bool(args.safe_only),
        set_safe_only=bool(args.set_safe_only),
    )
    if args.json:
        print(json.dumps({"rows": rows}, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    for idx, row in enumerate(rows, start=1):
        safety = row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}
        prefix = "[safe]" if bool(safety.get("safe")) else "[not safe]"
        line = (
            f"{prefix} {idx}. {row['uid']} | {row['title']} | status={row['status']} | "
            f"prio={row.get('execution_priority')} | wave={row.get('recommended_wave')}"
        )
        if row.get("critical_path"):
            line += " | critical_path"
        line += f" | why={_human_safety_reason(safety)}"
        print(line)
    safe_row = next(
        (
            row
            for row in rows
            if bool(
                (row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}).get("safe")
            )
        ),
        None,
    )
    if safe_row is not None:
        print(f"Next safe to start: {safe_row['uid']} | {safe_row['title']}")
    else:
        print("Next safe to start: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
