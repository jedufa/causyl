from __future__ import annotations

import json
from pathlib import Path

from tools.bin import ledger_storage_admin
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui.ledger import ledger_storage


def _pis_root(base: Path) -> Path:
    return base / "governance" / "ledger" / "patch_instruction_sheets"


def _write_item(
    base: Path,
    rel: str,
    *,
    uid: str,
    slug: str,
    status: str,
    created_utc: str,
    project_id: str = "causyl",
) -> Path:
    item_dir = base / rel
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (item_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": uid,
                "slug": slug,
                "title": uid,
                "status": status,
                "created_utc": created_utc,
                "updated_utc": created_utc,
                "project_id": project_id,
            }
        ),
        encoding="utf-8",
    )
    (item_dir / "pis.md").write_text(f"# {uid}\n", encoding="utf-8")
    (item_dir / "events.jsonl").write_text("", encoding="utf-8")
    return item_dir


def test_list_item_dirs_and_registry_support_flat_and_sharded_layouts(tmp_path: Path) -> None:
    items_root = _pis_root(tmp_path)
    flat = _write_item(
        items_root,
        "PIS-20260329-101010Z-AAAA__flat-item",
        uid="PIS-20260329-101010Z-AAAA",
        slug="flat-item",
        status="draft",
        created_utc="2026-03-29T10:10:10Z",
    )
    sharded = _write_item(
        items_root,
        "verified/2026/03/PIS-20260328-090000Z-BBBB__done-item",
        uid="PIS-20260328-090000Z-BBBB",
        slug="done-item",
        status="verified",
        created_utc="2026-03-28T09:00:00Z",
        project_id="causyl",
    )

    dirs = ledger_storage.list_item_dirs(items_root)

    assert [d.name for d in dirs] == [flat.name, sharded.name]

    registry = ledger_storage.write_uid_registry(items_root)
    assert registry["items"]["PIS-20260329-101010Z-AAAA"]["path"] == "PIS-20260329-101010Z-AAAA__flat-item"
    assert registry["items"]["PIS-20260328-090000Z-BBBB"]["path"] == "verified/2026/03/PIS-20260328-090000Z-BBBB__done-item"
    assert ledger_storage.item_dir_for_uid(items_root, "PIS-20260328-090000Z-BBBB") == sharded.resolve()


def test_planned_item_dir_uses_lifecycle_bucket_and_year_month() -> None:
    items_root = Path("/tmp/example-items")

    target = ledger_storage.planned_item_dir(
        items_root,
        uid="PIS-20260329-101010Z-AAAA",
        slug="flat-item",
        project_id="causyl",
        status="draft",
        created_utc="2026-03-29T10:10:10Z",
        storage_layout="status_sharded",
    )

    assert str(target).endswith("causyl/active/2026/03/PIS-20260329-101010Z-AAAA__flat-item")


def test_planned_item_dir_maps_deferred_status_to_deferred_bucket() -> None:
    items_root = Path("/tmp/example-items")

    target = ledger_storage.planned_item_dir(
        items_root,
        uid="PIS-20260329-121212Z-CCCC",
        slug="deferred-item",
        project_id="emergence",
        status="deferred",
        created_utc="2026-03-29T12:12:12Z",
        storage_layout="status_sharded",
    )

    assert str(target).endswith("emergence/deferred/2026/03/PIS-20260329-121212Z-CCCC__deferred-item")


def test_plan_rows_marks_flat_items_for_move(tmp_path: Path) -> None:
    repo_root = tmp_path
    items_root = _pis_root(repo_root)
    _write_item(
        items_root,
        "PIS-20260329-101010Z-AAAA__flat-item",
        uid="PIS-20260329-101010Z-AAAA",
        slug="flat-item",
        status="draft",
        created_utc="2026-03-29T10:10:10Z",
    )
    _write_item(
        items_root,
        "verified/2026/03/PIS-20260328-090000Z-BBBB__done-item",
        uid="PIS-20260328-090000Z-BBBB",
        slug="done-item",
        status="verified",
        created_utc="2026-03-28T09:00:00Z",
    )

    rows = ledger_storage_admin._plan_rows(repo_root)
    by_uid = {row["uid"]: row for row in rows}

    assert by_uid["PIS-20260329-101010Z-AAAA"]["needs_move"] is True
    assert by_uid["PIS-20260329-101010Z-AAAA"]["target_rel"] == "causyl/active/2026/03/PIS-20260329-101010Z-AAAA__flat-item"
    assert by_uid["PIS-20260328-090000Z-BBBB"]["needs_move"] is True
    assert by_uid["PIS-20260328-090000Z-BBBB"]["target_rel"] == "causyl/verified/2026/03/PIS-20260328-090000Z-BBBB__done-item"


