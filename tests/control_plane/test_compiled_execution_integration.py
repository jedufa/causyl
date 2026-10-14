from __future__ import annotations

from pathlib import Path

from infra.controlplane import context_bundles as bundles
from infra.controlplane import compiled_output_classifier as coc
from infra.controlplane import scheduled_jobs as sched


def _write_bundle_files(root: Path) -> None:
    for rel in bundles.LAB_CORE_V1:
        path = (root / rel).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {rel}\n", encoding="utf-8")


def test_single_turn_receipt_includes_compiled_execution_shadow_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda **kwargs: {"ok": True, "assistant": {"content": "analysis reply", "status": "ok", "meta": {}}},
    )
    sched.save_job(
        {
            "job_id": "compiled_shadow_single_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "abc"},
            "prompt": "Explain the bound work.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_shadow_single_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T16:05:00Z")
    receipt = sched.load_receipt("compiled_shadow_single_001", result["run_id"], repo_root=tmp_path)
    assert receipt["compiled_execution"]["enabled"] is True
    assert receipt["compiled_execution"]["execution_mode"] == "shadow_compile"
    assert receipt["compiled_execution"]["prompts"]["generalist"]["prompt_version"] == "compiled_prompt_v1"
    assert receipt["compiled_execution"]["dispatch_mode"] == "legacy_message"


def test_single_turn_soft_gate_dispatches_compiled_prompt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    seen: dict[str, str] = {}

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None):
        seen["message"] = message
        return {"ok": True, "assistant": {"content": "analysis reply", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    sched.save_job(
        {
            "job_id": "compiled_soft_gate_single_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "abc"},
            "prompt": "Explain the bound work.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"compiled_execution_mode": "soft_gate"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_soft_gate_single_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T16:05:00Z")
    receipt = sched.load_receipt("compiled_soft_gate_single_001", result["run_id"], repo_root=tmp_path)
    compiled_prompt = receipt["compiled_execution"]["prompts"]["generalist"]["prompt_text"]
    assert seen["message"] == compiled_prompt
    assert receipt["compiled_execution"]["execution_mode"] == "soft_gate"
    assert receipt["compiled_execution"]["dispatch_mode"] == "compiled_prompt"
    assert receipt["compiled_dispatch"]["dispatch_mode"] == "compiled_prompt"


def test_single_turn_hard_gate_blocks_when_required_manifest_refs_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda **kwargs: {"ok": True, "assistant": {"content": "should not send", "status": "ok", "meta": {}}},
    )
    sched.save_job(
        {
            "job_id": "compiled_hard_gate_single_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "abc"},
            "prompt": "Explain the bound work.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "runtime": {"compiled_execution_mode": "hard_gate"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_hard_gate_single_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T16:05:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("compiled_hard_gate_single_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "error"
    assert receipt["compiled_dispatch"]["dispatch_mode"] == "blocked_compiled_prompt"


def test_legacy_single_turn_receipt_preserves_compiled_execution_job_shape_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "compiled_shadow_legacy_001.json").write_text(
        """{
  "schema_version": "scheduled_job_v1",
  "job_id": "compiled_shadow_legacy_001",
  "enabled": true,
  "created_utc": "2026-04-06T06:00:00Z",
  "updated_utc": "2026-04-06T06:00:00Z",
  "job_kind": "single_turn",
  "role": "generalist",
  "binding": {"type": "task_id", "id": "mb3"},
  "depends_on": [],
  "context_refs": ["governance/ledger/patch_instruction_sheets/example/pis.md"],
  "required_context_bundle": "lab_core_v1",
  "additional_context_refs": [],
  "forbidden_context_refs": [],
  "expected_output": {"kind": "report", "format": "markdown"},
  "schedule": {"mode": "once", "run_at": "2026-04-06T06:30:00Z", "timezone": "UTC"},
  "policy": {"repo_change_mode": "strict"},
  "runtime": {"executor_agent_tech": "codex"},
  "display_title": "legacy compiled shadow",
  "prompt_ref": "",
  "prompt_sha256": "",
  "prompt_preview": "legacy single-turn job",
  "prompt": "Explain the bound item."
}
""",
        encoding="utf-8",
    )

    spec = sched.load_job("compiled_shadow_legacy_001", repo_root=tmp_path)
    compiled = sched._compiled_execution_shadow_bundle_for_job(spec, roles=["generalist"], repo_root=tmp_path)

    assert spec["job_shape"] == "verification_loop_job"
    assert spec["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert spec["job_shape_compatibility_mode"] is True
    assert compiled["job_shape"] == "verification_loop_job"
    assert compiled["job_shape_source"] == "compatibility_inferred_from_job_kind"
    assert compiled["job_shape_compatibility_mode"] is True
    assert compiled["manifest"]["manifest"]["required_context_bundle"] == "lab_core_v1"
    manifest_paths = [row["path"] for row in compiled["manifest"]["manifest"]["files"]]
    assert manifest_paths[:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert manifest_paths[-1] == "governance/ledger/patch_instruction_sheets/example/pis.md"


def test_output_classifier_recognizes_builder_and_verifier_shapes() -> None:
    builder_ir = {"required_output_kind": "unified_diff", "claim_scope": "bounded_repo_change", "allowed_files": ["a.py"]}
    assert coc.classify_builder_output("Changed files:\n- a.py\n\nValidation Commands Run:\n- pytest -q\n```diff\n--- a\n+++ b\n```", normalized_ir=builder_ir)["ok"] is True
    assert coc.classify_verifier_output("VERDICT: FAIL\nNeed more evidence.", normalized_ir={"mode": "verify", "required_output_kind": "verdict_report"})["ok"] is True
    bad = coc.classify_verifier_output("Verdict: FAIL\nnot canonical")
    assert bad["ok"] is False
    assert bad["reason"] in {"missing_verdict_header", "verdict_not_on_first_line"}


def test_output_classifier_rejects_verifier_metadata_preamble_before_verdict() -> None:
    result = coc.classify_verifier_output(
        (
            "STRUCTURAL VALIDITY: valid\n"
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - required blocks are present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit evidence is cited.\n"
            "- pass_fail_closed_check: satisfied - PASS stays fail-closed.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_compiled_execution_integration.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "verdict_not_on_first_line"
    assert result["verdict"] == "PASS"


def test_output_classifier_rejects_verifier_pass_missing_required_review_block() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - contract satisfied.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks exist.\n"
            "- pass_fail_closed_check: satisfied - evidence is explicit.\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "missing_validation_review_section"
    assert result["details"] == ["validation_review"]


def test_output_classifier_accepts_scheduled_high_impact_verifier_shape_under_review_packet_v1() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS\n"
            "Findings by Severity:\n"
            "- low | tests/control_plane/test_compiled_execution_integration.py | none\n"
            "Boundary Review:\n"
            "- in_scope_only: yes\n"
            "- sibling_overlap_found: no\n"
            "- boundary_notes: stayed inside the bound repair scope.\n"
            "Proof Review:\n"
            "- validation_evidence_sufficient: yes\n"
            "- remaining_proof_gaps: none\n"
            "Validation Run Review:\n"
            "- commands_checked: pytest tests/control_plane/test_compiled_execution_integration.py -q\n"
            "- results_checked: sufficient - targeted verifier contract regression passed.\n"
            "One-Iteration Assessment:\n"
            "- disposition: keep_as_is\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is True
    assert result["verdict"] == "PASS"
    assert result["result_packet"]["findings"][0].startswith("low |")
    assert result["result_packet"]["boundary_review"]["scope_alignment"].startswith("pass")
    assert result["result_packet"]["proof_review"]["evidence_status"].startswith("sufficient")
    assert result["result_packet"]["proof_review"]["pass_fail_closed_check"].startswith("satisfied")
    assert result["result_packet"]["validation_review"]["commands_reviewed"].startswith("pytest ")
    assert result["result_packet"]["one_iteration_assessment"]["disposition"] == "keep_as_is"


def test_output_classifier_rejects_scheduled_high_impact_pass_with_remaining_proof_gaps() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS\n"
            "Findings by Severity:\n"
            "- medium | tests/control_plane/test_compiled_execution_integration.py | regression proof still missing.\n"
            "Boundary Review:\n"
            "- in_scope_only: yes\n"
            "- sibling_overlap_found: no\n"
            "- boundary_notes: stayed inside the bound repair scope.\n"
            "Proof Review:\n"
            "- validation_evidence_sufficient: yes\n"
            "- remaining_proof_gaps: targeted host proof still missing\n"
            "Validation Run Review:\n"
            "- commands_checked: pytest tests/control_plane/test_compiled_execution_integration.py -q\n"
            "- results_checked: sufficient - local slice passed, but proof gap remains.\n"
            "One-Iteration Assessment:\n"
            "- disposition: patch_only\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "pass_without_explicit_proof_review"
    assert result["details"] == ["pass_fail_closed_check"]


def test_output_classifier_accepts_pass_with_explicit_none_and_low_severity_none_tail() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - stayed in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks.\n"
            "- pass_fail_closed_check: satisfied - evidence-bound.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest -q tests/control_plane/test_compiled_execution_integration.py\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
            "Findings by Severity:\n"
            "- severity low | none | none\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is True
    assert result["verdict"] == "PASS"


def test_output_classifier_rejects_confidence_only_pass_when_contract_active() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - contract satisfied.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - maybe fine.\n"
            "- pass_fail_closed_check: unsatisfied - mostly confidence.\n"
            "Validation Review:\n"
            "- commands_reviewed: none\n"
            "- outcome_support: sufficient - confidence is high.\n"
            "Confidence:\n"
            "- advisory_only: high confidence.\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "pass_without_explicit_proof_review"
    assert result["details"] == ["pass_fail_closed_check"]


def test_output_classifier_rejects_unsupported_verifier_verdict_when_review_contract_active() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS_WITH_CONFIDENCE\n"
            "Findings:\n"
            "- none\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks exist.\n"
            "- pass_fail_closed_check: satisfied - evidence is explicit.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest tests/control_plane/test_compiled_execution_integration.py -q\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "invalid_verdict_token"
    assert result["details"] == ["PASS_WITH_CONFIDENCE"]


def test_output_classifier_rejects_vague_findings_when_review_contract_active() -> None:
    result = coc.classify_verifier_output(
        (
            "VERDICT: PASS\n"
            "Findings:\n"
            "- looks good overall\n"
            "Boundary Review:\n"
            "- scope_alignment: pass - in scope.\n"
            "- contract_alignment: pass - present.\n"
            "Proof Review:\n"
            "- evidence_status: sufficient - explicit checks exist.\n"
            "- pass_fail_closed_check: satisfied - evidence is explicit.\n"
            "Validation Review:\n"
            "- commands_reviewed: pytest tests/control_plane/test_compiled_execution_integration.py -q\n"
            "- outcome_support: sufficient - targeted proof passed.\n"
        ),
        normalized_ir={
            "mode": "verify",
            "required_output_kind": "verdict_report",
            "metadata": {
                "verifier_contract_version": "verifier_review_contract_v1",
                "verifier_result_packet_version": "verifier_review_packet_v1",
            },
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "invalid_findings_block"
    assert result["details"] == ["findings"]


def test_output_classifier_accepts_markdown_emphasized_builder_headings() -> None:
    builder_ir = {"required_output_kind": "report", "claim_scope": "bounded_repo_change", "allowed_files": ["<scheduler_managed_scope>"]}
    content = (
        "**Goal**\n\n"
        "Ship a narrow honest slice.\n\n"
        "**Changed Files**\n\n"
        "- infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AppShell.kt\n\n"
        "**Validation Commands Run:**\n\n"
        "- `cd infra/mobile/android && ./gradlew :app:testDebugUnitTest`: pass\n"
    )
    result = coc.classify_builder_output(content, normalized_ir=builder_ir)
    assert result["ok"] is True
    assert result["reason"] == "recognized_builder_structure"
    assert "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AppShell.kt" in result["changed_files"]
    assert result["result_packet"]["required_sections"] == ["changed_files"]
    assert result["result_packet"]["claim_strength"] == "bounded_to_explicit_validation_commands"


def test_output_classifier_rejects_missing_validation_commands_for_build_turns() -> None:
    result = coc.classify_builder_output(
        "Changed files:\n- app.py\n",
        normalized_ir={"required_output_kind": "report", "claim_scope": "bounded_repo_change", "allowed_files": ["app.py"], "mode": "build"},
    )
    assert result["ok"] is False
    assert result["reason"] == "missing_validation_commands_section"
    assert result["result_packet"]["missing_sections"] == ["validation_commands"]


def test_output_classifier_rejects_missing_explicitly_required_builder_section() -> None:
    result = coc.classify_builder_output(
        (
            "Changed files:\n"
            "- app.py\n\n"
            "Validation Commands Run:\n"
            "- pytest -q tests/foo.py\n"
        ),
        normalized_ir={
            "required_output_kind": "report",
            "claim_scope": "bounded_repo_change",
            "allowed_files": ["app.py"],
            "mode": "build",
            "metadata": {"builder_required_sections": ["Assumptions Made"]},
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "missing_required_sections"
    assert result["details"] == ["assumptions"]


def test_output_classifier_rejects_done_section_mutation_claim_outside_changed_files() -> None:
    result = coc.classify_builder_output(
        (
            "Done:\n"
            "- Updated app.py and nope.py.\n\n"
            "Changed files:\n"
            "- app.py\n\n"
            "Validation Commands Run:\n"
            "- pytest -q tests/foo.py\n"
        ),
        normalized_ir={"required_output_kind": "report", "claim_scope": "bounded_repo_change", "allowed_files": ["app.py"], "mode": "build"},
    )
    assert result["ok"] is False
    assert result["reason"] == "mutation_claim_outside_changed_files"
    assert result["details"] == ["nope.py"]


def test_output_classifier_rejects_changed_files_outside_allowed_scope() -> None:
    result = coc.classify_builder_output(
        "Changed files:\n- nope.py\n\nValidation Commands Run:\n- pytest -q\n```diff\n--- a\n+++ b\n```",
        normalized_ir={"required_output_kind": "unified_diff", "claim_scope": "bounded_repo_change", "allowed_files": ["ok.py"]},
    )
    assert result["ok"] is False
    assert result["reason"] == "changed_files_outside_allowed_scope"


def test_output_classifier_accepts_markdown_linked_changed_files_with_glob_allowed_scope() -> None:
    content = (
        "Changed files:\n"
        "- [meta.json](/home/user/causyl/governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260407-041735Z-1DE7__resource-concurrency-governa/meta.json)\n"
        "- [events.jsonl](/home/user/causyl/governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260407-041735Z-1DE7__resource-concurrency-governa/events.jsonl)\n\n"
        "Validation Commands Run:\n"
        "- `pytest -q tests/control_plane/test_resource_concurrency_dependency_gate_regression.py`: pass\n"
    )
    result = coc.classify_builder_output(
        content,
        normalized_ir={
            "required_output_kind": "report",
            "claim_scope": "bounded_repo_change",
            "allowed_files": [
                "governance/ledger/patch_instruction_sheets/**/PIS-20260407-041735Z-1DE7__*/meta.json",
                "governance/ledger/patch_instruction_sheets/**/PIS-20260407-041735Z-1DE7__*/events.jsonl",
            ],
        },
    )
    assert result["ok"] is True
    assert result["reason"] == "recognized_builder_structure"
    assert result["changed_files"] == [
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260407-041735Z-1DE7__resource-concurrency-governa/meta.json",
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260407-041735Z-1DE7__resource-concurrency-governa/events.jsonl",
    ]


def test_output_classifier_accepts_files_under_allowed_artifact_directory_scope() -> None:
    content = (
        "Changed files:\n"
        "- [continuation_family_closeout_audit.md](/home/user/causyl/governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260407-055533Z-3826__continuation-packet-and-long/artifacts/verification/continuation_family_closeout_audit.md)\n"
        "- [continuation_family_closeout_audit.md](/home/user/causyl/governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260407-172959Z-DAB8__prove-continuation-packet-and-long-horizon-memory-contra/artifacts/verification/continuation_family_closeout_audit.md)\n\n"
        "Validation Commands Run:\n"
        "- `pytest -q tests/control_plane/test_continuation_intent_validation.py`: pass\n"
    )
    result = coc.classify_builder_output(
        content,
        normalized_ir={
            "required_output_kind": "report",
            "claim_scope": "bounded_repo_change",
            "allowed_files": [
                "governance/ledger/patch_instruction_sheets/**/PIS-20260407-055533Z-3826__*/artifacts/",
                "governance/ledger/patch_instruction_sheets/**/PIS-20260407-172959Z-DAB8__*/artifacts/",
            ],
        },
    )
    assert result["ok"] is True
    assert result["reason"] == "recognized_builder_structure"
    assert result["changed_files"] == [
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260407-055533Z-3826__continuation-packet-and-long/artifacts/verification/continuation_family_closeout_audit.md",
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260407-172959Z-DAB8__prove-continuation-packet-and-long-horizon-memory-contra/artifacts/verification/continuation_family_closeout_audit.md",
    ]


def test_build_verify_loop_rejects_structurally_invalid_verifier_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None):
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": "Changed files:\n- app.py\n\nValidation Commands Run:\n- pytest -q tests/foo.py\n\n```diff\n--- a/app.py\n+++ b/app.py\n```",
                    "status": "ok",
                    "meta": {},
                },
            }
        return {"ok": True, "assistant": {"content": "Verdict: FAIL\nnot canonical", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})
    sched.save_job(
        {
            "job_id": "compiled_shadow_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-EFAC"},
            "prompt": "Verify the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_shadow_loop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T16:05:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("compiled_shadow_loop_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "failed_verification"
    cls = receipt["loop"]["iterations"][0]["verifier"]["meta"]["compiled_output_classification"]
    assert cls["ok"] is False
    assert cls["reason"] in {"missing_verdict_header", "verdict_not_on_first_line"}


def test_build_verify_loop_soft_gate_dispatches_compiled_prompts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    seen: list[tuple[str, str]] = []

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None):
        seen.append((role, message))
        if role == "builder":
            return {
                "ok": True,
                "assistant": {
                    "content": "Changed files:\n- app.py\n\nValidation Commands Run:\n- pytest -q tests/foo.py\n\n```diff\n--- a/app.py\n+++ b/app.py\n```",
                    "status": "ok",
                    "meta": {},
                },
            }
        return {
            "ok": True,
            "assistant": {
                "content": (
                    "VERDICT: PASS\n"
                    "Findings:\n"
                    "- none\n"
                    "Boundary Review:\n"
                    "- scope_alignment: pass - builder stayed within the bound scope.\n"
                    "- contract_alignment: pass - required builder sections are present.\n"
                    "Proof Review:\n"
                    "- evidence_status: sufficient - explicit validation evidence is present.\n"
                    "- pass_fail_closed_check: satisfied - PASS is evidence-bound.\n"
                    "Validation Review:\n"
                    "- commands_reviewed: pytest -q tests/foo.py\n"
                    "- outcome_support: sufficient - targeted validation supports the claim.\n"
                ),
                "status": "ok",
                "meta": {},
            },
        }

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})
    sched.save_job(
        {
            "job_id": "compiled_soft_gate_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-EFAC"},
            "prompt": "Verify the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
            "runtime": {"compiled_execution_mode": "soft_gate"},
            "required_context_bundle": "lab_core_v1",
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 2, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_soft_gate_loop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T16:05:00Z")
    receipt = sched.load_receipt("compiled_soft_gate_loop_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] in {"ok", "proof_gap"}
    assert receipt["loop"]["iterations"][0]["builder"]["meta"]["compiled_dispatch"]["dispatch_mode"] == "compiled_prompt"
    if receipt["loop"]["iterations"][0].get("verifier"):
        assert receipt["loop"]["iterations"][0]["verifier"]["meta"]["compiled_dispatch"]["dispatch_mode"] == "compiled_prompt"
    builder_message = next(message for role, message in seen if role == "builder")
    assert "Compiled Role: builder" in builder_message
    assert "[Scheduled Stage Contract]" in builder_message
    assert "Validation Commands Run:" in builder_message
    verifier_messages = [message for role, message in seen if role == "verifier"]
    if verifier_messages:
        verifier_message = verifier_messages[0]
        assert "Compiled Role: verifier" in verifier_message
        assert "[Scheduled Stage Contract]" in verifier_message
        assert "start the first line with exactly `VERDICT: PASS`" in verifier_message
        assert "Boundary Review:" in verifier_message
        assert "Proof Review:" in verifier_message
        assert "Validation Review:" in verifier_message
