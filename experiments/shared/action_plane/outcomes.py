"""Outcome classification for deterministic action execution."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Optional


ACTION_OUTCOME_SCHEMA_PATH = Path("governance/contracts/action_outcome_schema_v1.json")

SUCCESS_OUTCOMES = {
    "success_deterministic",
    "success_after_deterministic_recovery",
    "success_after_codex_fallback",
}


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    return load_contract(root / ACTION_OUTCOME_SCHEMA_PATH)


def classify_action_outcome(payload: Dict[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _load_schema(repo_root=repo_root)
    requested = str(payload.get("requested_outcome") or payload.get("outcome") or "").strip()
    verifier_verdict = str(payload.get("verifier_verdict") or "").strip().upper()
    proof_gaps = [str(item or "").strip() for item in list(payload.get("proof_gaps") or []) if str(item or "").strip()]
    validation_commands = [str(item or "").strip() for item in list(payload.get("validation_commands_executed") or []) if str(item or "").strip()]
    evidence_artifacts = [str(item or "").strip() for item in list(payload.get("evidence_artifacts") or []) if str(item or "").strip()]

    reasons: List[str] = []
    final_outcome = requested or "failed_unknown_class"
    if requested in SUCCESS_OUTCOMES:
        if not validation_commands:
            reasons.append("missing_validation_commands")
        if not evidence_artifacts:
            reasons.append("missing_evidence_artifacts")
        if verifier_verdict != "PASS":
            reasons.append("missing_explicit_pass")
        if proof_gaps:
            reasons.append("unresolved_proof_gap")
        if reasons:
            final_outcome = "proof_gap_present" if "unresolved_proof_gap" in reasons else "blocked_requires_review"
    elif not final_outcome:
        final_outcome = "failed_unknown_class"

    return {
        "requested_outcome": requested,
        "outcome": final_outcome,
        "verifier_verdict": verifier_verdict,
        "validation_commands_executed": validation_commands,
        "evidence_artifacts": evidence_artifacts,
        "proof_gaps": proof_gaps,
        "downgrade_reasons": reasons,
        "pass_required": requested in SUCCESS_OUTCOMES,
    }


def validate_action_outcome_payload(payload: Dict[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = _load_schema(repo_root=repo_root)
    result = classify_action_outcome(payload, repo_root=repo_root)
    terminal_outcomes = {
        str(item or "").strip()
        for item in list(schema.get("terminal_outcomes") or [])
        if str(item or "").strip()
    }
    errors: List[Dict[str, str]] = []
    outcome = str(result.get("outcome") or "").strip()
    if outcome not in terminal_outcomes:
        errors.append({"code": "unknown_outcome", "message": f"unknown outcome: {outcome}"})
    return {"ok": not errors, "errors": errors, "result": result}
