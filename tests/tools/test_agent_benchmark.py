from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tools.benchmarks import agent_benchmark as bench


def test_validate_spec_requires_contender() -> None:
    with pytest.raises(bench.BenchmarkError):
        bench._validate_spec(
            {
                "binding": {"type": "task_id", "id": "mb3"},
                "objective": "x",
                "workflow": {"enabled": False},
                "swarm": {"enabled": False},
            }
        )


def test_tracked_diff_probe_spec_loads() -> None:
    spec = bench.load_benchmark_spec(Path("tools/benchmarks/specs/tracked_diff_probe.json"))

    assert spec["label"] == "tracked-diff-probe"
    assert spec["binding"] == {"type": "task_id", "id": "mb5"}
    assert "tracked file" in spec["objective"]


def test_tier1_manifest_and_specs_load() -> None:
    manifest = bench.load_benchmark_suite(Path("tools/benchmarks/specs/tier1_manifest.json"))

    assert manifest["label"] == "tier1-starter-pack"
    spec_paths = manifest["spec_paths"]
    assert len(spec_paths) == 12

    labels = []
    for raw_path in spec_paths:
        spec = bench.load_benchmark_spec(Path(raw_path))
        labels.append(spec["label"])

    assert labels[0] == "tier1-create-untracked-note"
    assert len(set(labels)) == len(labels)


def test_run_benchmark_suite_runs_each_spec_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run_benchmark(*, spec_path: Path) -> dict[str, object]:
        calls.append(str(spec_path))
        return {
            "run_id": f"run-{len(calls)}",
            "status": "completed",
            "summary_path": f"/tmp/summary-{len(calls)}.md",
            "result_path": f"/tmp/result-{len(calls)}.json",
            "contenders": {
                "workflow": {"ok": True},
                "swarm": {"ok": True},
            },
        }

    monkeypatch.setattr(bench, "run_benchmark", fake_run_benchmark)

    payload = bench.run_benchmark_suite(suite_path=Path("tools/benchmarks/specs/tier1_manifest.json"))

    assert payload["schema_version"] == bench.SCHEMA_SUITE_RESULT
    assert payload["spec_count"] == 12
    assert payload["ok_count"] == 12
    assert payload["failed_count"] == 0
    assert len(calls) == 12


def test_main_suite_returns_failure_when_any_run_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        bench,
        "run_benchmark_suite",
        lambda *, suite_path: {
            "schema_version": bench.SCHEMA_SUITE_RESULT,
            "suite_label": "tier1-starter-pack",
            "suite_path": str(suite_path),
            "status": "completed_with_failures",
            "spec_count": 2,
            "ok_count": 1,
            "failed_count": 1,
            "runs": [],
        },
    )

    code = bench.main(["--suite", "tools/benchmarks/specs/tier1_manifest.json"])

    captured = capsys.readouterr()
    assert code == 1
    assert '"failed_count": 1' in captured.out


def test_summarize_benchmark_result_summary_counts_appends_totals_line() -> None:
    summary = bench.summarize_benchmark_result(
        {
            "run_id": "bench_123",
            "status": "completed_with_failures",
            "summary_path": "/tmp/summary.md",
            "contenders": {
                "workflow": {"ok": True, "metrics": {"status": "completed", "elapsed_s": 12.5, "checks_failed": 0}},
                "swarm": {
                    "ok": False,
                    "metrics": {"status": "failed", "elapsed_s": 8.0, "checks_failed": 2},
                    "error": "swarm failed fast",
                },
            },
        }
    )

    assert summary.splitlines()[-1] == "Totals: ok=1 failed=1"
    assert "Report: /tmp/summary.md\nTotals: ok=1 failed=1" in summary


def test_summarize_benchmark_result_summary_counts_handles_empty_contenders() -> None:
    summary = bench.summarize_benchmark_result(
        {"run_id": "bench_empty", "status": "completed", "contenders": {}}
    )

    assert summary.splitlines()[-1] == "Totals: ok=0 failed=0"


