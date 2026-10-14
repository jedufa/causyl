from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tools.bin import schedule_job
from infra.controlplane import scheduled_jobs as sched


def _install_tty(monkeypatch) -> None:
    monkeypatch.setattr(schedule_job.sys, "stdin", SimpleNamespace(isatty=lambda: True))


def _install_answers(monkeypatch, answers: list[str]) -> None:
    iterator = iter(answers)

    def _input(prompt: str = "") -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError(f"No mocked answer left for prompt: {prompt}") from exc

    monkeypatch.setattr("builtins.input", _input)


def test_schedule_job_new_reprompts_invalid_values_and_saves(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _install_tty(monkeypatch)

    _install_answers(
        monkeypatch,
        [
            "bad id",
            "nightly_cli_001",
            "",
            "",
            "9",
            "3",
            "1",
            "AB",
            "mb3",
            "",
            "1",
            "Not/AZone",
            "UTC",
            "not-a-date",
            "2026-03-08T03:30:00Z",
            "Review the latest results.",
            ".",
            "",
            "",
            "",
            "abc",
            "600",
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
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )

    rc = schedule_job.main(["new", "--backend", "legacy"])
    assert rc == 0

    saved = sched.load_job("nightly_cli_001", repo_root=tmp_path)
    assert saved["required_context_bundle"] == "lab_core_v1"
    assert saved["role"] == "generalist"
    assert saved["binding"] == {"type": "task_id", "id": "mb3"}
    assert saved["schedule"]["run_at"] == "2026-03-08T03:30:00Z"
    assert saved["policy"]["max_runtime_seconds"] == 600

    stderr = capsys.readouterr().err
    assert "Invalid value" in stderr


def test_schedule_job_edit_preserves_job_id_and_does_not_clone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _install_tty(monkeypatch)

    sched.save_job(
        {
            "job_id": "nightly_cli_edit_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "job_kind": "single_turn",
            "prompt": "Original prompt.",
            "schedule": {"mode": "once", "run_at": "2026-03-08T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "required_context_bundle": "lab_core_v1",
        },
        repo_root=tmp_path,
    )

    _install_answers(
        monkeypatch,
        [""] * 9 + ["."] + [""] * 16,
    )

    rc = schedule_job.main(["edit", "--backend", "legacy", "nightly_cli_edit_001"])
    assert rc == 0

    jobs = sorted((sched.jobs_dir(tmp_path)).glob("*.json"))
    assert [path.name for path in jobs] == ["nightly_cli_edit_001.json"]


def test_schedule_job_new_cancel_returns_130(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _install_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(KeyboardInterrupt()))

    rc = schedule_job.main(["new", "--backend", "legacy"])

    assert rc == 130
    assert "Canceled:" in capsys.readouterr().err


def test_schedule_job_new_build_verify_loop_saves_loop_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _install_tty(monkeypatch)

    _install_answers(
        monkeypatch,
        [
            "nightly_loop_cli_001",
            "",
            "1",
            "2",
            "PIS-20260312-161622Z-DD8C",
            "",
            "1",
            "UTC",
            "2026-03-08T03:30:00Z",
            "Implement and verify DD8C.",
            ".",
            "",
            "",
            "",
            "900",
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
            "2",
            "5",
            "4",
            "",
            "",
        ]
    )

    rc = schedule_job.main(["new", "--backend", "legacy"])
    assert rc == 0

    saved = sched.load_job("nightly_loop_cli_001", repo_root=tmp_path)
    assert saved["job_kind"] == "build_verify_loop"
    assert saved["binding"] == {"type": "ledger_uid", "id": "PIS-20260312-161622Z-DD8C"}
    assert saved["depends_on"] == []
    assert saved["loop"]["builder_role"] == "builder"
    assert saved["loop"]["verifier_role"] == "verifier"
    assert saved["loop"]["max_iterations"] == 4
    assert saved["loop"]["pass_marker"] == "VERDICT: PASS"
    assert saved["policy"]["post_success_commit_tag_retries"] == 0
    assert saved["policy"]["post_success_commit_tag_backoff_seconds"] == 60


def test_schedule_job_new_build_verify_loop_can_set_controller_only_execution_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _install_tty(monkeypatch)

    _install_answers(
        monkeypatch,
        [
            "nightly_loop_cli_controller_only_001",
            "",
            "1",
            "2",
            "PIS-20260312-161622Z-DD8C",
            "",
            "1",
            "UTC",
            "2026-03-08T03:30:00Z",
            "Implement and verify DD8C.",
            ".",
            "",
            "",
            "",
            "900",
            "",
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
            "2",
            "5",
            "4",
            "",
            "",
        ]
    )

    rc = schedule_job.main(["new", "--backend", "legacy"])
    assert rc == 0

    saved = sched.load_job("nightly_loop_cli_controller_only_001", repo_root=tmp_path)
    assert saved["runtime"]["execution_mode"] == "controller_only"
    assert saved["loop"]["builder_runtime"]["execution_mode"] == "controller_only"
    assert saved["loop"]["verifier_runtime"]["execution_mode"] == "controller_only"


def test_schedule_job_delete_invokes_controlplane_delete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    called: dict[str, object] = {}

    def _fake_delete(job_id: str, *, repo_root=None, purge_history: bool = False):
        called["job_id"] = job_id
        called["purge_history"] = purge_history
        called["repo_root"] = repo_root
        return {"ok": True, "job_id": job_id, "deleted": True, "purge_history": purge_history}

    monkeypatch.setattr(sched, "delete_job", _fake_delete)

    rc = schedule_job.main(["delete", "--backend", "legacy", "nightly_delete_cli_001", "--yes", "--purge-history"])
    assert rc == 0
    assert called == {
        "job_id": "nightly_delete_cli_001",
        "purge_history": True,
        "repo_root": None,
    }


def test_schedule_job_delete_without_job_id_prompts_for_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _install_tty(monkeypatch)
    called: dict[str, object] = {}

    monkeypatch.setattr(
        sched,
        "list_jobs",
        lambda: [
            {
                "job_id": "nightly_delete_cli_002",
                "display_title": "Nightly delete candidate",
                "enabled": True,
                "state": {"last_status": "ok"},
            }
        ],
    )

    def _fake_delete(job_id: str, *, repo_root=None, purge_history: bool = False):
        called["job_id"] = job_id
        called["purge_history"] = purge_history
        called["repo_root"] = repo_root
        return {"ok": True, "job_id": job_id, "deleted": True}

    monkeypatch.setattr(sched, "delete_job", _fake_delete)
    _install_answers(monkeypatch, [""])

    rc = schedule_job.main(["delete", "--backend", "legacy", "--yes"])

    assert rc == 0
    assert called == {
        "job_id": "nightly_delete_cli_002",
        "purge_history": False,
        "repo_root": None,
    }
