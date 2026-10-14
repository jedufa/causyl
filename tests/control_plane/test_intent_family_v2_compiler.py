from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import intent_family_v2_compiler as compiler


def _write_workflow_contract(root: Path, *, extra_fields: dict | None = None) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    if extra_fields:
        payload.update(extra_fields)
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_family(
    root: Path,
    *,
    parent_uid: str = "PIS-20260409-194437Z-310F",
    intent_uid: str = "IS-20260409-190403Z-BFB6",
    validation_kind: str = "intent_validation_suite",
    closeout_kind: str = "intent_family_closeout",
    cyclic: bool = False,
) -> str:
    parent_dir = (
        root
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "active"
        / "2026"
        / "04"
        / f"{parent_uid}__parent"
    )
    parent_dir.mkdir(parents=True, exist_ok=True)
    impl_uid = "PIS-20260409-195201Z-A111"
    validation_uid = "PIS-20260409-195201Z-B222"
    closeout_uid = "PIS-20260409-195201Z-C333"
    child_uids = [impl_uid, validation_uid, closeout_uid]
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent item",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": child_uids,
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": closeout_kind,
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    rows = [
        (impl_uid, "impl", "Execution child", "execution_child", 1, [closeout_uid] if cyclic else []),
        (validation_uid, "validation", "Intent validation", validation_kind, 2, [impl_uid]),
        (closeout_uid, "closeout", "Close intent family", closeout_kind, 3, [impl_uid, validation_uid]),
    ]
    for uid, slug, title, relationship, rank, depends_on in rows:
        child_dir = (
            root
            / "governance"
            / "ledger"
            / "patch_instruction_sheets"
            / "causyl"
            / "active"
            / "2026"
            / "04"
            / f"{uid}__{slug}"
        )
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": relationship},
                }
            ),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": depends_on,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompt_path = root / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    return parent_uid


def test_compile_intent_family_v2_emits_canonical_graph_and_job_specs(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)

    compiled = compiler.compile_intent_family_v2(
        parent_uid=parent_uid,
        run_at_utc="2026-04-10T10:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=4,
        repo_root=tmp_path,
    )

    assert compiled["schema_version"] == "intent_family_v2_compilation_v1"
    assert compiled["workflow_definition_version"] == "intent_family_v2_workflow_definition_contract_v1"
    assert compiled["family_graph"]["required_final_child_kind"] == "intent_family_closeout"
    assert compiled["family_graph"]["recommended_execution_order"] == [
        "PIS-20260409-195201Z-A111",
        "PIS-20260409-195201Z-B222",
        "PIS-20260409-195201Z-C333",
    ]
    assert {row["node_kind"] for row in compiled["family_graph"]["nodes"]} == {
        "source_intent",
        "family_parent",
        "execution_child",
        "intent_validation_suite",
        "intent_family_closeout",
    }
    child_specs = compiled["job_specs"]
    assert [spec["binding"]["id"] for spec in child_specs] == [
        "PIS-20260409-195201Z-A111",
        "PIS-20260409-195201Z-B222",
        "PIS-20260409-195201Z-C333",
    ]
    assert all(spec["job_kind"] == "build_verify_loop" for spec in child_specs)
    assert all(spec["job_shape"] == "verification_loop_job" for spec in child_specs)
    assert child_specs[1]["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_validation_suite"
    assert child_specs[2]["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_family_closeout"
    assert child_specs[2]["depends_on"] == [child_specs[0]["job_id"], child_specs[1]["job_id"]]
    assert compiled["diagnostics"] == []
    assert isinstance(compiled["compilation_fingerprint"], str) and compiled["compilation_fingerprint"]


def test_compile_intent_family_v2_rejects_dependency_cycles(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path, cyclic=True)

    with pytest.raises(compiler.IntentFamilyV2CompileError) as exc:
        compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc="2026-04-10T10:00:00Z",
            builder_model="gpt-5.4-mini",
            verifier_model="gpt-5.4",
            max_iterations=4,
            repo_root=tmp_path,
        )

    assert any(row["code"] == "dependency_cycle" for row in exc.value.diagnostics)


def test_compile_intent_family_v2_rejects_illegal_final_child_kind(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path, closeout_kind="execution_child")

    with pytest.raises(compiler.IntentFamilyV2CompileError) as exc:
        compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc="2026-04-10T10:00:00Z",
            builder_model="gpt-5.4-mini",
            verifier_model="gpt-5.4",
            max_iterations=4,
            repo_root=tmp_path,
        )

    codes = {row["code"] for row in exc.value.diagnostics}
    assert "illegal_final_child_kind" in codes
    assert "invalid_closeout_tail" in codes


def test_compile_intent_family_v2_rejects_forbidden_runtime_fields_in_workflow_definition(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path, extra_fields={"retry_policy": {"mode": "always"}})
    parent_uid = _write_family(tmp_path)

    with pytest.raises(compiler.IntentFamilyV2CompileError) as exc:
        compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc="2026-04-10T10:00:00Z",
            builder_model="gpt-5.4-mini",
            verifier_model="gpt-5.4",
            max_iterations=4,
            repo_root=tmp_path,
        )

    assert any(row["code"] == "forbidden_workflow_field" for row in exc.value.diagnostics)


def test_compile_intent_family_v2_allows_verified_external_foundation_dependencies(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path)
    child_path = (
        tmp_path
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "active"
        / "2026"
        / "04"
        / "PIS-20260409-195201Z-A111__impl"
        / "pis.md"
    )
    child_path.write_text(
        (
            "# Execution child\n\n"
            "## 5. Dependencies, boundary, and overlap mapping\n"
            "Depends on:\n"
            "- `PIS-20260406-053253Z-2050` for canonical job-shape vocabulary\n"
        ),
        encoding="utf-8",
    )
    foundation_dir = (
        tmp_path
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "verified"
        / "2026"
        / "04"
        / "PIS-20260406-053253Z-2050__foundation"
    )
    foundation_dir.mkdir(parents=True, exist_ok=True)
    (foundation_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": "PIS-20260406-053253Z-2050",
                "title": "Canonical job shape taxonomy",
                "status": "verified",
            }
        ),
        encoding="utf-8",
    )

    compiled = compiler.compile_intent_family_v2(
        parent_uid=parent_uid,
        run_at_utc="2026-04-10T10:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=4,
        repo_root=tmp_path,
    )

    assert compiled["family_graph"]["required_final_child_uid"] == "PIS-20260409-195201Z-C333"
