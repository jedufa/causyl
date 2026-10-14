from __future__ import annotations

from infra.controlplane import compiled_execution_ir as cei
from infra.controlplane import scheduled_jobs as sched


def test_validate_execution_ir_normalizes_and_fingerprints_stably() -> None:
    raw_a = {
        "ir_version": cei.IR_VERSION,
        "task_id": "job_a",
        "title": "Job A",
        "goal": "Implement bounded behavior",
        "constraints": ["executor:codex", "binding:ledger_uid:X"],
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs": ["b.py", "a.py", "a.py"],
        "forbidden_context_refs": ["z.md"],
        "required_output_kind": "report",
        "allowed_files": [],
        "validation_plan": [],
        "claim_scope": "analysis_only",
        "mode": "plan",
        "metadata": {"b": 2, "a": 1},
    }
    raw_b = {
        "metadata": {"a": 1, "b": 2},
        "mode": "plan",
        "claim_scope": "analysis_only",
        "validation_plan": [],
        "allowed_files": [],
        "required_output_kind": "report",
        "forbidden_context_refs": ["z.md"],
        "additional_context_refs": ["a.py", "b.py"],
        "required_context_bundle": "lab_core_v1",
        "constraints": ["binding:ledger_uid:X", "executor:codex"],
        "goal": "Implement bounded behavior",
        "title": "Job A",
        "task_id": "job_a",
        "ir_version": cei.IR_VERSION,
    }
    va = cei.validate_execution_ir(raw_a)
    vb = cei.validate_execution_ir(raw_b)
    assert va["ok"] is True
    assert vb["ok"] is True
    assert va["normalized_ir"] == vb["normalized_ir"]
    assert va["fingerprint"] == vb["fingerprint"]


def test_validate_execution_ir_fails_with_structured_codes() -> None:
    result = cei.validate_execution_ir(
        {
            "ir_version": cei.IR_VERSION,
            "task_id": "job_b",
            "title": "Job B",
            "goal": "Write files",
            "constraints": [],
            "required_context_bundle": "lab_core_v1",
            "additional_context_refs": ["same.py"],
            "forbidden_context_refs": ["same.py"],
            "required_output_kind": "unified_diff",
            "allowed_files": [],
            "validation_plan": [],
            "claim_scope": "bounded_repo_change",
            "mode": "build",
            "metadata": {},
        }
    )
    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["failure_class"] == "ir_validation_failed"
    assert "contradictory_context_refs" in codes
    assert "missing_allowed_files" in codes
    assert "missing_validation_plan" in codes


def test_validate_execution_ir_strict_mode_rejects_unknown_fields() -> None:
    result = cei.validate_execution_ir(
        {
            "ir_version": cei.IR_VERSION,
            "task_id": "job_strict",
            "title": "Strict Job",
            "goal": "Verify",
            "constraints": [],
            "required_context_bundle": "lab_core_v1",
            "additional_context_refs": [],
            "forbidden_context_refs": [],
            "required_output_kind": "report",
            "allowed_files": [],
            "validation_plan": [],
            "claim_scope": "analysis_only",
            "mode": "plan",
            "metadata": {"compiled_execution_mode": "hard_gate"},
            "unexpected_field": "boom",
        }
    )
    assert result["ok"] is False
    assert any(row["code"] == "strict_unknown_field" for row in result["errors"])


def test_compiled_execution_ir_from_job_spec_build_verify_loop_is_write_capable() -> None:
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_ir_test_001",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-687C"},
            "job_kind": "build_verify_loop",
            "loop": {"max_iterations": 3},
            "prompt": "Implement the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "unified_diff", "format": "markdown"},
            "policy": {"max_runtime_seconds": 180, "repo_change_mode": "strict", "require_commit_journal": True},
            "runtime": {"executor_agent_tech": "codex"},
            "display_title": "Compiled IR build loop",
            "required_context_bundle": "lab_core_v1",
            "context_refs": ["governance/ledger/patch_instruction_sheets/active/2026/03/PIS-20260329-151108Z-687C__execution-ir-schema-normalization-and-validator/pis.md"],
        },
        now_utc="2026-03-29T15:30:00Z",
    )
    result = sched.compiled_execution_ir_from_job_spec(spec)
    assert result["ok"] is True
    ir = result["normalized_ir"]
    assert ir["claim_scope"] == "bounded_repo_change"
    assert ir["mode"] == "build"
    assert ir["allowed_files"] == ["<scheduler_managed_scope>"]
    assert ir["validation_plan"] == ["commit_journal_required", "validation_evidence_required"]
    assert ir["required_output_kind"] == "unified_diff"
    assert ir["metadata"]["builder_contract_version"] == "builder_turn_contract_v1"
    assert ir["metadata"]["builder_result_packet_version"] == "builder_result_packet_v1"
    assert ir["metadata"]["builder_contract_revision"]
    assert ir["metadata"]["verifier_result_packet_version"] == "verifier_review_packet_v1"