def test_write_summary_markdown_includes_checks_passed_for_each_contender(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.md"

    bench._write_summary_markdown(
        path=summary_path,
        result={
            "label": "Agent benchmark",
            "git_sha": "deadbeef",
            "git_ref": "main",
            "binding_key": "task:mb3",
            "contenders": {
                "workflow": {
                    "ok": True,
                    "runtime_ok": True,
                    "runtime_id": "wf-1",
                    "metrics": {
                        "status": "completed",
                        "elapsed_s": 12.5,
                        "event_count": 7,
                        "checks_passed": 3,
                        "checks_failed": 1,
                    },
                },
                "swarm": {
                    "ok": False,
                    "runtime_ok": False,
                    "runtime_id": "sw-1",
                    "metrics": {
                        "status": "failed",
                        "elapsed_s": 8.0,
                        "event_count": 5,
                        "checks_passed": 2,
                        "checks_failed": 4,
                    },
                },
            },
        },
    )

    summary = summary_path.read_text(encoding="utf-8")

    assert summary.count("- checks_passed: ") == 2
    assert "- checks_passed: 3" in summary
    assert "- checks_passed: 2" in summary


def test_write_summary_markdown_defaults_missing_checks_passed_to_zero(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.md"

    bench._write_summary_markdown(
        path=summary_path,
        result={
            "label": "Agent benchmark",
            "git_sha": "deadbeef",
            "git_ref": "main",
            "binding_key": "task:mb3",
            "contenders": {
                "workflow": {
                    "ok": True,
                    "runtime_ok": True,
                    "runtime_id": "wf-1",
                    "metrics": {
                        "status": "completed",
                        "elapsed_s": 12.5,
                        "event_count": 7,
                        "checks_failed": 1,
                    },
                }
            },
        },
    )

    summary = summary_path.read_text(encoding="utf-8")

    assert "- checks_passed: 0" in summary
    assert "- checks_passed: None" not in summary


def test_annotate_contender_outcome_preserves_runtime_failure_when_patch_was_delivered(tmp_path: Path) -> None:
    contender_dir = tmp_path / "workflow"
    exported = bench._empty_exported_changes_payload(contender_dir=contender_dir)
    Path(str(exported["patch_path"])).write_text("diff --git a/foo b/foo\n", encoding="utf-8")
    payload = {
        "mode": "workflow",
        "ok": False,
        "metrics": {"status": "failed", "checks_failed": 0},
        "exported_changes": exported,
    }

    annotated = bench._annotate_contender_outcome(payload)

    assert annotated["ok"] is False
    assert annotated["runtime_ok"] is False
    assert annotated["metrics"]["delivered_code"] is True
    assert annotated["metrics"]["delivery_patch_bytes"] > 0


def test_synthesize_swarm_exported_changes_from_runtime_uses_integration_artifacts(tmp_path: Path) -> None:
    contender_dir = tmp_path / "swarm"
    exported = bench._empty_exported_changes_payload(contender_dir=contender_dir)
    artifacts_root = contender_dir / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    integration_patch = artifacts_root / "integration.patch"
    integration_patch.write_text("diff --git a/BENCHMARK_SMOKE_NOTE.txt b/BENCHMARK_SMOKE_NOTE.txt\n", encoding="utf-8")
    runtime_state = contender_dir / "runtime_state.json"
    runtime_state.write_text(
        json.dumps(
            {
                "integration": {
                    "changed_files": ["BENCHMARK_SMOKE_NOTE.txt"],
                }
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "mode": "swarm",
        "ok": True,
        "exported_changes": exported,
        "copied_runtime": {
            "state_path": str(runtime_state),
            "artifacts_root": str(artifacts_root),
        },
    }

    synthesized = bench._synthesize_swarm_exported_changes_from_runtime(payload=payload, contender_dir=contender_dir)

    assert synthesized["exported_changes"]["tracked_changed"] == ["BENCHMARK_SMOKE_NOTE.txt"]
    assert Path(str(synthesized["exported_changes"]["patch_path"])).read_text(encoding="utf-8").startswith("diff --git")
    annotated = bench._annotate_contender_outcome(synthesized)
    assert annotated["metrics"]["delivered_code"] is True
    assert annotated["delivery"]["changed_path_count"] == 1


def test_summarize_benchmark_result_reports_runtime_ok_when_delivery_differs() -> None:
    summary = bench.summarize_benchmark_result(
        {
            "run_id": "bench_123",
            "status": "completed",
            "contenders": {
                "workflow": {
                    "ok": True,
                    "runtime_ok": False,
                    "metrics": {
                        "status": "failed",
                        "elapsed_s": 12.5,
                        "checks_failed": 0,
                        "delivered_code": True,
                    },
                    "error": "workflow runtime hit evidence_gap",
                },
            },
        }
    )

    assert "delivered_code=yes" in summary
    assert "score=1" in summary
    assert "runtime_ok=false" in summary


def test_annotate_contender_outcome_prefers_persisted_terminal_runtime_state(tmp_path: Path) -> None:
    contender_dir = tmp_path / "workflow"
    contender_dir.mkdir(parents=True, exist_ok=True)
    runtime_state = contender_dir / "runtime_state.json"
    runtime_state.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    payload = {
        "mode": "workflow",
        "ok": True,
        "metrics": {"status": "completed"},
        "copied_runtime": {"state_path": str(runtime_state)},
    }

    annotated = bench._annotate_contender_outcome(payload)

    assert annotated["ok"] is False
    assert annotated["runtime_ok"] is False
    assert annotated["metrics"]["runtime_status"] == "failed"
    assert annotated["metrics"]["runtime_outcome_source"].startswith("persisted_terminal:")


def test_comparison_summary_rewards_delivery_when_runtime_is_equal(tmp_path: Path) -> None:
    contender_dir = tmp_path / "workflow"
    exported = bench._empty_exported_changes_payload(contender_dir=contender_dir)
    Path(str(exported["patch_path"])).write_text("diff --git a/foo b/foo\n", encoding="utf-8")
    workflow = bench._annotate_contender_outcome(
        {
            "mode": "workflow",
            "ok": True,
            "metrics": {"status": "completed", "checks_failed": 0, "elapsed_s": 4.0},
            "exported_changes": exported,
        }
    )
    swarm = bench._annotate_contender_outcome(
        {
            "mode": "swarm",
            "ok": True,
            "metrics": {"status": "completed", "checks_failed": 0, "elapsed_s": 4.0},
            "exported_changes": {},
        }
    )

    comparison = bench._comparison_summary({"workflow": workflow, "swarm": swarm})

    assert comparison["winner"] == "workflow"
    assert comparison["contenders"]["workflow"]["score"] == 3
    assert comparison["contenders"]["swarm"]["score"] == 2


def test_run_benchmark_from_spec_creates_comparison_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_resolve_git_ref", lambda _ref: "deadbeef")

    created_worktrees: list[Path] = []
    removed_worktrees: list[Path] = []
    exported_worktrees: list[Path] = []

    def _fake_create_worktree(*, git_sha: str, run_id: str, mode: str) -> Path:
        path = tmp_path / "playground" / run_id / mode
        path.mkdir(parents=True, exist_ok=True)
        created_worktrees.append(path)
        return path

    def _fake_remove_worktree(path: Path) -> dict:
        removed_worktrees.append(path)
        return {"ok": True, "path": str(path)}

    monkeypatch.setattr(bench, "_create_worktree", _fake_create_worktree)
    monkeypatch.setattr(bench, "_create_baseline_commit", lambda *, worktree, message: None)
    monkeypatch.setattr(
        bench,
        "_capture_repo_overlay",
        lambda: {
            "tracked_patch": "",
            "tracked_dirty": False,
            "untracked_files": [],
            "untracked_count": 0,
            "untracked_skipped_count": 0,
        },
    )
    monkeypatch.setattr(bench, "_remove_worktree", _fake_remove_worktree)
    monkeypatch.setattr(
        bench,
        "_capture_worktree_exports",
        lambda *, worktree, contender_dir: exported_worktrees.append(worktree)
        or {
            "patch_path": str((contender_dir / "exported_changes" / "final_patch.diff").resolve()),
            "status_path": str((contender_dir / "exported_changes" / "git_status.txt").resolve()),
            "manifest_path": str((contender_dir / "exported_changes" / "manifest.json").resolve()),
            "snapshot_root": str((contender_dir / "exported_changes" / "files").resolve()),
            "copied_file_count": 1,
            "copied_files": ["tools/example.py"],
            "deleted_files": [],
            "tracked_changed": ["tools/example.py"],
            "untracked_files": [],
            "errors": [],
        },
    )

    def _fake_guarded(*, mode: str, worktree: Path, contender_dir: Path, spec: dict) -> dict:
        if mode == "workflow":
            return {
                "mode": "workflow",
                "ok": True,
                "runtime_id": "wf-1",
                "metrics": {"status": "completed", "turn": "done", "elapsed_s": 12.5, "event_count": 1, "checks_failed": 1},
                "checks": {"rows": [{"ok": True}, {"ok": False}], "summary": {"total": 2, "passed": 1, "failed": 1}},
                "copied_runtime": {
                    "state_path": str((contender_dir / "runtime_state.json").resolve()),
                    "events_path": str((contender_dir / "runtime_events.jsonl").resolve()),
                    "artifacts_root": str((contender_dir / "artifacts").resolve()),
                },
            }
        return {
            "mode": "swarm",
            "ok": True,
            "runtime_id": "sw-1",
            "metrics": {"status": "finished", "phase": "completed", "elapsed_s": 8.0, "event_count": 1, "checks_failed": 1},
            "checks": {"rows": [{"ok": True}, {"ok": False}], "summary": {"total": 2, "passed": 1, "failed": 1}},
            "copied_runtime": {
                "state_path": str((contender_dir / "runtime_state.json").resolve()),
                "events_path": str((contender_dir / "runtime_events.jsonl").resolve()),
                "artifacts_root": str((contender_dir / "artifacts").resolve()),
            },
        }

    monkeypatch.setattr(bench, "_run_contender_guarded", _fake_guarded)

    spec = bench._validate_spec(
        {
            "binding": {"type": "task_id", "id": "mb3"},
            "objective": "Compare runtimes",
            "check_commands": ["echo ok"],
            "cleanup_worktrees": False,
            "workflow": {"enabled": True, "phase_label": "Benchmark", "options": {}, "max_steps": 50},
            "swarm": {"enabled": True, "options": {}},
        }
    )

    result = bench.run_benchmark_from_spec(spec=spec)

    assert result["git_sha"] == "deadbeef"
    assert sorted(result["contenders"].keys()) == ["swarm", "workflow"]
    assert len(created_worktrees) == 2
    assert len(removed_worktrees) == 2
    assert len(exported_worktrees) == 2

    run_dir = runtime_root / "runs" / result["run_id"]
    assert (run_dir / "workflow" / "result.json").exists()
    assert (run_dir / "swarm" / "result.json").exists()
    assert (run_dir / "summary.md").exists()
    workflow_payload = json.loads((run_dir / "workflow" / "result.json").read_text(encoding="utf-8"))
    assert workflow_payload["metrics"]["checks_failed"] == 1
    assert workflow_payload["copied_runtime"]["artifacts_root"].endswith("/workflow/artifacts")
    assert workflow_payload["exported_changes"]["patch_path"].endswith("/workflow/exported_changes/final_patch.diff")
    assert "comparison" in result
    assert result["status"] == "completed"
    assert result["summary_path"].endswith("/summary.md")
    assert result["result_path"].endswith("/result.json")


def test_capture_repo_overlay_excludes_runtime_residue_from_git_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, list[list[str]]] = {}

    def _fake_run_git(args: list[str], *, cwd: Path | None = None, timeout_s: int = 180):
        seen.setdefault("calls", []).append(list(args))
        if args[:3] == ["ls-files", "--others", "--exclude-standard"]:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="notes/local.txt\nruntime/state/keepout.txt\n", stderr="")
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(bench, "_run_git", _fake_run_git)

    payload = bench._capture_repo_overlay()

    assert payload["tracked_dirty"] is False
    assert payload["untracked_files"] == ["notes/local.txt"]
    assert payload["untracked_count"] == 1
    assert payload["untracked_skipped_count"] == 1
    diff_args = seen["calls"][0]
    assert diff_args[:3] == ["diff", "--binary", "HEAD"]
    assert ":(exclude)runtime/state" in diff_args
    assert ":(exclude)runtime/state/**" in diff_args
    assert ":(exclude)governance/agents/runtime" in diff_args
    assert ":(exclude)governance/agents/sessions" in diff_args
    assert ":(exclude)commit journal error: no pending commit journal entries.message.txt" in diff_args


def test_apply_overlay_to_worktree_uses_nowarn_for_patch_whitespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = tmp_path / "sandbox"
    worktree.mkdir(parents=True, exist_ok=True)
    seen: dict[str, list[str]] = {}

    def _fake_run_git(args: list[str], *, cwd: Path | None = None, timeout_s: int = 180):
        seen["args"] = list(args)
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(bench, "_run_git", _fake_run_git)

    bench._apply_overlay_to_worktree(
        worktree=worktree,
        overlay={"tracked_patch": "diff --git a/x b/x\n", "untracked_files": []},
    )

    assert seen["args"][:3] == ["apply", "--binary", "--whitespace=nowarn"]


def test_apply_overlay_to_worktree_copies_untracked_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path / "repo"
    worktree = tmp_path / "sandbox"
    repo_root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)
    source = repo_root / "notes" / "draft.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("draft\n", encoding="utf-8")
    monkeypatch.setattr(bench, "_repo_root", lambda: repo_root)

    bench._apply_overlay_to_worktree(
        worktree=worktree,
        overlay={"tracked_patch": "", "untracked_files": ["notes/draft.txt"]},
    )

    copied = worktree / "notes" / "draft.txt"
    assert copied.exists()
    assert copied.read_text(encoding="utf-8") == "draft\n"


def test_configure_local_codex_env_defaults_sets_turn_cmd_when_codex_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EIEL_AGENTS_BACKEND", raising=False)
    monkeypatch.delenv("EIEL_CODEX_PROTOCOL", raising=False)
    monkeypatch.delenv("EIEL_CODEX_TURN_CMD", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(bench.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)

    bench._configure_local_codex_env_defaults(repo_root=Path("/tmp/repo"))

    assert os.environ["EIEL_AGENTS_BACKEND"] == "codex"
    assert os.environ["EIEL_CODEX_PROTOCOL"] == "oneshot"
    assert "--skip-git-repo-check -" in os.environ["EIEL_CODEX_TURN_CMD"]
    assert "exec -C /tmp/repo" in os.environ["EIEL_CODEX_TURN_CMD"]


def test_configure_local_codex_env_defaults_force_turn_cmd_retargets_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "codex exec -C /tmp/original --skip-git-repo-check -")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(bench.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)

    bench._configure_local_codex_env_defaults(repo_root=Path("/tmp/worktree"), force_turn_cmd=True)

    assert "exec -C /tmp/worktree" in os.environ["EIEL_CODEX_TURN_CMD"]
    assert "/tmp/original" not in os.environ["EIEL_CODEX_TURN_CMD"]


def test_run_benchmark_from_spec_runs_contenders_in_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_resolve_git_ref", lambda _ref: "deadbeef")
    monkeypatch.setattr(
        bench,
        "_create_worktree",
        lambda *, git_sha, run_id, mode: (tmp_path / "playground" / run_id / mode).resolve(),
    )
    monkeypatch.setattr(bench, "_sanitize_benchmark_worktree", lambda _worktree: {"removed": [], "errors": []})
    monkeypatch.setattr(
        bench,
        "_capture_worktree_exports",
        lambda *, worktree, contender_dir: {
            "patch_path": str((contender_dir / "exported_changes" / "final_patch.diff").resolve()),
            "status_path": str((contender_dir / "exported_changes" / "git_status.txt").resolve()),
            "manifest_path": str((contender_dir / "exported_changes" / "manifest.json").resolve()),
            "snapshot_root": str((contender_dir / "exported_changes" / "files").resolve()),
            "copied_file_count": 0,
            "copied_files": [],
            "deleted_files": [],
            "tracked_changed": [],
            "untracked_files": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(bench, "_remove_worktree", lambda path: {"ok": True, "path": str(path), "removed_by": "git", "error": ""})

    active_count = 0
    max_active = 0
    barrier = threading.Barrier(2, timeout=1.0)
    guard = threading.Lock()

    def _fake_guarded(*, mode: str, worktree: Path, contender_dir: Path, spec: dict) -> dict:
        nonlocal active_count, max_active
        with guard:
            active_count += 1
            max_active = max(max_active, active_count)
        barrier.wait()
        time.sleep(0.05)
        with guard:
            active_count -= 1
        return {
            "mode": mode,
            "ok": True,
            "runtime_id": f"{mode}-1",
            "metrics": {"status": "completed", "elapsed_s": 1.0, "checks_failed": 0},
            "checks": {"rows": [], "summary": {"total": 0, "passed": 0, "failed": 0}},
            "copied_runtime": {"state_path": "", "events_path": "", "artifacts_root": ""},
        }

    monkeypatch.setattr(bench, "_run_contender_guarded", _fake_guarded)

    spec = bench.create_benchmark_spec(
        objective="Compare runtimes",
        binding={"type": "task_id", "id": "mb3"},
    )
    result = bench.run_benchmark_from_spec(spec=spec)

    assert result["status"] == "completed"
    assert max_active == 2


def test_remove_worktree_defers_cleanup_after_sync_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmp_path / "playground" / "workflow"
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bench, "_try_remove_worktree_sync", lambda path, timeout_s: None)
    monkeypatch.setattr(
        bench,
        "_schedule_worktree_cleanup",
        lambda path: {
            "ok": False,
            "path": str(path),
            "error": "cleanup deferred after synchronous cleanup timed out",
            "removed_by": "deferred_child",
            "deferred": True,
            "runner_pid": 12345,
        },
    )

    payload = bench._remove_worktree(worktree)

    assert payload["removed_by"] == "deferred_child"
    assert payload["deferred"] is True
    assert payload["runner_pid"] == 12345


def test_prune_stale_worktrees_skips_running_and_removes_old_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    worktrees_root = runtime_root / "worktrees"
    runs_root = runtime_root / "runs"
    running_dir = worktrees_root / "bench_running__workflow"
    stale_dir = worktrees_root / "bench_done__workflow"
    running_dir.mkdir(parents=True, exist_ok=True)
    stale_dir.mkdir(parents=True, exist_ok=True)
    (runs_root / "bench_running").mkdir(parents=True, exist_ok=True)
    (runs_root / "bench_done").mkdir(parents=True, exist_ok=True)
    (runs_root / "bench_running" / "result.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    (runs_root / "bench_done" / "result.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    old = time.time() - 900
    os.utime(stale_dir, (old, old))
    os.utime(running_dir, (old, old))
    removed: list[Path] = []

    def _fake_cleanup(path: Path) -> dict:
        removed.append(path)
        return {"ok": True, "path": str(path), "removed_by": "filesystem"}

    monkeypatch.setattr(bench, "_cleanup_worktree_now", _fake_cleanup)

    summary = bench._prune_stale_worktrees(min_age_s=300)

    assert removed == [stale_dir.resolve()]
    assert any(row["reason"] == "running" for row in summary["skipped"])


def test_create_worktree_fails_fast_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_benchmark_worktree_create_timeout_s", lambda: 12)

    def _fake_run_git(args: list[str], *, cwd: Path | None = None, timeout_s: int = 180):
        if args[:2] == ["archive", "--format=tar"]:
            raise subprocess.TimeoutExpired(cmd=["git", *args], timeout=timeout_s)
        raise AssertionError(args)

    monkeypatch.setattr(bench, "_run_git", _fake_run_git)

    with pytest.raises(bench.BenchmarkError, match=r"git archive timed out for workflow after 12s"):
        bench._create_worktree(git_sha="deadbeef", run_id="bench_timeout", mode="workflow")


def test_create_worktree_archives_filtered_tree_without_runtime_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    seen: dict[str, list[str]] = {}

    def _fake_run_git(args: list[str], *, cwd: Path | None = None, timeout_s: int = 180):
        if args[:2] == ["archive", "--format=tar"]:
            seen["archive_args"] = list(args)
            output = next(part.split("=", 1)[1] for part in args if part.startswith("--output="))
            Path(output).write_bytes(b"")
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        if args == ["init", "-q"]:
            seen["init_args"] = list(args)
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        raise AssertionError(args)

    class _EmptyTar:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extractall(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bench, "_run_git", _fake_run_git)
    monkeypatch.setattr(bench.tarfile, "open", lambda *args, **kwargs: _EmptyTar())

    worktree = bench._create_worktree(git_sha="deadbeef", run_id="bench_filtered", mode="workflow")

    assert worktree.exists()
    assert seen["archive_args"][:4] == ["archive", "--format=tar", f"--output={worktree.parent / '.bench_filtered__workflow.tar'}", "deadbeef"]
    assert ":(exclude)runtime/state" in seen["archive_args"]
    assert ":(exclude)governance/agents/runtime" in seen["archive_args"]
    assert ":(exclude)governance/agents/sessions" in seen["archive_args"]


def test_run_benchmark_from_spec_continues_when_cleanup_is_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_resolve_git_ref", lambda _ref: "deadbeef")

    created_worktrees: list[Path] = []

    def _fake_create_worktree(*, git_sha: str, run_id: str, mode: str) -> Path:
        path = tmp_path / "playground" / run_id / mode
        path.mkdir(parents=True, exist_ok=True)
        created_worktrees.append(path)
        return path

    monkeypatch.setattr(bench, "_create_worktree", _fake_create_worktree)
    monkeypatch.setattr(
        bench,
        "_remove_worktree",
        lambda path: {
            "ok": False,
            "path": str(path),
            "error": "cleanup deferred after synchronous cleanup timed out",
            "removed_by": "deferred_child",
            "deferred": True,
            "runner_pid": 8888,
        },
    )
    monkeypatch.setattr(
        bench,
        "_capture_worktree_exports",
        lambda *, worktree, contender_dir: {
            "patch_path": str((contender_dir / "exported_changes" / "final_patch.diff").resolve()),
            "status_path": str((contender_dir / "exported_changes" / "git_status.txt").resolve()),
            "manifest_path": str((contender_dir / "exported_changes" / "manifest.json").resolve()),
            "snapshot_root": str((contender_dir / "exported_changes" / "files").resolve()),
            "copied_file_count": 0,
            "copied_files": [],
            "deleted_files": [],
            "tracked_changed": [],
            "untracked_files": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(
        bench,
        "_run_contender_guarded",
        lambda *, mode, worktree, contender_dir, spec: {
            "mode": mode,
            "ok": mode == "swarm",
            "runtime_id": f"{mode}-1",
            "metrics": {"status": "completed" if mode == "swarm" else "failed", "elapsed_s": 1.0, "checks_failed": 0},
            "checks": {"rows": [], "summary": {"total": 0, "passed": 0, "failed": 0}},
            "copied_runtime": {"state_path": "", "events_path": "", "artifacts_root": ""},
            **({"error": "workflow failed fast"} if mode == "workflow" else {}),
        },
    )

    spec = bench.create_benchmark_spec(
        objective="Compare runtimes",
        binding={"type": "task_id", "id": "mb3"},
    )
    result = bench.run_benchmark_from_spec(spec=spec)

    assert len(created_worktrees) == 2
    assert sorted(result["contenders"].keys()) == ["swarm", "workflow"]
    assert result["cleanup"]["workflow"]["cleanup"]["removed_by"] == "deferred_child"
    assert result["cleanup"]["swarm"]["cleanup"]["removed_by"] == "deferred_child"
    assert (runtime_root / "runs" / result["run_id"] / "summary.md").exists()


def test_run_benchmark_from_spec_exports_before_deferred_cleanup_after_contender_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_resolve_git_ref", lambda _ref: "deadbeef")

    def _fake_create_worktree(*, git_sha: str, run_id: str, mode: str) -> Path:
        path = tmp_path / "playground" / run_id / mode
        (path / "tools").mkdir(parents=True, exist_ok=True)
        (path / "notes").mkdir(parents=True, exist_ok=True)
        (path / "tools" / "changed.py").write_text("print('changed')\n", encoding="utf-8")
        (path / "notes" / "draft.txt").write_text("draft\n", encoding="utf-8")
        return path

    def _fake_run_git(args: list[str], *, cwd: Path | None = None, timeout_s: int = 180):
        if args[:4] == ["diff", "--binary", "HEAD", "--"]:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="diff --git a/tools/changed.py b/tools/changed.py\n", stderr="")
        if args == ["diff", "--name-only", "HEAD", "--diff-filter=ACMRTUXB", "--"]:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="tools/changed.py\n", stderr="")
        if args == ["diff", "--name-only", "HEAD", "--diff-filter=D", "--"]:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="docs/removed.md\n", stderr="")
        if args == ["ls-files", "--others", "--exclude-standard"]:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="notes/draft.txt\n", stderr="")
        raise AssertionError(args)

    def _fake_remove_worktree(path: Path) -> dict:
        export_roots = list(runtime_root.glob("runs/*/workflow/exported_changes"))
        assert len(export_roots) == 1
        export_dir = export_roots[0]
        assert (export_dir / "final_patch.diff").exists()
        assert (export_dir / "git_status.txt").exists()
        assert (export_dir / "manifest.json").exists()
        assert (export_dir / "files").is_dir()
        return {
            "ok": False,
            "path": str(path),
            "error": "cleanup deferred after synchronous cleanup timed out",
            "removed_by": "deferred_child",
            "deferred": True,
            "runner_pid": 4444,
        }

    monkeypatch.setattr(bench, "_create_worktree", _fake_create_worktree)
    monkeypatch.setattr(bench, "_run_git", _fake_run_git)
    monkeypatch.setattr(bench, "_remove_worktree", _fake_remove_worktree)
    monkeypatch.setattr(
        bench,
        "_run_contender_guarded",
        lambda *, mode, worktree, contender_dir, spec: {
            "mode": mode,
            "ok": False,
            "runtime_id": "wf-1",
            "metrics": {"status": "failed", "elapsed_s": 0.1, "checks_failed": 0},
            "checks": {"rows": [], "summary": {"total": 0, "passed": 0, "failed": 0}},
            "copied_runtime": {"state_path": "", "events_path": "", "artifacts_root": ""},
            "error": "workflow failed fast",
        },
    )

    spec = bench.create_benchmark_spec(
        objective="Compare runtimes",
        binding={"type": "task_id", "id": "mb3"},
        workflow_enabled=True,
        swarm_enabled=False,
    )
    result = bench.run_benchmark_from_spec(spec=spec)

    export_dir = runtime_root / "runs" / result["run_id"] / "workflow" / "exported_changes"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

    assert result["cleanup"]["workflow"]["cleanup"]["removed_by"] == "deferred_child"
    assert result["contenders"]["workflow"]["ok"] is False
    assert result["contenders"]["workflow"]["exported_changes"]["patch_path"].endswith("/workflow/exported_changes/final_patch.diff")
    assert manifest["tracked_changed"] == ["tools/changed.py"]
    assert manifest["untracked"] == ["notes/draft.txt"]
    assert manifest["deleted"] == ["docs/removed.md"]


def test_run_contender_returns_failed_payload_on_execution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "_execute_workflow", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("workflow blew up")))
    monkeypatch.setattr(
        bench,
        "_run_check_commands",
        lambda **_kwargs: {"rows": [], "summary": {"total": 0, "passed": 0, "failed": 0}},
    )

    worktree = tmp_path / "workflow"
    contender_dir = tmp_path / "artifacts"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert "workflow blew up" in payload["error"]
    assert payload["checks"]["summary"]["failed"] == 0


