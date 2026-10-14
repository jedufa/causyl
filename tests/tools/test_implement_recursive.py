from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui.ledger.ledger_manager import ChangeLedger
from tools.bin import implement_recursive as mod


def test_run_for_intent_sheet_creates_parent_and_starts_decompose_job(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Recursive implementation target",
        intent_markdown="# Intent Sheet\n\nImprove software development and tooling for this workflow.\n",
        uid_override="IS-20260403-212913Z-5817",
        project_id="causyl",
        workstream_type="platform",
        scope_boundary="platform-local",
        decomposition_class="workflow",
        decomposition_target_range={"min": 2, "max": 4},
        decomposition_rationale="Medium workflow that should stay compact.",
        mandatory_tail={"validation": True, "closeout": True},
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod._scheduler_create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    def fake_default_spec(row, *, prompt_mode: str, queue_child_jobs: bool = True, child_delay_minutes: int = 5):
        assert prompt_mode == "decompose_parent"
        assert queue_child_jobs is True
        return {
            "job_id": "seed_job",
            "job_kind": "build_verify_loop",
            "schedule": {"mode": "once", "run_at": "", "timezone": "UTC"},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 5},
            "prompt": "decompose parent",
            "prompt_meta": {},
        }

    captured = {}

    def fake_save_job(spec):
        captured["spec"] = dict(spec)
        return dict(spec)

    def fake_run_now(job_id: str):
        captured["job_id"] = job_id
        return {"ok": True, "run_id": "run-1"}

    monkeypatch.setattr(mod._scheduler_create_job, "_default_spec", fake_default_spec)
    monkeypatch.setattr(mod, "_save_job", fake_save_job)
    monkeypatch.setattr(mod, "_run_now", fake_run_now)

    result = mod.run("IS-20260403-212913Z-5817")
    updated = ledger.read_sheet("IS-20260403-212913Z-5817")
    parent_uid = str(result["resolved_parent_uid"])
    saved = captured["spec"]

    assert result["ok"] is True
    assert result["mode"] == "decompose_parent_then_queue_children"
    assert result["created_parent"] is True
    assert parent_uid.startswith("PIS-")
    assert updated["meta"]["derived_pis_uids"] == [parent_uid]
    assert saved["job_id"] == captured["job_id"]
    assert saved["loop"]["builder_only"] is True
    assert saved["loop"]["verifier_role"] == ""
    assert saved["loop"]["loop_policy"] == "build_verify_repair"
    assert saved["loop"]["max_iterations"] == 1
    assert "decomposition class: workflow" in str(saved["prompt"])
    assert "decomposition rationale: Medium workflow that should stay compact." in str(saved["prompt"])
    assert "aim for 2 to 4 implementation children" in str(saved["prompt"])
    assert "reserve one detailed late child PIS for intent-driven assembled-system E2E validation" in str(saved["prompt"])
    assert "require one separate final closeout child after the intent-validation child" in str(saved["prompt"])
    assert saved["prompt_meta"]["queue_child_implementation_jobs"]["enabled"] is True
    assert saved["prompt_meta"]["queue_child_implementation_jobs"]["child_mode"] == "explicit_pass_chain_v1"
    assert saved["prompt_meta"]["queue_child_implementation_jobs"]["max_iterations"] == 3
    assert saved["prompt_meta"]["implement_recursive_defaults"]["child_cap"] == 6
    assert saved["prompt_meta"]["implement_recursive_defaults"]["intent_guidance"]["decomposition_target_range"] == {"min": 2, "max": 4}
    assert saved["prompt_meta"]["implement_recursive_defaults"]["loop_policy"] == "build_verify_repair"
    assert saved["prompt_meta"]["implement_recursive_defaults"]["loop_alias"] == "1.5_iterations"
    assert saved["prompt_meta"]["mission_fit"]["classification"] == "aligned"
    parent_item = ChangeLedger(repo_root=tmp_path).read_item(parent_uid)
    assert updated["meta"]["integration_meta"]["mission_fit"]["classification"] == "aligned"
    assert parent_item["meta"]["integration_meta"]["mission_fit"]["classification"] == "aligned"
    assert parent_item["meta"]["integration_meta"]["execution_mode"] == "explicit_pass_chain_v1"


