from __future__ import annotations

import json
import io
from pathlib import Path

from tools.bin import scheduler_create_job as create_job


def _write_pis(root: Path, uid: str, slug: str, *, title: str, status: str, updated_utc: str, created_utc: str) -> None:
    item_dir = root / "governance" / "ledger" / "items" / f"{uid}__{slug}"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": uid,
                "slug": slug,
                "title": title,
                "status": status,
                "updated_utc": updated_utc,
                "created_utc": created_utc,
            }
        ),
        encoding="utf-8",
    )
    (item_dir / "pis.md").write_text(f"# Patch Instruction Sheet (PIS)\n\n**UID:** {uid}\n", encoding="utf-8")


def test_load_recent_pis_choices_orders_newest_first(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "newer-item",
        title="Newer item",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    _write_pis(
        tmp_path,
        "PIS-20260325-100000Z-A1B2",
        "older-item",
        title="Older item",
        status="verified",
        updated_utc="2026-03-25T10:00:00Z",
        created_utc="2026-03-25T10:00:00Z",
    )

    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())

    rows = create_job._load_recent_pis_choices(limit=10)
    assert [row.uid for row in rows] == [
        "PIS-20260326-203500Z-R7K2",
        "PIS-20260325-100000Z-A1B2",
    ]


def test_available_template_ids_reads_template_registry(tmp_path: Path, monkeypatch) -> None:
    templates_root = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "templates"
    templates_root.mkdir(parents=True, exist_ok=True)
    (templates_root / "runtime_cleanup_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_template_v1",
                "template_id": "runtime_cleanup_audit",
                "prompt": "Audit <LEDGER_UID>",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(create_job, "TEMPLATES_ROOT", templates_root.resolve())
    assert create_job._available_template_ids() == ["runtime_cleanup_audit"]


def test_prompt_menu_accepts_single_key_default(monkeypatch) -> None:
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: "")
    assert create_job._prompt_menu("Job type", choices=["implementation", "decompose_parent"], default_index=0) == "implementation"


def test_prompt_menu_accepts_single_key_number(monkeypatch) -> None:
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: "2")
    assert create_job._prompt_menu("Job type", choices=["implementation", "decompose_parent"], default_index=0) == "decompose_parent"


def test_prompt_menu_accepts_numbered_selection_in_default_line_mode(monkeypatch) -> None:
    monkeypatch.delenv("SCHEDULER_SINGLE_KEY_MENUS", raising=False)
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": "2")
    assert create_job._prompt_menu("Job type", choices=["implementation", "decompose_parent"], default_index=0) == "decompose_parent"


def test_prompt_bool_accepts_single_key_without_enter(monkeypatch) -> None:
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    monkeypatch.setattr(create_job, "termios", object())
    monkeypatch.setattr(create_job, "tty", object())
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": type("S", (), {"isatty": lambda self: True})()}))
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: "n")
    assert create_job._prompt_bool("Use defaults", default=True) is False


def test_select_pis_accepts_single_key_selection(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "newer-item",
        title="Newer item",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    _write_pis(
        tmp_path,
        "PIS-20260325-100000Z-A1B2",
        "older-item",
        title="Older item",
        status="verified",
        updated_utc="2026-03-25T10:00:00Z",
        created_utc="2026-03-25T10:00:00Z",
    )
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: "2")

    selected = create_job._select_pis(limit=10)
    assert selected.uid == "PIS-20260325-100000Z-A1B2"


