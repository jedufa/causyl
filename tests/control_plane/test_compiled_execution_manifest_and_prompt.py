from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import context_bundles as bundles
from infra.controlplane import compiled_execution_manifest as cem
from infra.controlplane import compiled_prompt_renderer as cpr
from infra.controlplane import continuation_packet_store as packet_store
from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as register
from infra.webui.ledger import ledger_storage


def _write_bundle_files(root: Path) -> None:
    for rel in bundles.LAB_CORE_V1:
        path = (root / rel).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {rel}\n", encoding="utf-8")


def _write_compiled_work_order(root: Path, *, artifact_rel: str, forbidden_ref: str = "") -> str:
    path = (root / artifact_rel).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "compiled_work_order_envelope_v1",
        "work_order_id": "wo_compiled_manifest",
        "source_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260329-151108Z-28C3"},
        "handoff_kind": "builder_to_verifier",
        "scope_fence": {
            "objective": "Use the compiled scope objective instead of reconstructing it from prose.",
            "in_scope": ["infra/controlplane/compiled_prompt_renderer.py", "tests/control_plane/test_compiled_execution_manifest_and_prompt.py"],
            "out_of_scope": ["sibling PIS work"],
        },
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs": [
            "infra/controlplane/compiled_prompt_renderer.py",
            "tests/control_plane/test_compiled_execution_manifest_and_prompt.py",
        ],
        "forbidden_context_refs": [forbidden_ref] if forbidden_ref else [],
        "expected_outputs": [
            {
                "output_id": "builder_report",
                "kind": "report",
                "summary": "Focused builder/verifier handoff report.",
                "required": True,
            }
        ],
        "validation_expectations": {
            "required_checks": ["targeted_prompt_render_regression"],
            "failure_mode": "fail_closed",
            "prose_coexistence": "fallback_prose_allowed",
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact_rel


def _sample_spec() -> dict:
    return sched.validate_job_spec(
        {
            "job_id": "compiled_manifest_001",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-28C3"},
            "job_kind": "build_verify_loop",
            "loop": {"max_iterations": 2},
            "prompt": "Implement the bound item using bounded repo changes.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "unified_diff", "format": "markdown"},
            "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
            "runtime": {"executor_agent_tech": "codex"},
            "display_title": "Compiled manifest test",
            "required_context_bundle": "lab_core_v1",
            "context_refs": ["docs/contracts/contract_compiled_execution_layer_operational_boundary.md"],
            "additional_context_refs": [
                "infra/controlplane/compiled_execution_ir.py",
                "tests/control_plane/test_compiled_execution_ir.py",
            ],
            "forbidden_context_refs": [],
        },
        now_utc="2026-03-29T15:45:00Z",
    )


def _seed_bound_task(repo_root: Path, *, task_id: str, ledger_uid: str, job_id: str) -> None:
    host.bootstrap_host_baseline(repo_root)
    current = register.load_or_init_task_register(repo_root)
    upserted = register.upsert_task(
        repo_root,
        expected_generation=int(current.get("write_generation") or 0),
        writer="compiled.manifest.test",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": "compiled continuation bootstrap",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "resume scheduled work",
        },
    )
    register.record_execution_surface_ownership(
        repo_root,
        expected_generation=int((upserted.get("task_register") or {}).get("write_generation") or 0),
        writer="compiled.manifest.test",
        task_id=task_id,
        selected_surface="workflow",
        execution_id=job_id,
        execution_ref=f"workflow:{job_id}",
        binding_ref=f"binding:{ledger_uid}",
        job_uri=f"runtime/state/lab_assistant/detached_jobs/jobs/{job_id}.json",
        trace_id=f"trace:{job_id}",
        checkpoint={
            "checkpoint_id": f"cp:{job_id}",
            "checkpoint_status": "recorded",
            "checkpoint_note": "safe resume point",
            "checkpoint_utc": "2026-04-07T09:03:00Z",
            "checkpoint_source": "test",
            "checkpoint_revision": 1,
            "progress_summary": "halfway there",
            "evidence_refs": [f"runtime/state/lab_assistant/checkpoints/{job_id}.json"],
        },
        resume_decision={
            "decision": "resume",
            "decision_reason": "job_state:interrupted",
            "decision_summary": "resume from durable checkpoint",
            "decision_source": "test",
            "decision_utc": "2026-04-07T09:03:00Z",
            "checkpoint_id": f"cp:{job_id}",
            "evidence_refs": [f"runtime/state/lab_assistant/checkpoints/{job_id}.json"],
        },
        reference_status="execution_ref_active",
    )


