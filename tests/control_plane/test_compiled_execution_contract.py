from __future__ import annotations

from infra.controlplane import compiled_execution_contract as cec


def test_compiled_execution_contract_v1_has_expected_truth_boundaries() -> None:
    contract = cec.compiled_execution_contract_v1()
    assert contract.version == "compiled_execution_contract_v1"
    assert contract.canonical_doc_rel.as_posix() == "docs/contracts/contract_compiled_execution_layer_operational_boundary.md"
    assert contract.truth_boundary_authorities["source_task_intent"] == "structured_execution_ir"
    assert contract.truth_boundary_authorities["run_status"] == "existing_execution_records_and_receipts"


def test_compiled_execution_rollout_defaults_to_shadow_for_selected_classes() -> None:
    assert cec.compiled_execution_rollout_mode_for_task_class("single_turn") == cec.ROLLOUT_SHADOW_COMPILE
    assert cec.compiled_execution_rollout_mode_for_task_class("build_verify_loop") == cec.ROLLOUT_SHADOW_COMPILE
    assert cec.compiled_execution_rollout_mode_for_task_class("unknown_task_class") == cec.ROLLOUT_LEGACY


def test_truth_boundary_authority_rejects_unknown_boundary() -> None:
    try:
        cec.truth_boundary_authority("not_real")
    except KeyError as exc:
        assert "unknown truth boundary" in str(exc)
    else:  # pragma: no cover - fail loudly if contract weakens
        raise AssertionError("expected unknown truth boundary to raise")


def test_validate_compiled_execution_contract_v1_returns_no_issues() -> None:
    assert cec.validate_compiled_execution_contract_v1() == ()