def test_run_contender_marks_check_failures_as_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bench,
        "_execute_workflow",
        lambda **_kwargs: {
            "ok": True,
            "runtime_id": "wf-1",
            "runtime_paths": {},
            "result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        bench,
        "_run_check_commands",
        lambda **_kwargs: {
            "rows": [{"command": "python -c 'raise SystemExit(1)'", "ok": False, "exit_code": 1}],
            "summary": {"total": 1, "passed": 0, "failed": 1},
        },
    )
    monkeypatch.setattr(
        bench,
        "_copy_runtime_artifacts",
        lambda **_kwargs: {"state_path": "", "events_path": "", "artifacts_root": ""},
    )
    monkeypatch.setattr(
        bench,
        "_workflow_metrics",
        lambda contender, checks: {
            "status": "completed",
            "elapsed_s": 1.0,
            "checks_failed": int((checks.get("summary") or {}).get("failed") or 0),
        },
    )

    worktree = tmp_path / "workflow"
    contender_dir = tmp_path / "artifacts"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": ["python -c 'raise SystemExit(1)'"]},
    )

    assert payload["ok"] is False
    assert payload["error"] == "workflow benchmark check commands failed (1)"
    assert payload["metrics"]["checks_failed"] == 1


def test_run_contender_guarded_returns_failed_payload_when_child_exits_without_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 42)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.pid = 123456
            self.returncode = 17

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    worktree = tmp_path / "workflow"
    contender_dir = tmp_path / "run" / "workflow"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    (contender_dir / "runner.stdout.txt").write_text("child stdout\n", encoding="utf-8")
    (contender_dir / "runner.stderr.txt").write_text("child stderr\n", encoding="utf-8")

    payload = bench._run_contender_guarded(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert "workflow contender subprocess exited before writing a result" in payload["error"]
    assert payload["runner"]["exit_code"] == 17
    assert payload["runner"]["stdout_path"].endswith("/runner.stdout.txt")
    assert payload["runner"]["stderr_path"].endswith("/runner.stderr.txt")


def test_run_contender_guarded_returns_failed_payload_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 41)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.pid = 223344
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 0.0, 42.0])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values, 20.0))
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)

    worktree = tmp_path / "swarm"
    contender_dir = tmp_path / "run" / "swarm"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="swarm",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert payload["error"] == "swarm contender timed out after 41s"
    assert payload["runner"]["timed_out"] is True
    assert payload["runner"]["pid"] == 223344