def test_resolve_context_manifest_is_deterministic_and_explicit() -> None:
    repo_root = Path(".").resolve()
    spec = _sample_spec()
    first = sched.compiled_context_manifest_from_job_spec(spec, repo_root=repo_root, max_refs=8)
    second = sched.compiled_context_manifest_from_job_spec(spec, repo_root=repo_root, max_refs=8)
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["manifest"] == second["manifest"]
    paths = [row["path"] for row in first["manifest"]["files"]]
    assert "tests/control_plane/test_compiled_execution_ir.py" in paths
    assert first["manifest"]["omitted_refs"] == []


def test_resolve_context_manifest_keeps_bundle_refs_ahead_of_shape_delta_refs() -> None:
    repo_root = Path(".").resolve()
    spec = _sample_spec()
    result = sched.compiled_context_manifest_from_job_spec(spec, repo_root=repo_root, max_refs=8)

    assert result["ok"] is True
    paths = [row["path"] for row in result["manifest"]["files"]]
    assert paths[:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert paths[-3:] == [
        "docs/contracts/contract_compiled_execution_layer_operational_boundary.md",
        "infra/controlplane/compiled_execution_ir.py",
        "tests/control_plane/test_compiled_execution_ir.py",
    ]


def test_resolve_context_manifest_blocks_invalid_ir() -> None:
    result = cem.resolve_context_manifest(
        {
            "ok": False,
            "failure_class": "ir_validation_failed",
            "errors": [{"code": "missing_field", "field": "goal", "message": "goal is required"}],
        }
    )
    assert result["ok"] is False
    assert result["failure_class"] == "context_manifest_resolution_blocked"


def test_resolve_context_manifest_strict_missing_refs_fails_closed(tmp_path: Path) -> None:
    spec = _sample_spec()
    result = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = cem.resolve_context_manifest(result, repo_root=tmp_path, strict_required_refs=True)
    assert manifest["ok"] is False
    assert manifest["failure_class"] == "required_context_refs_missing"


def test_bundle_refs_resolves_patch_instruction_sheet_uid_against_canonical_root(tmp_path: Path, monkeypatch) -> None:
    _write_bundle_files(tmp_path)
    items_root = ledger_storage.ledger_patch_instruction_sheets_root(tmp_path)
    item_dir = ledger_storage.ensure_item_dir(
        items_root,
        uid="PIS-20260312-150610Z-D821",
        slug="persistent-lab-assistant-v1-operational-contract",
        project_id="causyl",
        status="active",
        created_utc="2026-03-12T15:06:10Z",
        storage_layout="status_sharded",
    )
    (item_dir / "meta.json").write_text(
        '{"uid":"PIS-20260312-150610Z-D821","slug":"persistent-lab-assistant-v1-operational-contract","title":"d821","status":"active","created_utc":"2026-03-12T15:06:10Z","updated_utc":"2026-03-12T15:06:10Z"}',
        encoding="utf-8",
    )
    (item_dir / "pis.md").write_text("# D821\n", encoding="utf-8")
    monkeypatch.setattr(bundles, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bundles, "ITEMS_ROOT", items_root)

    refs = bundles.bundle_refs("lab_arch_gov_v1")

    assert "governance/ledger/patch_instruction_sheets/causyl/active/2026/03/PIS-20260312-150610Z-D821__persistent-lab-assistant-v1-operational-contract/pis.md" in refs


def test_compiled_prompt_artifact_from_job_spec_is_byte_stable() -> None:
    repo_root = Path(".").resolve()
    spec = _sample_spec()
    first = sched.compiled_prompt_artifact_from_job_spec(spec, role="builder", repo_root=repo_root, max_refs=8)
    second = sched.compiled_prompt_artifact_from_job_spec(spec, role="builder", repo_root=repo_root, max_refs=8)
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["artifact"]["prompt_text"] == second["artifact"]["prompt_text"]
    assert first["artifact"]["prompt_sha256"] == second["artifact"]["prompt_sha256"]
    assert "Allowed Files:" in first["artifact"]["prompt_text"]
    assert "Job Shape:" in first["artifact"]["prompt_text"]
    assert "Resolved Context Manifest:" in first["artifact"]["prompt_text"]


def test_render_compiled_prompt_includes_verifier_contract_section() -> None:
    spec = _sample_spec()
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="verifier",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )
    assert "Verifier Contract:" in artifact["prompt_text"]
    assert "required_blocks: Findings, Boundary Review, Proof Review, Validation Review" in artifact["prompt_text"]
    assert "contract_revision:" in artifact["prompt_text"]
    assert artifact["contract_revision"]
    assert artifact["contract"]["required_blocks"] == [
        "Findings",
        "Boundary Review",
        "Proof Review",
        "Validation Review",
    ]


