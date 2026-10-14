from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import tools.git.support.commit_journal as commit_journal


def _init_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Commit Journal Test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "operator@example.invalid"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)


def test_record_render_and_consume_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--task-id",
            "mbl",
            "--title",
            "Lower admin timeout",
            "--bullet",
            "Changed the admin terminal default timeout from 7000 ms to 6000 ms",
            "--bullet",
            "Updated AdminUiStateTest to match the new default",
            "--file",
            "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminViewModel.kt",
            "--file",
            "infra/mobile/android/app/src/test/java/com/eiel/chat/ui/AdminUiStateTest.kt",
            "--validation",
            "./gradlew :app:testDebugUnitTest --tests com.eiel.chat.ui.AdminUiStateTest",
        ]
    )
    assert rc == 0

    message_path = tmp_path / "message.txt"
    plan_path = tmp_path / "plan.json"
    rc = commit_journal.main(["render", "--subject-prefix", "baseline-401", "--message-out", str(message_path), "--plan-out", str(plan_path)])
    assert rc == 0

    message = message_path.read_text(encoding="utf-8")
    assert message.startswith("baseline-401 Lower admin timeout\n")
    assert "- Changed the admin terminal default timeout from 7000 ms to 6000 ms" in message
    assert "Validation:" in message

    rc = commit_journal.main(["consume", "--plan", str(plan_path), "--commit-sha", "abc123"])
    assert rc == 0

    pending = list((tmp_path / "runtime" / "state" / "commit_journal" / "pending").glob("*.json"))
    assert pending == []
    committed = list((tmp_path / "runtime" / "state" / "commit_journal" / "committed" / "abc123").glob("*.json"))
    committed_names = {path.name for path in committed}
    assert "receipt.json" in committed_names
    assert any(name.startswith("cjn_") for name in committed_names)


def test_render_can_require_full_worktree_coverage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Update tracked file",
            "--bullet",
            "Adjusted tracked content",
            "--file",
            "tracked.txt",
        ]
    )
    assert rc == 0

    rc = commit_journal.main(["audit"])
    assert rc == 0

    extra = tmp_path / "extra.txt"
    extra.write_text("untracked\n", encoding="utf-8")
    rc = commit_journal.main(["audit"])
    assert rc == 1
    rc = commit_journal.main(["render", "--require-full-coverage"])
    assert rc == 1


def test_render_can_synthesize_uncovered_paths_from_filtered_worktree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (tmp_path / "mobile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "mobile" / "extra.kt").write_text("class Extra\n", encoding="utf-8")
    ignored_dir = tmp_path / ".tooling" / "gradle-user-home" / "cache"
    ignored_dir.mkdir(parents=True, exist_ok=True)
    (ignored_dir / "state.bin").write_text("cache\n", encoding="utf-8")
    bootstrap_dir = tmp_path / ".tooling" / "gradle-bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "gradle-8.9-bin.zip").write_text("zip\n", encoding="utf-8")
    (tmp_path / ".tooling" / "commandlinetools-linux.zip").write_text("zip\n", encoding="utf-8")

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Update tracked file",
            "--bullet",
            "Adjusted tracked content",
            "--file",
            "tracked.txt",
        ]
    )
    assert rc == 0

    plan = commit_journal.create_compose_plan(
        require_full_coverage=True,
        allow_worktree_fallback=True,
        repo=tmp_path,
    )
    message = str(plan["message"])
    assert "- Adjusted tracked content" in message
    assert "Add mobile code in mobile/extra.kt (Extra)" in message
    coverage = dict(plan["coverage"])
    assert "mobile/extra.kt" in coverage["uncovered_paths"]
    assert any(path.startswith(".tooling/") for path in coverage["ignored_paths"])


def test_render_can_reconstruct_message_from_changed_files_without_pending_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)
    webui_path = tmp_path / "infra" / "webui" / "server_agents.py"
    webui_path.parent.mkdir(parents=True, exist_ok=True)
    webui_path.write_text("# changed\n", encoding="utf-8")
    mobile_path = tmp_path / "infra" / "mobile" / "android" / "README.md"
    mobile_path.parent.mkdir(parents=True, exist_ok=True)
    mobile_path.write_text("changed\n", encoding="utf-8")
    os.utime(webui_path, (2_000_000_000, 2_000_000_000))
    os.utime(mobile_path, (1_900_000_000, 1_900_000_000))

    plan = commit_journal.create_compose_plan(
        allow_worktree_fallback=True,
        repo=tmp_path,
    )
    message = str(plan["message"])

    assert message.startswith("Harden mobile plane and Android docs\n")
    assert "Add web server flow in infra/webui/server_agents.py" in message
    assert "Add Android project docs in infra/mobile/android/README.md" in message
    assert "No pending journal fragments were available" in message
    assert plan["entries"] == []
    assert plan["synthesized_entries"]
    assert plan["subject_source"] in {"entry_title", "worktree_semantic"}


