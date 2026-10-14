from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_batch_from_parent as batch
from infra.controlplane import scheduled_jobs as sched


def _write_item(root: Path, uid: str, slug: str, *, title: str, status: str = "draft", parent_uid: str = "", rank: int = 0, depends_on: list[str] | None = None) -> None:
    item_dir = root / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__{slug}"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "meta.json").write_text(json.dumps({"uid": uid, "slug": slug, "title": title, "status": status}), encoding="utf-8")
    (item_dir / "pis.md").write_text(f"# Patch Instruction Sheet (PIS)\n\n**UID:** {uid}\n", encoding="utf-8")
    events = [json.dumps({"type": "created", "payload": {"title": title}})]
    if parent_uid:
        events.append(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": depends_on or [],
                    },
                }
            )
        )
    (item_dir / "events.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")


def test_planned_child_specs_preserve_dependency_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    _write_item(tmp_path, parent_uid, "parent", title="Parent")
    _write_item(tmp_path, "PIS-20260326-203500Z-A7D1", "child-a", title="Child A", parent_uid=parent_uid, rank=1, depends_on=[])
    _write_item(tmp_path, "PIS-20260326-203500Z-B8E2", "child-b", title="Child B", parent_uid=parent_uid, rank=2, depends_on=["PIS-20260326-203500Z-A7D1"])

    specs = batch._planned_child_specs(
        parent_uid,
        run_at_utc="2026-03-27T00:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=5,
        repo_root=tmp_path,
    )
    assert [row["binding"]["id"] for row in specs[:2]] == [
        "PIS-20260326-203500Z-A7D1",
        "PIS-20260326-203500Z-B8E2",
    ]
    assert specs[0]["job_kind"] == "build_verify_loop"
    assert specs[0]["policy"]["post_success_commit_tag"] is True
    assert specs[0]["loop"]["builder_runtime"]["model"] == "gpt-5.4-mini"
    assert specs[0]["loop"]["verifier_runtime"]["model"] == "gpt-5.4"
    assert specs[1]["depends_on"] == [specs[0]["job_id"]]
    assert specs[2]["binding"] == {"type": "ledger_uid", "id": parent_uid}
    assert specs[2]["depends_on"] == [specs[0]["job_id"], specs[1]["job_id"]]
    assert specs[2]["prompt_meta"]["execution_family_closeout"]["parent_uid"] == parent_uid


def test_main_updates_existing_child_jobs_in_place(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    child_uid = "PIS-20260326-203500Z-A7D1"
    _write_item(tmp_path, parent_uid, "parent", title="Parent")
    _write_item(tmp_path, child_uid, "child-a", title="Child A", parent_uid=parent_uid, rank=1, depends_on=[])
    existing_job_id = sched._job_id_from_ledger_uid(child_uid, "Child A")
    sched.save_job(
        {
            "job_id": existing_job_id,
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": child_uid},
            "prompt": "old prompt",
            "schedule": {"mode": "once", "run_at": "2026-03-27T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
        },
        repo_root=tmp_path,
    )

    rc = batch.main(
        [
            parent_uid,
            "--start-in",
            "10m",
            "--builder-model",
            "gpt-5.4-mini",
            "--verifier-model",
            "gpt-5.4",
            "--backend",
            "legacy",
        ]
    )
    assert rc == 0
    saved = sched.load_job(existing_job_id, repo_root=tmp_path)
    assert saved["job_kind"] == "build_verify_loop"
    assert saved["policy"]["post_success_commit_tag"] is True
    assert saved["loop"]["max_iterations"] == 5
    assert saved["loop"]["builder_runtime"]["model"] == "gpt-5.4-mini"
    assert saved["loop"]["verifier_runtime"]["model"] == "gpt-5.4"


def test_planned_child_specs_omit_closeout_when_family_already_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    _write_item(tmp_path, parent_uid, "parent", title="Parent", status="verified")
    _write_item(tmp_path, "PIS-20260326-203500Z-A7D1", "child-a", title="Child A", status="verified", parent_uid=parent_uid, rank=1, depends_on=[])
    _write_item(tmp_path, "PIS-20260326-203500Z-B8E2", "child-b", title="Child B", status="verified", parent_uid=parent_uid, rank=2, depends_on=["PIS-20260326-203500Z-A7D1"])
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=tmp_path)
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "child_uids": ["PIS-20260326-203500Z-A7D1", "PIS-20260326-203500Z-B8E2"],
        "relationship": "execution_parent",
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta), encoding="utf-8")

    specs = batch._planned_child_specs(
        parent_uid,
        run_at_utc="2026-03-27T00:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=5,
        repo_root=tmp_path,
    )

    assert [str(spec.get("binding", {}).get("id") or "") for spec in specs] == [
        "PIS-20260326-203500Z-A7D1",
        "PIS-20260326-203500Z-B8E2",
    ]
    assert all(str(spec.get("job_id") or "") != sched._family_closeout_job_id(parent_uid) for spec in specs)