def test_compiled_execution_ir_from_job_spec_compiled_mode_sets_verifier_contract_version() -> None:
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_ir_test_001b",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-687C"},
            "job_kind": "build_verify_loop",
            "loop": {"max_iterations": 3},
            "prompt": "Implement the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "unified_diff", "format": "markdown"},
            "policy": {"max_runtime_seconds": 180, "repo_change_mode": "strict", "require_commit_journal": True},
            "runtime": {"executor_agent_tech": "codex", "compiled_execution_mode": "hard_gate"},
            "display_title": "Compiled IR build loop",
            "required_context_bundle": "lab_core_v1",
            "context_refs": ["governance/ledger/patch_instruction_sheets/active/2026/03/PIS-20260329-151108Z-687C__execution-ir-schema-normalization-and-validator/pis.md"],
        },
        now_utc="2026-03-29T15:30:00Z",
    )

    result = sched.compiled_execution_ir_from_job_spec(spec)

    assert result["ok"] is True
    ir = result["normalized_ir"]
    assert ir["metadata"]["verifier_contract_version"] == "verifier_review_contract_v1"
    assert ir["metadata"]["verifier_result_packet_version"] == "verifier_review_packet_v1"


def test_compiled_execution_ir_from_job_spec_single_turn_defaults_to_analysis_only() -> None:
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_ir_test_002",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "abc"},
            "prompt": "Explain the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "display_title": "Compiled IR single turn",
            "required_context_bundle": "lab_core_v1",
            "runtime": {"executor_agent_tech": "codex"},
        },
        now_utc="2026-03-29T15:30:00Z",
    )
    result = sched.compiled_execution_ir_from_job_spec(spec)
    assert result["ok"] is True
    ir = result["normalized_ir"]
    assert ir["claim_scope"] == "analysis_only"
    assert ir["mode"] == "plan"
    assert ir["allowed_files"] == []
    assert ir["validation_plan"] == []
    assert ir["metadata"]["job_shape"] == "verification_loop_job"
    assert ir["metadata"]["job_shape_source"] == "explicit"
    assert ir["metadata"]["job_shape_compatibility_mode"] is False


def test_compiled_execution_ir_from_legacy_job_carries_compatibility_shape_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "compiled_ir_legacy_shape_001.json").write_text(
        """{
  "schema_version": "scheduled_job_v1",
  "job_id": "compiled_ir_legacy_shape_001",
  "enabled": true,
  "created_utc": "2026-04-06T06:00:00Z",
  "updated_utc": "2026-04-06T06:00:00Z",
  "job_kind": "single_turn",
  "role": "generalist",
  "binding": {"type": "task_id", "id": "mb3"},
  "depends_on": [],
  "context_refs": [],
  "required_context_bundle": "lab_core_v1",
  "additional_context_refs": [],
  "forbidden_context_refs": [],
  "expected_output": {"kind": "report", "format": "markdown"},
  "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
  "policy": {"repo_change_mode": "strict"},
  "runtime": {"executor_agent_tech": "codex", "model": "gpt-5.4"},
  "display_title": "legacy shape job",
  "prompt_ref": "",
  "prompt_sha256": "",
  "prompt_preview": "legacy single-turn job",
  "prompt": "Explain the bound item."
}
""",
        encoding="utf-8",
    )

    spec = sched.load_job("compiled_ir_legacy_shape_001", repo_root=tmp_path)
    result = sched.compiled_execution_ir_from_job_spec(spec)

    assert result["ok"] is True
    metadata = result["normalized_ir"]["metadata"]
    assert metadata["job_shape"] == "verification_loop_job"
    assert metadata["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert metadata["job_shape_compatibility_mode"] is True
    assert spec["job_shape_migration_status"] == "legacy_pending_backfill"


def test_compiled_execution_ir_from_job_spec_carries_intent_validation_family_contract() -> None:
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_ir_test_003",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260406-060007Z-ACD1"},
            "job_kind": "build_verify_loop",
            "prompt": "Validate the bound intent family.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:00:07Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"executor_agent_tech": "codex", "compiled_execution_mode": "soft_gate"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_contract": {
                    "contract_kind": "intent_derived_execution_family_v1",
                    "source_intent_uid": "IS-20260403-212913Z-5817",
                    "parent_uid": "PIS-20260406-053253Z-2050",
                    "child_uid": "PIS-20260406-060007Z-ACD1",
                    "child_kind": "intent_validation_suite",
                    "required_final_child_kind": "intent_family_closeout",
                    "required_final_child_uid": "PIS-20260406-060009Z-CLO5",
                }
            },
        },
        now_utc="2026-04-06T06:00:07Z",
    )
    result = sched.compiled_execution_ir_from_job_spec(spec)
    assert result["ok"] is True
    ir = result["normalized_ir"]
    assert "intent_e2e_validation_required" in ir["validation_plan"]
    assert "intent_acceptance_summary_required" in ir["validation_plan"]
    assert ir["metadata"]["execution_family_child_kind"] == "intent_validation_suite"
    assert ir["metadata"]["execution_family_source_intent_uid"] == "IS-20260403-212913Z-5817"
    assert ir["metadata"]["execution_family_required_final_child_kind"] == "intent_family_closeout"


