from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import context_bundles
from infra.controlplane import job_shape_context_contract as contract
from infra.controlplane import job_shape_contract


def test_job_shape_context_contract_v1_exposes_expected_shape_binding() -> None:
    payload = contract.job_shape_context_contract_v1()

    assert payload.version == "job_shape_context_contract_v1"
    assert payload.uid == "PIS-20260406-053812Z-733A"
    assert payload.canonical_artifact_rel.as_posix() == "governance/job_shape_context_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_job_shape_context_bundle_policy_v1.md"
    assert payload.canonical_shape_contract_rel.as_posix() == "governance/canonical_job_shape_contract_v1.json"
    assert payload.bundle_registry_doc_rel.as_posix() == "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context_bundles.md"
    assert payload.shape_names == job_shape_contract.CANONICAL_JOB_SHAPES


def test_context_bundle_static_refs_exist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for bundle_id in context_bundles.available_bundle_ids():
        for ref in context_bundles.bundle_refs(bundle_id):
            if ref.startswith("governance/ledger/patch_instruction_sheets/PIS-"):
                continue
            assert (repo_root / ref).exists(), f"{bundle_id}: {ref}"


def test_job_shape_context_helper_resolves_effective_refs_from_durable_contract_first() -> None:
    result = contract.resolve_context_obligations_for_shape(
        "verification_loop_job",
        durable_delta_refs=["governance/ledger/patch_instruction_sheets/example/pis.md"],
        transcript_summary_refs=["governance/agents/sessions/task_123/session__builder.jsonl"],
    )

    assert result["required_context_bundle"] == "lab_core_v1"
    assert result["effective_context_refs"][:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert result["effective_context_refs"][-1] == "governance/ledger/patch_instruction_sheets/example/pis.md"
    assert result["ignored_transcript_summary_refs"] == ["governance/agents/sessions/task_123/session__builder.jsonl"]
    assert result["omitted_transcript_summary_refs"] == [
        {
            "path": "governance/agents/sessions/task_123/session__builder.jsonl",
            "reason": "forbidden_transcript_ref",
        }
    ]
    assert result["reentry_precedence"] == "durable_contract_then_durable_delta_then_transcript_summary"


def test_job_shape_context_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/job_shape_context_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["bundle_registry_doc_rel"] == contract.BUNDLE_REGISTRY_DOC_REL.as_posix()
    assert tuple(payload["shapes"].keys()) == job_shape_contract.CANONICAL_JOB_SHAPES
    assert payload["shapes"]["fanout_job"]["required_context_bundle"] == "lab_arch_gov_v1"
    assert payload["shapes"]["experiment_run_job"]["allowed_durable_delta_ref_kinds"] == [
        "binding_context_ref",
        "experiment_contract_ref",
        "run_input_ref",
    ]
    assert payload["shapes"]["campaign_job"]["additional_context_refs_allowed"] is False


def test_job_shape_context_contract_validation_passes() -> None:
    assert contract.validate_job_shape_context_contract_v1() == ()


def test_job_shape_context_contract_validation_rejects_missing_required_bundle(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_shape_context_contract_v1.json").read_text(encoding="utf-8"))
    payload["shapes"]["verification_loop_job"]["required_context_bundle"] = ""

    contract_path = tmp_path / "governance" / "job_shape_context_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    issues = contract.validate_job_shape_context_contract_v1(repo_root=tmp_path)
    assert "verification_loop_job required_context_bundle must be non-empty" in issues


def test_job_shape_context_helper_rejects_forbidden_refs_and_closed_delta_shapes() -> None:
    with pytest.raises(ValueError, match="forbidden context refs requested for verification_loop_job"):
        contract.resolve_context_obligations_for_shape(
            "verification_loop_job",
            durable_delta_refs=["legacy_transcript_blob"],
        )

    with pytest.raises(ValueError, match="campaign_job forbids durable delta context refs in v1"):
        contract.resolve_context_obligations_for_shape(
            "campaign_job",
            durable_delta_refs=["governance/ledger/patch_instruction_sheets/example/pis.md"],
        )


def test_job_shape_context_helper_supports_current_job_kind_lookup() -> None:
    result = contract.resolve_context_obligations_for_job_kind(
        "project_runner",
        durable_delta_refs=["experiments/projects/demo/contract.json"],
        transcript_summary_refs=["guessed/from/transcript.md"],
    )

    assert result["job_shape"] == "experiment_run_job"
    assert result["required_context_bundle"] == "lab_core_v1"
    assert "experiments/projects/demo/contract.json" in result["effective_context_refs"]
    assert result["ignored_transcript_summary_refs"] == ["guessed/from/transcript.md"]