def test_plan_rows_moves_deferred_items_out_of_active_bucket(tmp_path: Path) -> None:
    repo_root = tmp_path
    items_root = _pis_root(repo_root)
    _write_item(
        items_root,
        "active/2026/03/PIS-20260329-121212Z-CCCC__deferred-item",
        uid="PIS-20260329-121212Z-CCCC",
        slug="deferred-item",
        status="deferred",
        created_utc="2026-03-29T12:12:12Z",
    )

    rows = ledger_storage_admin._plan_rows(repo_root)
    by_uid = {row["uid"]: row for row in rows}

    assert by_uid["PIS-20260329-121212Z-CCCC"]["storage_bucket"] == "deferred"
    assert by_uid["PIS-20260329-121212Z-CCCC"]["needs_move"] is True
    assert by_uid["PIS-20260329-121212Z-CCCC"]["target_rel"] == "causyl/deferred/2026/03/PIS-20260329-121212Z-CCCC__deferred-item"


def test_change_ledger_create_item_defaults_to_status_sharded_layout(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)

    created = ledger.create_item(title="Storage Layout Default", slug="storage-layout-default")
    item_dir = ledger.get_item_dir(created["uid"])
    item = ledger.read_item(created["uid"])

    rel = str(item_dir.relative_to(_pis_root(tmp_path))).replace("\\", "/")
    assert rel.startswith("causyl/active/")
    assert item_dir.name.endswith("__storage-layout-default")
    assert item["meta"]["project_id"] == "causyl"
    assert item["meta"]["workstream_type"] == "platform"
    assert item["meta"]["scope_boundary"] == "platform-local"
    assert item["meta"]["artifact_roots"] == []


def test_change_ledger_read_item_applies_project_defaults_to_legacy_meta(tmp_path: Path) -> None:
    items_root = _pis_root(tmp_path)
    uid = "PIS-20260329-101010Z-AAAA"
    _write_item(
        items_root,
        "active/2026/03/PIS-20260329-101010Z-AAAA__legacy-item",
        uid=uid,
        slug="legacy-item",
        status="draft",
        created_utc="2026-03-29T10:10:10Z",
    )

    ledger = ChangeLedger(repo_root=tmp_path)
    item = ledger.read_item(uid)

    assert item["meta"]["project_id"] == "causyl"
    assert item["meta"]["workstream_type"] == "platform"
    assert item["meta"]["scope_boundary"] == "platform-local"
    assert item["meta"]["artifact_roots"] == []


def test_change_ledger_create_item_accepts_explicit_project_scope(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)

    created = ledger.create_item(
        title="Emergence import item",
        slug="emergence-import-item",
        project_id="emergence",
        workstream_type="project",
        scope_boundary="project-local",
        artifact_roots=["experiments/projects/emergence", "../emergence__dev_EIEL"],
    )
    item = ledger.read_item(created["uid"])
    item_dir = ledger.get_item_dir(created["uid"])
    rel = str(item_dir.relative_to(_pis_root(tmp_path))).replace("\\", "/")

    assert item["meta"]["project_id"] == "emergence"
    assert item["meta"]["workstream_type"] == "project"
    assert item["meta"]["scope_boundary"] == "project-local"
    assert item["meta"]["artifact_roots"] == ["experiments/projects/emergence", "../emergence__dev_EIEL"]
    assert rel.startswith("emergence/active/")


def test_change_ledger_update_status_relocates_item_between_lifecycle_buckets(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)

    created = ledger.create_item(title="Move Me", slug="move-me")
    uid = created["uid"]
    before = ledger.get_item_dir(uid)
    assert str(before.relative_to(_pis_root(tmp_path))).replace("\\", "/").startswith("causyl/active/")

    ledger.update_status(uid, "verified", note="close it")
    after = ledger.get_item_dir(uid)
    rel = str(after.relative_to(_pis_root(tmp_path))).replace("\\", "/")

    assert rel.startswith("causyl/verified/")
    assert not before.exists()
    registry = ledger_storage.load_uid_registry(_pis_root(tmp_path))
    assert registry["items"][uid]["path"] == rel


