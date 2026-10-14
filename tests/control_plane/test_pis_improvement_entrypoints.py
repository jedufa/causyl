from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import pis_improvement_entrypoints as entrypoints
from infra.controlplane import pis_improvement_session_contract as contract
from infra.webui.ledger import ledger_storage
from tools.bin import pis_improvement as cli


def _seed_pis(
    repo_root: Path,
    *,
    uid: str,
    slug: str,
    title: str,
    status: str,
    updated_utc: str,
    derived_intent_uid: str = "IS-20260403-212914Z-AED0",
) -> Path:
    item_dir = ledger_storage.ensure_item_dir(
        ledger_storage.ledger_patch_instruction_sheets_root(repo_root),
        uid=uid,
        slug=slug,
        status=status,
        created_utc=updated_utc,
    )
    ledger_storage.atomic_write_json(
        item_dir / "meta.json",
        {
            "uid": uid,
            "slug": slug,
            "title": title,
            "status": status,
            "created_utc": updated_utc,
            "updated_utc": updated_utc,
            "release_state": "draft",
        },
    )
    ledger_storage.atomic_write_text(
        item_dir / "pis.md",
        f"# Patch Instruction Sheet (PIS)\n\n**UID:** {uid}\n\n**Derived from Intent Sheet:** {derived_intent_uid}\n\n## 1. Title\n{title}\n",
    )
    return item_dir


def _round_payload() -> dict:
    return {
        "round_id": "round_001",
        "candidate_ref": "cand_recent_001",
        "critique_packet": {
            "summary": "The candidate needs an explicit response mapping for each critique item.",
            "packet_provenance": {
                "source_pis_hash": "a" * 64,
                "cited_artifacts": [
                    "docs/contracts/contract_pis_improvement_session_v1.md",
                    "governance/pis_improvement_session_contract_v1.json",
                ],
            },
            "critique_items": [
                {
                    "critique_id": "critique_item_001",
                    "category": "resolution_gap",
                    "severity": "major",
                    "finding": "A critique without an explicit response mapping will not survive replay.",
                    "evidence": "The shared contract requires critique-to-resolution linkage.",
                    "proposed_resolution": "Persist a reviser packet that answers critique_item_001 explicitly.",
                }
            ],
            "overall_verdict": "revision_required",
        },
        "reviser_packet": {
            "summary": "Linked the response back to the recorded critique item.",
            "resolution_items": [
                {
                    "critique_id": "critique_item_001",
                    "disposition": "addressed",
                    "response": "The reviser packet now cites the critique item directly.",
                    "change_summary": "Replay can prove the critique was answered.",
                }
            ],
            "overall_status": "ready_for_review",
        },
        "proposed_revision_text": "--- a/demo\n+++ b/demo\n@@\n+deterministic revision delta\n",
        "change_summary": "Persisted a replay-safe critique resolution mapping.",
    }


def test_entrypoint_contract_v1_exposes_expected_metadata() -> None:
    payload = entrypoints.pis_improvement_entrypoint_contract_v1()

    assert payload.version == "pis_improvement_entrypoint_contract_v1"
    assert payload.uid == "PIS-20260412-062103Z-D4F6"
    assert payload.canonical_artifact_rel.as_posix() == "governance/pis_improvement_entrypoint_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_pis_improvement_entrypoints_v1.md"
    assert payload.supported_actions == ("list_recent", "launch")
    assert payload.launch_required_fields == ("request_id", "source_pis_uid")
    assert "responses_must_return_operator_summary_runtime_status_and_durable_artifact_refs" in payload.fail_closed_rules


def test_entrypoint_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/pis_improvement_entrypoint_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == entrypoints.ENTRYPOINT_CONTRACT_DOC_REL.as_posix()
    assert payload["request_schema_version"] == entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION
    assert payload["result_schema_version"] == entrypoints.ENTRYPOINT_RESULT_SCHEMA_VERSION
    assert tuple(payload["supported_actions"]) == ("list_recent", "launch")
    assert tuple(payload["launch_required_fields"]) == entrypoints.LAUNCH_REQUIRED_FIELDS
    assert tuple(payload["launch_optional_fields"]) == entrypoints.LAUNCH_OPTIONAL_FIELDS
    assert tuple(payload["result_required_fields"]) == entrypoints.RESULT_REQUIRED_FIELDS
    assert tuple(payload["fail_closed_rules"]) == entrypoints.FAIL_CLOSED_RULES


def test_entrypoint_contract_artifact_validates() -> None:
    assert entrypoints.validate_pis_improvement_entrypoint_contract_v1() == ()


def test_list_recent_pis_candidates_orders_newest_first(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062103Z-D4F6",
        slug="child-entrypoint",
        title="Child entrypoint work",
        status="draft",
        updated_utc="2026-04-12T06:21:03Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="child-contract",
        title="Child contract work",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "list_recent",
            "limit": 5,
        },
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert [row["uid"] for row in result["recent_candidates"]] == [
        "PIS-20260412-062103Z-D4F6",
        "PIS-20260412-062101Z-B2D4",
    ]