def test_run_contender_guarded_returns_failed_payload_on_d_state_stall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 120)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 998877
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench, "_pid_process_state", lambda _pid: "D")
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 0.0, 1.0, 10.0, 20.0])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values, 20.0))
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)

    worktree = tmp_path / "workflow"
    contender_dir = tmp_path / "run" / "workflow"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert payload["error"] == "workflow contender stalled in D state for 15s"
    assert payload["runner"]["stalled"] is True
    assert payload["runner"]["process_state"] == "D"


def test_run_contender_guarded_fails_when_runtime_never_publishes_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 120)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 112233
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench, "_runtime_progress_snapshot", lambda **_kwargs: {})
    monkeypatch.setattr(bench, "_pid_process_state", lambda _pid: "")
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 0.0, 16.0])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)

    worktree = tmp_path / "workflow"
    contender_dir = tmp_path / "run" / "workflow"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert payload["error"] == "workflow contender did not publish runtime progress within 15s"
    assert payload["runner"]["stalled"] is True


def test_run_contender_guarded_fails_when_runtime_progress_stops_advancing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 120)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 445566
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    snapshot = {
        "runtime_id": "wf-stalled",
        "phase": "builder_run",
        "marker": "same-progress",
        "status": "running",
        "status_detail": "running_active",
        "updated_utc": "2026-03-12T08:42:25Z",
        "last_progress_utc": "2026-03-12T08:42:25Z",
        "active_deadline_utc": "",
    }

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench, "_runtime_progress_snapshot", lambda **_kwargs: dict(snapshot))
    monkeypatch.setattr(bench, "_pid_process_state", lambda _pid: "")
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 0.0, 1.0, 10.0, 20.0])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values, 20.0))
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)

    worktree = tmp_path / "workflow"
    contender_dir = tmp_path / "run" / "workflow"
    worktree.mkdir(parents=True, exist_ok=True)
    contender_dir.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert payload["error"] == "workflow contender made no runtime progress for 15s (wf-stalled, phase=builder_run)"
    assert payload["runner"]["stalled"] is True
    assert payload["runner"]["runtime_progress"]["runtime_id"] == "wf-stalled"


