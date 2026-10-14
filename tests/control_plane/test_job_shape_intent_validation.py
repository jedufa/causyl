from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import context_bundles as bundles
from infra.controlplane import job_shape_relationship_contract as relationships
from infra.controlplane import scheduled_jobs as sched


def _write_bundle_files(root: Path) -> None:
    seen: set[str] = set()
    for rel in (*bundles.LAB_CORE_V1, *bundles.LAB_ARCH_GOV_V1):
        if rel in seen:
            continue
        seen.add(rel)
        path = (root / rel).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {rel}\n", encoding="utf-8")


def test_job_shape_taxonomy_intent_validation_proves_explicit_operator_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)

    spec = sched.save_job(
        {
            "job_id": "intent_validation_shape_001",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "job_shape": "verification_loop_job",
            "binding": {"type": "ledger_uid", "id": "PIS-20260406-060007Z-ACD1"},
            "prompt": "Implement the requested bounded change.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "policy": {"repo_change_mode": "strict"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    loaded = sched.load_job("intent_validation_shape_001", repo_root=tmp_path)

    assert spec["job_shape"] == "verification_loop_job"
    assert loaded["job_shape"] == "verification_loop_job"
    assert loaded["job_shape_source"] == "explicit"
    assert loaded["job_shape_compatibility_mode"] is False
    assert loaded["required_context_bundle"] == "lab_core_v1"

    relationships.assert_legal_parent_child_relationship("fanout_job", loaded["job_shape"])

    listed = sched.list_jobs(now_utc="2026-04-06T06:31:00Z", repo_root=tmp_path)
    assert listed[0]["job_shape"] == "verification_loop_job"
    assert listed[0]["due"]["eligible"] is True

    sched._write_state(
        "intent_validation_shape_001",
        {"last_status": "blocked", "last_finished_utc": "2026-04-06T06:31:30Z"},
        repo_root=tmp_path,
    )
    summary = sched.job_summary(
        loaded,
        state=sched.load_state("intent_validation_shape_001", repo_root=tmp_path),
        repo_root=tmp_path,
    )
    assert summary["lifecycle_projection"]["recognized"] is True
    assert summary["lifecycle_projection"]["canonical_state"] == "blocked"
    assert summary["job_shape_summary"]["operator_summary"].startswith("verification_loop_job")
    assert summary["job_shape_summary"]["recovery_semantics"].startswith("Restart from the last build-verify iteration boundary")

    builder_message = sched._build_loop_builder_message(
        loaded,
        run_id="sj_intent_validation",
        trigger="scheduler",
        iteration=1,
        max_iterations=2,
        repo_root=tmp_path,
    )
    assert "required_context_bundle: lab_core_v1" in builder_message
    assert "context_reentry_precedence: durable_contract_then_durable_delta_then_transcript_summary" in builder_message
    assert "- job_shape: verification_loop_job" in builder_message

    compiled = sched._compiled_execution_shadow_bundle_for_job(loaded, roles=["builder"], repo_root=tmp_path)
    manifest = compiled["manifest"]["manifest"]
    assert compiled["job_shape"] == "verification_loop_job"
    assert compiled["job_shape_source"] == "explicit"
    assert compiled["job_shape_compatibility_mode"] is False
    assert manifest["required_context_bundle"] == "lab_core_v1"
    assert [row["path"] for row in manifest["files"][:3]] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]


def test_job_shape_taxonomy_intent_validation_marks_legacy_incompatible_records_without_guessing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "intent_validation_legacy_bad_001.json").write_text(
        json.dumps(
            {
                "schema_version": sched.SCHEMA_VERSION,
                "job_id": "intent_validation_legacy_bad_001",
                "enabled": True,
                "created_utc": "2026-04-06T06:00:00Z",
                "updated_utc": "2026-04-06T06:00:00Z",
                "job_kind": "single_turn",
                "role": "generalist",
                "binding": {"type": "task_id", "id": "mb3"},
                "depends_on": [],
                "context_refs": [],
                "required_context_bundle": "lab_arch_gov_v1",
                "additional_context_refs": [],
                "forbidden_context_refs": [],
                "expected_output": {"kind": "report", "format": "markdown"},
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "policy": {"repo_change_mode": "strict"},
                "runtime": {"executor_agent_tech": "codex"},
                "display_title": "legacy bad shape job",
                "prompt_ref": "",
                "prompt_sha256": "",
                "prompt_preview": "legacy shape bad job",
                "prompt": "Explain the bound item.",
            }
        ),
        encoding="utf-8",
    )

    loaded = sched.load_job("intent_validation_legacy_bad_001", repo_root=tmp_path)

    assert loaded["job_shape"] == "verification_loop_job"
    assert loaded["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert loaded["job_shape_compatibility_mode"] is True
    assert loaded["job_shape_migration_status"] == "legacy_incompatible"
    assert "requires required_context_bundle=lab_core_v1" in loaded["job_shape_migration_reason"]


def test_job_shape_taxonomy_intent_validation_rejects_new_invalid_context_bundle_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)

    with pytest.raises(ValueError, match="requires required_context_bundle=lab_core_v1"):
        sched.save_job(
            {
                "job_id": "intent_validation_shape_bad_001",
                "enabled": True,
                "job_kind": "build_verify_loop",
                "job_shape": "verification_loop_job",
                "binding": {"type": "ledger_uid", "id": "PIS-20260406-060007Z-ACD1"},
                "required_context_bundle": "lab_arch_gov_v1",
                "prompt": "Implement the requested bounded change.",
                "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
                "expected_output": {"kind": "report", "format": "markdown"},
                "policy": {"repo_change_mode": "strict"},
                "loop": {
                    "builder_role": "builder",
                    "verifier_role": "verifier",
                    "max_iterations": 2,
                    "pass_marker": "VERDICT: PASS",
                },
            },
            repo_root=tmp_path,
        )