def test_render_compiled_prompt_includes_builder_contract_section_and_revision_metadata() -> None:
    spec = _sample_spec()
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="builder",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )
    assert "Builder Contract:" in artifact["prompt_text"]
    assert "required_sections: Changed files, Validation Commands Run" in artifact["prompt_text"]
    assert artifact["contract_version"] == "builder_turn_contract_v1"
    assert artifact["result_packet_version"] == "builder_result_packet_v1"
    assert artifact["contract_doc_rel"] == "docs/contracts/contract_builder_turn_output.md"
    assert artifact["contract_revision"]
    assert artifact["contract"]["required_sections"] == ["Changed files", "Validation Commands Run"]


def test_render_compiled_prompt_projects_metadata_driven_builder_required_sections() -> None:
    spec = _sample_spec()
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    ir["normalized_ir"]["metadata"]["builder_required_sections"] = ["Residual Risks / Gaps", "Next Owner"]
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="builder",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )

    assert (
        "required_sections: Changed files, Validation Commands Run, Residual Risks / Gaps, Next Owner"
        in artifact["prompt_text"]
    )
    assert artifact["contract"]["required_sections"] == [
        "Changed files",
        "Validation Commands Run",
        "Residual Risks / Gaps",
        "Next Owner",
    ]


def test_render_compiled_prompt_prefers_builder_contract_identity_from_ir_metadata() -> None:
    spec = _sample_spec()
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    ir["normalized_ir"]["metadata"]["builder_contract_version"] = "builder_turn_contract_v1"
    ir["normalized_ir"]["metadata"]["builder_result_packet_version"] = "builder_result_packet_v1"
    ir["normalized_ir"]["metadata"]["builder_contract_doc_rel"] = "docs/contracts/contract_builder_turn_output.md"
    ir["normalized_ir"]["metadata"]["builder_contract_revision"] = "builder-rev-sentinel"
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="builder",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )
    assert artifact["contract_revision"] == "builder-rev-sentinel"
    assert "contract_revision: builder-rev-sentinel" in artifact["prompt_text"]


def test_render_compiled_prompt_prefers_verifier_contract_identity_from_ir_metadata() -> None:
    spec = _sample_spec()
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    ir["normalized_ir"]["metadata"]["verifier_contract_version"] = "verifier_review_contract_v1"
    ir["normalized_ir"]["metadata"]["verifier_result_packet_version"] = "verifier_review_packet_v1"
    ir["normalized_ir"]["metadata"]["verifier_contract_doc_rel"] = "docs/contracts/contract_verifier_review_output.md"
    ir["normalized_ir"]["metadata"]["verifier_contract_revision"] = "verifier-rev-sentinel"
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="verifier",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )
    assert artifact["contract_revision"] == "verifier-rev-sentinel"
    assert "contract_revision: verifier-rev-sentinel" in artifact["prompt_text"]


def test_render_compiled_prompt_canonicalizes_alias_roles() -> None:
    spec = _sample_spec()
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="orchestrator",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )
    assert artifact["role"] == "orchestrator"
    assert artifact["canonical_role"] == "generalist"
    assert "Compiled Role: generalist" in artifact["prompt_text"]