def test_compiled_execution_ir_from_job_spec_carries_execution_family_closeout_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched._write_state(
        "job_child_001",
        {
            "last_status": "ok",
            "last_receipt_path": str((sched.receipts_dir(tmp_path) / "job_child_001" / "sj_demo_001.json").resolve()),
        },
        repo_root=tmp_path,
    )
    receipt_path = sched.receipts_dir(tmp_path) / "job_child_001" / "sj_demo_001.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    sched._write_json_atomic(
        receipt_path,
        {
            "repo_residue": {
                "declared_changed_files": [
                    "infra/controlplane/job_budget_cost_ledger_store.py",
                    "tests/control_plane/test_job_budget_cost_ledger_store.py",
                ]
            }
        },
    )
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_ir_closeout_001",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260406-182612Z-BEB9"},
            "job_kind": "build_verify_loop",
            "prompt": "Audit, integrate, and close the execution family rooted at `PIS-20260406-182612Z-BEB9` only when the durable child-job evidence honestly supports closure.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T05:40:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"executor_agent_tech": "codex", "compiled_execution_mode": "soft_gate"},
            "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_closeout": {
                    "contract_kind": "execution_family_closeout_v1",
                    "parent_uid": "PIS-20260406-182612Z-BEB9",
                    "source_intent_uid": "IS-20260403-212913Z-4529",
                    "required_final_child_uid": "PIS-20260406-183447Z-1246",
                    "child_uids": ["PIS-20260406-183447Z-1246"],
                    "child_job_ids": ["job_child_001"],
                    "integration_audit_mode": "family_receipt_changed_files_v1",
                    "trust_hardening_mode": "broad_current_state_rerun_v1",
                }
            },
        },
        now_utc="2026-04-07T05:40:00Z",
        repo_root=tmp_path,
    )
    result = sched.compiled_execution_ir_from_job_spec(spec)
    assert result["ok"] is True
    ir = result["normalized_ir"]
    assert "execution_family_audit_required" in ir["validation_plan"]
    assert "execution_family_integration_repair_allowed" in ir["validation_plan"]
    assert "execution_family_trust_hardening_required" in ir["validation_plan"]
    assert ir["execution_family"]["contract_kind"] == "execution_family_closeout_v1"
    assert ir["execution_family"]["phase"] == "family_closeout"
    assert ir["execution_family"]["parent_uid"] == "PIS-20260406-182612Z-BEB9"
    assert ir["execution_family"]["child_job_ids"] == ["job_child_001"]
    assert ir["execution_family"]["trust_hardening_mode"] == "broad_current_state_rerun_v1"
    assert ir["execution_family"]["allowed_repo_paths"] == [
        "infra/controlplane/job_budget_cost_ledger_store.py",
        "tests/control_plane/test_job_budget_cost_ledger_store.py",
    ]


def test_compiled_execution_ir_from_job_spec_marks_intent_family_closeout_child() -> None:
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_ir_test_004",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260406-060009Z-CLO5"},
            "job_kind": "build_verify_loop",
            "prompt": "Close the bound intent family honestly.",
            "schedule": {"mode": "once", "run_at": "2026-04-06T06:00:09Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"executor_agent_tech": "codex", "compiled_execution_mode": "soft_gate"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 1, "pass_marker": "VERDICT: PASS"},
            "prompt_meta": {
                "execution_family_contract": {
                    "contract_kind": "intent_derived_execution_family_v1",
                    "source_intent_uid": "IS-20260403-212913Z-5817",
                    "parent_uid": "PIS-20260406-053253Z-2050",
                    "child_uid": "PIS-20260406-060009Z-CLO5",
                    "child_kind": "intent_family_closeout",
                    "required_final_child_kind": "intent_family_closeout",
                    "required_final_child_uid": "PIS-20260406-060009Z-CLO5",
                }
            },
        },
        now_utc="2026-04-06T06:00:09Z",
    )
    result = sched.compiled_execution_ir_from_job_spec(spec)
    assert result["ok"] is True
    ir = result["normalized_ir"]
    assert "execution_family_audit_required" in ir["validation_plan"]
    assert "execution_family_integration_repair_allowed" in ir["validation_plan"]
    assert "execution_family_closeout_required" in ir["validation_plan"]
    assert "execution_family_trust_hardening_required" in ir["validation_plan"]
    assert ir["metadata"]["execution_family_child_kind"] == "intent_family_closeout"
