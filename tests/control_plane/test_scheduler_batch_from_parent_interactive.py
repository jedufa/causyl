from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_batch_from_parent as batch


def _write_item(root: Path, uid: str, slug: str, *, title: str, status: str = "draft", updated_utc: str = "", created_utc: str = "", parent_uid: str = "") -> None:
    item_dir = root / "governance" / "ledger" / "patch_instruction_sheets" / f"{uid}__{slug}"
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
    (item_dir / "pis.md").write_text(f"# PIS\n\n**UID:** {uid}\n", encoding="utf-8")
    events = [json.dumps({"type": "created", "payload": {"title": title}})]
    if parent_uid:
        events.append(json.dumps({"type": "decomposed_from_parent", "payload": {"parent_uid": parent_uid, "relationship": "execution_child", "critical_path_rank": 1, "depends_on": []}}))
    (item_dir / "events.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")


def test_load_recent_parent_choices_orders_newest_first(tmp_path: Path) -> None:
    _write_item(tmp_path, "PIS-20260326-203500Z-R7K2", "parent-new", title="New Parent", updated_utc="2026-03-26T20:35:00Z", created_utc="2026-03-26T20:35:00Z")
    _write_item(tmp_path, "PIS-20260325-100000Z-A1B2", "parent-old", title="Old Parent", updated_utc="2026-03-25T10:00:00Z", created_utc="2026-03-25T10:00:00Z")
    _write_item(tmp_path, "PIS-20260326-210001Z-C1D2", "child-a", title="Child A", parent_uid="PIS-20260326-203500Z-R7K2")
    _write_item(tmp_path, "PIS-20260325-110001Z-E3F4", "child-b", title="Child B", parent_uid="PIS-20260325-100000Z-A1B2")

    rows = batch._load_recent_parent_choices(20, repo_root=tmp_path)
    assert [row.uid for row in rows] == [
        "PIS-20260326-203500Z-R7K2",
        "PIS-20260325-100000Z-A1B2",
    ]


def test_select_parent_uid_uses_default_recent_parent(monkeypatch, tmp_path: Path) -> None:
    _write_item(tmp_path, "PIS-20260326-203500Z-R7K2", "parent-new", title="New Parent", updated_utc="2026-03-26T20:35:00Z", created_utc="2026-03-26T20:35:00Z")
    _write_item(tmp_path, "PIS-20260325-100000Z-A1B2", "parent-old", title="Old Parent", updated_utc="2026-03-25T10:00:00Z", created_utc="2026-03-25T10:00:00Z")
    _write_item(tmp_path, "PIS-20260326-210001Z-C1D2", "child-a", title="Child A", parent_uid="PIS-20260326-203500Z-R7K2")
    _write_item(tmp_path, "PIS-20260325-110001Z-E3F4", "child-b", title="Child B", parent_uid="PIS-20260325-100000Z-A1B2")

    monkeypatch.setattr(batch, "prompt_menu", lambda *args, **kwargs: kwargs["choices"][0])
    selected = batch._select_parent_uid("", repo_root=tmp_path, limit=20)
    assert selected == "PIS-20260326-203500Z-R7K2"
