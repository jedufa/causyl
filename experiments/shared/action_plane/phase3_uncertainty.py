"""Phase-3 proof-gap and uncertainty surfacing."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase3_state_view import build_operator_derived_state_view_v1

PROOF_GAP_UNCERTAINTY_SURFACE_SCHEMA_PATH = Path("governance/contracts/proof_gap_uncertainty_surface_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_proof_gap_uncertainty_surface_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / PROOF_GAP_UNCERTAINTY_SURFACE_SCHEMA_PATH)


def build_proof_gap_uncertainty_surface_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_proof_gap_uncertainty_surface_schema(repo_root=repo_root)
    view = dict(payload.get("derived_state_view") or {})
    if not view:
        view = build_operator_derived_state_view_v1(payload, repo_root=repo_root)
    proof_gaps = [str(item or "").strip() for item in list(view.get("unresolved_proof_gaps") or []) if str(item or "").strip()]
    stale = bool(view.get("stale_evidence_present"))
    contradiction = bool(view.get("contradiction_present"))
    state_class = str(view.get("state_class") or "unknown").strip()
    verifier_verdict = str(view.get("final_verifier_verdict") or "").strip().upper()

    if contradiction:
        confidence = "contradictory"
    elif stale:
        confidence = "degraded"
    elif proof_gaps:
        confidence = "medium"
    elif state_class in {"pass", "blocked", "failed"} or verifier_verdict == "PASS":
        confidence = "high"
    else:
        confidence = "low"

    required_steps: List[str] = []
    if contradiction:
        required_steps.append("status_reconcile_v1")
    if stale:
        required_steps.append("refresh_stale_evidence")
    if proof_gaps:
        validation_like = any("validation" in gap.lower() or "pass" in gap.lower() or "proof" in gap.lower() for gap in proof_gaps)
        required_steps.append("run_required_validation" if validation_like else "collect_missing_evidence")
    if state_class == "running" and not required_steps:
        required_steps.append("await_or_refresh_status")

    return {
        "schema_version": "proof_gap_uncertainty_surface_v1",
        "proof_gap_present": bool(proof_gaps),
        "unresolved_items": proof_gaps,
        "stale_evidence_present": stale,
        "contradiction_present": contradiction,
        "confidence_bucket": confidence,
        "required_next_proof_steps": required_steps,
        "evidence_references": dict(view.get("evidence_provenance") or {}),
    }


def validate_proof_gap_uncertainty_surface_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_proof_gap_uncertainty_surface_schema(repo_root=repo_root)
    required = [str(item or "").strip() for item in list(schema.get("required_fields") or []) if str(item or "").strip()]
    missing = [field for field in required if field not in payload]
    errors: List[Dict[str, str]] = []
    for field in missing:
        errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    bucket = str(payload.get("confidence_bucket") or "").strip()
    allowed = {str(item or "").strip() for item in list(schema.get("allowed_confidence_buckets") or []) if str(item or "").strip()}
    if bucket and bucket not in allowed:
        errors.append({"code": "unknown_confidence_bucket", "message": f"unknown confidence bucket: {bucket}"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}