def test_run_accepts_uid_suffix_for_active_intent(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Suffix target",
        intent_markdown="# Intent Sheet\n\nImprove software development and tooling for suffix-triggered execution.\n",
        uid_override="IS-20260403-212913Z-U4E2",
        project_id="causyl",
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod._scheduler_create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(mod._scheduler_create_job, "_default_spec", lambda *args, **kwargs: {"job_id": "seed", "job_kind": "build_verify_loop", "schedule": {"mode": "once", "run_at": "", "timezone": "UTC"}, "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 5}, "prompt": "x", "prompt_meta": {}})
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": "run-1"})

    result = mod.run("u4e2")
    assert result["ok"] is True
    assert result["target_uid"] == "IS-20260403-212913Z-U4E2"


def test_run_blocks_off_mission_intent_without_exception(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Autonomous lab director expansion",
        intent_markdown="# Intent Sheet\n\nBuild fully autonomous lab director behavior in the near term.\n",
        uid_override="IS-20260403-212913Z-OFF1",
        project_id="causyl",
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    result = mod.run("IS-20260403-212913Z-OFF1")
    loaded = ledger.read_sheet("IS-20260403-212913Z-OFF1")

    assert result["ok"] is False
    assert result["mode"] == "mission_fit_blocked"
    assert result["blocked_reason"] == "mission_drift_blocked"
    assert result["mission_fit"]["classification"] == "blocked"
    assert loaded["meta"]["integration_meta"]["mission_fit"]["classification"] == "blocked"
    assert loaded["meta"]["derived_pis_uids"] == []


def test_run_blocks_unmatched_mission_intent_without_exception(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Neighborhood bakery expansion",
        intent_markdown="# Intent Sheet\n\nOpen a second bakery location with better pastries and weekend seating.\n",
        uid_override="IS-20260403-212913Z-MIS1",
        project_id="causyl",
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    result = mod.run("IS-20260403-212913Z-MIS1")
    loaded = ledger.read_sheet("IS-20260403-212913Z-MIS1")

    assert result["ok"] is False
    assert result["mode"] == "mission_fit_blocked"
    assert result["blocked_reason"] == "mission_drift_blocked"
    assert result["mission_fit"]["classification"] == "mismatch"
    assert result["mission_fit"]["matched_aligned_boundaries"] == []
    assert loaded["meta"]["integration_meta"]["mission_fit"]["classification"] == "mismatch"
    assert loaded["meta"]["derived_pis_uids"] == []


def test_run_allows_recorded_non_goal_exception(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Autonomous lab director research boundary",
        intent_markdown="# Intent Sheet\n\nStudy fully autonomous lab director behavior in the near term.\n",
        uid_override="IS-20260403-212913Z-EXC1",
        project_id="causyl",
    )
    ledger.update_meta(
        "IS-20260403-212913Z-EXC1",
        {
            "integration_meta": {
                "mission_fit_exception": {
                    "allowed": True,
                    "reason": "Boundary study for governance hardening rather than product expansion.",
                    "approved_by": "operator",
                }
            }
        },
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod._scheduler_create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(mod._scheduler_create_job, "_default_spec", lambda *args, **kwargs: {"job_id": "seed", "job_kind": "build_verify_loop", "schedule": {"mode": "once", "run_at": "", "timezone": "UTC"}, "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 5}, "prompt": "x", "prompt_meta": {}})
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": "run-1"})

    result = mod.run("IS-20260403-212913Z-EXC1", decompose_only=True)
    sheet = ledger.read_sheet("IS-20260403-212913Z-EXC1")
    parent_uid = str(result["resolved_parent_uid"])
    parent_item = ChangeLedger(repo_root=tmp_path).read_item(parent_uid)

    assert result["ok"] is True
    assert sheet["meta"]["integration_meta"]["mission_fit"]["classification"] == "needs_exception"
    assert parent_item["meta"]["integration_meta"]["mission_fit"]["classification"] == "needs_exception"
    assert parent_item["meta"]["integration_meta"]["mission_fit"]["exception"]["allowed"] is True


def test_run_rejects_verified_or_deferred_targets(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Verified target",
        intent_markdown="# Intent Sheet\n\nverified\n",
        uid_override="IS-20260403-212913Z-V4F1",
        project_id="causyl",
        status="verified",
    )
    ledger.create_sheet(
        title="Deferred target",
        intent_markdown="# Intent Sheet\n\ndeferred\n",
        uid_override="IS-20260403-212913Z-D3F2",
        project_id="causyl",
        status="deferred",
    )
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="no active implementable item matched|not active and implementable"):
        mod.run("v4f1")
    with pytest.raises(ValueError, match="no active implementable item matched|not active and implementable"):
        mod.run("IS-20260403-212913Z-D3F2")


def test_run_accepts_next_pis_selector(monkeypatch, tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    first = ledger.create_item(title="Later PIS", slug="later", pis_markdown="# Later\n")
    second = ledger.create_item(title="Earlier PIS", slug="earlier", pis_markdown="# Earlier\n")
    ledger.update_meta(str(first["uid"]), {"execution_priority": 200})
    ledger.update_meta(str(second["uid"]), {"execution_priority": 100})
    prompt_path = tmp_path / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement child `<CHILD_UID>` titled `<TITLE>`.\n```\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": "run-1"})

    result = mod.run("next_pis")
    assert result["ok"] is True
    assert result["target_uid"] == str(second["uid"])
    assert result["resolved_kind"] == "pis"


def test_run_accepts_next_intent_selector(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    first = ledger.create_sheet(title="Later Intent", intent_markdown="# Intent\n\nImprove software development and tooling for a later intent.\n", uid_override="IS-20260403-212913Z-L4T1", project_id="causyl")
    second = ledger.create_sheet(title="Earlier Intent", intent_markdown="# Intent\n\nImprove software development and tooling for an earlier intent.\n", uid_override="IS-20260403-212913Z-E4R1", project_id="causyl")
    ledger.update_meta(first["uid"], {"execution_priority": 200})
    ledger.update_meta(second["uid"], {"execution_priority": 100})

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(mod._scheduler_create_job, "_default_spec", lambda *args, **kwargs: {"job_id": "seed", "job_kind": "build_verify_loop", "schedule": {"mode": "once", "run_at": "", "timezone": "UTC"}, "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 5}, "prompt": "x", "prompt_meta": {}})
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": "run-1"})

    result = mod.run("next_intent")
    assert result["ok"] is True
    assert result["target_uid"] == "IS-20260403-212913Z-E4R1"
    assert result["resolved_kind"] == "intent_sheet"


def test_plan_only_for_next_safe_intent_stays_non_mutating_when_parent_missing(monkeypatch, tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Safe Intent",
        intent_markdown="# Intent Sheet\n\nImprove software development and tooling with a safe execution path.\n",
        uid_override="IS-20260407-185942Z-0DC3",
        project_id="causyl",
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        mod._intent_sheet_next,
        "next_sheets",
        lambda repo_root=None, limit=200: [
            {"uid": "IS-20260407-185942Z-0DC3", "parallel_safety": {"safe": True}},
        ],
    )

    result = mod.run("NEXT_SAFE_INTENT", plan_only=True)

    sheet = ledger.read_sheet("IS-20260407-185942Z-0DC3")
    assert result["ok"] is True
    assert result["target_uid"] == "IS-20260407-185942Z-0DC3"
    assert result["mode"] == "plan_create_parent_then_queue_children"
    assert result["would_create_parent"] is True
    assert result["created_parent"] is False
    assert list(sheet["meta"].get("derived_pis_uids") or []) == []


def test_plan_only_existing_parent_with_children_does_not_save_jobs_or_add_tail_children(monkeypatch, tmp_path: Path) -> None:
    intent_ledger = IntentSheetLedger(repo_root=tmp_path)
    intent_ledger.create_sheet(
        title="Parent intent",
        intent_markdown="# Intent Sheet\n\nImprove software development and tooling and keep assembled-system proof honest.\n",
        uid_override="IS-20260406-060000Z-TST1",
        project_id="causyl",
    )
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    child_a = ledger.create_item(title="Child A", slug="child-a", pis_markdown="# Child A\n")
    child_b = ledger.create_item(title="Child B", slug="child-b", pis_markdown="# Child B\n")
    intent_ledger.update_meta("IS-20260406-060000Z-TST1", {"derived_pis_uids": [str(parent["uid"])]})
    ledger.update_meta(
        str(parent["uid"]),
        {
            "integration_meta": {
                "child_uids": [str(child_a["uid"]), str(child_b["uid"])],
                "intent_sheet_uid": "IS-20260406-060000Z-TST1",
                "relationship": "execution_parent",
            }
        },
    )
    for uid, rank, depends_on in (
        (str(child_a["uid"]), 1, []),
        (str(child_b["uid"]), 2, [str(child_a["uid"])]),
    ):
        ledger.update_meta(
            uid,
            {
                "parent_uid": str(parent["uid"]),
                "critical_path_position": rank,
                "depends_on": list(depends_on),
                "integration_meta": {"relationship": "execution_child", "sequence_index": rank},
            },
        )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        mod._batch_from_parent,
        "_planned_child_specs",
        lambda *args, **kwargs: [
            {"job_id": "job_child_a", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": []},
            {"job_id": "job_child_b", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a"]},
        ],
    )
    monkeypatch.setattr(mod._batch_from_parent, "_default_builder_model", lambda: "builder")
    monkeypatch.setattr(mod._batch_from_parent, "_default_verifier_model", lambda: "verifier")
    monkeypatch.setattr(mod, "_save_job", lambda spec: pytest.fail("plan_only must not save jobs"))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: pytest.fail("plan_only must not request run_now"))

    result = mod.run("IS-20260406-060000Z-TST1", plan_only=True)

    rows = mod.sched._iter_parent_child_rows(parent_uid=str(parent["uid"]), repo_root=tmp_path)
    assert result["ok"] is True
    assert result["mode"] == "plan_children_existing_parent"
    assert result["planned_job_ids"] == ["job_child_a", "job_child_b"]
    assert result["first_ready_job_id"] == "job_child_a"
    assert len(rows) == 2
    assert [str(row.get("relationship") or "") for row in rows] == ["execution_child", "execution_child"]


def test_batch_children_uses_explicit_pass_count_for_explicit_family(monkeypatch, tmp_path: Path) -> None:
    intent_ledger = IntentSheetLedger(repo_root=tmp_path)
    intent_ledger.create_sheet(
        title="Parent intent",
        intent_markdown="# Intent Sheet\n\nNeed explicit pass-chain execution.\n",
        uid_override="IS-20260406-060000Z-EPC1",
        project_id="causyl",
    )
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    child = ledger.create_item(title="Child A", slug="child-a", pis_markdown="# Child A\n")
    ledger.update_meta(
        str(parent["uid"]),
        {
            "integration_meta": {
                "child_uids": [str(child["uid"])],
                "intent_sheet_uid": "IS-20260406-060000Z-EPC1",
                "relationship": "execution_parent",
                "execution_mode": "explicit_pass_chain_v1",
            }
        },
    )
    ledger.update_meta(
        str(child["uid"]),
        {
            "parent_uid": str(parent["uid"]),
            "critical_path_position": 1,
            "depends_on": [],
            "integration_meta": {"relationship": "execution_child", "sequence_index": 1},
        },
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    captured: dict[str, int] = {}

    def fake_planned_child_specs(parent_uid, *, run_at_utc, builder_model, verifier_model, max_iterations, repo_root):
        captured["max_iterations"] = int(max_iterations)
        return [
            {"job_id": "job_child_a_p1of3", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": []},
            {"job_id": "job_child_a_p2of3", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a_p1of3"]},
            {"job_id": "job_child_a_p3of3", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a_p2of3"]},
        ]

    monkeypatch.setattr(mod._batch_from_parent, "_planned_child_specs", fake_planned_child_specs)
    monkeypatch.setattr(mod._batch_from_parent, "_default_builder_model", lambda: "builder")
    monkeypatch.setattr(mod._batch_from_parent, "_default_verifier_model", lambda: "verifier")
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": "run-1"})
    monkeypatch.setattr(mod, "_job_terminal_success", lambda job_id: False)
    monkeypatch.setattr(mod, "_job_currently_active", lambda job_id: False)

    result = mod._batch_children(str(parent["uid"]), options={"start_now": True})

    assert captured["max_iterations"] == mod.DEFAULT_CHILD_PASS_COUNT
    assert result["started_job_id"] == "job_child_a_p1of3"


def test_batch_children_honors_explicit_child_pass_count_override(monkeypatch, tmp_path: Path) -> None:
    intent_ledger = IntentSheetLedger(repo_root=tmp_path)
    intent_ledger.create_sheet(
        title="Parent intent",
        intent_markdown="# Intent Sheet\n\nNeed single-pass execution.\n",
        uid_override="IS-20260406-060000Z-EPC2",
        project_id="causyl",
    )
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    child = ledger.create_item(title="Child A", slug="child-a", pis_markdown="# Child A\n")
    ledger.update_meta(
        str(parent["uid"]),
        {
            "integration_meta": {
                "child_uids": [str(child["uid"])],
                "intent_sheet_uid": "IS-20260406-060000Z-EPC2",
                "relationship": "execution_parent",
                "execution_mode": "explicit_pass_chain_v1",
            }
        },
    )
    ledger.update_meta(
        str(child["uid"]),
        {
            "parent_uid": str(parent["uid"]),
            "critical_path_position": 1,
            "depends_on": [],
            "integration_meta": {"relationship": "execution_child", "sequence_index": 1},
        },
    )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    captured: dict[str, int] = {}

    def fake_planned_child_specs(parent_uid, *, run_at_utc, builder_model, verifier_model, max_iterations, repo_root):
        captured["max_iterations"] = int(max_iterations)
        return [
            {"job_id": "job_child_a_p1of1", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": []},
        ]

    monkeypatch.setattr(mod._batch_from_parent, "_planned_child_specs", fake_planned_child_specs)
    monkeypatch.setattr(mod._batch_from_parent, "_default_builder_model", lambda: "builder")
    monkeypatch.setattr(mod._batch_from_parent, "_default_verifier_model", lambda: "verifier")
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": "run-1"})
    monkeypatch.setattr(mod, "_job_terminal_success", lambda job_id: False)
    monkeypatch.setattr(mod, "_job_currently_active", lambda job_id: False)

    result = mod._batch_children(str(parent["uid"]), options={"start_now": True, "child_pass_count": 1})

    assert captured["max_iterations"] == 1
    assert result["started_job_id"] == "job_child_a_p1of1"


def test_run_batches_existing_children_when_only_meta_links_exist(monkeypatch, tmp_path: Path) -> None:
    intent_ledger = IntentSheetLedger(repo_root=tmp_path)
    intent_ledger.create_sheet(
        title="Parent intent",
        intent_markdown="# Intent Sheet\n\nNeed e2e proof.\n",
        uid_override="IS-20260406-060000Z-TST1",
        project_id="causyl",
    )
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    child_a = ledger.create_item(title="Child A", slug="child-a", pis_markdown="# Child A\n")
    child_b = ledger.create_item(title="Child B", slug="child-b", pis_markdown="# Child B\n")
    ledger.update_meta(
        str(child_a["uid"]),
        {
            "parent_uid": str(parent["uid"]),
            "critical_path_position": 1,
            "depends_on": [],
            "integration_meta": {"relationship": "execution_child", "sequence_index": 1},
        },
    )
    ledger.update_meta(
        str(child_b["uid"]),
        {
            "parent_uid": str(parent["uid"]),
            "critical_path_position": 2,
            "depends_on": [str(child_a["uid"])],
            "integration_meta": {"relationship": "execution_child", "sequence_index": 2},
        },
    )
    for uid, rank, depends_on in (
        (str(child_a["uid"]), 1, []),
        (str(child_b["uid"]), 2, [str(child_a["uid"])]),
    ):
        child_dir = ledger.get_item_dir(uid)
        child_meta_path = child_dir / "meta.json"
        child_meta = json.loads(child_meta_path.read_text(encoding="utf-8"))
        child_meta["parent_uid"] = str(parent["uid"])
        child_meta["critical_path_position"] = rank
        child_meta["depends_on"] = list(depends_on)
        child_meta["integration_meta"] = {"relationship": "execution_child", "sequence_index": rank}
        child_meta_path.write_text(json.dumps(child_meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    parent_dir = ledger.get_item_dir(str(parent["uid"]))
    parent_meta_path = parent_dir / "meta.json"
    parent_meta = json.loads(parent_meta_path.read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "child_uids": [str(child_a["uid"]), str(child_b["uid"])],
        "intent_sheet_uid": "IS-20260406-060000Z-TST1",
        "relationship": "execution_parent",
    }
    parent_meta_path.write_text(json.dumps(parent_meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        mod._batch_from_parent,
        "_planned_child_specs",
        lambda *args, **kwargs: [
            {"job_id": "job_child_a", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": []},
            {"job_id": "job_child_b", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a"]},
            {"job_id": "job_validation", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a", "job_child_b"]},
            {"job_id": "job_closeout", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a", "job_child_b", "job_validation"]},
        ],
    )
    monkeypatch.setattr(mod._batch_from_parent, "_default_builder_model", lambda: "builder")
    monkeypatch.setattr(mod._batch_from_parent, "_default_verifier_model", lambda: "verifier")

    saved: list[str] = []
    monkeypatch.setattr(mod, "_save_job", lambda spec: saved.append(str(spec.get("job_id") or "")) or dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": f"run-{job_id}"})

    result = mod.run(str(parent["uid"]))

    assert result["ok"] is True
    assert result["mode"] == "batch_children_existing_parent"
    assert result["saved_job_ids"] == ["job_child_a", "job_child_b", "job_validation", "job_closeout"]
    rows = mod.sched._iter_parent_child_rows(parent_uid=str(parent["uid"]), repo_root=tmp_path)
    assert len(rows) == 4
    assert [str(row.get("uid") or "") for row in rows[:2]] == [str(child_a["uid"]), str(child_b["uid"])]
    assert str(rows[-2].get("relationship") or "") == "intent_validation_suite"
    assert str(rows[-2].get("title") or "") == "Validate Parent intent Assembled-System Intent E2E Suite"
    assert str(rows[-1].get("relationship") or "") == "intent_family_closeout"
    assert str(rows[-1].get("title") or "") == "Close Parent intent Intent Boundary, Proof, and Family Reconciliation"
    updated_sheet = intent_ledger.read_sheet("IS-20260406-060000Z-TST1")
    assert len(list(updated_sheet["meta"].get("validating_pis_uids") or [])) == 1


def test_build_intent_validation_pis_markdown_stays_e2e_first_not_closeout_first() -> None:
    body = mod._build_intent_validation_pis_markdown(
        intent_uid="IS-20260403-212913Z-5817",
        intent_title="Canonical Job Shape Taxonomy",
        parent_uid="PIS-20260406-053253Z-2050",
    )

    assert "Validate Canonical Job Shape Taxonomy Assembled-System Intent E2E Suite" in body
    assert "Design and execute a detailed intent-driven assembled-system E2E validation campaign" in body
    assert "Intent requirement map" in body
    assert "Scenario matrix" in body
    assert "map each requirement to observable system behavior" in body
    assert "prefer scenario-driven E2E proof that tests the whole family contract" in body
    assert "do not spend this child on ledger-only closeout work" in body
    assert "Intent Closure Gate" not in body
    assert "truthfully promote the parent PIS to verified" not in body


def test_build_intent_closeout_pis_markdown_allows_bounded_repair_and_honest_closure() -> None:
    body = mod._build_intent_closeout_pis_markdown(
        intent_uid="IS-20260403-212913Z-5817",
        intent_title="Canonical Job Shape Taxonomy",
        parent_uid="PIS-20260406-053253Z-2050",
    )

    assert "Close Canonical Job Shape Taxonomy Intent Boundary, Proof, and Family Reconciliation" in body
    assert "bounded repair, reconciliation, and honest closure" in body
    assert "implementation children and the intent-validation child together" in body
    assert "may repair bounded findings that block honest closure" in body
    assert "rerun the focused validation needed to prove that the repaired state actually resolves the finding" in body


def test_run_recovers_event_linked_parent_with_empty_child_uids_before_adding_tail_children(monkeypatch, tmp_path: Path) -> None:
    intent_ledger = IntentSheetLedger(repo_root=tmp_path)
    intent_ledger.create_sheet(
        title="Recovered parent intent",
        intent_markdown="# Intent Sheet\n\nRecover event-linked children.\n",
        uid_override="IS-20260406-060000Z-REC1",
        project_id="causyl",
    )
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    child_a = ledger.create_item(title="Child A", slug="child-a", pis_markdown="# Child A\n")
    child_b = ledger.create_item(title="Child B", slug="child-b", pis_markdown="# Child B\n")
    for uid, title, rank, deps in (
        (str(child_a["uid"]), "Child A", 1, []),
        (str(child_b["uid"]), "Child B", 2, [str(child_a["uid"])]),
    ):
        child_dir = ledger.get_item_dir(uid)
        child_meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        child_meta["parent_uid"] = str(parent["uid"])
        child_meta["critical_path_position"] = rank
        child_meta["depends_on"] = list(deps)
        child_meta["integration_meta"] = {"relationship": "execution_child", "sequence_index": rank}
        (child_dir / "meta.json").write_text(json.dumps(child_meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": str(parent["uid"]),
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": list(deps),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    parent_dir = ledger.get_item_dir(str(parent["uid"]))
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["integration_meta"] = {
        "child_uids": [],
        "intent_sheet_uid": "IS-20260406-060000Z-REC1",
        "relationship": "execution_parent",
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        mod._batch_from_parent,
        "_planned_child_specs",
        lambda *args, **kwargs: [
            {"job_id": "job_child_a", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": []},
            {"job_id": "job_child_b", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a"]},
            {"job_id": "job_validation", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a", "job_child_b"]},
            {"job_id": "job_closeout", "schedule": {"run_at": ""}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a", "job_child_b", "job_validation"]},
        ],
    )
    monkeypatch.setattr(mod._batch_from_parent, "_default_builder_model", lambda: "builder")
    monkeypatch.setattr(mod._batch_from_parent, "_default_verifier_model", lambda: "verifier")
    monkeypatch.setattr(mod, "_save_job", lambda spec: dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: {"ok": True, "run_id": f"run-{job_id}"})

    result = mod.run(str(parent["uid"]))

    assert result["ok"] is True
    rows = mod.sched._iter_parent_child_rows(parent_uid=str(parent["uid"]), repo_root=tmp_path)
    assert [str(row.get("relationship") or "") for row in rows] == [
        "execution_child",
        "execution_child",
        "intent_validation_suite",
        "intent_family_closeout",
    ]


def test_iter_parent_child_rows_prefers_parent_canonical_child_uids_over_duplicate_event_rows(monkeypatch, tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    old_a = ledger.create_item(title="Old Child A", slug="old-child-a", pis_markdown="# Old A\n")
    old_b = ledger.create_item(title="Old Child B", slug="old-child-b", pis_markdown="# Old B\n")
    new_a = ledger.create_item(title="New Child A", slug="new-child-a", pis_markdown="# New A\n")
    new_b = ledger.create_item(title="New Child B", slug="new-child-b", pis_markdown="# New B\n")

    parent_uid = str(parent["uid"])
    canonical = [str(new_a["uid"]), str(new_b["uid"])]
    ledger.update_meta(
        parent_uid,
        {
            "integration_meta": {
                "child_uids": list(canonical),
                "recommended_execution_order": list(canonical),
                "relationship": "execution_parent",
                "intent_sheet_uid": "IS-20260406-060000Z-CAN1",
            }
        },
    )

    def _attach(uid: str, *, relationship: str, rank: int) -> None:
        child_dir = ledger.get_item_dir(uid)
        meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        meta["parent_uid"] = parent_uid
        meta["critical_path_position"] = rank
        meta["integration_meta"] = {"relationship": relationship, "sequence_index": rank}
        (child_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": [],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    _attach(str(old_a["uid"]), relationship="execution_child", rank=1)
    _attach(str(old_b["uid"]), relationship="intent_family_closeout", rank=2)
    _attach(str(new_a["uid"]), relationship="execution_child", rank=1)
    _attach(str(new_b["uid"]), relationship="intent_family_closeout", rank=2)

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)

    rows = mod.sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=tmp_path)

    assert [str(row.get("uid") or "") for row in rows] == canonical


def test_batch_children_skips_rebatch_for_verified_family(monkeypatch, tmp_path: Path) -> None:
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"status": "verified"}), encoding="utf-8")

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_resolve_ledger_item_dir_for_uid", lambda uid, repo_root=None: parent_dir if uid == "PIS-20260326-203500Z-R7K2" else None)
    monkeypatch.setattr(
        mod.sched,
        "_iter_parent_child_rows",
        lambda parent_uid, repo_root=None: [
            {"uid": "PIS-20260326-203500Z-A7D1"},
            {"uid": "PIS-20260326-203500Z-B8E2"},
        ],
    )
    monkeypatch.setattr(mod.sched, "_ledger_status_for_uid", lambda uid, repo_root=None: "verified")

    saved: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(mod, "_save_job", lambda spec: saved.append(str(spec.get("job_id") or "")) or dict(spec))
    monkeypatch.setattr(mod, "_run_now", lambda job_id: started.append(job_id) or {"ok": True, "run_id": f"run-{job_id}"})

    result = mod._batch_children("PIS-20260326-203500Z-R7K2", options={"start_now": True})

    assert result["saved_job_ids"] == []
    assert result["started_job_id"] == ""
    assert result["skipped_reason"] == "family_already_verified"
    assert saved == []
    assert started == []


def test_batch_children_reconciles_source_intent_when_family_already_verified(monkeypatch, tmp_path: Path) -> None:
    intent_ledger = IntentSheetLedger(repo_root=tmp_path)
    intent_ledger.create_sheet(
        title="Verified family source intent",
        intent_markdown="# Intent Sheet\n\nstale draft\n",
        uid_override="IS-20260406-060000Z-RCN1",
        project_id="causyl",
    )
    intent_ledger.update_meta(
        "IS-20260406-060000Z-RCN1",
        {"derived_pis_uids": ["PIS-20260406-060001Z-PAR1"], "validating_pis_uids": ["PIS-20260406-060003Z-VAL1"]},
    )
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent item", slug="parent-item", pis_markdown="# Parent\n")
    validation = ledger.create_item(title="Validation item", slug="validation-item", pis_markdown="# Validation\n")
    closeout = ledger.create_item(title="Closeout item", slug="closeout-item", pis_markdown="# Closeout\n")

    parent_dir = ledger.get_item_dir(str(parent["uid"]))
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    parent_meta["status"] = "verified"
    parent_meta["integration_meta"] = {
        "intent_sheet_uid": "IS-20260406-060000Z-RCN1",
        "intent_validation_child_uid": str(validation["uid"]),
        "required_final_child_uid": str(closeout["uid"]),
        "required_final_child_kind": "intent_family_closeout",
        "child_uids": [str(validation["uid"]), str(closeout["uid"])],
        "relationship": "execution_parent",
    }
    (parent_dir / "meta.json").write_text(json.dumps(parent_meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for uid, relationship in ((str(validation["uid"]), "intent_validation_suite"), (str(closeout["uid"]), "intent_family_closeout")):
        child_dir = ledger.get_item_dir(uid)
        child_meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        child_meta["status"] = "verified"
        child_meta["parent_uid"] = str(parent["uid"])
        child_meta["integration_meta"] = {"relationship": relationship, "intent_sheet_uid": "IS-20260406-060000Z-RCN1"}
        child_meta["critical_path_position"] = 1
        child_meta["depends_on"] = []
        (child_dir / "meta.json").write_text(json.dumps(child_meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {"parent_uid": str(parent["uid"]), "relationship": relationship, "critical_path_rank": 1, "depends_on": []},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    result = mod._batch_children(str(parent["uid"]), options={"start_now": True})

    assert result["skipped_reason"] == "family_already_verified"
    assert result["source_intent_reconciled"] is True
    sheet = intent_ledger.read_sheet("IS-20260406-060000Z-RCN1")
    assert sheet["meta"]["status"] == "verified"
    assert sheet["meta"]["validating_pis_uids"] == [str(closeout["uid"])]


def test_batch_children_starts_first_nonterminal_runnable_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(mod._batch_from_parent, "_default_builder_model", lambda: "builder")
    monkeypatch.setattr(mod._batch_from_parent, "_default_verifier_model", lambda: "verifier")
    monkeypatch.setattr(
        mod._batch_from_parent,
        "_planned_child_specs",
        lambda *args, **kwargs: [
            {"job_id": "job_child_a", "schedule": {"run_at": "old"}, "loop": {}, "prompt_meta": {}, "depends_on": []},
            {"job_id": "job_child_b", "schedule": {"run_at": "old"}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_a"]},
            {"job_id": "job_child_c", "schedule": {"run_at": "old"}, "loop": {}, "prompt_meta": {}, "depends_on": ["job_child_b"]},
        ],
    )
    monkeypatch.setattr(mod.sched, "_resolve_ledger_item_dir_for_uid", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod.sched, "_read_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(mod.sched, "_iter_parent_child_rows", lambda *args, **kwargs: [{"uid": "PIS-A"}, {"uid": "PIS-B"}, {"uid": "PIS-C"}])
    monkeypatch.setattr(mod.sched, "_ledger_status_for_uid", lambda *args, **kwargs: "draft")

    state_by_job = {
        "job_child_a": {"last_status": "ok", "active_run_id": "", "run_now_requested_utc": ""},
        "job_child_b": {"last_status": "", "active_run_id": "", "run_now_requested_utc": ""},
        "job_child_c": {"last_status": "", "active_run_id": "", "run_now_requested_utc": ""},
    }
    monkeypatch.setattr(mod.sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job.get(job_id, {})))

    saved_specs: dict[str, dict] = {}

    def fake_save_job(spec):
        saved_specs[str(spec.get("job_id"))] = json.loads(json.dumps(spec))
        return dict(spec)

    started: list[str] = []
    monkeypatch.setattr(mod, "_save_job", fake_save_job)
    monkeypatch.setattr(mod, "_run_now", lambda job_id: started.append(job_id) or {"ok": True, "run_id": f"run-{job_id}"})

    result = mod._batch_children("PIS-20260411-180354Z-D4C5", options={"start_now": True})

    assert result["started_job_id"] == "job_child_b"
    assert started == ["job_child_b"]
    assert saved_specs["job_child_a"]["schedule"]["run_at"] == "old"
    assert saved_specs["job_child_b"]["schedule"]["run_at"] != "old"
