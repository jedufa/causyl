"""Mission-fit admission checks for durable planning/decomposition surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from infra.controlplane import plane_mission_contract


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _normalize_text(value).casefold() in {"1", "true", "yes", "y", "allow", "allowed"}


def _normalize_exception(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "allowed": _normalize_bool(raw.get("allowed")),
        "reason": _normalize_text(raw.get("reason")),
        "approved_by": _normalize_text(raw.get("approved_by")),
    }


def assess_intent_mission_fit(
    *,
    title: str,
    intent_markdown: str,
    exception_record: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    mission = plane_mission_contract.read_plane_mission_contract(repo_root=repo_root)
    if not mission.get("ok") and repo_root is not None:
        mission = plane_mission_contract.read_plane_mission_contract()
    if not mission.get("ok"):
        return {
            "ok": False,
            "status": "unclear",
            "classification": "blocked",
            "decision": "block",
            "summary": "Mission-fit admission could not load the canonical mission contract.",
            "reason_codes": ["mission_contract_unavailable"],
            "matched_aligned_boundaries": [],
            "matched_non_goals": [],
            "exception": _normalize_exception(exception_record),
            "source_fingerprint": "",
            "mission_id": "",
        }

    contract = mission.get("contract") if isinstance(mission.get("contract"), Mapping) else {}
    body = f"{_normalize_text(title)}\n{_normalize_text(intent_markdown)}".casefold()
    aligned = [
        text
        for text in list(contract.get("aligned_expansion_boundaries") or [])
        if _normalize_text(text) and _normalize_text(text).casefold() in body
    ]
    non_goals = [
        text
        for text in list(contract.get("explicit_non_goals") or [])
        if _normalize_text(text) and _normalize_text(text).casefold() in body
    ]
    normalized_exception = _normalize_exception(exception_record)
    exception_allowed = bool(normalized_exception["allowed"] and normalized_exception["reason"])

    if non_goals and exception_allowed:
        return {
            "ok": True,
            "status": "partially_aligned",
            "classification": "needs_exception",
            "decision": "allow_with_exception",
            "summary": "Intent overlaps an explicit mission non-goal but carries a recorded exception.",
            "reason_codes": ["non_goal_exception_used"],
            "matched_aligned_boundaries": aligned,
            "matched_non_goals": non_goals,
            "exception": normalized_exception,
            "source_fingerprint": _normalize_text(contract.get("source_fingerprint")),
            "mission_id": _normalize_text(contract.get("mission_id")),
        }

    if non_goals:
        return {
            "ok": False,
            "status": "mission_misaligned",
            "classification": "blocked",
            "decision": "block",
            "summary": "Intent overlaps an explicit mission non-goal and has no recorded exception.",
            "reason_codes": ["explicit_non_goal_match"],
            "matched_aligned_boundaries": aligned,
            "matched_non_goals": non_goals,
            "exception": normalized_exception,
            "source_fingerprint": _normalize_text(contract.get("source_fingerprint")),
            "mission_id": _normalize_text(contract.get("mission_id")),
        }

    if aligned:
        return {
            "ok": True,
            "status": "mission_aligned",
            "classification": "aligned",
            "decision": "allow",
            "summary": "Intent stays within mission boundaries and references an aligned expansion area.",
            "reason_codes": ["aligned_boundary_match"],
            "matched_aligned_boundaries": aligned,
            "matched_non_goals": [],
            "exception": normalized_exception,
            "source_fingerprint": _normalize_text(contract.get("source_fingerprint")),
            "mission_id": _normalize_text(contract.get("mission_id")),
        }

    if exception_allowed:
        return {
            "ok": True,
            "status": "partially_aligned",
            "classification": "needs_exception",
            "decision": "allow_with_exception",
            "summary": "Intent does not match any aligned mission boundary but carries a recorded exception.",
            "reason_codes": ["mission_boundary_exception_used"],
            "matched_aligned_boundaries": [],
            "matched_non_goals": [],
            "exception": normalized_exception,
            "source_fingerprint": _normalize_text(contract.get("source_fingerprint")),
            "mission_id": _normalize_text(contract.get("mission_id")),
        }

    return {
        "ok": False,
        "status": "mission_misaligned",
        "classification": "mismatch",
        "decision": "block",
        "summary": "Intent does not match any aligned mission boundary and has no recorded exception.",
        "reason_codes": ["no_aligned_boundary_match"],
        "matched_aligned_boundaries": [],
        "matched_non_goals": [],
        "exception": normalized_exception,
        "source_fingerprint": _normalize_text(contract.get("source_fingerprint")),
        "mission_id": _normalize_text(contract.get("mission_id")),
    }