def test_planned_child_specs_use_child_closeout_and_emit_execution_family_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    _write_item(tmp_path, parent_uid, "parent", title="Parent")
    _write_item(tmp_path, "PIS-20260326-203500Z-A7D1", "child-a", title="Child A", parent_uid=parent_uid, rank=1, depends_on=[])
    _write_item(tmp_path, "PIS-20260326-203500Z-B8E2", "validation", title="Validation Child", parent_uid=parent_uid, rank=2, depends_on=["PIS-20260326-203500Z-A7D1"])
    _write_item(tmp_path, "PIS-20260326-203500Z-C9F3", "closeout", title="Closeout Child", parent_uid=parent_uid, rank=3, depends_on=["PIS-20260326-203500Z-A7D1", "PIS-20260326-203500Z-B8E2"])
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=tmp_path)
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "intent_sheet_uid": "IS-20260326-000001Z-TEST",
        "child_uids": [
            "PIS-20260326-203500Z-A7D1",
            "PIS-20260326-203500Z-B8E2",
            "PIS-20260326-203500Z-C9F3",
        ],
        "required_final_child_uid": "PIS-20260326-203500Z-C9F3",
        "required_final_child_kind": "intent_family_closeout",
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta), encoding="utf-8")
    for uid, relationship in (
        ("PIS-20260326-203500Z-B8E2", "intent_validation_suite"),
        ("PIS-20260326-203500Z-C9F3", "intent_family_closeout"),
    ):
        child_dir = sched._resolve_ledger_item_dir_for_uid(uid, repo_root=tmp_path)
        child_meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        child_meta["integration_meta"] = {"relationship": relationship}
        (child_dir / "meta.json").write_text(json.dumps(child_meta), encoding="utf-8")
        events_path = child_dir / "events.jsonl"
        raw = events_path.read_text(encoding="utf-8").replace('"relationship": "execution_child"', f'"relationship": "{relationship}"')
        events_path.write_text(raw, encoding="utf-8")

    specs = batch._planned_child_specs(
        parent_uid,
        run_at_utc="2026-03-27T00:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=5,
        repo_root=tmp_path,
    )

    assert [str(spec.get("binding", {}).get("id") or "") for spec in specs] == [
        "PIS-20260326-203500Z-A7D1",
        "PIS-20260326-203500Z-B8E2",
        "PIS-20260326-203500Z-C9F3",
    ]
    assert all(str(spec.get("job_id") or "") != sched._family_closeout_job_id(parent_uid) for spec in specs)
    validation_spec = specs[1]
    closeout_spec = specs[2]
    assert validation_spec["job_shape"] == "verification_loop_job"
    assert validation_spec["job_shape_source"] == "explicit"
    assert validation_spec["job_shape_compatibility_mode"] is False
    assert validation_spec["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_validation_suite"
    assert validation_spec["prompt_meta"]["execution_family_contract"]["required_final_child_kind"] == "intent_family_closeout"
    assert closeout_spec["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_family_closeout"
    assert closeout_spec["prompt_meta"]["execution_family_contract"]["required_final_child_uid"] == "PIS-20260326-203500Z-C9F3"


def test_planned_child_specs_use_explicit_pass_chain_when_parent_execution_mode_requests_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    _write_item(tmp_path, parent_uid, "parent", title="Parent")
    _write_item(tmp_path, "PIS-20260326-203500Z-A7D1", "child-a", title="Child A", parent_uid=parent_uid, rank=1, depends_on=[])
    _write_item(tmp_path, "PIS-20260326-203500Z-B8E2", "validation", title="Validation Child", parent_uid=parent_uid, rank=2, depends_on=["PIS-20260326-203500Z-A7D1"])
    _write_item(tmp_path, "PIS-20260326-203500Z-C9F3", "closeout", title="Closeout Child", parent_uid=parent_uid, rank=3, depends_on=["PIS-20260326-203500Z-A7D1", "PIS-20260326-203500Z-B8E2"])
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=tmp_path)
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "intent_sheet_uid": "IS-20260326-000001Z-TEST",
        "execution_mode": "explicit_pass_chain_v1",
        "child_uids": [
            "PIS-20260326-203500Z-A7D1",
            "PIS-20260326-203500Z-B8E2",
            "PIS-20260326-203500Z-C9F3",
        ],
        "intent_validation_child_uid": "PIS-20260326-203500Z-B8E2",
        "required_final_child_uid": "PIS-20260326-203500Z-C9F3",
        "required_final_child_kind": "intent_family_closeout",
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta), encoding="utf-8")
    for uid, relationship in (
        ("PIS-20260326-203500Z-B8E2", "intent_validation_suite"),
        ("PIS-20260326-203500Z-C9F3", "intent_family_closeout"),
    ):
        child_dir = sched._resolve_ledger_item_dir_for_uid(uid, repo_root=tmp_path)
        child_meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        child_meta["integration_meta"] = {"relationship": relationship}
        (child_dir / "meta.json").write_text(json.dumps(child_meta), encoding="utf-8")
        events_path = child_dir / "events.jsonl"
        raw = events_path.read_text(encoding="utf-8").replace('"relationship": "execution_child"', f'"relationship": "{relationship}"')
        events_path.write_text(raw, encoding="utf-8")

    specs = batch._planned_child_specs(
        parent_uid,
        run_at_utc="2026-03-27T00:00:00Z",
        builder_model="gpt-5.4",
        verifier_model="gpt-5.4",
        max_iterations=3,
        repo_root=tmp_path,
    )

    implementation_specs = [spec for spec in specs if str(spec.get("binding", {}).get("id") or "") == "PIS-20260326-203500Z-A7D1"]
    assert [spec["job_id"].rsplit("_", 1)[-1] for spec in implementation_specs] == ["p1of3", "p2of3", "p3of3"]
    assert all(spec["loop"]["builder_only"] is True for spec in implementation_specs)
    assert implementation_specs[-1]["prompt_meta"]["explicit_pass_chain"]["projection_job_ids"] == [
        implementation_specs[0]["job_id"],
        implementation_specs[1]["job_id"],
        implementation_specs[2]["job_id"],
    ]


