from __future__ import annotations

import json
from pathlib import Path
import zipfile

from tools.bin.pis_import_zip import import_bundle
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui.ledger import ledger_storage


def _build_bundle(zip_path: Path) -> None:
    root = "sample_pis_bundle"
    manifest = {
        "bundle_revision": "test_bundle_v1",
        "items": [
            {
                "uid": "PIS-20260404-193000Z-X4K1-parent-item",
                "title": "Parent Item",
                "parent_uid": None,
                "family": "family_a",
                "depends_on": [],
                "filename": "01__20260404-193000Z-X4K1-parent-item__parent-item.md",
                "pis_kind": "governance_parent",
                "claim_strength_target": "integration",
                "proof_mode": "none",
                "scope_fence": "parent only",
                "structured_dependencies": [],
            },
            {
                "uid": "PIS-20260404-193100Z-C7R2-child-item",
                "title": "Child Item",
                "parent_uid": "PIS-20260404-193000Z-X4K1-parent-item",
                "family": "family_a",
                "depends_on": ["PIS-20260404-193000Z-X4K1-parent-item"],
                "filename": "02__20260404-193100Z-C7R2-child-item__child-item.md",
                "pis_kind": "execution_child",
                "claim_strength_target": "substrate",
                "proof_mode": "narrow",
                "scope_fence": "child only",
                "structured_dependencies": [
                    {"uid": "PIS-20260404-193000Z-X4K1-parent-item", "kind": "hard"}
                ],
            },
        ],
    }
    parent_md = """---
document_type: patch_instruction_sheet
uid: PIS-20260404-193000Z-X4K1-parent-item
title: Parent Item
---

# Patch Instruction Sheet (PIS)

**UID:** PIS-20260404-193000Z-X4K1-parent-item
"""
    child_md = """---
document_type: patch_instruction_sheet
uid: PIS-20260404-193100Z-C7R2-child-item
parent_uid: PIS-20260404-193000Z-X4K1-parent-item
---

# Patch Instruction Sheet (PIS)

**UID:** PIS-20260404-193100Z-C7R2-child-item
**Parent UID:** PIS-20260404-193000Z-X4K1-parent-item
"""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/pis_manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(f"{root}/01__20260404-193000Z-X4K1-parent-item__parent-item.md", parent_md)
        zf.writestr(f"{root}/02__20260404-193100Z-C7R2-child-item__child-item.md", child_md)


def test_import_bundle_creates_canonical_ledger_items_and_preserves_order(tmp_path: Path) -> None:
    zip_path = tmp_path / "bundle.zip"
    _build_bundle(zip_path)

    result = import_bundle(
        repo_root=tmp_path,
        zip_path=zip_path,
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )

    assert result["ok"] is True
    assert len(result["imported"]) == 2

    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.read_item("PIS-20260404-193000Z-X4K1")
    child = ledger.read_item("PIS-20260404-193100Z-C7R2")

    assert parent["meta"]["execution_priority"] == 1
    assert child["meta"]["execution_priority"] == 2
    assert child["meta"]["parent_uid"] == "PIS-20260404-193000Z-X4K1"
    assert child["meta"]["integration_meta"]["source_uid"] == "PIS-20260404-193100Z-C7R2-child-item"
    assert child["meta"]["integration_meta"]["depends_on"] == ["PIS-20260404-193000Z-X4K1"]
    assert child["meta"]["integration_meta"]["structured_dependencies"] == [
        {"uid": "PIS-20260404-193000Z-X4K1", "kind": "hard"}
    ]
    assert "**UID:** PIS-20260404-193100Z-C7R2" in child["pis_markdown"]
    assert "**Parent UID:** PIS-20260404-193000Z-X4K1" in child["pis_markdown"]

    item_dir = ledger.get_item_dir("PIS-20260404-193100Z-C7R2")
    import_record = ledger_storage.read_json(item_dir / "artifacts" / "import_record.json")
    assert import_record["canonical_uid"] == "PIS-20260404-193100Z-C7R2"


def test_import_bundle_skips_existing_canonical_uid(tmp_path: Path) -> None:
    zip_path = tmp_path / "bundle.zip"
    _build_bundle(zip_path)

    first = import_bundle(
        repo_root=tmp_path,
        zip_path=zip_path,
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    second = import_bundle(
        repo_root=tmp_path,
        zip_path=zip_path,
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )

    assert len(first["imported"]) == 2
    assert len(second["imported"]) == 0
    assert len(second["skipped"]) == 2