def test_select_pis_accepts_numbered_selection_in_default_line_mode(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "newer-item",
        title="Newer item",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    _write_pis(
        tmp_path,
        "PIS-20260325-100000Z-A1B2",
        "older-item",
        title="Older item",
        status="verified",
        updated_utc="2026-03-25T10:00:00Z",
        created_utc="2026-03-25T10:00:00Z",
    )
    monkeypatch.delenv("SCHEDULER_SINGLE_KEY_MENUS", raising=False)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": "2")

    selected = create_job._select_pis(limit=10)
    assert selected.uid == "PIS-20260325-100000Z-A1B2"


def test_build_spec_from_answers_uses_defaults_and_selected_pis(tmp_path: Path, monkeypatch) -> None:
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")

    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    menu_answers = iter(["", "", "", "", ""])
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: next(menu_answers))
    prompts = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(prompts))
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO("")}))

    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )

    spec = create_job._build_spec_from_answers(choice)
    assert spec["job_id"].startswith("pis_r7k2_20260326_")
    assert spec["binding"] == {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"}
    assert spec["runtime"]["agent_tech"] == "openai"
    assert spec["runtime"]["advisory_agent_tech"] == "openai"
    assert spec["runtime"]["executor_agent_tech"] == "codex"
    assert spec["runtime"]["model"] == "gpt-5.4"
    assert spec["runtime"]["advisory_model"] == "gpt-5.4"
    assert spec["runtime"]["fallback_models"] == ["gpt-5.4"]
    assert spec["required_context_bundle"] == "lab_core_v1"
    assert spec["context_refs"] == ["governance/ledger/items/PIS-20260326-203500Z-R7K2__test-item/pis.md"]
    assert spec["schedule"]["run_at"] == "2026-03-27T03:30:00Z"
    assert spec["_prompt_mode"] == "implementation"


def test_default_spec_from_template_instantiates_placeholders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")
    templates_root = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "templates"
    templates_root.mkdir(parents=True, exist_ok=True)
    (templates_root / "scheduler_robustness_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_template_v1",
                "template_id": "scheduler_robustness_audit",
                "job_title": "scheduler robustness audit <TITLE>",
                "job_kind": "build_verify_loop",
                "display_title_template": "scheduler robustness <UID_SUFFIX> <TITLE>",
                "prompt": "Audit `<LEDGER_UID>` with `<PIS_PATH>`",
                "context_refs": ["<PIS_PATH>"],
                "expected_output": {"kind": "scheduler_robustness_patch", "format": "markdown"},
                "required_context_bundle": "lab_core_v1",
                "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 4, "pass_marker": "VERDICT: PASS"},
                "policy": {"repo_change_mode": "strict"}
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(create_job, "TEMPLATES_ROOT", templates_root.resolve())
    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )
    spec = create_job._default_spec_from_template(choice, template_id="scheduler_robustness_audit")
    assert spec["job_kind"] == "build_verify_loop"
    assert spec["display_title"] == "scheduler robustness R7K2 Scheduler helper polish"
    assert spec["prompt"] == "Audit `PIS-20260326-203500Z-R7K2` with `governance/ledger/items/PIS-20260326-203500Z-R7K2__test-item/pis.md`"
    assert spec["context_refs"] == ["governance/ledger/items/PIS-20260326-203500Z-R7K2__test-item/pis.md"]
    assert spec["policy"]["repo_change_mode"] == "strict"
    assert spec["prompt_meta"]["scheduled_template"]["template_id"] == "scheduler_robustness_audit"


def test_prompt_multiline_uses_eof_and_falls_back_to_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO("custom line 1\ncustom line 2\n")}))
    text = create_job._prompt_multiline("prompt", default="default prompt")
    assert text == "custom line 1\ncustom line 2"
    assert "finish with EOF" in capsys.readouterr().out

    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO("")}))
    text = create_job._prompt_multiline("prompt", default="default prompt")
    assert text == "default prompt"


def test_default_prompt_uses_lab_task_prompt_template_for_implementation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")
    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )
    prompt = create_job._default_prompt(choice, prompt_mode="implementation")
    assert "Implement ledger item `PIS-20260326-203500Z-R7K2` in the repo" in prompt
    assert "Stay inside the child scope." in prompt
    assert "Ledger binding:" in prompt
    assert "pis_path:" in prompt


def test_default_prompt_uses_lab_task_prompt_template_for_parent_decomposition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")
    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )
    prompt = create_job._default_prompt(choice, prompt_mode="decompose_parent")
    assert "Decompose parent ledger item `PIS-20260326-203500Z-R7K2`" in prompt
    assert "Create child ledger items that make the parent buildable in phases" in prompt
    assert "Aim for about 7-9 child items" in prompt
    assert "Ledger binding:" in prompt