def test_run_contender_guarded_fails_when_swarm_progress_snapshot_stalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 120)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 556677
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    snapshot = {
        "runtime_id": "sw-stalled",
        "phase": "makers",
        "marker": "same-sw-progress",
        "status": "running",
        "status_detail": "",
        "updated_utc": "2026-03-12T09:46:25Z",
        "last_progress_utc": "2026-03-12T09:46:25Z",
        "active_deadline_utc": "2026-03-12T10:44:05Z",
    }

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench, "_runtime_progress_snapshot", lambda **_kwargs: dict(snapshot))
    monkeypatch.setattr(bench, "_pid_process_state", lambda _pid: "")
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 0.0, 1.0, 10.0, 20.0])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values, 20.0))

    contender_dir = tmp_path / "run" / "swarm"
    worktree = tmp_path / "worktree"
    contender_dir.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="swarm",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    assert payload["ok"] is False
    assert payload["error"] == "swarm contender made no runtime progress for 15s (sw-stalled, phase=makers)"
    assert payload["runner"]["stalled"] is True
    assert payload["runner"]["runtime_progress"]["runtime_id"] == "sw-stalled"


def test_run_benchmark_from_spec_persists_contender_failure_when_worktree_create_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_resolve_git_ref", lambda _ref: "deadbeef")

    def _fake_create_worktree(*, git_sha: str, run_id: str, mode: str) -> Path:
        if mode == "workflow":
            raise SystemExit("workflow setup aborted")
        path = tmp_path / "playground" / run_id / mode
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(bench, "_create_worktree", _fake_create_worktree)
    monkeypatch.setattr(bench, "_remove_worktree", lambda path: {"ok": True, "path": str(path), "removed_by": "git", "error": ""})
    monkeypatch.setattr(
        bench,
        "_capture_worktree_exports",
        lambda *, worktree, contender_dir: {
            "patch_path": str((contender_dir / "exported_changes" / "final_patch.diff").resolve()),
            "status_path": str((contender_dir / "exported_changes" / "git_status.txt").resolve()),
            "manifest_path": str((contender_dir / "exported_changes" / "manifest.json").resolve()),
            "snapshot_root": str((contender_dir / "exported_changes" / "files").resolve()),
            "copied_file_count": 0,
            "copied_files": [],
            "deleted_files": [],
            "tracked_changed": [],
            "untracked_files": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(
        bench,
        "_run_contender_guarded",
        lambda *, mode, worktree, contender_dir, spec: {
            "mode": mode,
            "runtime_id": f"{mode}-1",
            "ok": mode == "swarm",
            "metrics": {"status": "completed" if mode == "swarm" else "failed", "elapsed_s": 1.0, "checks_failed": 0},
            "checks": {"rows": [], "summary": {"total": 0, "passed": 0, "failed": 0}},
            "copied_runtime": {"state_path": "", "events_path": "", "artifacts_root": ""},
            **({"error": "workflow contender returned not ok"} if mode == "workflow" else {}),
        },
    )

    spec = bench._validate_spec(
        {
            "binding": {"type": "task_id", "id": "mb3"},
            "objective": "Compare runtimes",
            "workflow": {"enabled": True, "phase_label": "Benchmark", "options": {}, "max_steps": 50},
            "swarm": {"enabled": True, "options": {}},
        }
    )

    result = bench.run_benchmark_from_spec(spec=spec)

    run_dir = runtime_root / "runs" / result["run_id"]
    workflow_payload = json.loads((run_dir / "workflow" / "result.json").read_text(encoding="utf-8"))
    top_level = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

    assert workflow_payload["ok"] is False
    assert workflow_payload["error"] == "workflow setup aborted"
    assert top_level["status"] == "completed_with_failures"
    assert top_level["contenders"]["workflow"]["error"] == "workflow setup aborted"
    assert (run_dir / "swarm" / "result.json").exists()


def test_create_benchmark_spec_uses_simple_defaults() -> None:
    spec = bench.create_benchmark_spec(
        objective="Compare runtimes",
        binding={"type": "task_id", "id": "mb3"},
    )

    assert spec["label"] == "Compare runtimes"
    assert spec["workflow"]["phase_label"] == "Benchmark"
    assert spec["workflow"]["max_steps"] == 200
    assert spec["swarm"]["enabled"] is True
    assert spec["cleanup_worktrees"] is True


def test_run_simple_benchmark_builds_default_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*, spec: dict, spec_path: Path | None = None) -> dict:
        captured["spec"] = spec
        captured["spec_path"] = spec_path
        return {"ok": True, "contenders": {}}

    monkeypatch.setattr(bench, "run_benchmark_from_spec", _fake_run)

    result = bench.run_simple_benchmark(
        objective="Compare runtimes",
        binding={"type": "task_id", "id": "mb3"},
        check_commands=["echo ok"],
    )

    assert result["ok"] is True
    spec = captured["spec"]
    assert isinstance(spec, dict)
    assert spec["objective"] == "Compare runtimes"
    assert spec["binding"]["id"] == "mb3"
    assert spec["check_commands"] == ["echo ok"]


def test_start_background_benchmark_persists_final_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_benchmark_job_timeout_s", lambda: 123)
    monkeypatch.setattr(bench, "_resolve_git_ref", lambda _ref: "deadbeef")

    class _FakePopen:
        def __init__(self, args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.pid = 424242

        def wait(self, timeout=None) -> int:
            response_path = Path(str(self.args[-1])).resolve()
            run_dir = runtime_root / "runs" / "bench_fake"
            run_dir.mkdir(parents=True, exist_ok=True)
            summary_path = run_dir / "summary.md"
            result_path = run_dir / "result.json"
            summary_path.write_text("# summary\n", encoding="utf-8")
            result = {
                "run_id": "bench_fake",
                "status": "completed_with_failures",
                "summary_path": str(summary_path),
                "result_path": str(result_path),
                "contenders": {
                    "workflow": {
                        "ok": True,
                        "runtime_id": "wf-1",
                        "metrics": {"status": "completed", "elapsed_s": 11.0, "checks_failed": 0},
                    },
                    "swarm": {
                        "ok": False,
                        "runtime_id": "sw-1",
                        "metrics": {"status": "partial", "elapsed_s": 9.0, "checks_failed": 1},
                        "error": "swarm broke",
                    },
                },
            }
            result_path.write_text(json.dumps(result), encoding="utf-8")
            response_path.write_text(json.dumps({"ok": True, "result": result}), encoding="utf-8")
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    callback_rows: list[dict] = []
    started = bench.start_background_benchmark(
        objective="Compare runtimes",
        binding={"type": "task_id", "id": "mb3"},
        on_complete=lambda payload: callback_rows.append(dict(payload)),
    )

    benchmark_id = str(started["benchmark_id"])
    deadline = time.time() + 5.0
    final: dict | None = None
    while time.time() < deadline:
        current = bench.get_benchmark_status(benchmark_id=benchmark_id)
        if str(current.get("status") or "") in {"completed", "completed_with_failures", "failed"}:
            final = current
            break
        time.sleep(0.02)

    assert final is not None
    assert final["status"] == "completed_with_failures"
    assert final["summary_path"].endswith("/summary.md")
    assert "workflow: completed" in str(final.get("report_text") or "").lower()
    assert "swarm: partial" in str(final.get("report_text") or "").lower()
    callback_deadline = time.time() + 1.0
    while time.time() < callback_deadline and not callback_rows:
        time.sleep(0.02)
    assert callback_rows
    assert callback_rows[-1]["benchmark_id"] == benchmark_id
    assert callback_rows[-1]["thread_alive"] is False
    assert callback_rows[-1]["runner_alive"] is False
    assert final["run_id"] == "bench_fake"


def test_get_benchmark_status_recovers_finished_job_from_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_thread_alive", lambda _job_id: False)
    monkeypatch.setattr(bench, "_pid_alive", lambda _pid: False)

    job_id = "benchjob_recover"
    run_dir = runtime_root / "runs" / "bench_recover"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "run_id": "bench_recover",
        "status": "completed",
        "summary_path": str((run_dir / "summary.md").resolve()),
        "result_path": str((run_dir / "result.json").resolve()),
        "contenders": {
            "workflow": {"ok": True, "runtime_id": "wf-1", "metrics": {"status": "completed", "elapsed_s": 3.0, "checks_failed": 0}},
            "swarm": {"ok": True, "runtime_id": "sw-1", "metrics": {"status": "finished", "elapsed_s": 2.0, "checks_failed": 0}},
        },
    }
    bench._write_job_state(
        job_id,
        {
            "schema_version": bench.SCHEMA_JOB,
            "benchmark_id": job_id,
            "status": "running",
            "thread_alive": True,
            "runner_pid": 31337,
            "runner_response_path": str(bench._job_response_file(job_id)),
        },
    )
    bench._write_json(bench._job_response_file(job_id), {"ok": True, "result": result})

    status = bench.get_benchmark_status(benchmark_id=job_id)

    assert status["status"] == "completed"
    assert status["run_id"] == "bench_recover"
    assert status["thread_alive"] is False
    assert status["runner_alive"] is False
    assert "workflow: completed" in str(status.get("report_text") or "").lower()


def test_get_benchmark_status_forces_terminal_jobs_not_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_thread_alive", lambda _job_id: True)
    monkeypatch.setattr(bench, "_pid_alive", lambda _pid: True)

    job_id = "benchjob_terminal"
    bench._write_job_state(
        job_id,
        {
            "schema_version": bench.SCHEMA_JOB,
            "benchmark_id": job_id,
            "status": "completed_with_failures",
            "thread_alive": True,
            "runner_alive": True,
            "runner_pid": 1234,
        },
    )

    status = bench.get_benchmark_status(benchmark_id=job_id)

    assert status["status"] == "completed_with_failures"
    assert status["thread_alive"] is False
    assert status["runner_alive"] is False


def test_get_benchmark_status_recovers_when_job_file_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_thread_alive", lambda _job_id: False)
    monkeypatch.setattr(bench, "_pid_alive", lambda _pid: False)

    job_id = "benchjob_missing"
    run_dir = runtime_root / "runs" / "bench_missing"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "run_id": "bench_missing",
        "status": "completed_with_failures",
        "summary_path": str((run_dir / "summary.md").resolve()),
        "result_path": str((run_dir / "result.json").resolve()),
        "contenders": {
            "workflow": {"ok": True, "runtime_id": "wf-1", "metrics": {"status": "completed", "elapsed_s": 3.0, "checks_failed": 0}},
            "swarm": {"ok": False, "runtime_id": "sw-1", "metrics": {"status": "failed", "elapsed_s": 2.0, "checks_failed": 1}, "error": "swarm broke"},
        },
    }
    bench._write_json(
        bench._job_request_file(job_id),
        {
            "spec": bench.create_benchmark_spec(
                objective="Compare runtimes",
                binding={"type": "task_id", "id": "mb3"},
            )
        },
    )
    bench._write_json(bench._job_response_file(job_id), {"ok": True, "result": result})

    status = bench.get_benchmark_status(benchmark_id=job_id)

    assert status["status"] == "completed_with_failures"
    assert status["run_id"] == "bench_missing"
    assert Path(bench._job_file(job_id)).exists()
    assert "swarm broke" in str(status.get("report_text") or "")