def test_render_prefers_semantic_subject_over_generic_scope_subject(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    webui_path = tmp_path / "infra" / "webui" / "server_agents.py"
    webui_path.parent.mkdir(parents=True, exist_ok=True)
    webui_path.write_text("# changed\n", encoding="utf-8")
    git_path = tmp_path / "tools" / "git" / "commit_journal.py"
    git_path.parent.mkdir(parents=True, exist_ok=True)
    git_path.write_text("# changed\n", encoding="utf-8")
    os.utime(webui_path, (2_000_000_000, 2_000_000_000))
    os.utime(git_path, (1_900_000_000, 1_900_000_000))

    commit_journal.write_entry(
        {
            "title": "Wire server worker dispatch",
            "summary_bullets": ["Route host verification through the real web server worker."],
            "files": ["infra/webui/server_agents.py"],
            "scope_hint": "infra/webui",
        },
        repo=tmp_path,
    )
    commit_journal.write_entry(
        {
            "title": "Add journal commit path",
            "summary_bullets": ["Let the admin git actions compose the message from pending fragments."],
            "files": ["tools/git/support/commit_journal.py"],
            "scope_hint": "tools/git",
        },
        repo=tmp_path,
    )

    plan = commit_journal.create_compose_plan(repo=tmp_path)

    assert str(plan["message"]).startswith("Harden mobile plane and journal git flow\n")


def test_render_enriches_generic_journal_bullets_from_changed_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)

    webui_path = tmp_path / "infra" / "webui" / "server_agents.py"
    webui_path.parent.mkdir(parents=True, exist_ok=True)
    webui_path.write_text("def dispatch():\n    return True\n", encoding="utf-8")
    mobile_path = tmp_path / "infra" / "mobile" / "android" / "README.md"
    mobile_path.parent.mkdir(parents=True, exist_ok=True)
    mobile_path.write_text("updated\n", encoding="utf-8")

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Update mobile stuff",
            "--bullet",
            "Updated files",
            "--file",
            "infra/webui/server_agents.py",
            "--file",
            "infra/mobile/android/README.md",
        ]
    )
    assert rc == 0

    plan = commit_journal.create_compose_plan(
        allow_worktree_fallback=True,
        repo=tmp_path,
    )
    message = str(plan["message"])

    assert message.startswith("Harden mobile plane and Android docs\n")
    assert "- Updated files" in message
    assert "Add web server flow in infra/webui/server_agents.py" in message
    assert "Add Android project docs in infra/mobile/android/README.md" in message
    assert "pending journal fragments were too generic" in message


def test_scan_uses_pending_filename_as_entry_id_when_fragment_omits_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    pending_dir = tmp_path / "runtime" / "state" / "commit_journal" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / "cjn_20260401t000000000000z__missing-entry-id.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": commit_journal.SCHEMA_ENTRY,
                "created_utc": "2026-04-01T00:00:00Z",
                "title": "Missing explicit entry id",
                "summary_bullets": ["Backfill the id from the pending filename."],
                "files": ["tools/git/support/commit_journal.py"],
                "outcome": "applied",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    report = commit_journal.scan_pending_entries(repo=tmp_path)

    assert report["ok"] is True
    assert report["invalid"] == []
    assert report["pending_count"] == 1
    assert report["valid"][0]["entry"]["entry_id"] == path.stem


def test_render_defaults_to_filtered_worktree_enrichment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)

    webui_path = tmp_path / "infra" / "webui" / "server_agents.py"
    webui_path.parent.mkdir(parents=True, exist_ok=True)
    webui_path.write_text("def dispatch():\n    return True\n", encoding="utf-8")

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Update mobile stuff",
            "--bullet",
            "Updated files",
            "--file",
            "infra/webui/server_agents.py",
        ]
    )
    assert rc == 0

    message_path = tmp_path / "message.txt"
    rc = commit_journal.main(["render", "--message-out", str(message_path)])
    assert rc == 0

    message = message_path.read_text(encoding="utf-8")
    assert "Add web server flow in infra/webui/server_agents.py" in message


def test_render_journal_only_disables_default_worktree_enrichment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)

    webui_path = tmp_path / "infra" / "webui" / "server_agents.py"
    webui_path.parent.mkdir(parents=True, exist_ok=True)
    webui_path.write_text("def dispatch():\n    return True\n", encoding="utf-8")

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Update mobile stuff",
            "--bullet",
            "Updated files",
            "--file",
            "infra/webui/server_agents.py",
        ]
    )
    assert rc == 0

    message_path = tmp_path / "message.txt"
    rc = commit_journal.main(["render", "--journal-only", "--message-out", str(message_path)])
    assert rc == 0

    message = message_path.read_text(encoding="utf-8")
    assert message.startswith("Update mobile stuff\n")
    assert "Add web server flow in infra/webui/server_agents.py" not in message