def test_default_spec_builds_valid_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")
    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )
    spec = create_job._default_spec(choice, prompt_mode="implementation")
    assert spec["binding"] == {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"}
    assert spec["schedule"]["run_at"] == "2026-03-27T03:30:00Z"
    assert spec["runtime"]["executor_agent_tech"] == "codex"
    assert spec["runtime"]["model"] == "gpt-5.4"
    assert spec["runtime"]["fallback_models"] == ["gpt-5.4"]
    assert spec["required_context_bundle"] == "lab_core_v1"
    assert spec["_prompt_mode"] == "implementation"


def test_default_spec_uses_decompose_mode_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")
    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )
    spec = create_job._default_spec(choice, prompt_mode="decompose_parent", queue_child_jobs=True, child_delay_minutes=5)
    assert spec["job_kind"] == "build_verify_loop"
    assert spec["expected_output"]["kind"] == "governance_decomposition_patch"
    assert spec["_prompt_mode"] == "decompose_parent"
    assert spec["runtime"]["model"] == "gpt-5.4"
    assert spec["runtime"]["executor_agent_tech"] == "codex"
    assert spec["runtime"]["fallback_models"] == ["deepseek-reasoner"]
    assert spec["loop"]["builder_role"] == "builder"
    assert spec["loop"]["verifier_role"] == "verifier"
    assert spec["loop"]["builder_runtime"]["model"] == "gpt-5.4"
    assert spec["loop"]["builder_runtime"]["executor_agent_tech"] == "codex"
    assert spec["loop"]["builder_runtime"]["fallback_models"] == ["gpt-5.4"]
    assert spec["loop"]["verifier_runtime"]["model"] == "gpt-5.4"
    assert spec["loop"]["verifier_runtime"]["executor_agent_tech"] == "codex"
    assert spec["loop"]["verifier_runtime"]["fallback_models"] == ["deepseek-reasoner"]
    assert spec["prompt_meta"]["queue_child_implementation_jobs"]["enabled"] is True


def test_available_model_choices_include_default_and_pipeline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    models = create_job._available_model_choices("gpt-5.3-codex-spark")
    assert "gpt-5.3-codex-spark" in models
    assert "gpt-5.4" in models
    assert "gpt-5.4-mini" in models
    assert "gpt-5.4-nano" in models
    assert "claude-sonnet-4-6" in models
    assert "gemini-2.5-pro" in models
    assert "deepseek-chat" in models
    assert "pipeline" in models


def test_loop_role_choices_are_narrowed_for_build_verify() -> None:
    assert create_job._loop_builder_role_choices() == ["builder", "generalist", "orchestrator"]
    assert create_job._loop_verifier_role_choices() == ["verifier", "assurer", "generalist"]


def test_build_spec_stepwise_prompts_show_inline_options(tmp_path: Path, monkeypatch, capsys) -> None:
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")

    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    menu_answers = iter(["", "", "", "", ""])
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: next(menu_answers))
    prompts_seen = []
    answers = iter(["", "", "", "", "", "", "", "", ""])

    def _fake_read_input(prompt: str = "") -> str:
        prompts_seen.append(prompt)
        return next(answers)

    monkeypatch.setattr(create_job, "_read_input", _fake_read_input)
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO("")}))

    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )

    create_job._build_spec_from_answers(choice)
    joined = "\n".join(prompts_seen)
    assert "timezone (IANA timezone, examples:" in joined
    captured = capsys.readouterr()
    assert "role (options:" in captured.out
    assert "advisory model (available:" in captured.out