def test_get_benchmark_status_marks_dead_runner_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_thread_alive", lambda _job_id: False)
    monkeypatch.setattr(bench, "_pid_alive", lambda _pid: False)

    job_id = "benchjob_dead"
    bench._write_job_state(
        job_id,
        {
            "schema_version": bench.SCHEMA_JOB,
            "benchmark_id": job_id,
            "status": "running",
            "thread_alive": True,
            "runner_pid": 991,
            "runner_exit_code": 17,
        },
    )
    bench._write_text(bench._job_stderr_file(job_id), "trace\nrunner died badly\n")

    status = bench.get_benchmark_status(benchmark_id=job_id)

    assert status["status"] == "failed"
    assert "exit_code=17" in str(status.get("error") or "")
    assert "runner died badly" in str(status.get("error") or "")


def test_get_benchmark_status_surfaces_live_run_progress_without_monitor_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime" / "state" / "agent_benchmarks"
    monkeypatch.setattr(bench, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(bench, "_thread_alive", lambda _job_id: False)
    monkeypatch.setattr(bench, "_pid_alive", lambda _pid: True)

    job_id = "benchjob_progress"
    run_id = "bench_progress"
    run_dir = runtime_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    bench._write_json(
        run_dir / "result.json",
        {
            "run_id": run_id,
            "status": "running",
            "created_utc": "2026-03-12T09:00:00Z",
            "updated_utc": "2026-03-12T09:05:00Z",
            "result_path": str((run_dir / "result.json").resolve()),
            "summary_path": str((run_dir / "summary.md").resolve()),
            "contenders": {},
        },
    )
    bench._write_json(
        run_dir / "workflow" / "result.json",
        {
            "mode": "workflow",
            "ok": False,
            "runtime_id": "wf-live",
            "metrics": {"status": "running", "phase": "builder_run"},
            "runner": {"runtime_progress": {"runtime_id": "wf-live", "phase": "builder_run", "updated_utc": "2026-03-12T09:05:00Z"}},
        },
    )
    bench._write_job_state(
        job_id,
        {
            "schema_version": bench.SCHEMA_JOB,
            "benchmark_id": job_id,
            "status": "running",
            "thread_alive": False,
            "runner_pid": 333,
            "run_id": run_id,
            "result_path": str((run_dir / "result.json").resolve()),
            "summary_path": str((run_dir / "summary.md").resolve()),
        },
    )

    status = bench.get_benchmark_status(benchmark_id=job_id)

    assert status["status"] == "running"
    assert status["run_id"] == run_id
    assert status["runner_alive"] is True
    assert status["progress_excerpt"]["workflow"]["runtime_id"] == "wf-live"
    assert status["progress_excerpt"]["workflow"]["phase"] == "builder_run"


def test_run_contender_guarded_writes_progress_placeholder_while_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 120)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 15)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 551122
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    snapshot = {
        "runtime_id": "sw-live",
        "phase": "planning",
        "marker": "m1",
        "status": "running",
        "status_detail": "planning",
        "updated_utc": "2026-03-12T09:10:00Z",
        "last_progress_utc": "2026-03-12T09:10:00Z",
        "active_deadline_utc": "",
    }

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench, "_runtime_progress_snapshot", lambda **_kwargs: dict(snapshot))
    monkeypatch.setattr(bench, "_pid_process_state", lambda _pid: "")
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)
    monotonic_values = iter([0.0, 0.0, 0.0, 1.2, 16.5])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values, 16.5))
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)

    contender_dir = tmp_path / "run" / "swarm"
    worktree = tmp_path / "worktree"
    contender_dir.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="swarm",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    progress_payload = json.loads((contender_dir / "result.json").read_text(encoding="utf-8"))
    assert progress_payload["runner"]["runtime_progress"]["runtime_id"] == "sw-live"
    assert progress_payload["runner"]["runtime_progress"]["phase"] == "planning"
    assert payload["ok"] is False