def test_compiled_manifest_projects_continuation_bootstrap_when_packet_is_ready(tmp_path: Path) -> None:
    ledger_uid = "PIS-20260407-063104Z-D4F4"
    _seed_bound_task(tmp_path, task_id="task_manifest_ready", ledger_uid=ledger_uid, job_id="job_manifest_ready")
    packet_store.append_packet_revision_once(
        {
            "schema_version": "continuation_packet_v1",
            "packet_id": "detached_job:job_manifest_ready",
            "packet_revision": 1,
            "created_utc": "2026-04-07T09:15:00Z",
            "source_run": {
                "job_id": "job_manifest_ready",
                "run_id": "req_manifest_ready",
                "attempt_id": "turn_manifest_ready",
                "receipt_refs": ["runtime/state/lab_assistant/detached_jobs/jobs/job_manifest_ready.json"],
            },
            "handoff_brief": "Use the latest durable packet first.",
            "result_summary": {"outcome": "interrupted", "summary": "Resume from the latest durable checkpoint."},
            "evidence_created": [
                {
                    "evidence_id": "ev_manifest_ready",
                    "kind": "unit_test",
                    "summary": "continuation bootstrap manifest fixture",
                    "refs": ["tests/control_plane/test_compiled_execution_manifest_and_prompt.py"],
                }
            ],
            "artifact_index": [
                {
                    "artifact_id": "art_manifest_ready",
                    "kind": "json",
                    "uri": "runtime/state/lab_assistant/detached_jobs/jobs/job_manifest_ready.json",
                    "summary": "job record",
                }
            ],
            "open_questions": [{"question_id": "q_manifest_ready", "question": "Resume now?"}],
            "failed_paths": [{"path_id": "fp_manifest_ready", "summary": "Success is not yet proven."}],
            "recommended_next_steps": [{"step_id": "step_manifest_ready", "priority": "now", "step": "Resume from the checkpoint."}],
            "confidence": {"level": "medium", "rationale": "checkpoint present"},
            "applicability": {"scope": "immediate_successor", "limitations": ["Lifecycle truth remains authoritative."]},
        },
        repo_root=tmp_path,
    )
    spec = sched.validate_job_spec(
        {
            "job_id": "compiled_manifest_ready",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": ledger_uid},
            "job_kind": "build_verify_loop",
            "loop": {"max_iterations": 1},
            "prompt": "Implement the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T09:30:00Z", "timezone": "UTC"},
            "expected_output": {"kind": "report", "format": "markdown"},
            "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
            "runtime": {"executor_agent_tech": "codex"},
            "display_title": "Continuation bootstrap manifest",
            "required_context_bundle": "lab_core_v1",
            "context_refs": [],
            "additional_context_refs": [],
            "forbidden_context_refs": [],
        },
        now_utc="2026-04-07T09:20:00Z",
        repo_root=tmp_path,
    )

    result = sched.compiled_context_manifest_from_job_spec(spec, repo_root=tmp_path)

    assert result["ok"] is True
    bootstrap = result["manifest"]["continuation_bootstrap"]
    assert bootstrap["status"] == "ready"
    assert bootstrap["selected_source"] == "continuation_packet"


def test_render_compiled_prompt_includes_continuation_bootstrap_section() -> None:
    normalized_ir = {
        "ir_version": "compiled_execution_ir_v1",
        "goal": "Resume safely.",
        "mode": "build",
        "claim_scope": "bounded_repo_change",
        "required_output_kind": "report",
        "constraints": [],
        "allowed_files": [],
        "validation_plan": [],
    }
    manifest = {
        "ir_fingerprint": "ir",
        "fingerprint": "mf",
        "files": [],
        "omitted_refs": [],
        "continuation_bootstrap": {
            "status": "degraded",
            "selected_source": "direct_current_context",
            "reason": "no_valid_continuation_packet_for_resumed_or_successor_work",
            "visible_gaps": ["no_valid_continuation_packet_for_resumed_or_successor_work"],
        },
    }

    artifact = cpr.render_compiled_prompt(role="builder", normalized_ir=normalized_ir, manifest=manifest)

    assert "Continuation Bootstrap:" in artifact["prompt_text"]
    assert "status: degraded" in artifact["prompt_text"]
    assert "selected_source: direct_current_context" in artifact["prompt_text"]


