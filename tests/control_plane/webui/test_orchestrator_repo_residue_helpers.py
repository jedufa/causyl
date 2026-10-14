from infra.webui import server_agents_orchestrator_repo_residue as residue


def test_summarize_repo_residue_delta_splits_clean_and_preexisting_paths() -> None:
    baseline = [
        {"path": "dirty_existing.txt", "tracked_dirty": True, "untracked": False, "exists": True, "kind": "file", "sha256": "a", "size_bytes": 1},
        {"path": "unchanged.txt", "tracked_dirty": True, "untracked": False, "exists": True, "kind": "file", "sha256": "same", "size_bytes": 1},
    ]
    current = [
        {"path": "dirty_existing.txt", "tracked_dirty": True, "untracked": False, "exists": True, "kind": "file", "sha256": "b", "size_bytes": 1},
        {"path": "new_file.txt", "tracked_dirty": True, "untracked": False, "exists": True, "kind": "file", "sha256": "c", "size_bytes": 1},
        {"path": "scratch.txt", "tracked_dirty": False, "untracked": True, "exists": True, "kind": "file", "sha256": "d", "size_bytes": 1},
        {"path": "unchanged.txt", "tracked_dirty": True, "untracked": False, "exists": True, "kind": "file", "sha256": "same", "size_bytes": 1},
    ]

    payload = residue.summarize_repo_residue_delta(baseline_entries=baseline, current_entries=current)

    assert payload["changed_since_start_paths"] == ["dirty_existing.txt", "new_file.txt", "scratch.txt"]
    assert payload["clean_at_start_changed_paths"] == ["new_file.txt", "scratch.txt"]
    assert payload["preexisting_dirty_changed_paths"] == ["dirty_existing.txt"]
    assert payload["new_untracked_paths"] == ["scratch.txt"]