def test_main_accepts_default_preview_without_stepwise_edit(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "test-item",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setattr(create_job, "_require_tty", lambda: None)
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: "")
    answers = iter(["", "", "", ""])
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(answers))

    rc = create_job.main(["--backend", "legacy"])
    assert rc == 0
    jobs = list((tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox").glob("*.json"))
    assert len(jobs) == 1


def test_main_can_switch_from_preview_to_stepwise_edit(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "test-item",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setattr(create_job, "_require_tty", lambda: None)
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    menu_answers = iter(["", "", "", "", "", "", ""])
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: next(menu_answers))
    answers = iter(["", "", "n", "", "", "", "", "", "", "", "", ""])
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(answers))
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO(""), "stderr": io.StringIO()}))

    rc = create_job.main(["--backend", "legacy"])
    assert rc == 0
    jobs = list((tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox").glob("*.json"))
    assert len(jobs) == 1


def test_main_supports_decompose_parent_mode_as_first_question(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "test-item",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setattr(create_job, "_require_tty", lambda: None)
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    menu_answers = iter(["2", "", ""])
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: next(menu_answers))
    answers = iter(["", "", "", "", ""])
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(answers))

    rc = create_job.main(["--backend", "legacy"])
    assert rc == 0
    jobs = list((tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox").glob("*.json"))
    assert len(jobs) == 1
    spec = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert spec["job_kind"] == "build_verify_loop"
    assert spec["expected_output"]["kind"] == "governance_decomposition_patch"
    assert spec["prompt_meta"]["queue_child_implementation_jobs"]["enabled"] is True


def test_main_supports_build_verify_loop_for_implementation_mode(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "test-item",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setattr(create_job, "_require_tty", lambda: None)
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    menu_answers = iter(["", "2", "", ""])
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: next(menu_answers))
    answers = iter(["", "", "", ""])
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(answers))

    rc = create_job.main(["--backend", "legacy"])
    assert rc == 0
    jobs = list((tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox").glob("*.json"))
    assert len(jobs) == 1
    spec = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert spec["job_kind"] == "build_verify_loop"
    assert spec["loop"]["builder_role"] == "builder"
    assert spec["loop"]["verifier_role"] == "verifier"
    assert "prompt_meta" not in spec or "queue_child_implementation_jobs" not in dict(spec.get("prompt_meta") or {})


def test_main_can_save_job_from_template_id(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "test-item",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    templates_root = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "templates"
    templates_root.mkdir(parents=True, exist_ok=True)
    (templates_root / "control_plane_preflight.json").write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_template_v1",
                "template_id": "control_plane_preflight",
                "job_title": "control plane preflight <TITLE>",
                "job_kind": "single_turn",
                "role": "assurer",
                "display_title_template": "preflight <UID_SUFFIX> <TITLE>",
                "prompt": "Preflight `<LEDGER_UID>` with `<PIS_PATH>`",
                "context_refs": ["<PIS_PATH>"],
                "expected_output": {"kind": "report", "format": "markdown"},
                "required_context_bundle": "lab_core_v1"
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job, "TEMPLATES_ROOT", templates_root.resolve())
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setattr(create_job, "_require_tty", lambda: None)
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: "")
    answers = iter(["", ""])
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(answers))

    rc = create_job.main(["--template-id", "control_plane_preflight", "--backend", "legacy"])
    assert rc == 0
    jobs = list((tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox").glob("*.json"))
    assert len(jobs) == 1
    spec = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert spec["role"] == "assurer"
    assert spec["prompt_meta"]["scheduled_template"]["template_id"] == "control_plane_preflight"


def test_main_decompose_mode_asks_child_queue_settings_before_preview(tmp_path: Path, monkeypatch) -> None:
    _write_pis(
        tmp_path,
        "PIS-20260326-203500Z-R7K2",
        "test-item",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
    )
    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job, "LEDGER_ROOT", (tmp_path / "governance" / "ledger" / "items").resolve())
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.setattr(create_job, "_require_tty", lambda: None)
    monkeypatch.setenv("SCHEDULER_SINGLE_KEY_MENUS", "1")
    menu_answers = iter(["2", "", ""])
    monkeypatch.setattr(create_job, "_read_menu_key", lambda: next(menu_answers))
    answers = iter(["", "", "", "", ""])
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(answers))

    rc = create_job.main(["--backend", "legacy"])
    assert rc == 0
    jobs = list((tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox").glob("*.json"))
    spec = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert spec["prompt_meta"]["queue_child_implementation_jobs"]["enabled"] is True
    assert spec["prompt_meta"]["queue_child_implementation_jobs"]["run_after_minutes"] == 5


def test_build_spec_from_answers_can_override_builder_and_verifier_models(tmp_path: Path, monkeypatch) -> None:
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")

    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.delenv("SCHEDULER_SINGLE_KEY_MENUS", raising=False)
    prompts = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
                "",
                "",
                "",
                "",
                "",
                "1",
                "13",
                "",
                "",
                "",
            "",
        ]
    )
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(prompts))
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO(""), "stderr": io.StringIO()}))

    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )

    existing = create_job._default_spec(choice, prompt_mode="decompose_parent", queue_child_jobs=True, child_delay_minutes=5)
    spec = create_job._build_spec_from_answers(choice, existing=existing)
    assert spec["runtime"]["model"] == "gpt-5.4"
    assert spec["runtime"]["agent_tech"] == "openai"
    assert spec["loop"]["builder_runtime"]["model"] == "gpt-5.4"
    assert spec["loop"]["builder_runtime"]["agent_tech"] == "openai"
    assert spec["loop"]["verifier_runtime"]["model"] == "deepseek-reasoner"
    assert spec["loop"]["verifier_runtime"]["agent_tech"] == "deepseek"