def test_request_validation_rejects_missing_schema_version(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062103Z-D4F6",
        slug="child-entrypoint",
        title="Child entrypoint work",
        status="draft",
        updated_utc="2026-04-12T06:21:03Z",
    )

    try:
        entrypoints.run_request(
            {
                "action": "list_recent",
                "limit": 5,
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        assert "schema_version is required" in str(exc)
    else:
        raise AssertionError("expected missing schema_version to fail closed")


def test_launch_entrypoint_materializes_shared_session_and_returns_envelope(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_launch_001",
            "source_pis_uid": "PIS-20260412-060451Z-EB85",
            "limit": 5,
        },
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert result["status"] == "resumable"
    assert result["session_id"].startswith("pis_improve_pis_20260412_060451z_eb85_")
    assert result["selected_candidate_uids"] == ["PIS-20260412-062101Z-B2D4"]
    assert result["recommendation"]["status"] == "pending"
    manifest_path = tmp_path / result["artifacts"]["session_manifest"]
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["candidate_selection"]["selected_candidate_refs"] == ["cand_recent_001"]
    assert payload["candidate_selection"]["deterministic_order"] == [
        "cand_recent_001",
        "cand_source_pis",
    ]


def test_launch_entrypoint_can_complete_shared_session_with_rounds_and_recommendation(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_launch_002",
            "source_pis_uid": "PIS-20260412-060451Z-EB85",
            "rounds": [_round_payload()],
            "final_recommendation": {
                "recommendation_id": "recommendation_001",
                "outcome": "adopt_revision",
                "winning_candidate_ref": "cand_recent_001",
                "rationale_markdown": "# Final recommendation\n\nAdopt the revision with explicit critique linkage.\n",
            },
        },
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["recommendation"]["status"] == "available"
    assert result["recommendation"]["outcome"] == "adopt_revision"
    assert "Adopt the revision" in result["recommendation"]["summary"]
    final_path = tmp_path / result["artifacts"]["final_recommendation"]
    assert final_path.exists()
    manifest_path = tmp_path / result["artifacts"]["session_manifest"]
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation = contract.validate_pis_improvement_session(payload)
    assert validation["ok"] is True


def test_launch_entrypoint_fails_closed_when_only_source_pis_exists(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )

    try:
        entrypoints.run_request(
            {
                "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
                "action": "launch",
                "request_id": "req_launch_003",
                "source_pis_uid": "PIS-20260412-060451Z-EB85",
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        assert "no recent comparison candidates" in str(exc)
    else:
        raise AssertionError("expected launch to fail closed when no non-source candidate exists")


def test_validate_result_payload_accepts_list_and_launch_envelopes(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    list_result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "list_recent",
            "limit": 5,
        },
        repo_root=tmp_path,
    )
    launch_result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_launch_003a",
            "source_pis_uid": "PIS-20260412-060451Z-EB85",
        },
        repo_root=tmp_path,
    )

    assert entrypoints.validate_result_payload(list_result) == ()
    assert entrypoints.validate_result_payload(launch_result) == ()


def test_launch_entrypoint_rejects_non_object_policy_overrides(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    try:
        entrypoints.run_request(
            {
                "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
                "action": "launch",
                "request_id": "req_launch_004",
                "source_pis_uid": "PIS-20260412-060451Z-EB85",
                "policy_overrides": "hidden state",
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        assert "policy_overrides must be an object" in str(exc)
    else:
        raise AssertionError("expected launch to fail closed for non-object policy_overrides")


def test_launch_entrypoint_rejects_non_list_selected_candidate_uids(tmp_path: Path) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    try:
        entrypoints.run_request(
            {
                "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
                "action": "launch",
                "request_id": "req_launch_005",
                "source_pis_uid": "PIS-20260412-060451Z-EB85",
                "selected_candidate_uids": "PIS-20260412-062101Z-B2D4",
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        assert "selected_candidate_uids must be a list" in str(exc)
    else:
        raise AssertionError("expected launch to fail closed for non-list selected_candidate_uids")


def test_cli_launch_emits_shared_session_summary(tmp_path: Path, capsys) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    exit_code = cli.main(
        [
            "launch",
            "--request-id",
            "req_cli_001",
            "--pis",
            "PIS-20260412-060451Z-EB85",
        ],
        repo_root=tmp_path,
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Improvement session launched" in stdout
    assert "Session: pis_improve_pis_20260412_060451z_eb85_" in stdout


def test_cli_launch_json_matches_shared_handler(tmp_path: Path, capsys) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    expected = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_cli_json_001",
            "source_pis_uid": "PIS-20260412-060451Z-EB85",
            "selected_candidate_uids": [],
            "limit": entrypoints.DEFAULT_RECENT_LIMIT,
            "session_id": "",
            "title": "",
            "policy_overrides": {"iteration_cap": entrypoints.DEFAULT_ITERATION_CAP},
            "overwrite": False,
        },
        repo_root=tmp_path,
    )

    exit_code = cli.main(
        [
            "launch",
            "--request-id",
            "req_cli_json_001",
            "--pis",
            "PIS-20260412-060451Z-EB85",
            "--json",
        ],
        repo_root=tmp_path,
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == expected


def test_cli_launch_json_fails_closed_without_traceback(tmp_path: Path, capsys) -> None:
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )

    exit_code = cli.main(
        [
            "launch",
            "--request-id",
            "req_cli_json_002",
            "--pis",
            "PIS-20260412-060451Z-EB85",
            "--candidate",
            "PIS-20260412-099999Z-MISSING",
            "--json",
        ],
        repo_root=tmp_path,
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["error_code"] == "invalid_request"
    assert "selected candidate(s) not available" in payload["operator_summary"]
    assert entrypoints.validate_result_payload(payload) == ()
