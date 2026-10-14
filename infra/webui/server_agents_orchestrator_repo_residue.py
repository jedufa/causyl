"""Repo-residue helper seams for server_agents_orchestrator.

These helpers are intentionally pure so residue-delta behavior can be tested
without importing the entire orchestration runtime.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List


def repo_entry_signature(entry: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "path": str(entry.get("path") or ""),
            "tracked_dirty": bool(entry.get("tracked_dirty")),
            "untracked": bool(entry.get("untracked")),
            "exists": bool(entry.get("exists")),
            "kind": str(entry.get("kind") or ""),
            "sha256": str(entry.get("sha256") or ""),
            "size_bytes": int(entry.get("size_bytes") or 0),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def summarize_repo_residue_delta(
    *,
    baseline_entries: Iterable[Dict[str, Any]],
    current_entries: Iterable[Dict[str, Any]],
) -> Dict[str, List[str]]:
    baseline_by_path = {
        str(row.get("path") or ""): row
        for row in baseline_entries
        if isinstance(row, dict) and str(row.get("path") or "").strip()
    }
    current_by_path = {
        str(row.get("path") or ""): row
        for row in current_entries
        if isinstance(row, dict) and str(row.get("path") or "").strip()
    }
    changed_since_start_paths: List[str] = []
    clean_at_start_changed_paths: List[str] = []
    preexisting_dirty_changed_paths: List[str] = []
    new_untracked_paths: List[str] = []
    for relpath, current_row in current_by_path.items():
        base_row = baseline_by_path.get(relpath)
        changed = base_row is None or repo_entry_signature(base_row) != repo_entry_signature(current_row)
        if not changed:
            continue
        changed_since_start_paths.append(relpath)
        if base_row is None:
            clean_at_start_changed_paths.append(relpath)
        else:
            preexisting_dirty_changed_paths.append(relpath)
        if bool(current_row.get("untracked")):
            new_untracked_paths.append(relpath)
    return {
        "changed_since_start_paths": sorted(changed_since_start_paths),
        "clean_at_start_changed_paths": sorted(clean_at_start_changed_paths),
        "preexisting_dirty_changed_paths": sorted(preexisting_dirty_changed_paths),
        "new_untracked_paths": sorted(new_untracked_paths),
    }