def test_build_spec_from_answers_can_select_pipeline_executor(tmp_path: Path, monkeypatch) -> None:
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")

    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.delenv("SCHEDULER_SINGLE_KEY_MENUS", raising=False)
    prompts = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "2",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr(create_job, "_read_input", lambda prompt="": next(prompts))
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO(""), "stderr": io.StringIO()}))

    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )

    spec = create_job._build_spec_from_answers(choice)
    assert spec["runtime"]["executor_agent_tech"] == "pipeline"
    assert spec["runtime"]["pipeline_executor_mode"] == "stage8_backend"


def test_build_spec_from_answers_can_select_controller_only_execution_mode(tmp_path: Path, monkeypatch) -> None:
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260326-203500Z-R7K2__test-item"
    item_dir.mkdir(parents=True, exist_ok=True)
    pis_path = item_dir / "pis.md"
    pis_path.write_text("# PIS\n", encoding="utf-8")

    monkeypatch.setattr(create_job, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(create_job.sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_job, "_default_run_at", lambda now=None: "2026-03-27T03:30:00Z")
    monkeypatch.delenv("SCHEDULER_SINGLE_KEY_MENUS", raising=False)
    selection_count = {"n": 0}

    def _fake_read_input(prompt: str = "") -> str:
        if prompt.startswith("Selection"):
            selection_count["n"] += 1
            return "2" if selection_count["n"] == 5 else ""
        return ""

    monkeypatch.setattr(create_job, "_read_input", _fake_read_input)
    monkeypatch.setattr(create_job, "sys", type("T", (), {"stdin": io.StringIO(""), "stderr": io.StringIO()}))

    choice = create_job.PisChoice(
        uid="PIS-20260326-203500Z-R7K2",
        title="Scheduler helper polish",
        status="draft",
        updated_utc="2026-03-26T20:35:00Z",
        created_utc="2026-03-26T20:35:00Z",
        slug="test-item",
        dir_path=item_dir,
        pis_path=pis_path,
    )

    spec = create_job._build_spec_from_answers(choice)
    assert spec["runtime"]["execution_mode"] == "controller_only"


def test_warn_if_pipeline_executor_prints_warning(capsys) -> None:
    create_job._warn_if_pipeline_executor(executor_agent_tech="pipeline", job_kind="build_verify_loop")
    out = capsys.readouterr().out
    assert "pipeline executor does not run tools" in out
    assert "build_verify_loop will use pipeline full-auto" in out
