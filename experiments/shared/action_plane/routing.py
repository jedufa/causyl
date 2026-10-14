"""Deterministic phase-1 action routing and registry validation."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Optional

from .capability_registry import validate_tool_capability_registry
from .phase1_boundary import PHASE1_ACTION_IDS, RULE_MAPPING_VERSION


ACTION_REGISTRY_PATH = Path("governance/contracts/action_registry_v1.json")
EXECUTION_PLAYBOOK_REGISTRY_PATH = Path("governance/contracts/execution_playbook_registry_v1.json")
RULE_MAPPING_REGISTRY_PATH = Path("governance/contracts/action_rule_mapping_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_action_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    return _load_json((root / ACTION_REGISTRY_PATH).resolve())


def load_execution_playbook_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    return _load_json((root / EXECUTION_PLAYBOOK_REGISTRY_PATH).resolve())


def load_rule_mapping_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    return _load_json((root / RULE_MAPPING_REGISTRY_PATH).resolve())


def validate_action_plane_registries(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    action_registry = load_action_registry(repo_root=repo_root)
    playbooks = load_execution_playbook_registry(repo_root=repo_root)
    mappings = load_rule_mapping_registry(repo_root=repo_root)
    capability_result = validate_tool_capability_registry(repo_root=repo_root)
    errors: List[Dict[str, str]] = []

    action_ids = [str(row.get("action_id") or "").strip() for row in list(action_registry.get("actions") or []) if str(row.get("action_id") or "").strip()]
    if tuple(action_ids) != PHASE1_ACTION_IDS:
        errors.append({"code": "unexpected_action_set", "message": "action registry must contain exactly the 3 phase-1 actions in canonical order"})

    playbook_action_ids = [str(row.get("action_id") or "").strip() for row in list(playbooks.get("playbooks") or []) if str(row.get("action_id") or "").strip()]
    if sorted(playbook_action_ids) != sorted(action_ids):
        errors.append({"code": "playbook_action_mismatch", "message": "playbook registry must match action registry exactly"})

    capability_ids = {
        str(row.get("tool_id") or "").strip()
        for row in list(capability_result.get("registry", {}).get("entries") or [])
        if str(row.get("tool_id") or "").strip()
    }
    for playbook in list(playbooks.get("playbooks") or []):
        for tool_id in list(playbook.get("required_capability_ids") or []):
            text = str(tool_id or "").strip()
            if text and text not in capability_ids:
                errors.append({"code": "unknown_capability_reference", "message": f"unknown capability referenced by playbook: {text}"})

    rule_ids = set()
    rule_action_ids = set()
    for rule in list(mappings.get("rules") or []):
        rule_id = str(rule.get("rule_id") or "").strip()
        action_id = str(rule.get("action_id") or "").strip()
        if rule_id in rule_ids:
            errors.append({"code": "duplicate_rule_id", "message": f"duplicate rule_id: {rule_id}"})
        rule_ids.add(rule_id)
        if action_id not in action_ids:
            errors.append({"code": "unknown_action_reference", "message": f"rule references unknown action_id: {action_id}"})
        rule_action_ids.add(action_id)
    if rule_action_ids != set(action_ids):
        errors.append({"code": "missing_rule_action_coverage", "message": "rule mapping must cover every phase-1 action exactly once"})
    if str(mappings.get("mapping_version") or "").strip() != RULE_MAPPING_VERSION:
        errors.append({"code": "unexpected_mapping_version", "message": "rule mapping version mismatch"})
    errors.extend(list(capability_result.get("errors") or []))
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": list(capability_result.get("warnings") or []),
        "action_registry": action_registry,
        "playbook_registry": playbooks,
        "mapping_registry": mappings,
    }


def _contains_any(text: str, terms: List[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms if str(term or "").strip())


def classify_phase1_action_request(
    request: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    validation = validate_action_plane_registries(repo_root=repo_root)
    if not validation.get("ok"):
        raise ValueError("action-plane registries invalid")
    mapping = validation["mapping_registry"]
    request_text = str(request.get("request_text") or "").strip()
    artifact_flags = {
        str(k).strip(): bool(v)
        for k, v in dict(request.get("artifact_presence_flags") or {}).items()
        if str(k).strip()
    }
    target_identifiers = {
        str(k).strip(): str(v or "").strip()
        for k, v in dict(request.get("target_identifiers") or {}).items()
        if str(k).strip()
    }

    matches: List[Dict[str, Any]] = []
    for rule in list(mapping.get("rules") or []):
        keywords = [str(item or "").strip() for item in list(rule.get("keywords_any") or []) if str(item or "").strip()]
        required_artifacts = [str(item or "").strip() for item in list(rule.get("required_artifact_flags") or []) if str(item or "").strip()]
        target_keys = [str(item or "").strip() for item in list(rule.get("required_target_keys") or []) if str(item or "").strip()]
        if keywords and not _contains_any(request_text, keywords):
            continue
        if required_artifacts and not all(artifact_flags.get(flag, False) for flag in required_artifacts):
            continue
        if target_keys and not all(target_identifiers.get(key, "") for key in target_keys):
            continue
        matches.append(rule)

    if not matches:
        return {
            "mapping_version": RULE_MAPPING_VERSION,
            "action_id": "",
            "confidence_bucket": "none",
            "required_evidence_gatherers": [],
            "fallback_eligibility_class": "none",
            "unsupported_reason": "no_supported_phase1_rule_match",
            "matched_rule_id": "",
        }

    matches.sort(key=lambda row: int(row.get("priority") or 999999))
    top_priority = int(matches[0].get("priority") or 999999)
    top_matches = [row for row in matches if int(row.get("priority") or 999999) == top_priority]
    if len(top_matches) > 1:
        return {
            "mapping_version": RULE_MAPPING_VERSION,
            "action_id": "",
            "confidence_bucket": "conflict",
            "required_evidence_gatherers": [],
            "fallback_eligibility_class": "none",
            "unsupported_reason": "rule_conflict_tie",
            "matched_rule_id": "",
        }
    chosen = top_matches[0]
    return {
        "mapping_version": RULE_MAPPING_VERSION,
        "action_id": str(chosen.get("action_id") or "").strip(),
        "confidence_bucket": str(chosen.get("confidence_bucket") or "medium").strip(),
        "required_evidence_gatherers": list(chosen.get("required_evidence_gatherers") or []),
        "fallback_eligibility_class": str(chosen.get("fallback_eligibility_class") or "bounded").strip(),
        "unsupported_reason": "",
        "matched_rule_id": str(chosen.get("rule_id") or "").strip(),
    }