def test_render_compiled_prompt_includes_execution_family_contract_section() -> None:
    spec = _sample_spec()
    spec["prompt_meta"] = {
        "execution_family_contract": {
            "contract_kind": "intent_derived_execution_family_v1",
            "source_intent_uid": "IS-20260403-212913Z-5817",
            "parent_uid": "PIS-20260406-053253Z-2050",
            "child_uid": "PIS-20260406-060007Z-ACD1",
            "child_kind": "intent_validation_suite",
            "required_final_child_kind": "intent_family_closeout",
            "required_final_child_uid": "PIS-20260406-060009Z-CLO5",
        }
    }
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=Path(".").resolve())
    artifact = cpr.render_compiled_prompt(
        role="builder",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )
    assert "Execution Family Contract:" in artifact["prompt_text"]
    assert "Child Kind: intent_validation_suite" in artifact["prompt_text"]
    assert "Required Final Child Kind: intent_family_closeout" in artifact["prompt_text"]


def test_compiled_work_order_overrides_context_manifest_and_prompt_sections(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    compiled_artifact_rel = _write_compiled_work_order(
        tmp_path,
        artifact_rel="runtime/state/controlplane/tests/compiled_work_order_envelope.json",
        forbidden_ref="docs/contracts/contract_compiled_execution_layer_operational_boundary.md",
    )
    spec = _sample_spec()
    spec["prompt_meta"] = {
        "compiled_work_order": {
            "status": "compiled_emitted",
            "compiled_artifact_rel": compiled_artifact_rel,
        }
    }

    ir = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=tmp_path)
    prompt = sched.compiled_prompt_artifact_from_job_spec(spec, role="builder", repo_root=tmp_path)

    assert ir["ok"] is True
    assert ir["normalized_ir"]["goal"] == "Use the compiled scope objective instead of reconstructing it from prose."
    assert "targeted_prompt_render_regression" in ir["normalized_ir"]["validation_plan"]
    assert ir["normalized_ir"]["metadata"]["compiled_work_order_mode"] == "authoritative"
    assert manifest["ok"] is True
    manifest_paths = [row["path"] for row in manifest["manifest"]["files"]]
    assert "infra/controlplane/compiled_prompt_renderer.py" in manifest_paths
    assert "tests/control_plane/test_compiled_execution_manifest_and_prompt.py" in manifest_paths
    assert "docs/contracts/contract_compiled_execution_layer_operational_boundary.md" not in manifest_paths
    assert prompt["ok"] is True
    prompt_text = prompt["artifact"]["prompt_text"]
    assert "Compiled Work Order:" in prompt_text
    assert "mode: authoritative" in prompt_text
    assert "scope_objective: Use the compiled scope objective instead of reconstructing it from prose." in prompt_text
    assert "validation_checks: targeted_prompt_render_regression" in prompt_text


def test_render_compiled_prompt_validator_fails_closed_for_missing_contract_revision() -> None:
    result = cpr.validate_compiled_prompt_artifact(
        {
            "prompt_version": "compiled_prompt_v1",
            "role": "builder",
            "canonical_role": "builder",
            "contract_version": "builder_turn_contract_v1",
            "result_packet_version": "builder_result_packet_v1",
            "contract_doc_rel": "docs/contracts/contract_builder_turn_output.md",
            "contract_revision": "",
            "contract": {"required_sections": ["Changed files", "Validation Commands Run"]},
            "prompt_text": (
                "Compiled Role: builder\n\n"
                "Builder Contract:\n"
                "- contract_version: builder_turn_contract_v1\n"
                "- result_packet_version: builder_result_packet_v1\n"
                "- contract_doc: docs/contracts/contract_builder_turn_output.md\n"
                "- contract_revision: \n"
                "- required_sections: Changed files, Validation Commands Run\n"
            ),
        }
    )
    assert result["ok"] is False
    assert result["failure_class"] == "compiled_prompt_contract_invalid"
    assert "missing_contract_revision" in result["errors"]
    assert "missing_contract_revision_line" in result["errors"]


