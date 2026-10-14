"""Closure-blocker and dependency validation for generated child PIS families."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class ChildPISContractError(ValueError):
    """Raised when generated child PIS output is too broad or under-blocked."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def validate_generated_child_family(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    children = list(payload.get("child_pis_family") or [])
    if not children:
        raise ChildPISContractError("child_family_missing", "generated child family cannot be empty")
    normalized: List[Dict[str, Any]] = []
    for child in children:
        key = _clean_text(child.get("child_key"))
        if not key:
            raise ChildPISContractError("child_key_missing", "every child requires child_key")
        dominant = _clean_text(child.get("dominant_failure_story"))
        if not dominant:
            raise ChildPISContractError("dominant_failure_story_missing", f"{key} needs one dominant failure story")
        blockers = _normalized_list(child.get("closure_blockers") or [])
        if not blockers:
            raise ChildPISContractError("closure_blockers_missing", f"{key} needs explicit closure blockers")
        non_goals = _normalized_list(child.get("non_goals") or [])
        if not non_goals:
            raise ChildPISContractError("non_goals_missing", f"{key} needs explicit non-goals")
        breadth = int(child.get("max_proof_target_breadth") or 0)
        if breadth <= 0 or breadth > 3:
            raise ChildPISContractError("max_proof_target_breadth_invalid", f"{key} breadth must stay between 1 and 3")
        depends = _normalized_list(child.get("depends_on_child_keys") or [])
        if key in depends:
            raise ChildPISContractError("self_dependency_invalid", f"{key} cannot depend on itself")
        normalized.append(
            {
                "child_key": key,
                "dominant_failure_story": dominant,
                "closure_blockers": blockers,
                "non_goals": non_goals,
                "max_proof_target_breadth": breadth,
                "depends_on_child_keys": depends,
            }
        )
    known = {child["child_key"] for child in normalized}
    for child in normalized:
        unknown = [dep for dep in child["depends_on_child_keys"] if dep not in known]
        if unknown:
            raise ChildPISContractError("unknown_dependency_invalid", f"{child['child_key']} depends on unknown child keys")
    return {
        "schema_version": "generated_child_pis_closure_report_v1",
        "child_pis_family": normalized,
        "maximum_supported_claim": "generated_child_pis_family_bounded_for_planning_only",
    }
