from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import explicit_pass_chain_compiler as compiler


def _write_family(root: Path) -> str:
    parent_uid = "PIS-20260502-200000Z-PA01"
    intent_uid = "IS-20260502-200000Z-IN01"
    impl_uid = "PIS-20260502-200001Z-IM01"
    validation_uid = "PIS-20260502-200002Z-VA01"
    closeout_uid = "PIS-20260502-200003Z-CL01"
    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "execution_mode": "explicit_pass_chain_v1",
                    "child_uids": [impl_uid, validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, relationship, rank, deps in [
        (impl_uid, "impl", "Implementation", "execution_child", 1, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 2, [impl_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 3, [impl_uid, validation_uid]),
    ]:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps({"uid": uid, "title": title, "status": "draft", "integration_meta": {"relationship": relationship}}),
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
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompt_path = root / "docs/prompts/lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n", encoding="utf-8")
    return parent_uid


def _write_strict_family(root: Path) -> str:
    parent_uid = "PIS-20260502-210000Z-PA02"
    intent_uid = "IS-20260502-210000Z-IN02"
    impl_a_uid = "PIS-20260502-210001Z-AA01"
    impl_b_uid = "PIS-20260502-210002Z-BB02"
    impl_c_uid = "PIS-20260502-210003Z-CC03"
    validation_uid = "PIS-20260502-210004Z-VA04"
    closeout_uid = "PIS-20260502-210005Z-CL05"
    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "execution_mode": "explicit_pass_chain_v1",
                    "child_uids": [impl_a_uid, impl_b_uid, impl_c_uid, validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, relationship, rank, deps in [
        (impl_a_uid, "impl-a", "Implementation A", "execution_child", 1, []),
        (impl_b_uid, "impl-b", "Implementation B", "execution_child", 2, []),
        (impl_c_uid, "impl-c", "Implementation C", "execution_child", 3, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 4, [impl_a_uid, impl_b_uid, impl_c_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 5, [impl_a_uid, impl_b_uid, impl_c_uid, validation_uid]),
    ]:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/05" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps({"uid": uid, "title": title, "status": "draft", "integration_meta": {"relationship": relationship}}),
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
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompt_path = root / "docs/prompts/lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n", encoding="utf-8")
    return parent_uid


def test_compile_explicit_pass_chain_emits_build_improve_passes_and_explicit_tails(tmp_path: Path) -> None:
    parent_uid = _write_family(tmp_path)

    compiled = compiler.compile_explicit_pass_chain(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T20:10:00Z",
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        pass_count=3,
        repo_root=tmp_path,
    )

    assert compiled["schema_version"] == "explicit_pass_chain_compilation_v1"
    assert compiled["workflow_kind"] == "intent_family_v2"
    assert compiled["execution_mode"] == "explicit_pass_chain_v1"
    assert compiled["family_graph"]["pass_count"] == 3
    specs = compiled["job_specs"]
    assert len(specs) == 5
    pass_specs = specs[:3]
    assert [spec["job_id"].rsplit("_", 1)[-1] for spec in pass_specs] == ["p1of3", "p2of3", "p3of3"]
    assert all(spec["loop"]["builder_only"] is True for spec in pass_specs)
    assert pass_specs[1]["depends_on"] == [pass_specs[0]["job_id"]]
    assert pass_specs[2]["depends_on"] == [pass_specs[1]["job_id"]]
    assert pass_specs[2]["prompt_meta"]["explicit_pass_chain"]["projection_for_child_uid"] == "PIS-20260502-200001Z-IM01"
    assert pass_specs[2]["prompt_meta"]["explicit_pass_chain"]["projection_job_ids"] == [
        pass_specs[0]["job_id"],
        pass_specs[1]["job_id"],
        pass_specs[2]["job_id"],
    ]
    validation_spec = specs[3]
    closeout_spec = specs[4]
    assert validation_spec["prompt_meta"]["explicit_pass_chain"]["job_type"] == "intent_e2e_validate"
    assert closeout_spec["prompt_meta"]["explicit_pass_chain"]["job_type"] == "family_closeout"
    assert closeout_spec["policy"]["delivery_policy"] == "delivery_guaranteed"


def test_compile_explicit_pass_chain_serializes_execution_children_strictly(tmp_path: Path) -> None:
    parent_uid = _write_strict_family(tmp_path)

    compiled = compiler.compile_explicit_pass_chain(
        parent_uid=parent_uid,
        run_at_utc="2026-05-02T20:10:00Z",
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        pass_count=3,
        repo_root=tmp_path,
    )

    specs_by_id = {spec["job_id"]: spec for spec in compiled["job_specs"]}
    a_p3 = next(jid for jid in specs_by_id if jid.startswith("pis_aa01") and jid.endswith("p3of3"))
    b_p1 = next(jid for jid in specs_by_id if jid.startswith("pis_bb02") and jid.endswith("p1of3"))
    b_p3 = next(jid for jid in specs_by_id if jid.startswith("pis_bb02") and jid.endswith("p3of3"))
    c_p1 = next(jid for jid in specs_by_id if jid.startswith("pis_cc03") and jid.endswith("p1of3"))

    assert a_p3 in specs_by_id[b_p1]["depends_on"]
    assert b_p3 in specs_by_id[c_p1]["depends_on"]