def test_planned_child_specs_leave_legacy_tail_shape_unpromoted_without_explicit_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    impl_uid = "PIS-20260326-203500Z-A7D1"
    proof_uid = "PIS-20260326-203500Z-B8E2"
    closeout_uid = "PIS-20260326-203500Z-C9F3"
    _write_item(tmp_path, parent_uid, "parent", title="Parent")
    _write_item(tmp_path, impl_uid, "child-a", title="Child A", parent_uid=parent_uid, rank=1, depends_on=[])
    _write_item(tmp_path, proof_uid, "proof", title="Prove Parent End-to-End", parent_uid=parent_uid, rank=2, depends_on=[impl_uid])
    _write_item(tmp_path, closeout_uid, "closeout", title="Close Parent Intent Boundary and Family Acceptance", parent_uid=parent_uid, rank=3, depends_on=[impl_uid, proof_uid])
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=tmp_path)
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "intent_sheet_uid": "IS-20260326-000001Z-TEST",
        "proof_child_uid": proof_uid,
        "required_final_child_uid": proof_uid,
        "child_uids": [impl_uid, proof_uid, closeout_uid],
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta), encoding="utf-8")

    specs = batch._planned_child_specs(
        parent_uid,
        run_at_utc="2026-03-27T00:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=5,
        repo_root=tmp_path,
    )

    assert [str(spec.get("binding", {}).get("id") or "") for spec in specs] == [impl_uid, proof_uid, closeout_uid]
    assert all(str(spec.get("job_id") or "") != sched._family_closeout_job_id(parent_uid) for spec in specs)
    assert specs[1]["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    assert specs[2]["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    repaired_parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    repaired_integration = repaired_parent_meta["integration_meta"]
    assert "intent_validation_child_uid" not in repaired_integration
    assert repaired_integration["required_final_child_uid"] == proof_uid
    assert "required_final_child_kind" not in repaired_integration


def test_default_stage_models_follow_runtime_policy(monkeypatch) -> None:
    values = {
        "scheduler.child_implementation.default_model": "deepseek-chat",
        "scheduler.build_verify.default_model": "gpt-5.4-mini",
    }
    monkeypatch.setattr(batch._runtime_policy, "get_runtime_policy_text", lambda key, default, env_names=(): values.get(key, default))

    assert batch._default_builder_model() == "deepseek-chat"
    assert batch._default_verifier_model() == "gpt-5.4-mini"


def test_planned_child_specs_compile_when_legacy_intermediate_kinds_coexist_with_canonical_tail(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(batch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    parent_uid = "PIS-20260326-203500Z-R7K2"
    impl_uid = "PIS-20260326-203500Z-A7D1"
    proof_uid = "PIS-20260326-203500Z-B8E2"
    closeout_impl_uid = "PIS-20260326-203500Z-C9F3"
    validation_uid = "PIS-20260326-203500Z-D0E1"
    family_closeout_uid = "PIS-20260326-203500Z-F2G3"

    _write_item(tmp_path, parent_uid, "parent", title="Parent")
    _write_item(tmp_path, impl_uid, "child-a", title="Child A", parent_uid=parent_uid, rank=1, depends_on=[])
    _write_item(tmp_path, proof_uid, "proof", title="Prove Parent End-to-End", parent_uid=parent_uid, rank=2, depends_on=[impl_uid])
    _write_item(
        tmp_path,
        closeout_impl_uid,
        "closeout",
        title="Close Parent Intent Boundary and Family Acceptance",
        parent_uid=parent_uid,
        rank=3,
        depends_on=[proof_uid],
    )
    _write_item(
        tmp_path,
        validation_uid,
        "intent-validation",
        title="Validate Parent End-to-End",
        parent_uid=parent_uid,
        rank=4,
        depends_on=[closeout_impl_uid],
    )
    _write_item(
        tmp_path,
        family_closeout_uid,
        "family-closeout",
        title="Family Closeout",
        parent_uid=parent_uid,
        rank=5,
        depends_on=[validation_uid],
    )
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=tmp_path)
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "intent_sheet_uid": "IS-20260326-000001Z-TEST",
        "intent_validation_child_uid": validation_uid,
        "required_final_child_uid": family_closeout_uid,
        "required_final_child_kind": "intent_family_closeout",
        "child_uids": [impl_uid, proof_uid, closeout_impl_uid, validation_uid, family_closeout_uid],
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta), encoding="utf-8")

    for uid, relationship in [
        (proof_uid, "proof_child"),
        (closeout_impl_uid, "closeout_child"),
        (validation_uid, "intent_validation_suite"),
        (family_closeout_uid, "intent_family_closeout"),
    ]:
        child_dir = sched._resolve_ledger_item_dir_for_uid(uid, repo_root=tmp_path)
        child_meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        child_meta["integration_meta"] = {"relationship": relationship}
        (child_dir / "meta.json").write_text(json.dumps(child_meta), encoding="utf-8")
        events_path = child_dir / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        events[1]["payload"]["relationship"] = relationship
        events_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    specs = batch._planned_child_specs(
        parent_uid,
        run_at_utc="2026-03-27T00:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=5,
        repo_root=tmp_path,
    )

    assert [str(spec.get("binding", {}).get("id") or "") for spec in specs] == [
        impl_uid,
        proof_uid,
        closeout_impl_uid,
        validation_uid,
        family_closeout_uid,
    ]
    assert specs[1]["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    assert specs[2]["prompt_meta"]["execution_family_contract"]["child_kind"] == "execution_child"
    assert specs[3]["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_validation_suite"
    assert specs[4]["prompt_meta"]["execution_family_contract"]["child_kind"] == "intent_family_closeout"
