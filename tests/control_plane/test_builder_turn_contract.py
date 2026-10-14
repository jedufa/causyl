from __future__ import annotations

from infra.controlplane import builder_turn_contract as btc


def test_builder_turn_contract_v1_exposes_canonical_doc_and_packet_fields() -> None:
    contract = btc.builder_turn_contract_v1()
    assert contract.version == "builder_turn_contract_v1"
    assert contract.canonical_doc_rel.as_posix() == "docs/contracts/contract_builder_turn_output.md"
    assert "changed_files" in contract.section_aliases
    assert "validation_commands" in contract.section_aliases
    assert contract.result_packet_fields[-1] == "contract_violations"


def test_validate_builder_turn_contract_v1_returns_no_issues() -> None:
    assert btc.validate_builder_turn_contract_v1() == ()


def test_build_result_packet_extracts_required_sections_and_scope_fence_fields() -> None:
    packet = btc.build_result_packet(
        (
            "Done:\n"
            "- Updated infra/controlplane/compiled_output_classifier.py\n\n"
            "Changed files:\n"
            "- infra/controlplane/compiled_output_classifier.py\n\n"
            "Validation Commands Run:\n"
            "- pytest -q tests/control_plane/test_compiled_execution_integration.py\n\n"
            "Assumptions Made:\n"
            "- none\n\n"
            "Residual Risks / Gaps:\n"
            "- broader integration still unproven\n\n"
            "Next Owner:\n"
            "- verifier\n\n"
            "Continuation Point:\n"
            "- none\n"
        ),
        normalized_ir={
            "mode": "build",
            "claim_scope": "bounded_repo_change",
            "allowed_files": ["infra/controlplane/compiled_output_classifier.py"],
            "metadata": {"builder_required_sections": ["Assumptions Made", "Residual Risks / Gaps", "Next Owner", "Continuation Point"]},
        },
    )

    assert packet["required_sections"] == [
        "changed_files",
        "validation_commands",
        "assumptions",
        "residual_risks",
        "next_owner",
        "continuation_point",
    ]
    assert packet["claim_strength"] == "bounded_to_explicit_validation_commands"
    assert packet["scope_fence"]["summary_mutation_claims"] == [
        "infra/controlplane/compiled_output_classifier.py"
    ]
