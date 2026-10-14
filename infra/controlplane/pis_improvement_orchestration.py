"""Deterministic filesystem-backed orchestration for PIS improvement sessions."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from infra.controlplane import pis_improvement_session_contract as contract
from tools.shared import fsio


SESSION_RUNTIME_SCHEMA_VERSION = "pis_improvement_runtime_state_v1"
ROUND_RECORD_SCHEMA_VERSION = "pis_improvement_round_record_v1"
CANDIDATE_SNAPSHOT_SCHEMA_VERSION = "pis_improvement_candidate_snapshot_v1"

RUNTIME_STATUSES = frozenset({"draft", "running", "resumable", "blocked", "completed", "failed"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return fsio.utc_now_iso()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _ensure_safe_path_component(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value)
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValueError(f"{field_name} must be a single safe path component")
    return text


def _ensure_safe_repo_rel(path_text: str, *, field_name: str) -> Path:
    text = _normalize_text(path_text)
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} must be a safe repo-relative path")
    return path


def _resolve_repo_rel(repo_root: Path, path_text: str, *, field_name: str) -> Path:
    return repo_root / _ensure_safe_repo_rel(path_text, field_name=field_name)


def _session_root(repo_root: Path, session: Mapping[str, Any]) -> Path:
    manifest_rel = session.get("artifact_pointers", {}).get("session_manifest")
    manifest_path = _resolve_repo_rel(repo_root, str(manifest_rel or ""), field_name="artifact_pointers.session_manifest")
    return manifest_path.parent


def _runtime_state_path(repo_root: Path, session: Mapping[str, Any]) -> Path:
    return _session_root(repo_root, session) / "runtime_state.json"


def _events_path(repo_root: Path, session: Mapping[str, Any]) -> Path:
    return _session_root(repo_root, session) / "events.jsonl"


def _round_dir(repo_root: Path, session: Mapping[str, Any], round_id: str) -> Path:
    return _session_root(repo_root, session) / "rounds" / _ensure_safe_path_component(round_id, field_name="round_id")


def _round_record_path(repo_root: Path, session: Mapping[str, Any], round_id: str) -> Path:
    return _round_dir(repo_root, session, round_id) / "round_record.json"


def _build_candidate_snapshot(session: Mapping[str, Any]) -> Dict[str, Any]:
    selection = copy.deepcopy(dict(session.get("candidate_selection") or {}))
    return {
        "schema_version": CANDIDATE_SNAPSHOT_SCHEMA_VERSION,
        "session_id": _normalize_text(session.get("session_id")),
        "created_at_utc": _utc_now(),
        "selection_basis": selection.get("selection_basis", ""),
        "candidate_pool_materialization_ref": selection.get("candidate_pool_materialization_ref", ""),
        "preserved_filters": list(selection.get("preserved_filters") or []),
        "preserved_exclusions": list(selection.get("preserved_exclusions") or []),
        "candidate_refs": list(selection.get("candidate_refs") or []),
        "selected_candidate_refs": list(selection.get("selected_candidate_refs") or []),
        "deterministic_order": list(selection.get("deterministic_order") or []),
    }


def _build_runtime_state(
    session: Mapping[str, Any],
    *,
    status: str,
    current_iteration: int,
    last_completed_round_id: str = "",
    failure_code: str = "",
    failure_message: str = "",
) -> Dict[str, Any]:
    if status not in RUNTIME_STATUSES:
        raise ValueError(f"unsupported runtime status {status}")
    return {
        "schema_version": SESSION_RUNTIME_SCHEMA_VERSION,
        "session_id": _normalize_text(session.get("session_id")),
        "status": status,
        "current_iteration": int(current_iteration),
        "last_completed_round_id": _normalize_text(last_completed_round_id),
        "updated_at_utc": _utc_now(),
        "failure_code": _normalize_text(failure_code),
        "failure_message": _normalize_text(failure_message),
    }


def _write_runtime_state(
    repo_root: Path,
    session: Mapping[str, Any],
    *,
    status: str,
    current_iteration: int,
    last_completed_round_id: str = "",
    failure_code: str = "",
    failure_message: str = "",
) -> Dict[str, Any]:
    payload = _build_runtime_state(
        session,
        status=status,
        current_iteration=current_iteration,
        last_completed_round_id=last_completed_round_id,
        failure_code=failure_code,
        failure_message=failure_message,
    )
    fsio.write_json_atomic(_runtime_state_path(repo_root, session), payload)
    return payload


def _load_runtime_state(repo_root: Path, session: Mapping[str, Any]) -> Dict[str, Any]:
    return fsio.read_json_dict(_runtime_state_path(repo_root, session))


def _write_round_bundle(
    repo_root: Path,
    session: Mapping[str, Any],
    *,
    round_record: Mapping[str, Any],
    critique_packet: Mapping[str, Any],
    reviser_packet: Mapping[str, Any],
    proposed_revision_text: str,
) -> None:
    round_id = _normalize_text(round_record.get("round_id"))
    round_dir = _round_dir(repo_root, session, round_id)
    fsio.write_json_atomic(round_dir / "critique_packet.json", dict(critique_packet))
    fsio.write_json_atomic(round_dir / "reviser_packet.json", dict(reviser_packet))
    revision_path = round_dir / "revision_delta.patch"
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(proposed_revision_text, encoding="utf-8")
    # Treat round_record.json as the durable commit marker for the whole bundle.
    fsio.write_json_atomic(round_dir / "round_record.json", dict(round_record))


def _validate_round_record_bundle(
    repo_root: Path,
    session: Mapping[str, Any],
    round_record: Mapping[str, Any],
) -> Dict[str, Any] | None:
    round_id = _normalize_text(round_record.get("round_id"))
    round_dir = _round_dir(repo_root, session, round_id)
    missing: list[str] = []
    for required_name in ("critique_packet.json", "reviser_packet.json", "revision_delta.patch"):
        if not (round_dir / required_name).exists():
            missing.append(required_name)
    if not missing:
        return None
    return {
        "code": "incomplete_round_bundle",
        "message": f"round {round_id or '<unknown>'} is missing durable artifact(s): {', '.join(missing)}",
        "round_id": round_id,
    }


def _load_round_records(repo_root: Path, session: Mapping[str, Any]) -> tuple[list[Dict[str, Any]], Dict[str, Any] | None]:
    rounds_root = _session_root(repo_root, session) / "rounds"
    if not rounds_root.exists():
        return [], None
    rows: list[Dict[str, Any]] = []
    seen_round_ids: set[str] = set()
    seen_delta_ids: set[str] = set()
    for path in sorted(rounds_root.glob("*/round_record.json")):
        row = fsio.read_json_dict(path)
        if row:
            bundle_error = _validate_round_record_bundle(repo_root, session, row)
            if bundle_error is not None:
                return [], bundle_error
            round_id = _normalize_text(row.get("round_id"))
            if round_id in seen_round_ids:
                return [], {
                    "code": "duplicate_round_id",
                    "message": f"round_id {round_id} appears more than once in durable round history",
                    "round_id": round_id,
                }
            if round_id:
                seen_round_ids.add(round_id)
            delta_id = _normalize_text(row.get("revision_delta", {}).get("delta_id"))
            if delta_id in seen_delta_ids:
                return [], {
                    "code": "duplicate_delta_id",
                    "message": f"delta_id {delta_id} appears more than once in durable round history",
                    "delta_id": delta_id,
                }
            if delta_id:
                seen_delta_ids.add(delta_id)
            rows.append(row)
    rows.sort(key=lambda row: (int(row.get("iteration_index") or 0), _normalize_text(row.get("round_id"))))
    return rows, None


def _materialize_revision_ledger(round_records: Sequence[Mapping[str, Any]], recommendation_history: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    critique_rounds = []
    revision_deltas = []
    for row in round_records:
        critique_round = row.get("critique_round")
        revision_delta = row.get("revision_delta")
        if isinstance(critique_round, Mapping):
            critique_rounds.append(copy.deepcopy(dict(critique_round)))
        if isinstance(revision_delta, Mapping):
            revision_deltas.append(copy.deepcopy(dict(revision_delta)))
    return {
        "ledger_schema_version": contract.SESSION_SCHEMA_VERSION,
        "critique_rounds": critique_rounds,
        "revision_deltas": revision_deltas,
        "recommendation_history": [copy.deepcopy(dict(row)) for row in recommendation_history if isinstance(row, Mapping)],
    }


def _write_session_aggregate(repo_root: Path, session: Mapping[str, Any]) -> None:
    manifest_path = _resolve_repo_rel(
        repo_root,
        str(session.get("artifact_pointers", {}).get("session_manifest") or ""),
        field_name="artifact_pointers.session_manifest",
    )
    ledger_path = _resolve_repo_rel(
        repo_root,
        str(session.get("artifact_pointers", {}).get("revision_ledger") or ""),
        field_name="artifact_pointers.revision_ledger",
    )
    fsio.write_json_atomic(ledger_path, dict(session.get("revision_ledger") or {}))
    fsio.write_json_atomic(manifest_path, dict(session))


def _write_final_recommendation_text(repo_root: Path, session: Mapping[str, Any], content: str) -> None:
    out_path = _resolve_repo_rel(
        repo_root,
        str(session.get("artifact_pointers", {}).get("final_recommendation") or ""),
        field_name="artifact_pointers.final_recommendation",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def _append_event(repo_root: Path, session: Mapping[str, Any], event: Mapping[str, Any]) -> None:
    fsio.append_jsonl(_events_path(repo_root, session), dict(event))


def _default_candidate_ref(session: Mapping[str, Any], iteration_index: int) -> str:
    selection = session.get("candidate_selection", {})
    selected = [item for item in list(selection.get("selected_candidate_refs") or []) if _normalize_text(item)]
    deterministic_order = [item for item in list(selection.get("deterministic_order") or []) if _normalize_text(item)]
    if selected:
        if len(selected) == 1:
            return selected[0]
        index = min(iteration_index - 1, len(selected) - 1)
        return selected[index]
    if deterministic_order:
        index = min(iteration_index - 1, len(deterministic_order) - 1)
        return deterministic_order[index]
    return ""


def _profile_id_for_role(session: Mapping[str, Any], role: str) -> str:
    for profile in list(session.get("model_profiles") or []):
        if _normalize_text(profile.get("role")).lower() == role:
            return _normalize_text(profile.get("profile_id"))
    return ""


def _recommendation_entry(
    session: Mapping[str, Any],
    recommendation: Mapping[str, Any],
) -> Dict[str, Any]:
    winning_candidate_ref = _normalize_text(recommendation.get("winning_candidate_ref")) or _default_candidate_ref(session, 1)
    return {
        "recommendation_id": _normalize_text(recommendation.get("recommendation_id")) or "recommendation_001",
        "decided_at_utc": _normalize_text(recommendation.get("decided_at_utc")) or _utc_now(),
        "outcome": _normalize_text(recommendation.get("outcome")),
        "winning_candidate_ref": winning_candidate_ref,
        "rationale_ref": _normalize_text(session.get("artifact_pointers", {}).get("final_recommendation")),
    }


def _merge_recommendation_history(
    session: Mapping[str, Any],
    existing_history: Sequence[Mapping[str, Any]],
    recommendation: Mapping[str, Any],
) -> tuple[list[Dict[str, Any]], Dict[str, Any] | None]:
    entry = _recommendation_entry(session, recommendation)
    merged: list[Dict[str, Any]] = [copy.deepcopy(dict(row)) for row in existing_history if isinstance(row, Mapping)]
    for existing in merged:
        if _normalize_text(existing.get("recommendation_id")) != entry["recommendation_id"]:
            continue
        if existing == entry:
            return merged, None
        return merged, {
            "code": "recommendation_history_conflict",
            "message": f"recommendation_id {entry['recommendation_id']} already exists with different durable content",
        }
    merged.append(entry)
    return merged, None


def _write_blocked_runtime_state(
    repo_root: Path,
    session: Mapping[str, Any],
    *,
    current_iteration: int,
    last_completed_round_id: str,
    failure_code: str,
    failure_message: str,
) -> Dict[str, Any]:
    return _write_runtime_state(
        repo_root,
        session,
        status="blocked",
        current_iteration=current_iteration,
        last_completed_round_id=last_completed_round_id,
        failure_code=failure_code,
        failure_message=failure_message,
    )


def materialize_pis_improvement_session(
    raw_session: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    normalized = contract.normalize_pis_improvement_session(dict(raw_session or {}))
    session_dir = _session_root(root, normalized)
    manifest_path = _resolve_repo_rel(
        root,
        str(normalized.get("artifact_pointers", {}).get("session_manifest") or ""),
        field_name="artifact_pointers.session_manifest",
    )
    if manifest_path.exists() and not overwrite:
        return {"ok": False, "error": "session_exists", "session": normalized}

    snapshot = _build_candidate_snapshot(normalized)
    candidate_snapshot_path = _resolve_repo_rel(
        root,
        str(normalized.get("artifact_pointers", {}).get("candidate_snapshot") or ""),
        field_name="artifact_pointers.candidate_snapshot",
    )
    fsio.write_json_atomic(candidate_snapshot_path, snapshot)
    normalized["revision_ledger"] = _materialize_revision_ledger([], normalized.get("revision_ledger", {}).get("recommendation_history", []))
    _write_session_aggregate(root, normalized)
    runtime = _write_runtime_state(
        root,
        normalized,
        status="draft" if _normalize_text(normalized.get("workflow_state", {}).get("status")) == "draft" else "resumable",
        current_iteration=int(normalized.get("workflow_state", {}).get("current_iteration") or 0),
    )
    _append_event(
        root,
        normalized,
        {
            "event": "materialized",
            "session_id": normalized.get("session_id"),
            "session_root": session_dir.relative_to(root).as_posix(),
            "at_utc": _utc_now(),
        },
    )
    return {"ok": True, "session": normalized, "runtime_state": runtime}


def _load_session(session_or_path: Mapping[str, Any] | str | Path, *, repo_root: Path) -> Dict[str, Any]:
    if isinstance(session_or_path, Mapping):
        return contract.normalize_pis_improvement_session(dict(session_or_path))
    payload = fsio.read_json_dict_strict(Path(session_or_path))
    return contract.normalize_pis_improvement_session(payload)


def rebuild_session_from_round_records(
    session_or_path: Mapping[str, Any] | str | Path,
    *,
    repo_root: Path | None = None,
    final_recommendation: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    session = _load_session(session_or_path, repo_root=root)
    runtime = _load_runtime_state(root, session)
    round_records, bundle_error = _load_round_records(root, session)
    if bundle_error is not None:
        runtime = _write_blocked_runtime_state(
            root,
            session,
            current_iteration=int(runtime.get("current_iteration") or 0),
            last_completed_round_id=_normalize_text(runtime.get("last_completed_round_id")),
            failure_code=bundle_error["code"],
            failure_message=bundle_error["message"],
        )
        return {"ok": False, "error": bundle_error["code"], "runtime_state": runtime, "session": session}
    current_iteration = len(round_records)
    last_completed_round_id = _normalize_text(round_records[-1].get("round_id")) if round_records else ""
    recommendation_history = []
    existing_history = session.get("revision_ledger", {}).get("recommendation_history")
    if isinstance(existing_history, list):
        recommendation_history.extend(copy.deepcopy(existing_history))
    if final_recommendation is not None:
        recommendation_history, history_error = _merge_recommendation_history(
            session,
            recommendation_history,
            final_recommendation,
        )
        if history_error is not None:
            runtime = _write_blocked_runtime_state(
                root,
                session,
                current_iteration=current_iteration,
                last_completed_round_id=last_completed_round_id,
                failure_code=history_error["code"],
                failure_message=history_error["message"],
            )
            return {"ok": False, "error": history_error["code"], "runtime_state": runtime, "session": session}
        try:
            _write_final_recommendation_text(root, session, _normalize_text(final_recommendation.get("rationale_markdown")))
        except Exception as exc:
            runtime = _write_blocked_runtime_state(
                root,
                session,
                current_iteration=current_iteration,
                last_completed_round_id=last_completed_round_id,
                failure_code="final_recommendation_persist_failed",
                failure_message=str(exc),
            )
            return {
                "ok": False,
                "error": "final_recommendation_persist_failed",
                "runtime_state": runtime,
                "session": session,
            }

    session["revision_ledger"] = _materialize_revision_ledger(round_records, recommendation_history)
    session["workflow_state"]["current_iteration"] = len(round_records)
    session["workflow_state"]["last_transition_utc"] = _utc_now()

    if recommendation_history:
        session["workflow_state"]["status"] = "completed"
    else:
        session["workflow_state"]["status"] = "running" if round_records else "draft"

    try:
        _write_session_aggregate(root, session)
    except Exception as exc:
        runtime = _write_blocked_runtime_state(
            root,
            session,
            current_iteration=current_iteration,
            last_completed_round_id=last_completed_round_id,
            failure_code="aggregate_persist_failed",
            failure_message=str(exc),
        )
        return {"ok": False, "error": "aggregate_persist_failed", "runtime_state": runtime, "session": session}
    next_status = "completed" if recommendation_history else "resumable"
    updated_runtime = _write_runtime_state(
        root,
        session,
        status=next_status,
        current_iteration=current_iteration,
        last_completed_round_id=last_completed_round_id,
    )
    if runtime.get("status") == "blocked":
        _append_event(
            root,
            session,
            {
                "event": "recovered_from_round_records",
                "session_id": session.get("session_id"),
                "at_utc": _utc_now(),
                "completed_rounds": len(round_records),
            },
        )
    return {"ok": True, "session": session, "runtime_state": updated_runtime}


def orchestrate_pis_improvement_session(
    session_or_path: Mapping[str, Any] | str | Path,
    *,
    rounds: Sequence[Mapping[str, Any]] = (),
    final_recommendation: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    if isinstance(session_or_path, Mapping):
        materialized = materialize_pis_improvement_session(session_or_path, repo_root=root, overwrite=overwrite)
        if not materialized["ok"]:
            return materialized
        session = materialized["session"]
    else:
        session = _load_session(session_or_path, repo_root=root)

    recovered = rebuild_session_from_round_records(session, repo_root=root)
    if not recovered["ok"]:
        return recovered
    session = recovered["session"]
    if session.get("revision_ledger", {}).get("recommendation_history"):
        if rounds:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=int(session.get("workflow_state", {}).get("current_iteration") or 0),
                last_completed_round_id=_normalize_text(session.get("revision_ledger", {}).get("critique_rounds", [{}])[-1].get("round_id"))
                if session.get("revision_ledger", {}).get("critique_rounds")
                else "",
                failure_code="session_already_completed",
                failure_message="completed sessions may not accept additional critique rounds",
            )
            return {"ok": False, "error": "session_already_completed", "runtime_state": runtime, "session": session}
        if final_recommendation is not None:
            return rebuild_session_from_round_records(session, repo_root=root, final_recommendation=final_recommendation)
        return {"ok": True, "session": session, "runtime_state": _load_runtime_state(root, session)}

    iteration_cap = int(session.get("iteration_policy", {}).get("default_iteration_cap") or 0)
    round_records, bundle_error = _load_round_records(root, session)
    if bundle_error is not None:
        runtime = _write_blocked_runtime_state(
            root,
            session,
            current_iteration=int(session.get("workflow_state", {}).get("current_iteration") or 0),
            last_completed_round_id="",
            failure_code=bundle_error["code"],
            failure_message=bundle_error["message"],
        )
        return {"ok": False, "error": bundle_error["code"], "runtime_state": runtime, "session": session}
    next_iteration = len(round_records) + 1
    existing_round_ids = {_normalize_text(row.get("round_id")) for row in round_records}
    existing_delta_ids = {_normalize_text(row.get("revision_delta", {}).get("delta_id")) for row in round_records}

    for raw_round in rounds:
        if next_iteration > iteration_cap:
            _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="iteration_cap_exceeded",
                failure_message="iteration cap exceeded before processing the next critique round",
            )
            return {"ok": False, "error": "iteration_cap_exceeded", "session": session}

        candidate_ref = _normalize_text(raw_round.get("candidate_ref")) or _default_candidate_ref(session, next_iteration)
        reviewer_profile_id = _normalize_text(raw_round.get("reviewer_profile_id")) or _profile_id_for_role(session, "reviewer")
        reviser_profile_id = _normalize_text(raw_round.get("reviser_profile_id")) or _profile_id_for_role(session, "reviser")
        try:
            round_id = _ensure_safe_path_component(
                _normalize_text(raw_round.get("round_id")) or f"round_{next_iteration:03d}",
                field_name="round_id",
            )
        except ValueError as exc:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="invalid_round_id",
                failure_message=str(exc),
            )
            return {"ok": False, "error": "invalid_round_id", "runtime_state": runtime, "session": session}
        if round_id in existing_round_ids:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="duplicate_round_id",
                failure_message=f"round_id {round_id} already exists in durable session history",
            )
            return {"ok": False, "error": "duplicate_round_id", "runtime_state": runtime, "session": session}
        critique_packet_ref = (
            _round_dir(root, session, round_id).relative_to(root).as_posix() + "/critique_packet.json"
        )
        proposed_revision_ref = (
            _round_dir(root, session, round_id).relative_to(root).as_posix() + "/revision_delta.patch"
        )
        delta_id = _normalize_text(raw_round.get("delta_id")) or f"delta_{next_iteration:03d}"
        if delta_id in existing_delta_ids:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="duplicate_delta_id",
                failure_message=f"delta_id {delta_id} already exists in durable session history",
            )
            return {"ok": False, "error": "duplicate_delta_id", "runtime_state": runtime, "session": session}

        critique_packet = copy.deepcopy(dict(raw_round.get("critique_packet") or {}))
        critique_packet.setdefault("packet_schema_version", contract.CRITIQUE_PACKET_SCHEMA_VERSION)
        critique_packet.setdefault("packet_id", f"critique_packet_{next_iteration:03d}")
        critique_packet.setdefault("round_id", round_id)
        critique_packet.setdefault("candidate_ref", candidate_ref)
        critique_packet.setdefault("reviewer_profile_id", reviewer_profile_id)

        reviser_packet = copy.deepcopy(dict(raw_round.get("reviser_packet") or {}))
        reviser_packet.setdefault("packet_schema_version", contract.REVISER_PACKET_SCHEMA_VERSION)
        reviser_packet.setdefault("packet_id", f"reviser_packet_{next_iteration:03d}")
        reviser_packet.setdefault("round_id", round_id)
        reviser_packet.setdefault("candidate_ref", candidate_ref)
        reviser_packet.setdefault("reviser_profile_id", reviser_profile_id)
        reviser_packet.setdefault("source_critique_packet_id", critique_packet["packet_id"])

        critique_result = contract.validate_pis_improvement_critique_packet(
            critique_packet,
            expected_round_id=round_id,
            expected_candidate_ref=candidate_ref,
            expected_reviewer_profile_id=reviewer_profile_id,
        )
        if not critique_result["ok"]:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="invalid_critique_packet",
                failure_message=critique_result["errors"][0]["message"],
            )
            return {"ok": False, "error": "invalid_critique_packet", "validation": critique_result, "runtime_state": runtime}

        reviser_result = contract.validate_pis_improvement_reviser_packet(
            reviser_packet,
            expected_round_id=round_id,
            expected_candidate_ref=candidate_ref,
            expected_reviser_profile_id=reviser_profile_id,
            expected_critique_packet=critique_packet,
        )
        if not reviser_result["ok"]:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="invalid_reviser_packet",
                failure_message=reviser_result["errors"][0]["message"],
            )
            return {"ok": False, "error": "invalid_reviser_packet", "validation": reviser_result, "runtime_state": runtime}

        _write_runtime_state(
            root,
            session,
            status="running",
            current_iteration=len(round_records),
            last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
        )
        critique_round = {
            "round_id": round_id,
            "iteration_index": next_iteration,
            "candidate_ref": candidate_ref,
            "reviewer_profile_id": reviewer_profile_id,
            "critique_packet_ref": critique_packet_ref,
            "critique_packet": critique_result["normalized_packet"],
            "status": _normalize_text(raw_round.get("status")) or "accepted",
        }
        revision_delta = {
            "delta_id": delta_id,
            "round_id": round_id,
            "reviser_profile_id": reviser_profile_id,
            "proposed_revision_ref": proposed_revision_ref,
            "reviser_packet": reviser_result["normalized_packet"],
            "change_summary": _normalize_text(raw_round.get("change_summary")) or _normalize_text(reviser_packet.get("summary")),
        }
        round_record = {
            "schema_version": ROUND_RECORD_SCHEMA_VERSION,
            "session_id": _normalize_text(session.get("session_id")),
            "round_id": round_id,
            "iteration_index": next_iteration,
            "candidate_ref": candidate_ref,
            "recorded_at_utc": _utc_now(),
            "critique_round": critique_round,
            "revision_delta": revision_delta,
        }
        try:
            _write_round_bundle(
                root,
                session,
                round_record=round_record,
                critique_packet=critique_result["normalized_packet"],
                reviser_packet=reviser_result["normalized_packet"],
                proposed_revision_text=_normalize_text(raw_round.get("proposed_revision_text")),
            )
            _append_event(
                root,
                session,
                {"event": "round_recorded", "session_id": session.get("session_id"), "round_id": round_id, "at_utc": _utc_now()},
            )
            round_records.append(round_record)
            session["revision_ledger"] = _materialize_revision_ledger(
                round_records,
                session.get("revision_ledger", {}).get("recommendation_history", []),
            )
            session["workflow_state"]["current_iteration"] = next_iteration
            session["workflow_state"]["status"] = "running"
            session["workflow_state"]["last_transition_utc"] = _utc_now()
            _write_session_aggregate(root, session)
            _write_runtime_state(
                root,
                session,
                status="resumable",
                current_iteration=next_iteration,
                last_completed_round_id=round_id,
            )
            existing_round_ids.add(round_id)
            existing_delta_ids.add(delta_id)
        except Exception as exc:
            runtime = _write_runtime_state(
                root,
                session,
                status="blocked",
                current_iteration=len(round_records),
                last_completed_round_id=_normalize_text(round_records[-1].get("round_id")) if round_records else "",
                failure_code="aggregate_persist_failed",
                failure_message=str(exc),
            )
            return {"ok": False, "error": "aggregate_persist_failed", "runtime_state": runtime, "session": session}
        next_iteration += 1

    if final_recommendation is not None:
        return rebuild_session_from_round_records(session, repo_root=root, final_recommendation=final_recommendation)

    return {"ok": True, "session": session, "runtime_state": _load_runtime_state(root, session)}
