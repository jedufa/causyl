from infra.webui import server_routes_and_main as routes


def test_admin_commit_journal_status_includes_pending_entry_summaries(monkeypatch):
    entry = {
        "entry_id": "cjn_demo",
        "created_utc": "2026-03-11T10:37:43Z",
        "title": "add mobile journal viewer",
        "summary_bullets": ["Added swipeable journal entry viewer"],
        "files": ["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt"],
        "validation": ["python -m py_compile infra/webui/server_routes_and_main.py"],
        "notes": ["Android host verification remains separate."],
        "outcome": "applied",
        "scope_hint": "mobile-ui",
        "author": {
            "role": "generalist",
            "source": "codex",
            "binding": {
                "type": "task_id",
                "id": "mbl",
            },
        },
    }

    monkeypatch.setattr(
        routes._commit_journal,
        "scan_pending_entries",
        lambda: {
            "pending_count": 1,
            "invalid_count": 0,
            "ok": True,
            "valid": [{"path": "runtime/state/commit_journal/pending/cjn_demo.json", "entry": entry}],
        },
    )
    monkeypatch.setattr(routes._commit_journal, "_outcome_counts", lambda entries: {"applied": len(entries)})  # type: ignore[attr-defined]
    monkeypatch.setattr(
        routes._commit_journal,
        "audit_worktree_coverage",
        lambda: {"ok": True, "uncovered_paths": [], "changed_paths": [], "ignored_paths": []},
    )
    monkeypatch.setattr(routes._commit_journal, "_build_worktree_fallback_entry", lambda *args, **kwargs: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes._commit_journal, "_subject_from_entries", lambda entries: ("Review mobile journal viewer", "entry"))  # type: ignore[attr-defined]

    payload = routes._admin_commit_journal_status()

    assert payload["pending_count"] == 1
    assert payload["preview_subject"] == "Review mobile journal viewer"
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["entry_id"] == "cjn_demo"
    assert payload["entries"][0]["author"]["binding"]["id"] == "mbl"
    assert payload["entries"][0]["summary_bullets"] == ["Added swipeable journal entry viewer"]