def test_render_compiled_prompt_validator_fails_closed_for_incomplete_verifier_contract_block() -> None:
    result = cpr.validate_compiled_prompt_artifact(
        {
            "prompt_version": "compiled_prompt_v1",
            "role": "verifier",
            "canonical_role": "verifier",
            "contract_version": "verifier_review_contract_v1",
            "result_packet_version": "verifier_review_packet_v1",
            "contract_doc_rel": "docs/contracts/contract_verifier_review_output.md",
            "contract_revision": "abc123",
            "contract": {"required_blocks": ["Findings", "Boundary Review", "Proof Review", "Validation Review"]},
            "prompt_text": (
                "Compiled Role: verifier\n\n"
                "Verifier Contract:\n"
                "- contract_version: verifier_review_contract_v1\n"
                "- result_packet_version: verifier_review_packet_v1\n"
                "- contract_doc: docs/contracts/contract_verifier_review_output.md\n"
                "- contract_revision: abc123\n"
            ),
        }
    )
    assert result["ok"] is False
    assert result["failure_class"] == "compiled_prompt_contract_invalid"
    assert "missing_required_blocks_line" in result["errors"]


def test_compiled_work_order_fallback_mode_is_explicit_when_artifact_is_invalid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    artifact_rel = "runtime/state/controlplane/tests/compiled_work_order_invalid.json"
    invalid_path = (tmp_path / artifact_rel).resolve()
    invalid_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_path.write_text("{\"schema_version\": \"compiled_work_order_envelope_v1\"}\n", encoding="utf-8")
    spec = _sample_spec()
    spec["prompt_meta"] = {
        "compiled_work_order": {
            "status": "compiled_emitted",
            "compiled_artifact_rel": artifact_rel,
        }
    }

    ir = sched.compiled_execution_ir_from_job_spec(spec)
    prompt = sched.compiled_prompt_artifact_from_job_spec(spec, role="builder", repo_root=tmp_path)

    assert ir["ok"] is True
    assert ir["normalized_ir"]["metadata"]["compiled_work_order_mode"] == "compatibility_fallback"
    assert ir["normalized_ir"]["metadata"]["compiled_work_order_fallback_reason"] == "compiled_work_order_validation_failed"
    assert "targeted_prompt_render_regression" not in ir["normalized_ir"]["validation_plan"]
    assert prompt["ok"] is True
    prompt_text = prompt["artifact"]["prompt_text"]
    assert "Compiled Work Order:" in prompt_text
    assert "mode: compatibility_fallback" in prompt_text
    assert "fallback_reason: compiled_work_order_validation_failed" in prompt_text


