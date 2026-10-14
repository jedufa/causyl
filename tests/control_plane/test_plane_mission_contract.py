from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import plane_mission_contract as contract


def test_plane_mission_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/plane_mission_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == contract.SCHEMA_VERSION
    assert payload["uid"] == contract.UID
    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert contract.validate_plane_mission_contract_artifact() == ()


def test_plane_mission_contract_rejects_overlapping_boundary_and_non_goal() -> None:
    raw = json.loads(Path("governance/plane_mission_contract_v1.json").read_text(encoding="utf-8"))
    raw["explicit_non_goals"] = list(raw["explicit_non_goals"]) + [raw["aligned_expansion_boundaries"][0]]

    result = contract.validate_plane_mission_contract(raw)

    assert result["ok"] is False
    assert any(row["code"] == "contradictory_boundary_statement" for row in result["errors"])


def test_runtime_and_operator_projections_share_loaded_contract_fingerprint() -> None:
    result = contract.read_plane_mission_contract()

    assert result["ok"] is True
    runtime_projection = result["runtime_projection"]
    operator_projection = result["operator_projection"]
    assert runtime_projection["source_fingerprint"] == operator_projection["source_fingerprint"]
    assert runtime_projection["product_promise"] == operator_projection["headline"]
    assert runtime_projection["operator_success_model"] == operator_projection["operator_success_model"]
    assert runtime_projection["explicit_non_goals"] == operator_projection["explicit_non_goals"]
    assert runtime_projection["claim_ceiling_summary"]
    assert runtime_projection["overreach_guardrails"]
    assert runtime_projection["refresh_scope_summary"]
    assert runtime_projection["refresh_scope_reload_limits"]
    assert operator_projection["claim_ceiling_summary"] == runtime_projection["claim_ceiling_summary"]
    assert operator_projection["refresh_scope_summary"] == runtime_projection["refresh_scope_summary"]


def test_read_plane_mission_contract_surfaces_invalid_contract_without_projection(tmp_path: Path) -> None:
    invalid_payload = json.loads(Path("governance/plane_mission_contract_v1.json").read_text(encoding="utf-8"))
    invalid_payload.pop("claim_ceiling_summary", None)
    target = tmp_path / "governance" / "plane_mission_contract_v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(invalid_payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    result = contract.read_plane_mission_contract(repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_class"] == "mission_contract_invalid"
    assert result["summary"] == "Canonical plane mission contract failed validation."
    assert any(row["field"] == "claim_ceiling_summary" and row["code"] == "missing_field" for row in result["errors"])
    assert "runtime_projection" not in result
    assert "operator_projection" not in result
