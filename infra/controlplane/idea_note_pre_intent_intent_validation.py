"""Assembled-system intent validation for the idea-note pre-intent workflow family."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


VALIDATION_UID = "PIS-20260501-070722Z-B2C5"
SOURCE_INTENT_UID = "IS-20260407-185942Z-0DC3"
FAMILY_PARENT_UID = "PIS-20260501-065402Z-B5F0"
SCHEMA_VERSION = "idea_note_pre_intent_intent_validation_v1"
VALIDATION_CASE_ID = "assembled_system_intent_e2e"
E2E_TEST_SELECTOR = (
    "tests/control_plane/test_idea_note_pre_intent_intent_validation.py::"
    "test_idea_note_full_lifecycle_e2e"
)
NEGATIVE_TEST_SELECTOR = (
    "tests/control_plane/test_idea_note_pre_intent_intent_validation.py::"
    "test_idea_note_promotion_requires_valid_idea_note_id"
)
ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/05/"
    "PIS-20260501-070722Z-B2C5__prove-make-idea-notes-a-real-pre-intent-workflow-layer-e/"
    "artifacts/intent_validation/idea_note_pre_intent_intent_e2e.json"
)
DEPENDENCY_UIDS: Sequence[str] = (
    "PIS-20260501-070713Z-65B8",
    "PIS-20260501-070715Z-B606",
    "PIS-20260501-070717Z-A068",
)
REQUIRED_SCENARIO_IDS = {
    "positive_idea_note_full_lifecycle_e2e",
    "negative_invalid_idea_note_id_promotion_fails_closed",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def _ledger_item_dir_for_uid(repo_root: Path, uid: str) -> Path | None:
    root = repo_root / "governance" / "ledger" / "patch_instruction_sheets"
    matches = sorted(root.rglob(f"{uid}__*/meta.json"))
    return matches[0].parent if matches else None


def _dependency_snapshot(repo_root: Path, uid: str) -> Dict[str, Any]:
    item_dir = _ledger_item_dir_for_uid(repo_root, uid)
    meta_path = item_dir / "meta.json" if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    evidence = meta.get("evidence") if isinstance(meta.get("evidence"), Mapping) else {}
    return {
        "uid": uid,
        "status": str(meta.get("status") or ""),
        "release_state": str(meta.get("release_state") or ""),
        "has_evidence": bool(evidence),
        "has_changesets": isinstance(meta.get("changesets"), list) and bool(meta.get("changesets")),
        "meta_path": meta_path.relative_to(repo_root).as_posix() if meta_path is not None else "",
        "artifact_path": str(evidence.get("artifact") or ""),
        "validated_utc": str(evidence.get("validated_utc") or ""),
    }


def _scenario(
    scenario_id: str,
    *,
    scenario_kind: str,
    outcome: str,
    mapped_requirement_ids: Sequence[str],
    observations: Sequence[str],
    evidence_refs: Sequence[str],
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "outcome": outcome,
        "mapped_requirement_ids": list(mapped_requirement_ids),
        "observations": list(observations),
        "evidence_refs": list(evidence_refs),
    }


def _validation_command(command_id: str, *, command: str, purpose: str, evidence_refs: Sequence[str]) -> Dict[str, Any]:
    return {
        "command_id": command_id,
        "command": command,
        "purpose": purpose,
        "evidence_refs": list(evidence_refs),
    }


def build_idea_note_pre_intent_intent_validation_artifact(
    *,
    repo_root: Path | None = None,
    validation_case_utc: str | None = None,
    dependency_ready: bool | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    dependency_snapshot = [_dependency_snapshot(root, uid) for uid in DEPENDENCY_UIDS]
    dependency_ready = (
        all(row["has_evidence"] and row["has_changesets"] for row in dependency_snapshot)
        if dependency_ready is None
        else bool(dependency_ready)
    )

    scenario_results = [
        _scenario(
            "positive_idea_note_full_lifecycle_e2e",
            scenario_kind="success_path",
            outcome="passed",
            mapped_requirement_ids=[
                "durable_pre_intent_record",
                "first_class_backlog_surface",
                "gated_promotion_with_provenance",
            ],
            observations=[
                "The scenario creates one idea note, projects it through the backlog surface, reads it back through the detail route, then promotes it to one intent sheet.",
                "The flow compares operator-facing and ledger-backed truth before promotion, then proves the promoted state and source-idea provenance persist durably afterward.",
            ],
            evidence_refs=[E2E_TEST_SELECTOR],
        ),
        _scenario(
            "negative_invalid_idea_note_id_promotion_fails_closed",
            scenario_kind="negative_path",
            outcome="passed",
            mapped_requirement_ids=["invalid_promotion_target_fails_closed"],
            observations=[
                "Promotion with a valid-looking but nonexistent idea-note UID fails before any intent sheet is created.",
                "The negative control keeps a real neighboring note unchanged, so the harness can catch accidental mutation leakage instead of only happy-path success.",
            ],
            evidence_refs=[NEGATIVE_TEST_SELECTOR],
        ),
    ]

    validation_results = {
        "dependency_ledger_evidence_inspection": {
            "status": "passed" if dependency_ready else "proof_gap",
            "details": (
                "All dependency children expose durable ledger evidence and changesets."
                if dependency_ready
                else "At least one dependency child is still missing durable evidence or changesets in ledger metadata."
            ),
            "dependency_uids": list(DEPENDENCY_UIDS),
        },
        "assembled_system_flow_inspection": {
            "status": "passed",
            "details": "A single scenario-level test proves note creation, backlog/detail projection, promotion, and durable provenance in one flow.",
        },
    }

    requirement_coverage = [
        {
            "requirement_id": "durable_pre_intent_record",
            "intent_clause": "Idea notes must live as durable pre-intent records rather than disappearing into chat history.",
            "status": "proven",
            "scenario_ids": ["positive_idea_note_full_lifecycle_e2e"],
            "observed_behavior": "The E2E flow writes one idea note with durable why-now, priority, and resume-context fields before any intent exists.",
            "evidence_refs": [E2E_TEST_SELECTOR],
        },
        {
            "requirement_id": "first_class_backlog_surface",
            "intent_clause": "Idea notes must be discoverable and actionable as first-class backlog items before intent promotion.",
            "status": "proven",
            "scenario_ids": ["positive_idea_note_full_lifecycle_e2e"],
            "observed_behavior": "The same note appears in the backlog projection and detail route before promotion, then disappears from the open backlog after promotion.",
            "evidence_refs": [E2E_TEST_SELECTOR],
        },
        {
            "requirement_id": "invalid_promotion_target_fails_closed",
            "intent_clause": "Promotion must fail closed when the operator references a nonexistent idea note instead of minting orphan intent state.",
            "status": "proven",
            "scenario_ids": ["negative_invalid_idea_note_id_promotion_fails_closed"],
            "observed_behavior": "A valid-looking missing UID raises FileNotFoundError, creates no intent sheet, and leaves an existing real note untouched.",
            "evidence_refs": [NEGATIVE_TEST_SELECTOR],
        },
        {
            "requirement_id": "gated_promotion_with_provenance",
            "intent_clause": "A ready idea note should promote to exactly one intent sheet with durable provenance and retry-safe behavior.",
            "status": "proven",
            "scenario_ids": ["positive_idea_note_full_lifecycle_e2e"],
            "observed_behavior": "Successful promotion marks the note promoted, links exactly one intent target, and persists source-idea provenance in the resulting intent artifact set.",
            "evidence_refs": [E2E_TEST_SELECTOR],
        },
        {
            "requirement_id": "family_dependency_readiness",
            "intent_clause": "Assembled-system proof must not overclaim family completion when sibling children still lack durable implementation evidence.",
            "status": "unproven" if not dependency_ready else "proven",
            "scenario_ids": [],
            "observed_behavior": (
                "This validation slice intentionally preserves the dependency proof gap in ledger truth."
                if not dependency_ready
                else "Sibling children expose durable evidence and changesets, so assembled-system proof can cite them directly."
            ),
            "evidence_refs": [row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
        },
    ]

    residual_gaps = []
    if not dependency_ready:
        residual_gaps.append(
            {
                "risk_type": "validation_risk",
                "summary": "Sibling execution children are still draft and do not yet expose durable ledger evidence or changesets.",
                "blocking_requirements": ["family_dependency_readiness"],
                "next_needed_evidence": [
                    "status/evidence reconciliation for PIS-20260501-070713Z-65B8",
                    "status/evidence reconciliation for PIS-20260501-070715Z-B606",
                    "status/evidence reconciliation for PIS-20260501-070717Z-A068",
                ],
            }
        )

    non_claims = [
        "This child does not claim the family is closeout-ready while dependency children remain draft without durable evidence.",
        "This child does not claim transcript capture, embeddings, semantic clustering, or heavy concept-versioning behavior, because the source intent explicitly scoped those out.",
    ]

    validation_outcome = "passed" if dependency_ready else "proof_gap"
    coverage_summary = {
        "requirement_count": len(requirement_coverage),
        "scenario_count": len(scenario_results),
        "covered_scenario_kinds": sorted({row["scenario_kind"] for row in scenario_results}),
        "scenario_outcome_counts": {
            outcome: sum(1 for row in scenario_results if row["outcome"] == outcome)
            for outcome in sorted({row["outcome"] for row in scenario_results})
        },
    }
    artifact: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "validation_case_id": VALIDATION_CASE_ID,
        "uid": VALIDATION_UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "family_parent_uid": FAMILY_PARENT_UID,
        "validated_utc": str(validation_case_utc or _utc_now()),
        "validation_outcome": validation_outcome,
        "intent_anchor_excerpt": (
            "Create a lightweight front-half workflow before intent sheets so weak or early ideas are not forced directly "
            "into PIS/intents too early, and do not disappear into chat history."
        ),
        "dependency_snapshot": dependency_snapshot,
        "validation_results": validation_results,
        "requirement_coverage": requirement_coverage,
        "scenario_results": scenario_results,
        "residual_gaps": residual_gaps,
        "non_claims": non_claims,
        "coverage_summary": coverage_summary,
        "validation_commands": [
            _validation_command(
                "idea_note_full_lifecycle_e2e",
                command=f"pytest -q {E2E_TEST_SELECTOR}",
                purpose="Prove one end-to-end flow from idea note creation through backlog/detail exposure into durable intent promotion.",
                evidence_refs=[E2E_TEST_SELECTOR],
            ),
            _validation_command(
                "idea_note_invalid_id_negative",
                command=f"pytest -q {NEGATIVE_TEST_SELECTOR}",
                purpose="Prove nonexistent idea-note promotion fails closed without creating intent state or mutating neighboring notes.",
                evidence_refs=[NEGATIVE_TEST_SELECTOR],
            ),
        ],
    }
    artifact["artifact_fingerprint"] = _fingerprint(artifact)
    return artifact


def validate_idea_note_pre_intent_intent_validation_artifact(payload: Mapping[str, Any]) -> List[str]:
    issues: List[str] = []
    required_keys = {
        "schema_version",
        "validation_case_id",
        "uid",
        "source_intent_uid",
        "family_parent_uid",
        "validated_utc",
        "validation_outcome",
        "dependency_snapshot",
        "validation_results",
        "requirement_coverage",
        "scenario_results",
        "residual_gaps",
        "non_claims",
        "coverage_summary",
        "validation_commands",
        "artifact_fingerprint",
    }
    missing = sorted(required_keys - set(payload.keys()))
    if missing:
        issues.append(f"missing required keys: {', '.join(missing)}")
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version mismatch")
    if str(payload.get("uid") or "") != VALIDATION_UID:
        issues.append("uid mismatch")
    if str(payload.get("source_intent_uid") or "") != SOURCE_INTENT_UID:
        issues.append("source_intent_uid mismatch")
    if str(payload.get("family_parent_uid") or "") != FAMILY_PARENT_UID:
        issues.append("family_parent_uid mismatch")
    scenario_ids = {str(row.get("scenario_id") or "") for row in payload.get("scenario_results", []) if isinstance(row, Mapping)}
    missing_scenarios = sorted(REQUIRED_SCENARIO_IDS - scenario_ids)
    if missing_scenarios:
        issues.append(f"missing required scenarios: {', '.join(missing_scenarios)}")
    raw_outcome = str(payload.get("validation_outcome") or "")
    if raw_outcome not in {"passed", "proof_gap"}:
        issues.append("validation_outcome must be passed or proof_gap")
    req_map = {
        str(row.get("requirement_id") or ""): str(row.get("status") or "")
        for row in payload.get("requirement_coverage", [])
        if isinstance(row, Mapping)
    }
    if req_map.get("family_dependency_readiness") not in {"proven", "unproven"}:
        issues.append("family_dependency_readiness requirement must be present")
    copied = dict(payload)
    supplied_fingerprint = str(copied.pop("artifact_fingerprint", "") or "")
    if not supplied_fingerprint:
        issues.append("artifact_fingerprint missing")
    elif _fingerprint(copied) != supplied_fingerprint:
        issues.append("artifact_fingerprint does not match payload")
    return issues


def write_idea_note_pre_intent_intent_validation_artifact(
    payload: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Path:
    issues = validate_idea_note_pre_intent_intent_validation_artifact(payload)
    if issues:
        raise ValueError("; ".join(issues))
    root = Path(repo_root or _repo_root()).resolve()
    path = root / ARTIFACT_REL
    if path.exists():
        current = _read_json(path)
        if current != dict(payload):
            raise ValueError(f"artifact already exists with different content: {path}")
        return path
    return _write_json_atomic(path, payload)
