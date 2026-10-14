from __future__ import annotations

from infra.controlplane import verifier_review_contract as vrc


def test_verifier_review_contract_v1_exposes_canonical_doc_and_packet_fields() -> None:
    contract = vrc.verifier_review_contract_v1()
    assert contract.version == "verifier_review_contract_v1"
    assert contract.canonical_doc_rel.as_posix() == "docs/contracts/contract_verifier_review_output.md"
    assert contract.allowed_verdicts == ("PASS", "FAIL", "UNKNOWN", "PASS_PENDING_ACTIONS")
    assert "findings" in contract.section_aliases
    assert "validation_review" in contract.section_aliases
    assert contract.result_packet_fields[-1] == "contract_violations"


def test_validate_verifier_review_contract_v1_returns_no_issues() -> None:
    assert vrc.validate_verifier_review_contract_v1() == ()


def test_build_verifier_result_packet_extracts_required_sections_and_fields() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - scope stayed inside the bound item.\n"
            "- contract_alignment: pass - required blocks are present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - focused tests and file inspection support the claim.\n"
            "- pass_fail_closed_check: satisfied - PASS is based on explicit evidence.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_compiled_execution_integration.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
            "Confidence:\n"
            "- advisory_only: medium confidence; no broader system claim.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    assert packet["required_sections"] == ["findings", "boundary_review", "proof_review", "validation_review"]
    assert packet["proof_review"]["pass_fail_closed_check"].startswith("satisfied")
    assert packet["validation_review"]["outcome_support"].startswith("sufficient")


def test_validate_verifier_result_packet_fails_closed_for_missing_review_block() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks.\n"
            "- pass_fail_closed_check: satisfied - pass is evidence-bound.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == "missing_validation_review_section"
    assert details == ("validation_review",)


def test_validate_verifier_result_packet_rejects_pass_without_explicit_proof() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - maybe okay.\n"
            "- pass_fail_closed_check: unsatisfied - this is just a confidence call.\n"
            "Validation Review:\n"
            "- commands_reviewed: none\n"
            "- outcome_support: sufficient - trust the tone.\n"
            "Confidence:\n"
            "- advisory_only: high confidence.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == "pass_without_explicit_proof_review"
    assert details == ("pass_fail_closed_check",)


def test_validate_verifier_result_packet_rejects_unsupported_verdict_token() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS_WITH_CONFIDENCE\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks.\n"
            "- pass_fail_closed_check: satisfied - evidence-bound.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_verifier_review_contract.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == "invalid_verdict_token"
    assert details == ("PASS_WITH_CONFIDENCE",)


def test_validate_verifier_result_packet_rejects_vague_findings_block() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- looks good overall\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks.\n"
            "- pass_fail_closed_check: satisfied - evidence-bound.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_verifier_review_contract.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == "invalid_findings_block"
    assert details == ("findings",)


def test_validate_verifier_result_packet_requires_pending_actions_blocks_for_repair_verdict() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS_PENDING_ACTIONS\n"
            "Findings:\n"
            "- medium | tests/control_plane/test_verifier_review_contract.py | missing one bounded regression assertion\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - exact gap is identified.\n"
            "- pass_fail_closed_check: satisfied - verdict is not overstated.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_verifier_review_contract.py\n"
            "- outcome_support: sufficient - one bounded repair remains.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == "missing_pending_actions_section"
    assert details == ("pending_actions",)


def test_validate_verifier_result_packet_accepts_explicit_none_plus_low_severity_none() -> None:
    packet = vrc.build_result_packet(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks.\n"
            "- pass_fail_closed_check: satisfied - evidence-bound.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_verifier_review_contract.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
            "Findings by Severity:\n"
            "- severity low | none | none\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == ""
    assert details == ()


def test_build_verifier_result_packet_rejects_verdict_not_on_first_line() -> None:
    packet = vrc.build_result_packet(
        (
            "STRUCTURAL VALIDITY: valid\n"
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks.\n"
            "- pass_fail_closed_check: satisfied - evidence-bound.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_verifier_review_contract.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
        ),
        normalized_ir={
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            }
        },
    )

    reason, details = vrc.validate_verifier_result_packet(packet)
    assert reason == "missing_verdict_token"
    assert details == ("verdict",)
