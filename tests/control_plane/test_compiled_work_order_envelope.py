from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import compiled_work_order_envelope as envelope


def _base_envelope() -> dict:
    return {
        "schema_version": "compiled_work_order_envelope_v1",
        "work_order_id": "cwo_demo_001",
        "source_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260411-144241Z-154B"},
        "handoff_kind": "builder_to_verifier",
        "scope_fence": {
            "objective": "Validate the canonical compiled handoff contract only.",
            "in_scope": ["infra/controlplane/compiled_work_order_envelope.py"],
            "out_of_scope": ["infra/controlplane/scheduled_jobs.py"],
        },
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs": ["governance/fanout_child_contract_v1.json"],
        "forbidden_context_refs": [],
        "expected_outputs": [
            {
                "output_id": "out_report",
                "kind": "report",
                "summary": "Focused builder/verifier handoff report.",
                "required": True,
            }
        ],
        "validation_expectations": {
            "required_checks": ["targeted_unit_tests", "syntax_check"],
            "failure_mode": "fail_closed",
            "prose_coexistence": "fallback_prose_allowed",
        },
    }


def test_compiled_work_order_envelope_contract_v1_exposes_expected_metadata() -> None:
    payload = envelope.compiled_work_order_envelope_contract_v1()

    assert payload.version == "compiled_work_order_envelope_v1"
    assert payload.uid == "PIS-20260411-144241Z-154B"
    assert payload.canonical_artifact_rel.as_posix() == "governance/compiled_work_order_envelope_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_compiled_work_order_envelope_v1.md"
    assert payload.required_context_bundle_rule == "bundle_plus_delta_minus_forbidden"
    assert payload.compiled_precedence_rule == "compiled_envelope_authoritative_when_present"


def test_validate_compiled_work_order_envelope_and_context_resolution_succeed() -> None:
    result = envelope.validate_compiled_work_order_envelope(_base_envelope())
    resolved = envelope.resolve_compiled_work_order_context_refs(_base_envelope())

    assert result["ok"] is True
    assert result["computed_fingerprint"] != ""
    assert resolved["ok"] is True
    assert resolved["effective_refs"][:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert resolved["effective_refs"][-1] == "governance/fanout_child_contract_v1.json"


def test_validate_compiled_work_order_envelope_rejects_missing_validation_and_contradictory_refs() -> None:
    raw = _base_envelope()
    raw["additional_context_refs"] = ["dup.md"]
    raw["forbidden_context_refs"] = ["dup.md"]
    raw["validation_expectations"]["required_checks"] = []

    result = envelope.validate_compiled_work_order_envelope(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["failure_class"] == "compiled_work_order_validation_failed"
    assert "contradictory_context_refs" in codes
    assert "missing_field" in codes


def test_resolve_compiled_work_order_coexistence_prefers_compiled_on_dual_path() -> None:
    result = envelope.resolve_compiled_work_order_coexistence(
        compiled_envelope=_base_envelope(),
        prose_brief_present=True,
    )

    assert result["ok"] is True
    assert result["effective_path"] == "dual_path_shadow"
    assert result["precedence"] == "compiled_envelope"
    assert any(row["code"] == "prose_fallback_present" for row in result["warnings"])


def test_resolve_compiled_work_order_coexistence_rejects_unmarked_dual_path_override() -> None:
    raw = _base_envelope()
    raw["validation_expectations"]["prose_coexistence"] = "compiled_only"

    result = envelope.resolve_compiled_work_order_coexistence(
        compiled_envelope=raw,
        prose_brief_present=True,
    )

    assert result["ok"] is False
    assert result["failure_class"] == "compiled_work_order_coexistence_blocked"
    assert any(row["code"] == "coexistence_conflict" for row in result["errors"])


def test_resolve_compiled_work_order_coexistence_allows_legacy_prose_only_rollout() -> None:
    result = envelope.resolve_compiled_work_order_coexistence(
        compiled_envelope=None,
        prose_brief_present=True,
    )

    assert result["ok"] is True
    assert result["effective_path"] == "prose_only"
    assert result["precedence"] == "prose_brief"
    assert any(row["code"] == "legacy_prose_path" for row in result["warnings"])


def test_validate_compiled_work_order_envelope_contract_v1_returns_no_issues() -> None:
    assert envelope.validate_compiled_work_order_envelope_contract_v1() == ()


def test_validate_compiled_work_order_envelope_contract_v1_fails_on_artifact_drift(tmp_path: Path) -> None:
    artifact_rel = Path("governance/compiled_work_order_envelope_v1.json")
    artifact_path = tmp_path / artifact_rel
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(Path(artifact_rel).read_text(encoding="utf-8"))
    payload["required_fields"] = list(payload["required_fields"][:-1])
    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    issues = envelope.validate_compiled_work_order_envelope_contract_v1(repo_root=tmp_path)

    assert issues == ("required_fields must remain stable and ordered",)