def test_runtime_progress_timeout_uses_longer_planning_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "_benchmark_planning_progress_timeout_s", lambda: 180)

    timeout_s = bench._runtime_progress_timeout_s(
        timeout_s=300,
        stall_timeout_s=60,
        snapshot={
            "phase": "planning",
            "status_detail": "planning",
            "active_deadline_utc": "",
        },
    )

    assert timeout_s == 180


def test_runtime_progress_timeout_honors_inner_runtime_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "_benchmark_runtime_deadline_grace_s", lambda: 10)
    monkeypatch.setattr(
        bench,
        "_utc_now",
        lambda: bench._parse_iso_utc("2026-03-12T10:00:00Z"),
    )

    timeout_s = bench._runtime_progress_timeout_s(
        timeout_s=300,
        stall_timeout_s=60,
        snapshot={
            "phase": "builder_run",
            "status_detail": "running_waiting_command",
            "active_deadline_utc": "2026-03-12T10:02:00Z",
        },
    )

    assert timeout_s == 130


def test_runtime_progress_timeout_uses_last_progress_to_honor_remaining_slice_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bench, "_benchmark_runtime_deadline_grace_s", lambda: 10)
    monkeypatch.setattr(
        bench,
        "_utc_now",
        lambda: bench._parse_iso_utc("2026-03-12T10:01:30Z"),
    )

    timeout_s = bench._runtime_progress_timeout_s(
        timeout_s=300,
        stall_timeout_s=60,
        snapshot={
            "phase": "finishers",
            "status_detail": "running_finishers",
            "last_progress_utc": "2026-03-12T10:00:00Z",
            "active_deadline_utc": "2026-03-12T10:02:00Z",
        },
    )

    assert timeout_s == 130