def test_render_compiled_prompt_includes_execution_family_closeout_typed_fields() -> None:
    normalized_ir = {
        "ir_version": "compiled_execution_ir_v1",
        "task_id": "close_beb9_20260406_execution_family",
        "title": "beb9 execution family closeout",
        "goal": "Audit, integrate, and close the execution family.",
        "constraints": ["binding:ledger_uid:PIS-20260406-182612Z-BEB9"],
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs": [],
        "forbidden_context_refs": [],
        "required_output_kind": "report",
        "allowed_files": [
            "infra/controlplane/job_budget_cost_ledger_store.py",
            "tests/control_plane/test_job_budget_cost_ledger_store.py",
        ],
        "validation_plan": [
            "execution_family_audit_required",
            "execution_family_integration_repair_allowed",
            "execution_family_closeout_required",
            "execution_family_trust_hardening_required",
        ],
        "claim_scope": "bounded_repo_change",
        "mode": "build",
        "execution_family": {
            "contract_kind": "execution_family_closeout_v1",
            "phase": "family_closeout",
            "source_intent_uid": "IS-20260403-212913Z-4529",
            "parent_uid": "PIS-20260406-182612Z-BEB9",
            "required_final_child_uid": "PIS-20260406-183447Z-1246",
            "child_uids": ["PIS-20260406-183447Z-1246"],
            "child_job_ids": ["job_child_001"],
            "allowed_repo_paths": [
                "infra/controlplane/job_budget_cost_ledger_store.py",
                "tests/control_plane/test_job_budget_cost_ledger_store.py",
            ],
            "allowed_ledger_paths": [
                "governance/ledger/patch_instruction_sheets/uid_index.json",
                "governance/ledger/patch_instruction_sheets/**/PIS-20260406-182612Z-BEB9__*/meta.json",
            ],
            "closeout_rules": [
                "required_final_child_must_be_verified",
                "family_closeout_must_emit_closure_artifact",
            ],
            "integration_audit_mode": "family_receipt_changed_files_v1",
            "trust_hardening_mode": "broad_current_state_rerun_v1",
            "status_promotion_policy": "verified_only_after_family_audit_and_required_final_child",
            "receipt_selection_policy": "latest_successful_child_receipts_only",
        },
        "metadata": {"job_shape": "verification_loop_job", "job_shape_source": "explicit", "job_shape_compatibility_mode": False},
    }
    manifest = {"ir_fingerprint": "ir", "fingerprint": "mf", "files": [], "omitted_refs": []}
    artifact = cpr.render_compiled_prompt(role="builder", normalized_ir=normalized_ir, manifest=manifest)
    assert "Execution Family Contract:" in artifact["prompt_text"]
    assert "Phase: family_closeout" in artifact["prompt_text"]
    assert "Integration Audit Mode: family_receipt_changed_files_v1" in artifact["prompt_text"]
    assert "Trust Hardening Mode: broad_current_state_rerun_v1" in artifact["prompt_text"]
    assert "Status Promotion Policy: verified_only_after_family_audit_and_required_final_child" in artifact["prompt_text"]
    assert "Receipt Selection Policy: latest_successful_child_receipts_only" in artifact["prompt_text"]
    assert "Allowed Family Repo Paths: infra/controlplane/job_budget_cost_ledger_store.py, tests/control_plane/test_job_budget_cost_ledger_store.py" in artifact["prompt_text"]
    assert "Allowed Family Ledger Paths: governance/ledger/patch_instruction_sheets/uid_index.json, governance/ledger/patch_instruction_sheets/**/PIS-20260406-182612Z-BEB9__*/meta.json" in artifact["prompt_text"]
    assert "Closeout Rules: required_final_child_must_be_verified, family_closeout_must_emit_closure_artifact" in artifact["prompt_text"]


def test_render_compiled_prompt_preserves_legacy_job_shape_compatibility_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_bundle_files(tmp_path)
    inbox = sched.jobs_dir(tmp_path)
    (inbox / "compiled_manifest_legacy_001.json").write_text(
        """{
  "schema_version": "scheduled_job_v1",
  "job_id": "compiled_manifest_legacy_001",
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
  "runtime": {"executor_agent_tech": "codex"},
  "display_title": "legacy compiled prompt",
  "prompt_ref": "",
  "prompt_sha256": "",
  "prompt_preview": "legacy single-turn job",
  "prompt": "Explain the bound item."
}
""",
        encoding="utf-8",
    )

    spec = sched.load_job("compiled_manifest_legacy_001", repo_root=tmp_path)
    ir = sched.compiled_execution_ir_from_job_spec(spec)
    manifest = sched.compiled_context_manifest_from_job_spec(spec, repo_root=tmp_path)
    artifact = cpr.render_compiled_prompt(
        role="generalist",
        normalized_ir=ir["normalized_ir"],
        manifest=manifest["manifest"],
    )

    assert "Job Shape:" in artifact["prompt_text"]
    assert "- name: verification_loop_job" in artifact["prompt_text"]
    assert "- source: compatibility_inferred_from_job_kind" in artifact["prompt_text"]
    assert "- compatibility_mode: true" in artifact["prompt_text"]


def test_compiled_execution_shadow_bundle_exposes_job_shape_fields_explicitly() -> None:
    spec = _sample_spec()
    compiled = sched._compiled_execution_shadow_bundle_for_job(spec, roles=["builder"], repo_root=Path(".").resolve())

    assert compiled["job_shape"] == "verification_loop_job"
    assert compiled["job_shape_source"] == "explicit"
    assert compiled["job_shape_compatibility_mode"] is False
