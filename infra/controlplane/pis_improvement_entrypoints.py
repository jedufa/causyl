"""Shared CLI and assistant-trigger entrypoints for PIS improvement sessions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import pis_improvement_orchestration as orchestration
from infra.controlplane import pis_improvement_session_contract as contract
from infra.webui.ledger import ledger_storage
from tools.shared import fsio


ENTRYPOINT_REQUEST_SCHEMA_VERSION = "pis_improvement_entrypoint_request_v1"
ENTRYPOINT_RESULT_SCHEMA_VERSION = "pis_improvement_entrypoint_result_v1"
ENTRYPOINT_CONTRACT_SCHEMA_VERSION = "pis_improvement_entrypoint_contract_v1"
ENTRYPOINT_CONTRACT_ARTIFACT_REL = Path("governance/pis_improvement_entrypoint_contract_v1.json")
ENTRYPOINT_CONTRACT_DOC_REL = Path("docs/contracts/contract_pis_improvement_entrypoints_v1.md")
UID = "PIS-20260412-062103Z-D4F6"
DEFAULT_RECENT_LIMIT = 10
DEFAULT_ITERATION_CAP = 2
_ALLOWED_LEDGER_STATUSES = {"draft", "active", "in_progress", "blocked", "verified"}
_ALLOWED_ACTIONS = ("list_recent", "launch")
_DEFAULT_STOP_CONDITIONS = (
    "stop when recommendation outcome is adopt_revision or keep_source",
    "stop immediately if any revision-ledger entry fails validation",
)
LIST_RECENT_OPTIONAL_FIELDS: Tuple[str, ...] = ("request_id", "limit")
LAUNCH_REQUIRED_FIELDS: Tuple[str, ...] = ("request_id", "source_pis_uid")
LAUNCH_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "selected_candidate_uids",
    "limit",
    "session_id",
    "title",
    "policy_overrides",
    "overwrite",
    "rounds",
    "final_recommendation",
)
RESULT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "ok",
    "action",
    "request_id",
    "status",
    "blocked",
    "error_code",
    "operator_summary",
    "source_pis",
    "recent_candidates",
    "selected_candidate_uids",
    "session_id",
    "runtime_state",
    "recommendation",
    "artifacts",
    "artifact_refs",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "schema_version_must_match_the_shared_entrypoint_contract",
    "launch_requests_require_explicit_request_id_and_source_pis_uid",
    "assistant_and_cli_surfaces_must_call_the_same_backend_request_handler",
    "selected_candidate_uids_must_be_explicit_and_come_from_the_materialized_recent_pool",
    "policy_overrides_must_be_structured_json_not_hidden_chat_state",
    "responses_must_return_operator_summary_runtime_status_and_durable_artifact_refs",
    "malformed_requests_or_unknown_actions_fail_closed_instead_of_falling_back",
)


@dataclass(frozen=True)
class PISImprovementEntrypointContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    request_schema_version: str
    result_schema_version: str
    supported_actions: Tuple[str, ...]
    list_recent_optional_fields: Tuple[str, ...]
    launch_required_fields: Tuple[str, ...]
    launch_optional_fields: Tuple[str, ...]
    result_required_fields: Tuple[str, ...]
    fail_closed_rules: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return fsio.utc_now_iso()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_text_list(values: Sequence[Any]) -> List[str]:
    out: List[str] = []
    for raw in list(values or []):
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_limit(value: Any, *, default: int, minimum: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def pis_improvement_entrypoint_contract_v1() -> PISImprovementEntrypointContractV1:
    return PISImprovementEntrypointContractV1(
        version=ENTRYPOINT_CONTRACT_SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=ENTRYPOINT_CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=ENTRYPOINT_CONTRACT_DOC_REL,
        request_schema_version=ENTRYPOINT_REQUEST_SCHEMA_VERSION,
        result_schema_version=ENTRYPOINT_RESULT_SCHEMA_VERSION,
        supported_actions=_ALLOWED_ACTIONS,
        list_recent_optional_fields=LIST_RECENT_OPTIONAL_FIELDS,
        launch_required_fields=LAUNCH_REQUIRED_FIELDS,
        launch_optional_fields=LAUNCH_OPTIONAL_FIELDS,
        result_required_fields=RESULT_REQUIRED_FIELDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_pis_improvement_entrypoint_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    return json.loads((root / ENTRYPOINT_CONTRACT_ARTIFACT_REL).read_text(encoding="utf-8"))


def validate_pis_improvement_entrypoint_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_pis_improvement_entrypoint_contract(repo_root=repo_root)
    issues: List[str] = []
    contract = pis_improvement_entrypoint_contract_v1()
    if payload.get("schema_version") != contract.version:
        issues.append(f"schema_version must remain {contract.version}")
    if payload.get("uid") != contract.uid:
        issues.append(f"uid must remain {contract.uid}")
    if payload.get("canonical_doc_rel") != contract.canonical_doc_rel.as_posix():
        issues.append(f"canonical_doc_rel must remain {contract.canonical_doc_rel.as_posix()}")
    if payload.get("request_schema_version") != contract.request_schema_version:
        issues.append(f"request_schema_version must remain {contract.request_schema_version}")
    if payload.get("result_schema_version") != contract.result_schema_version:
        issues.append(f"result_schema_version must remain {contract.result_schema_version}")
    if tuple(payload.get("supported_actions") or ()) != contract.supported_actions:
        issues.append("supported_actions drifted from runtime contract")
    if tuple(payload.get("list_recent_optional_fields") or ()) != contract.list_recent_optional_fields:
        issues.append("list_recent_optional_fields drifted from runtime contract")
    if tuple(payload.get("launch_required_fields") or ()) != contract.launch_required_fields:
        issues.append("launch_required_fields drifted from runtime contract")
    if tuple(payload.get("launch_optional_fields") or ()) != contract.launch_optional_fields:
        issues.append("launch_optional_fields drifted from runtime contract")
    if tuple(payload.get("result_required_fields") or ()) != contract.result_required_fields:
        issues.append("result_required_fields drifted from runtime contract")
    if tuple(payload.get("fail_closed_rules") or ()) != contract.fail_closed_rules:
        issues.append("fail_closed_rules drifted from runtime contract")
    return tuple(issues)


def _validate_request(raw_request: Mapping[str, Any]) -> Dict[str, Any]:
    schema_version = _clean_text(raw_request.get("schema_version"))
    if not schema_version:
        raise ValueError("schema_version is required")
    if schema_version != ENTRYPOINT_REQUEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {schema_version}")
    action = _clean_text(raw_request.get("action")) or "launch"
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    if action == "list_recent":
        return {
            "schema_version": schema_version,
            "action": action,
            "request_id": _clean_text(raw_request.get("request_id")),
            "limit": _normalize_limit(raw_request.get("limit"), default=DEFAULT_RECENT_LIMIT),
        }

    request_id = _request_id(raw_request)
    source_pis_uid = _clean_text(raw_request.get("source_pis_uid"))
    if not source_pis_uid:
        raise ValueError("source_pis_uid is required")
    policy_overrides = raw_request.get("policy_overrides") or {}
    if not isinstance(policy_overrides, Mapping):
        raise ValueError("policy_overrides must be an object when provided")
    rounds = raw_request.get("rounds") or []
    if not isinstance(rounds, Sequence) or isinstance(rounds, (str, bytes, bytearray)):
        raise ValueError("rounds must be a list when provided")
    final_recommendation = raw_request.get("final_recommendation") or {}
    if final_recommendation and not isinstance(final_recommendation, Mapping):
        raise ValueError("final_recommendation must be an object when provided")
    selected_candidate_uids = raw_request.get("selected_candidate_uids") or []
    if not isinstance(selected_candidate_uids, Sequence) or isinstance(selected_candidate_uids, (str, bytes, bytearray)):
        raise ValueError("selected_candidate_uids must be a list when provided")
    return {
        "schema_version": schema_version,
        "action": action,
        "request_id": request_id,
        "source_pis_uid": source_pis_uid,
        "selected_candidate_uids": list(selected_candidate_uids),
        "limit": _normalize_limit(raw_request.get("limit"), default=DEFAULT_RECENT_LIMIT),
        "session_id": _clean_text(raw_request.get("session_id")),
        "title": _clean_text(raw_request.get("title")),
        "policy_overrides": dict(policy_overrides),
        "overwrite": bool(raw_request.get("overwrite")),
        "rounds": list(rounds),
        "final_recommendation": dict(final_recommendation) if final_recommendation else {},
    }


def _request_id(raw_request: Mapping[str, Any]) -> str:
    request_id = _clean_text(raw_request.get("request_id"))
    if not request_id:
        raise ValueError("request_id is required")
    return request_id


def _ledger_root(repo_root: Path) -> Path:
    return ledger_storage.ledger_patch_instruction_sheets_root(repo_root)


def _recent_rows(repo_root: Path, *, limit: int) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for item_dir in ledger_storage.list_item_dirs(_ledger_root(repo_root)):
        meta = ledger_storage.read_json(item_dir / "meta.json") or {}
        uid = _clean_text(meta.get("uid"))
        status = _clean_text(meta.get("status")).lower()
        if not uid or status not in _ALLOWED_LEDGER_STATUSES:
            continue
        rows.append(
            {
                "uid": uid,
                "title": _clean_text(meta.get("title")) or uid,
                "status": _clean_text(meta.get("status")) or "draft",
                "created_utc": _clean_text(meta.get("created_utc")),
                "updated_utc": _clean_text(meta.get("updated_utc")) or _clean_text(meta.get("created_utc")),
                "path": item_dir.relative_to(repo_root).as_posix(),
            }
        )
    rows.sort(key=lambda row: (row["updated_utc"], row["created_utc"], row["uid"]), reverse=True)
    return rows[:limit]


def list_recent_pis_candidates(*, repo_root: Path | None = None, limit: int = DEFAULT_RECENT_LIMIT) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    normalized_limit = _normalize_limit(limit, default=DEFAULT_RECENT_LIMIT)
    rows = _recent_rows(root, limit=normalized_limit)
    return {
        "schema_version": ENTRYPOINT_RESULT_SCHEMA_VERSION,
        "ok": True,
        "action": "list_recent",
        "request_id": "",
        "status": "listed",
        "blocked": False,
        "error_code": "",
        "operator_summary": f"Listed {len(rows)} recent PIS candidates from durable ledger state.",
        "source_pis": {},
        "recent_candidates": rows,
        "selected_candidate_uids": [],
        "session_id": "",
        "runtime_state": {},
        "recommendation": {
            "status": "not_started",
            "outcome": "",
            "summary": "",
            "rationale_ref": "",
        },
        "artifacts": {},
        "artifact_refs": [],
    }


def error_result(
    *,
    action: str,
    request_id: str = "",
    error_code: str,
    message: str,
) -> Dict[str, Any]:
    safe_action = _clean_text(action) if _clean_text(action) in _ALLOWED_ACTIONS else "launch"
    return {
        "schema_version": ENTRYPOINT_RESULT_SCHEMA_VERSION,
        "ok": False,
        "action": safe_action,
        "request_id": _clean_text(request_id),
        "status": "blocked",
        "blocked": True,
        "error_code": _clean_text(error_code) or "invalid_request",
        "operator_summary": _clean_text(message) or "PIS improvement request blocked.",
        "source_pis": {},
        "recent_candidates": [],
        "selected_candidate_uids": [],
        "session_id": "",
        "runtime_state": {"status": "blocked", "failure_message": _clean_text(message)},
        "recommendation": {
            "status": "blocked",
            "outcome": "",
            "summary": "",
            "rationale_ref": "",
        },
        "artifacts": {},
        "artifact_refs": [],
    }


def validate_result_payload(payload: Mapping[str, Any]) -> Tuple[str, ...]:
    issues: List[str] = []
    if _clean_text(payload.get("schema_version")) != ENTRYPOINT_RESULT_SCHEMA_VERSION:
        issues.append(f"schema_version must remain {ENTRYPOINT_RESULT_SCHEMA_VERSION}")
    for field in RESULT_REQUIRED_FIELDS:
        if field not in payload:
            issues.append(f"missing required field: {field}")
    action = _clean_text(payload.get("action"))
    if action not in _ALLOWED_ACTIONS:
        issues.append(f"unsupported result action: {action or '<empty>'}")
    return tuple(issues)


def _resolve_pis(repo_root: Path, uid: str) -> Dict[str, Any]:
    item_dir = ledger_storage.item_dir_for_uid(_ledger_root(repo_root), uid)
    if item_dir is None:
        raise ValueError(f"PIS UID not found in ledger: {uid}")
    meta = ledger_storage.read_json(item_dir / "meta.json") or {}
    pis_path = item_dir / "pis.md"
    if not pis_path.exists():
        raise ValueError(f"PIS markdown missing for UID: {uid}")
    pis_text = pis_path.read_text(encoding="utf-8", errors="replace")
    return {
        "uid": uid,
        "title": _clean_text(meta.get("title")) or uid,
        "status": _clean_text(meta.get("status")) or "draft",
        "created_utc": _clean_text(meta.get("created_utc")),
        "updated_utc": _clean_text(meta.get("updated_utc")) or _clean_text(meta.get("created_utc")),
        "path": pis_path.relative_to(repo_root).as_posix(),
        "pis_text": pis_text,
        "pis_hash": hashlib.sha256(pis_text.encode("utf-8")).hexdigest(),
        "meta": meta,
    }


def _source_intent_uid(source_row: Mapping[str, Any]) -> str:
    meta = dict(source_row.get("meta") or {})
    integration_meta = dict(meta.get("integration_meta") or {})
    for candidate in (
        integration_meta.get("intent_sheet_uid"),
        meta.get("intent_sheet_uid"),
        meta.get("source_intent_uid"),
    ):
        text = _clean_text(candidate)
        if text:
            return text
    pis_text = str(source_row.get("pis_text") or "")
    match = re.search(r"\*\*Derived from Intent Sheet:\*\*\s*([A-Z]{2}-\d{8}-\d{6}Z-[A-Z0-9]{4})", pis_text)
    if match:
        return match.group(1)
    return "IS-UNKNOWN"


def _session_id(source_uid: str, request_id: str, explicit: str) -> str:
    if explicit:
        return explicit
    suffix = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:10]
    source_suffix = source_uid.replace("-", "_").lower()
    return f"pis_improve_{source_suffix}_{suffix}"


def _artifact_pointers(session_id: str) -> Dict[str, str]:
    prefix = f"runtime/live/pis_improvement/sessions/{session_id}"
    return {
        "session_manifest": f"{prefix}/session.json",
        "candidate_snapshot": f"{prefix}/candidate_snapshot.json",
        "revision_ledger": f"{prefix}/revision_ledger.json",
        "final_recommendation": f"{prefix}/final_recommendation.md",
    }


def _recommendation_summary(path: Path) -> str:
    if not path.exists():
        return ""
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = _clean_text(raw.lstrip("#").strip())
        if text:
            lines.append(text)
    return lines[1] if len(lines) > 1 else (lines[0] if lines else "")


def _build_session(
    raw_request: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[Dict[str, Any], Dict[str, str], List[Dict[str, str]], List[str]]:
    request_id = _request_id(raw_request)
    source_uid = _clean_text(raw_request.get("source_pis_uid"))
    if not source_uid:
        raise ValueError("source_pis_uid is required")
    source = _resolve_pis(repo_root, source_uid)
    limit = _normalize_limit(raw_request.get("limit"), default=DEFAULT_RECENT_LIMIT)
    recent = _recent_rows(repo_root, limit=max(limit + 1, DEFAULT_RECENT_LIMIT))
    non_source = [row for row in recent if row["uid"] != source_uid]
    if not non_source:
        raise ValueError("no recent comparison candidates are available after excluding the source PIS")
    explicit_selected = _clean_text_list(raw_request.get("selected_candidate_uids") or [])
    if not explicit_selected:
        explicit_selected = [non_source[0]["uid"]]
    if source_uid in explicit_selected:
        raise ValueError("selected_candidate_uids may not include the source PIS")
    pool_by_uid = {row["uid"]: row for row in non_source}
    missing = [uid for uid in explicit_selected if uid not in pool_by_uid]
    if missing:
        raise ValueError(f"selected candidate(s) not available in recent deterministic pool: {', '.join(missing)}")

    session_id = _session_id(source_uid, request_id, _clean_text(raw_request.get("session_id")))
    artifacts = _artifact_pointers(session_id)
    iteration_policy = dict(raw_request.get("policy_overrides") or {})
    iteration_cap = _normalize_limit(iteration_policy.get("iteration_cap"), default=DEFAULT_ITERATION_CAP, maximum=10)

    candidate_refs = [
        {
            "candidate_ref": "cand_source_pis",
            "source_kind": "ledger",
            "locator": source_uid,
            "recency_basis": f"meta.updated_utc:{source['updated_utc'] or source['created_utc']}",
        }
    ]
    uid_to_candidate_ref: Dict[str, str] = {source_uid: "cand_source_pis"}
    deterministic_order: List[str] = []
    for index, row in enumerate(non_source, start=1):
        candidate_ref = f"cand_recent_{index:03d}"
        uid_to_candidate_ref[row["uid"]] = candidate_ref
        deterministic_order.append(candidate_ref)
        candidate_refs.append(
            {
                "candidate_ref": candidate_ref,
                "source_kind": "ledger",
                "locator": row["uid"],
                "recency_basis": f"meta.updated_utc:{row['updated_utc'] or row['created_utc']}",
            }
        )
    # The session contract requires replayable ordering across the full
    # materialized candidate pool, including excluded source entries.
    deterministic_order.append("cand_source_pis")

    created_at = _utc_now()
    session = {
        "schema_version": contract.SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "session_version": "1.0.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": _clean_text(raw_request.get("title")) or f"Improve {source_uid} through the shared deterministic entrypoint contract",
            "created_at_utc": created_at,
            "source_intent_uid": _source_intent_uid(source),
            "ledger_refs": [contract.UID, source_uid],
        },
        "source_pis_ref": {
            "ledger_uid": source_uid,
            "pis_path": source["path"],
            "content_sha256": source["pis_hash"],
        },
        "model_profiles": [
            {
                "profile_id": "reviewer_default",
                "role": "reviewer",
                "model_family": "gpt-5.4",
                "selection_reason": "Default reviewer profile for deterministic PIS critique rounds.",
            },
            {
                "profile_id": "reviser_default",
                "role": "reviser",
                "model_family": "gpt-5.4-mini",
                "selection_reason": "Default reviser profile for deterministic PIS revision passes.",
            },
        ],
        "iteration_policy": {
            "default_iteration_cap": iteration_cap,
            "stop_conditions": list(_DEFAULT_STOP_CONDITIONS),
            "fail_closed_on_invalid_revision_entry": True,
        },
        "workflow_state": {
            "status": "draft",
            "current_iteration": 0,
            "last_transition_utc": created_at,
        },
        "candidate_selection": {
            "selection_basis": "recent_ledger_items",
            "candidate_pool_materialization_ref": artifacts["candidate_snapshot"],
            "preserved_filters": [
                {
                    "filter_id": "filter_recent_allowed_ledger_statuses",
                    "field": "ledger_status",
                    "operator": "in",
                    "value": ",".join(sorted(_ALLOWED_LEDGER_STATUSES)),
                }
            ],
            "preserved_exclusions": [
                {
                    "exclusion_id": "exclude_source_pis",
                    "candidate_ref": "cand_source_pis",
                    "reason_code": "source_pis_under_review_not_candidate",
                }
            ],
            "candidate_refs": candidate_refs,
            "selected_candidate_refs": [uid_to_candidate_ref[uid] for uid in explicit_selected],
            "deterministic_order": deterministic_order,
        },
        "artifact_pointers": dict(artifacts),
        "revision_ledger": {
            "ledger_schema_version": contract.SESSION_SCHEMA_VERSION,
            "critique_rounds": [],
            "revision_deltas": [],
            "recommendation_history": [],
        },
    }
    return session, source, non_source[:limit], explicit_selected


def _result_from_session(
    *,
    repo_root: Path,
    raw_request: Mapping[str, Any],
    source: Mapping[str, str],
    recent_candidates: Sequence[Mapping[str, str]],
    selected_candidate_uids: Sequence[str],
    session: Mapping[str, Any],
    runtime_state: Mapping[str, Any],
    ok: bool,
    error_code: str = "",
) -> Dict[str, Any]:
    artifacts = dict(session.get("artifact_pointers") or {})
    runtime_state_path = (
        Path(str(artifacts.get("session_manifest") or "")).parent / "runtime_state.json"
        if artifacts.get("session_manifest")
        else None
    )
    events_path = (
        Path(str(artifacts.get("session_manifest") or "")).parent / "events.jsonl"
        if artifacts.get("session_manifest")
        else None
    )
    if runtime_state_path is not None:
        artifacts["runtime_state"] = runtime_state_path.as_posix()
    if events_path is not None:
        artifacts["events"] = events_path.as_posix()

    recommendation_history = list(session.get("revision_ledger", {}).get("recommendation_history") or [])
    latest = dict(recommendation_history[-1]) if recommendation_history else {}
    recommendation_path = repo_root / str(artifacts.get("final_recommendation") or "")
    recommendation_summary = _recommendation_summary(recommendation_path)
    runtime_status = _clean_text(runtime_state.get("status")) or _clean_text(session.get("workflow_state", {}).get("status")) or "unknown"
    blocked = runtime_status in {"blocked", "failed"} or not ok
    if latest:
        operator_summary = recommendation_summary or f"Improvement session completed with outcome {latest.get('outcome') or 'unknown'}."
        recommendation_status = "available"
    elif blocked:
        operator_summary = _clean_text(runtime_state.get("failure_message")) or error_code or "Improvement session blocked."
        recommendation_status = "blocked"
    else:
        operator_summary = f"Improvement session launched for {source['uid']}; durable artifacts are ready for later critique rounds."
        recommendation_status = "pending"
    return {
        "schema_version": ENTRYPOINT_RESULT_SCHEMA_VERSION,
        "ok": bool(ok),
        "action": "launch",
        "request_id": _request_id(raw_request),
        "status": runtime_status,
        "blocked": blocked,
        "error_code": _clean_text(error_code),
        "operator_summary": operator_summary,
        "source_pis": {key: source[key] for key in ("uid", "title", "status", "path")},
        "recent_candidates": [dict(row) for row in recent_candidates],
        "selected_candidate_uids": list(selected_candidate_uids),
        "session_id": _clean_text(session.get("session_id")),
        "runtime_state": dict(runtime_state),
        "recommendation": {
            "status": recommendation_status,
            "outcome": _clean_text(latest.get("outcome")),
            "summary": recommendation_summary,
            "rationale_ref": _clean_text(latest.get("rationale_ref")) or _clean_text(artifacts.get("final_recommendation")),
        },
        "artifacts": artifacts,
        "artifact_refs": _clean_text_list(list(artifacts.values())),
    }


def launch_pis_improvement(raw_request: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    session, source, recent_candidates, selected_candidate_uids = _build_session(raw_request, repo_root=root)
    manifest_path = root / str(session["artifact_pointers"]["session_manifest"])
    session_input: Mapping[str, Any] | Path = session
    if manifest_path.exists() and not bool(raw_request.get("overwrite")):
        existing = fsio.read_json_dict_strict(manifest_path)
        existing_source_uid = _clean_text(existing.get("source_pis_ref", {}).get("ledger_uid"))
        if existing_source_uid != source["uid"]:
            raise ValueError("existing session_id points at a different source PIS")
        session_input = manifest_path
    result = orchestration.orchestrate_pis_improvement_session(
        session_input,
        repo_root=root,
        overwrite=bool(raw_request.get("overwrite")),
        rounds=list(raw_request.get("rounds") or []),
        final_recommendation=dict(raw_request.get("final_recommendation") or {}) or None,
    )
    runtime_state = dict(result.get("runtime_state") or {})
    session_payload = dict(result.get("session") or (session if isinstance(session_input, Mapping) else fsio.read_json_dict_strict(manifest_path)))
    return _result_from_session(
        repo_root=root,
        raw_request=raw_request,
        source=source,
        recent_candidates=recent_candidates,
        selected_candidate_uids=selected_candidate_uids,
        session=session_payload,
        runtime_state=runtime_state,
        ok=bool(result.get("ok")),
        error_code=_clean_text(result.get("error")),
    )


def run_request(raw_request: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    request = _validate_request(raw_request)
    action = request["action"]
    if action == "list_recent":
        result = list_recent_pis_candidates(repo_root=root, limit=int(request["limit"]))
        result["request_id"] = str(request["request_id"])
        return result
    if action == "launch":
        return launch_pis_improvement(request, repo_root=root)
    raise ValueError(f"unsupported action: {action}")