def test_render_can_override_subject_while_preserving_journal_body(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Lower admin timeout",
            "--bullet",
            "Changed the admin terminal default timeout from 7000 ms to 6000 ms",
            "--file",
            "tracked.txt",
            "--validation",
            "python -m pytest -q tests/test_admin_timeout.py",
        ]
    )
    assert rc == 0

    message_path = tmp_path / "message.txt"
    plan_path = tmp_path / "plan.json"
    rc = commit_journal.main(
        [
            "render",
            "--subject-prefix",
            "baseline-401",
            "--subject-override",
            "Tighten scheduler receipts",
            "--message-out",
            str(message_path),
            "--plan-out",
            str(plan_path),
        ]
    )
    assert rc == 0

    message = message_path.read_text(encoding="utf-8")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    assert message.startswith("baseline-401 Tighten scheduler receipts\n")
    assert "- Changed the admin terminal default timeout from 7000 ms to 6000 ms" in message
    assert "Validation:" in message
    assert plan["subject_override"] == "Tighten scheduler receipts"
    assert plan["subject_source"] == "subject_override"


def test_render_reconstruction_avoids_raw_code_snippets_in_bullets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)

    target = tmp_path / "infra" / "webui" / "server_http_core.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "class AdminCmdJob:\n"
        "    def to_dict(self):\n"
        "        return {\n"
        "            \"output_seen\": bool((self.stdout or \"\") or (self.stderr or \"\")),\n"
        "            \"last_output_utc\": str(self.last_output_utc or \"\"),\n"
        "        }\n",
        encoding="utf-8",
    )

    plan = commit_journal.create_compose_plan(
        allow_worktree_fallback=True,
        repo=tmp_path,
    )
    message = str(plan["message"])

    assert "Add web server flow in infra/webui/server_http_core.py" in message
    assert "\"output_seen\": bool" not in message
    assert "\"last_output_utc\":" not in message


def test_render_reconstruction_demotes_ledger_metadata_below_code_scopes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)

    webui_path = tmp_path / "infra" / "webui" / "server_agents.py"
    webui_path.parent.mkdir(parents=True, exist_ok=True)
    webui_path.write_text("def dispatch():\n    return True\n", encoding="utf-8")
    os.utime(webui_path, (2_000_000_000, 2_000_000_000))

    for idx in range(9):
        ledger_dir = tmp_path / "governance" / "ledger" / "items" / f"PIS-demo-{idx:02d}"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        (ledger_dir / "meta.json").write_text("{\"ok\":true}\n", encoding="utf-8")
        os.utime(ledger_dir / "meta.json", (1_900_000_000 - idx, 1_900_000_000 - idx))

    plan = commit_journal.create_compose_plan(
        allow_worktree_fallback=True,
        repo=tmp_path,
    )
    message = str(plan["message"])

    assert message.startswith("Harden mobile plane and governance docs\n")
    assert "Add web server flow in infra/webui/server_agents.py" in message
    assert "Reconstructed additional coverage for" in message


def test_render_refreshes_latest_preview_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Lower admin timeout",
            "--bullet",
            "Changed the admin terminal default timeout from 7000 ms to 6000 ms",
            "--file",
            "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminViewModel.kt",
        ]
    )
    assert rc == 0

    rc = commit_journal.main(["render"])
    assert rc == 0

    compose_dir = tmp_path / "runtime" / "state" / "commit_journal" / "compose"
    latest_message = compose_dir / "latest_commit_message.txt"
    latest_plan = compose_dir / "latest_commit_plan.json"
    assert latest_message.exists()
    assert latest_plan.exists()
    assert latest_message.read_text(encoding="utf-8").startswith("Lower admin timeout\n")


def test_render_rejects_invalid_pending_fragment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    pending_dir = tmp_path / "runtime" / "state" / "commit_journal" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / "broken.json").write_text(
        json.dumps({"schema_version": commit_journal.SCHEMA_ENTRY, "entry_id": "broken", "title": "Broken"}),
        encoding="utf-8",
    )

    rc = commit_journal.main(["render"])
    assert rc == 1