def test_run_contender_guarded_fails_when_runtime_is_terminal_but_child_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "_benchmark_contender_timeout_s", lambda: 120)
    monkeypatch.setattr(bench, "_benchmark_contender_stall_timeout_s", lambda: 30)
    monkeypatch.setattr(bench, "_benchmark_terminal_runtime_grace_s", lambda: 3)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 661122
            self.returncode = None

        def poll(self):
            return None

        def kill(self) -> None:
            self.returncode = -9

    snapshot = {
        "runtime_id": "wf-live",
        "phase": "intent_validation",
        "marker": "m-terminal",
        "status": "failed",
        "status_detail": "failed",
        "updated_utc": "2026-03-12T10:04:12Z",
        "last_progress_utc": "2026-03-12T10:03:04Z",
        "active_deadline_utc": "",
    }

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(bench, "_runtime_progress_snapshot", lambda **_kwargs: dict(snapshot))
    monkeypatch.setattr(bench, "_pid_process_state", lambda _pid: "")
    monkeypatch.setattr(bench.os, "killpg", lambda _pid, _sig: None)
    monotonic_values = iter([0.0, 0.0, 0.0, 0.2, 0.2, 3.5, 3.5])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(monotonic_values, 3.5))
    monkeypatch.setattr(bench.time, "sleep", lambda _seconds: None)

    contender_dir = tmp_path / "run" / "workflow"
    worktree = tmp_path / "worktree"
    contender_dir.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    payload = bench._run_contender_guarded(
        mode="workflow",
        worktree=worktree,
        contender_dir=contender_dir,
        spec={"check_commands": []},
    )

    persisted = json.loads((contender_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["runtime_id"] == "wf-live"
    assert payload["metrics"]["status"] == "failed"
    assert payload["metrics"]["phase"] == "intent_validation"
    assert "terminal status failed" in str(payload["error"] or "")
    assert persisted["runner"]["terminal_runtime"] is True
    assert persisted["runner"]["runtime_progress"]["runtime_id"] == "wf-live"


def test_capture_worktree_exports_preserves_patch_and_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmp_path / "playground"
    contender_dir = tmp_path / "run" / "workflow"
    tracked_file = worktree / "tools" / "changed.py"
    untracked_file = worktree / "notes" / "draft.txt"
    denied_file = worktree / "governance" / "agents" / "runtime" / "workers_state.json"
    tracked_file.parent.mkdir(parents=True, exist_ok=True)
    untracked_file.parent.mkdir(parents=True, exist_ok=True)
    denied_file.parent.mkdir(parents=True, exist_ok=True)
    tracked_file.write_text("print('changed')\n", encoding="utf-8")
    untracked_file.write_text("draft\n", encoding="utf-8")
    denied_file.write_text("{}\n", encoding="utf-8")

    def _fake_run_git(args: list[str], *, cwd: Path | None = None, timeout_s: int = 180):
        if args[:4] == ["diff", "--binary", "HEAD", "--"]:
            assert args[4:] == ["tools/changed.py", "docs/removed.md"]
            return subprocess.CompletedProcess(["git", *args], 0, stdout="diff --git a/tools/changed.py b/tools/changed.py\n", stderr="")
        if args == ["diff", "--name-only", "HEAD", "--diff-filter=ACMRTUXB", "--"]:
            return subprocess.CompletedProcess(
                ["git", *args],
                0,
                stdout="tools/changed.py\ngovernance/agents/runtime/workers_state.json\n",
                stderr="",
            )
        if args == ["diff", "--name-only", "HEAD", "--diff-filter=D", "--"]:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="docs/removed.md\n", stderr="")
        if args == ["ls-files", "--others", "--exclude-standard"]:
            return subprocess.CompletedProcess(
                ["git", *args],
                0,
                stdout="notes/draft.txt\ngovernance/agents/runtime/workers_state.json\n",
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(bench, "_run_git", _fake_run_git)

    exported = bench._capture_worktree_exports(worktree=worktree, contender_dir=contender_dir)

    assert exported["copied_file_count"] == 2
    assert exported["deleted_files"] == ["docs/removed.md"]
    assert (contender_dir / "exported_changes" / "final_patch.diff").read_text(encoding="utf-8").startswith("diff --git")
    assert (contender_dir / "exported_changes" / "files" / "tools" / "changed.py").exists()
    assert (contender_dir / "exported_changes" / "files" / "notes" / "draft.txt").exists()
    assert not (contender_dir / "exported_changes" / "files" / "governance" / "agents" / "runtime" / "workers_state.json").exists()
    status_text = (contender_dir / "exported_changes" / "git_status.txt").read_text(encoding="utf-8")
    manifest = json.loads((contender_dir / "exported_changes" / "manifest.json").read_text(encoding="utf-8"))
    assert "tools/changed.py" in status_text
    assert "notes/draft.txt" in status_text
    assert "workers_state.json" not in status_text
    assert manifest["tracked_changed"] == ["tools/changed.py"]
    assert manifest["untracked"] == ["notes/draft.txt"]
    assert manifest["deleted"] == ["docs/removed.md"]
    assert manifest["copied_files"] == ["tools/changed.py", "notes/draft.txt"]
    assert Path(manifest["snapshot_root"]).is_dir()


def test_capture_worktree_exports_safe_keeps_placeholder_artifacts_on_export_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "playground"
    contender_dir = tmp_path / "run" / "workflow"
    worktree.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        bench,
        "_capture_worktree_exports",
        lambda *, worktree, contender_dir: (_ for _ in ()).throw(RuntimeError("export boom")),
    )

    exported = bench._capture_worktree_exports_safe(worktree=worktree, contender_dir=contender_dir)
    manifest = json.loads(Path(exported["manifest_path"]).read_text(encoding="utf-8"))

    assert Path(exported["patch_path"]).exists()
    assert Path(exported["status_path"]).exists()
    assert Path(exported["snapshot_root"]).is_dir()
    assert "failed to export contender changes: export boom" in exported["errors"]
    assert "failed to export contender changes: export boom" in manifest["errors"]


def test_sanitize_benchmark_worktree_removes_runtime_residue(tmp_path: Path) -> None:
    worktree = tmp_path / "playground"
    runtime_file = worktree / "governance" / "agents" / "runtime" / "orchestration" / "workflow.json"
    session_file = worktree / "governance" / "agents" / "sessions" / "mb3" / "session__generalist.jsonl"
    state_file = worktree / "runtime" / "state" / "agent_benchmarks" / "jobs" / "old.json"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("runtime\n", encoding="utf-8")
    session_file.write_text("session\n", encoding="utf-8")
    state_file.write_text("state\n", encoding="utf-8")

    payload = bench._sanitize_benchmark_worktree(worktree)

    assert sorted(payload["removed"]) == ["governance/agents/runtime", "governance/agents/sessions", "runtime/state"]
    assert payload["errors"] == []
    assert not (worktree / "governance" / "agents" / "runtime").exists()
    assert not (worktree / "governance" / "agents" / "sessions").exists()
    assert not (worktree / "runtime" / "state").exists()


def test_copy_runtime_artifacts_skips_blank_paths(tmp_path: Path) -> None:
    copied = bench._copy_runtime_artifacts(
        contender_dir=tmp_path / "run" / "workflow",
        runtime_paths={"state_path": "", "events_path": "   ", "artifacts_root": ""},
    )

    assert copied == {"state_path": "", "events_path": "", "artifacts_root": ""}
    assert not (tmp_path / "run" / "workflow" / "artifacts").exists()


def test_benchmark_cli_runs_outside_repo_root() -> None:
    cli_path = (bench._repo_root() / "tools" / "bin" / "agents_benchmark_cli.py").resolve()
    completed = subprocess.run(
        [sys.executable, str(cli_path), "--help"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "workflow vs swarm playground benchmark" in str(completed.stdout or "")


def test_repo_root_override_redirects_modules(tmp_path: Path) -> None:
    original_agents_root = bench._agents._repo_root()
    original_policy_root = bench._runtime_policy._repo_root()

    with bench._repo_root_override(tmp_path):
        assert bench._agents._repo_root() == tmp_path
        assert bench._runtime_policy._repo_root() == tmp_path

    assert bench._agents._repo_root() == original_agents_root
    assert bench._runtime_policy._repo_root() == original_policy_root