def test_item_dir_for_uid_fails_closed_when_duplicate_uid_exists(tmp_path: Path) -> None:
    items_root = _pis_root(tmp_path)
    uid = "PIS-20260328-090000Z-BBBB"
    _write_item(
        items_root,
        "active/2026/03/PIS-20260328-090000Z-BBBB__first",
        uid=uid,
        slug="first",
        status="draft",
        created_utc="2026-03-28T09:00:00Z",
    )
    _write_item(
        items_root,
        "verified/2026/03/PIS-20260328-090000Z-BBBB__second",
        uid=uid,
        slug="second",
        status="verified",
        created_utc="2026-03-28T09:00:00Z",
    )

    registry = ledger_storage.write_uid_registry(items_root)

    assert uid in registry["duplicates"]
    assert ledger_storage.item_dir_for_uid(items_root, uid) is None
    duplicates = ledger_storage.duplicate_paths_for_uid(items_root, uid)
    assert len(duplicates) == 2


def test_load_uid_registry_repairs_stale_entry_after_verified_move(tmp_path: Path) -> None:
    items_root = _pis_root(tmp_path)
    uid = "PIS-20260328-090000Z-BBBB"
    verified = _write_item(
        items_root,
        "verified/2026/03/PIS-20260328-090000Z-BBBB__done-item",
        uid=uid,
        slug="done-item",
        status="verified",
        created_utc="2026-03-28T09:00:00Z",
    )
    stale_active = items_root / "causyl" / "active" / "2026" / "03" / f"{uid}__done-item"
    stale_active.mkdir(parents=True, exist_ok=True)
    ledger_storage.atomic_write_json(
        ledger_storage.ledger_registry_path(items_root),
        {
            "schema_version": "ledger_uid_registry_v1",
            "generated_utc": "2026-03-28T09:00:00Z",
            "items": {
                uid: {
                    "path": "causyl/active/2026/03/PIS-20260328-090000Z-BBBB__done-item",
                    "slug": "done-item",
                    "title": uid,
                    "status": "draft",
                    "project_id": "causyl",
                    "storage_bucket": "active",
                    "created_utc": "2026-03-28T09:00:00Z",
                    "updated_utc": "2026-03-28T09:00:00Z",
                }
            },
            "duplicates": {},
        },
    )

    repaired = ledger_storage.load_uid_registry(items_root)

    assert repaired["items"][uid]["path"] == "verified/2026/03/PIS-20260328-090000Z-BBBB__done-item"
    assert ledger_storage.item_dir_for_uid(items_root, uid) == verified.resolve()