def test_doctor_can_quarantine_invalid_fragment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    pending_dir = tmp_path / "runtime" / "state" / "commit_journal" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    bad_path = pending_dir / "bad.json"
    bad_path.write_text("{not-json}\n", encoding="utf-8")

    rc = commit_journal.main(["doctor", "--quarantine-invalid"])
    assert rc == 1
    assert not bad_path.exists()

    invalid_dir = tmp_path / "runtime" / "state" / "commit_journal" / "failed" / "invalid"
    moved = list(invalid_dir.glob("*.json"))
    reason = list(invalid_dir.glob("*.reason.json"))
    assert moved
    assert reason


def test_consume_is_idempotent_after_partial_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")

    rc = commit_journal.main(
        [
            "record",
            "--agent-role",
            "generalist",
            "--title",
            "Update tracked file",
            "--bullet",
            "Adjusted tracked content",
            "--file",
            "tracked.txt",
        ]
    )
    assert rc == 0

    plan = commit_journal.create_compose_plan(require_full_coverage=True, repo=tmp_path)
    plan_path = commit_journal.write_compose_plan(plan, repo=tmp_path)
    pending_dir = tmp_path / "runtime" / "state" / "commit_journal" / "pending"
    pending_entry = next(pending_dir.glob("*.json"))
    commit_dir = tmp_path / "runtime" / "state" / "commit_journal" / "committed" / "abc123"
    commit_dir.mkdir(parents=True, exist_ok=True)
    archived_entry = commit_dir / pending_entry.name
    shutil.move(str(pending_entry), str(archived_entry))

    rc = commit_journal.main(["consume", "--plan", str(plan_path), "--commit-sha", "abc123"])
    assert rc == 0


def test_render_moves_note_only_entries_into_notes_and_marks_partial(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)

    commit_journal.write_entry(
        {
            "title": "Lower admin timeout",
            "summary_bullets": [
                "Changed the admin terminal default timeout from 7000 ms to 6000 ms",
            ],
            "files": ["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminViewModel.kt"],
            "outcome": "applied",
        },
        repo=tmp_path,
    )
    commit_journal.write_entry(
        {
            "title": "Harden runner tracing",
            "summary_bullets": [
                "Added worker diagnostics around filesystem host-verification pickup",
            ],
            "files": ["infra/webui/server_agents.py"],
            "notes": ["One remaining edge path still depends on live server restarts."],
            "outcome": "partial",
        },
        repo=tmp_path,
    )
    commit_journal.write_entry(
        {
            "title": "Investigated stale mobile stream reports",
            "summary_bullets": [
                "Captured that Android idle timeout can masquerade as a fetch failure",
            ],
            "notes": ["No code landed for this investigation-only step."],
            "outcome": "note_only",
        },
        repo=tmp_path,
    )

    plan = commit_journal.create_compose_plan(repo=tmp_path)
    message = str(plan["message"])

    assert "- Changed the admin terminal default timeout from 7000 ms to 6000 ms" in message
    assert "- Added worker diagnostics around filesystem host-verification pickup" in message
    assert "Notes:\n- Partial outcome: Harden runner tracing" in message
    assert "- Note only: Investigated stale mobile stream reports" in message
    notes_section = message.split("Notes:\n", 1)[1]
    assert "- Captured that Android idle timeout can masquerade as a fetch failure" in notes_section


def test_status_reports_outcome_counts(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)

    commit_journal.write_entry(
        {"title": "Applied change", "summary_bullets": ["Did one thing"], "outcome": "applied"},
        repo=tmp_path,
    )
    commit_journal.write_entry(
        {"title": "Partial change", "summary_bullets": ["Did part of it"], "outcome": "partial"},
        repo=tmp_path,
    )
    commit_journal.write_entry(
        {"title": "Investigation", "summary_bullets": ["Observed one blocker"], "outcome": "note_only"},
        repo=tmp_path,
    )

    rc = commit_journal.main(["status"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome_counts"] == {"applied": 1, "note_only": 1, "partial": 1}


def test_consume_fails_if_pending_fragment_changes_after_compose(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(commit_journal, "repo_root", lambda: tmp_path)

    entry_path = commit_journal.write_entry(
        {
            "title": "Lower admin timeout",
            "summary_bullets": ["Changed the admin terminal default timeout from 7000 ms to 6000 ms"],
            "files": ["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminViewModel.kt"],
        },
        repo=tmp_path,
    )
    plan = commit_journal.create_compose_plan(repo=tmp_path)
    plan_path = commit_journal.write_compose_plan(plan, repo=tmp_path)

    raw = json.loads(entry_path.read_text(encoding="utf-8"))
    raw["summary_bullets"] = ["Changed the admin terminal default timeout from 7000 ms to 5500 ms"]
    entry_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    rc = commit_journal.main(["consume", "--plan", str(plan_path), "--commit-sha", "abc123"])
    assert rc == 1
    assert not (tmp_path / "runtime" / "state" / "commit_journal" / "committed" / "abc123" / "receipt.json").exists()
