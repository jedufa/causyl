from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools.bin import scheduler_change_job_model as change_model
from infra.controlplane import scheduled_jobs as sched


def test_job_display_title_falls_back_to_humanized_job_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    sched.save_job(
        {
            "job_id": "overnight_20260314_q4a6b_rw_20_48_7ed3dcbf",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": "PIS-20260314-041711Z-Q4A6"},
            "prompt": "Prompt.",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
        repo_root=tmp_path,
    )

    row = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-15T01:00:00Z")[0]
    assert row["display_title"] == "rw step 20/48"


def test_reason_display_uses_blocking_job_display_title() -> None:
    rows = {
        "overnight_20260314_q4a6b_rw_20_7ed3dcbf": {"display_title": "rw step 20/48"},
    }
    assert (
        change_model._reason_display(
            "dependency_not_ok:overnight_20260314_q4a6b_rw_20_7ed3dcbf",
            rows,
        )
        == "blocked by rw step 20/48"
    )


def test_list_pending_jobs_orders_by_dependencies_not_job_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(change_model, "REPO_ROOT", tmp_path)
    sched.save_job(
        {
            "job_id": "z_head",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "head",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "display_title": "head",
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": "a_child",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "depends_on": ["z_head"],
            "prompt": "child",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:10:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "display_title": "child",
        },
        repo_root=tmp_path,
    )

    rows = change_model._list_pending_jobs(include_all=True)
    assert [row["job_id"] for row in rows] == ["z_head", "a_child"]


def test_list_pending_jobs_includes_disabled_jobs_that_would_be_due_soon(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(change_model, "REPO_ROOT", tmp_path)
    fixed_now = datetime(2026, 3, 15, 0, 0, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(change_model, "datetime", _FixedDatetime)

    sched.save_job(
        {
            "job_id": "disabled_soon",
            "enabled": False,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "soon",
            "schedule": {"mode": "once", "run_at": "2026-03-16T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "display_title": "disabled soon",
        },
        repo_root=tmp_path,
    )

    rows = change_model._list_pending_jobs(include_all=False)
    assert [row["job_id"] for row in rows] == ["disabled_soon"]
    effective_due = change_model._row_effective_due(rows[0], now=fixed_now)
    assert effective_due["reason"] == "not_due_yet"
    assert effective_due["due_utc"] == "2026-03-16T00:00:00Z"


def test_disabled_reason_display_for_future_due_job() -> None:
    out = change_model._disabled_reason_display(
        {"reason": "not_due_yet", "due_utc": "2026-03-16T00:00:00Z"},
        {},
    )
    assert out == "disabled; due 2026-03-16T00:00:00Z"


def test_infer_agent_tech_from_pipeline_model_alias() -> None:
    assert change_model._infer_agent_tech_from_model("pipeline") == "pipeline"
    assert change_model._infer_agent_tech_from_model("pipeline:gpt-5-codex") == "pipeline"


def test_infer_agent_tech_from_deepseek_model_alias() -> None:
    assert change_model._infer_agent_tech_from_model("deepseek-chat") == "deepseek"
    assert change_model._infer_agent_tech_from_model("deepseek-reasoner") == "deepseek"


def test_gather_available_models_includes_deepseek_candidates() -> None:
    models = change_model._gather_available_models({"runtime": {}})
    assert "gpt-5.4-mini" in models
    assert "gpt-5.4-nano" in models
    assert "deepseek-chat" in models
    assert "deepseek-reasoner" in models


def test_safe_update_job_with_model_updates_both_loop_stages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(change_model, "REPO_ROOT", tmp_path)
    sched.save_job(
        {
            "job_id": "build_verify_job",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Prompt.",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 3,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
                "verifier_runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            },
        },
        repo_root=tmp_path,
    )

    saved = change_model._safe_update_job("build_verify_job", model="gpt-5.4")
    assert saved["runtime"]["model"] == "gpt-5.4"
    assert saved["runtime"]["advisory_model"] == "gpt-5.4"
    assert saved["loop"]["builder_runtime"]["model"] == "gpt-5.4"
    assert saved["loop"]["builder_runtime"]["advisory_model"] == "gpt-5.4"
    assert saved["loop"]["verifier_runtime"]["model"] == "gpt-5.4"
    assert saved["loop"]["verifier_runtime"]["advisory_model"] == "gpt-5.4"


def test_safe_update_job_can_update_only_verifier_stage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(change_model, "REPO_ROOT", tmp_path)
    sched.save_job(
        {
            "job_id": "build_verify_job",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Prompt.",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 3,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {"agent_tech": "openai", "model": "gpt-5.4"},
                "verifier_runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
            },
        },
        repo_root=tmp_path,
    )

    saved = change_model._safe_update_job("build_verify_job", verifier_model="deepseek-chat")
    assert saved["runtime"]["model"] == "gpt-5.3-codex-spark"
    assert saved["loop"]["builder_runtime"]["model"] == "gpt-5.4"
    assert saved["loop"]["verifier_runtime"]["model"] == "deepseek-chat"
    assert saved["loop"]["verifier_runtime"]["agent_tech"] == "deepseek"
    assert saved["loop"]["verifier_runtime"]["advisory_model"] == "deepseek-chat"
    assert saved["loop"]["verifier_runtime"]["advisory_agent_tech"] == "deepseek"


def test_safe_update_job_clears_stale_runtime_routing_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(change_model, "REPO_ROOT", tmp_path)
    sched.save_job(
        {
            "job_id": "build_verify_job",
            "enabled": True,
            "job_kind": "build_verify_loop",
            "binding": {"type": "ledger_uid", "id": "PIS-20260326-203500Z-R7K2"},
            "prompt": "Prompt.",
            "schedule": {"mode": "once", "run_at": "2026-03-15T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "runtime": {
                "agent_tech": "codex",
                "model": "gpt-5.3-codex-spark",
                "fallback_models": ["gpt-5.2-codex"],
                "fallback_agent_techs": ["openai"],
                "tech_model_sequence": [{"agent_tech": "codex", "model": "gpt-5.3-codex-spark"}],
            },
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": 3,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": {
                    "agent_tech": "codex",
                    "model": "gpt-5.3-codex-spark",
                    "fallback_models": ["gpt-5.2-codex"],
                    "fallback_agent_techs": ["openai"],
                    "tech_model_sequence": [{"agent_tech": "codex", "model": "gpt-5.3-codex-spark"}],
                },
                "verifier_runtime": {
                    "agent_tech": "codex",
                    "model": "gpt-5.3-codex-spark",
                    "fallback_models": ["gpt-5.2-codex"],
                    "fallback_agent_techs": ["openai"],
                    "tech_model_sequence": [{"agent_tech": "codex", "model": "gpt-5.3-codex-spark"}],
                },
            },
        },
        repo_root=tmp_path,
    )

    saved = change_model._safe_update_job("build_verify_job", model="gpt-5.4")

    for runtime in (saved["runtime"], saved["loop"]["builder_runtime"], saved["loop"]["verifier_runtime"]):
        assert runtime["model"] == "gpt-5.4"
        assert runtime["fallback_models"] == []
        assert runtime["fallback_agent_techs"] == []
        assert runtime["tech_model_sequence"] == []