def test_change_ledger_get_item_dir_rejects_duplicate_uid_state(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    items_root = _pis_root(tmp_path)
    uid = "PIS-20260328-090000Z-BBBB"
    _write_item(
        items_root,
        "active/2026/03/PIS-20260328-090000Z-BBBB__first",
        uid=uid,
        slug="first",
        status="draft",
        created_utc="2026-03-28T09:00:00Z",
    )
    _write_item(
        items_root,
        "verified/2026/03/PIS-20260328-090000Z-BBBB__second",
        uid=uid,
        slug="second",
        status="verified",
        created_utc="2026-03-28T09:00:00Z",
    )
    ledger_storage.write_uid_registry(items_root)

    try:
        ledger.get_item_dir(uid)
    except RuntimeError as exc:
        assert "ambiguous uid" in str(exc)
    else:
        raise AssertionError("expected duplicate uid lookup to fail closed")


def test_change_ledger_update_status_relocates_item_into_deferred_bucket(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)

    created = ledger.create_item(title="Defer Me", slug="defer-me")
    uid = created["uid"]

    ledger.update_status(uid, "deferred", note="triaged_for_later")
    after = ledger.get_item_dir(uid)
    rel = str(after.relative_to(_pis_root(tmp_path))).replace("\\", "/")

    assert rel.startswith("causyl/deferred/")
    registry = ledger_storage.load_uid_registry(_pis_root(tmp_path))
    assert registry["items"][uid]["storage_bucket"] == "deferred"
    assert registry["items"][uid]["path"] == rel


def test_change_ledger_update_meta_relocates_item_into_project_folder(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Project Move", slug="project-move")
    uid = created["uid"]

    before = ledger.get_item_dir(uid)
    ledger.update_meta(
        uid,
        {
            "project_id": "emergence",
            "workstream_type": "project",
            "scope_boundary": "project-local",
            "artifact_roots": ["/home/user/emergence"],
        },
    )
    after = ledger.get_item_dir(uid)
    meta = ledger.read_item(uid)["meta"]
    rel = str(after.relative_to(_pis_root(tmp_path))).replace("\\", "/")

    assert before != after
    assert not before.exists()
    assert rel.startswith("emergence/active/")
    assert meta["project_id"] == "emergence"
    assert meta["workstream_type"] == "project"
    assert meta["scope_boundary"] == "project-local"
    assert meta["artifact_roots"] == ["/home/user/emergence"]


def test_change_ledger_create_item_normalizes_flat_creation_into_status_sharded_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    original_ensure_item_dir = ledger_storage.ensure_item_dir

    def _flat_ensure_item_dir(items_root: Path, **kwargs: object) -> Path:
        kwargs.pop("storage_layout", None)
        return original_ensure_item_dir(items_root, storage_layout="flat", **kwargs)

    monkeypatch.setattr(ledger_storage, "ensure_item_dir", _flat_ensure_item_dir)

    created = ledger.create_item(title="Normalize Flat Create", slug="normalize-flat-create")
    item_dir = ledger.get_item_dir(created["uid"])
    rel = str(item_dir.relative_to(_pis_root(tmp_path))).replace("\\", "/")

    assert rel.startswith("causyl/active/")
    assert item_dir.name.endswith("__normalize-flat-create")
    assert not (_pis_root(tmp_path) / item_dir.name).exists()


def test_change_ledger_create_item_rejects_duplicate_uid_at_write_time(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = "PIS-20260329-121212Z-CCCC"

    created = ledger.create_item(title="First", slug="first", uid_override=uid)

    try:
        ledger.create_item(title="Second", slug="second", uid_override=uid)
    except FileExistsError as exc:
        assert uid in str(exc)
    else:
        raise AssertionError("expected duplicate uid create to fail closed")

    item_dir = ledger.get_item_dir(created["uid"])
    assert item_dir.name.endswith("__first")
    assert ledger_storage.duplicate_paths_for_uid(_pis_root(tmp_path), uid) == []


def test_change_ledger_update_status_rolls_back_when_meta_write_fails(tmp_path: Path, monkeypatch) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Rollback Me", slug="rollback-me")
    uid = created["uid"]
    original_dir = ledger.get_item_dir(uid)
    original_meta = ledger_storage.read_json(original_dir / "meta.json") or {}
    original_atomic_write_json = ledger_storage.atomic_write_json

    def _failing_atomic_write_json(path: Path, obj: object) -> None:
        if Path(path) == ledger_storage.planned_item_dir(
            _pis_root(tmp_path),
            uid=uid,
            slug="rollback-me",
            project_id="causyl",
            status="verified",
            created_utc=str(original_meta.get("created_utc") or ""),
            storage_layout="status_sharded",
        ) / "meta.json":
            raise RuntimeError("boom")
        original_atomic_write_json(path, obj)

    monkeypatch.setattr(ledger_storage, "atomic_write_json", _failing_atomic_write_json)

    try:
        ledger.update_status(uid, "verified", note="should fail")
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected status update to fail")

    current_dir = ledger.get_item_dir(uid)
    current_meta = ledger_storage.read_json(current_dir / "meta.json") or {}
    rel = str(current_dir.relative_to(_pis_root(tmp_path))).replace("\\", "/")
    assert current_dir == original_dir
    assert rel.startswith("causyl/active/")
    assert current_meta["status"] == original_meta["status"]


def test_change_ledger_create_revision_fails_closed_when_old_deprecation_fails(tmp_path: Path, monkeypatch) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Original", slug="original")
    uid = created["uid"]
    original_update_status = ledger.update_status

    def _failing_update_status(target_uid: str, status: str, *, note: str = "") -> dict[str, object]:
        if target_uid == uid and status == "deprecated":
            raise RuntimeError("cannot deprecate old item")
        return original_update_status(target_uid, status, note=note)

    monkeypatch.setattr(ledger, "update_status", _failing_update_status)

    try:
        ledger.create_revision(uid)
    except RuntimeError as exc:
        assert "failed to deprecate prior revision" in str(exc)
    else:
        raise AssertionError("expected revision creation to fail closed")

    items = ledger.list_items(limit=20)
    assert [item["uid"] for item in items] == [uid]
    old_item = ledger.read_item(uid)
    assert old_item["meta"]["status"] == "draft"
