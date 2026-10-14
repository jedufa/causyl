from __future__ import annotations

import json
from pathlib import Path
import zipfile

from tools.bin.intent_sheet_import_zip import import_bundle
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui.ledger import ledger_storage


def _build_bundle(zip_path: Path) -> None:
    root = "sample_bundle"
    manifest = {
        "bundle_revision": "test_bundle_v1",
        "sheets": [
            {
                "id": "IS-01",
                "filename": "IS-01_alpha.md",
                "slug": "alpha",
                "title": "Alpha Contract",
                "legacy_title": "Alpha Contract",
                "naming_class": "contract",
                "intent_kind": "substrate_contract",
                "portfolio_group": "substrate",
                "wave": "W1",
                "wave_label": "Foundations",
                "sequence_index": 1,
            },
            {
                "id": "IS-02",
                "filename": "IS-02_beta.md",
                "slug": "beta",
                "title": "Beta Policy",
                "legacy_title": "Beta Policy",
                "naming_class": "policy",
                "intent_kind": "governance_policy",
                "portfolio_group": "operations",
                "wave": "W2",
                "wave_label": "Ops",
                "sequence_index": 2,
            },
        ],
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/intent_sheet_manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(f"{root}/IS-01_alpha.md", "---\ntitle: Alpha Contract\n---\n\n# Alpha Contract\n\nBody.\n")
        zf.writestr(f"{root}/IS-02_beta.md", "# Beta Policy\n\nPolicy body.\n")


def _build_bundle_v2(zip_path: Path) -> None:
    root = "sample_bundle_v2"
    manifest = {
        "bundle_revision": "test_bundle_v2",
        "sheets": [
            {
                "id": "IS-A01",
                "path": "portfolio_a/IS-A01_alpha.md",
                "slug": "alpha",
                "title": "Alpha Contract",
                "intent_kind": "contract_or_policy",
                "portfolio_group": "substrate",
                "execution_wave": 0,
                "wave_label": "foundation_backbone",
                "critical_path": True,
                "depends_on": [],
            },
            {
                "id": "IS-A02",
                "path": "portfolio_a/IS-A02_beta.md",
                "slug": "beta",
                "title": "Beta Policy",
                "intent_kind": "concept",
                "portfolio_group": "operations",
                "execution_wave": 1,
                "wave_label": "operationalization",
                "depends_on": ["IS-A01"],
            },
        ],
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/intent_sheet_manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(f"{root}/portfolio_a/IS-A01_alpha.md", "# Alpha Contract\n\nBody.\n")
        zf.writestr(f"{root}/portfolio_a/IS-A02_beta.md", "# Beta Policy\n\nPolicy body.\n")


def test_import_bundle_creates_intent_sheets_with_provenance(tmp_path: Path) -> None:
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
    ledger = IntentSheetLedger(repo_root=tmp_path)
    rows = ledger.list_sheets(limit=10)
    assert len(rows) == 2
    by_title = {row["title"]: row for row in rows}
    alpha = ledger.read_sheet(by_title["Alpha Contract"]["uid"])
    assert alpha["intent_markdown"].startswith("---\n")
    assert alpha["meta"]["project_id"] == "causyl"
    assert alpha["meta"]["workstream_type"] == "shared_capability"
    assert alpha["meta"]["scope_boundary"] == "cross-project"
    assert alpha["meta"]["execution_priority"] == 101
    assert alpha["meta"]["recommended_wave"] == "W1"
    assert alpha["meta"]["critical_path"] is False
    assert "contract" in alpha["meta"]["tags"]
    d = ledger.get_sheet_dir(by_title["Alpha Contract"]["uid"])
    import_record = ledger_storage.read_json(d / "artifacts" / "import_record.json")
    assert import_record["manifest_entry"]["id"] == "IS-01"
    assert import_record["source_member"].endswith("/IS-01_alpha.md")


def test_import_bundle_supports_v2_manifest_path_and_derives_ordering(tmp_path: Path) -> None:
    zip_path = tmp_path / "bundle_v2.zip"
    _build_bundle_v2(zip_path)

    result = import_bundle(
        repo_root=tmp_path,
        zip_path=zip_path,
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )

    assert result["ok"] is True
    assert len(result["imported"]) == 2
    ledger = IntentSheetLedger(repo_root=tmp_path)
    rows = ledger.list_sheets(limit=10)
    by_title = {row["title"]: row for row in rows}
    alpha = ledger.read_sheet(by_title["Alpha Contract"]["uid"])
    beta = ledger.read_sheet(by_title["Beta Policy"]["uid"])

    assert alpha["meta"]["sequence_index"] == 1
    assert alpha["meta"]["recommended_order_in_wave"] == 1
    assert alpha["meta"]["recommended_wave"] == "W0"
    assert alpha["meta"]["execution_priority"] == 1
    assert alpha["meta"]["critical_path"] is True

    assert beta["meta"]["sequence_index"] == 2
    assert beta["meta"]["recommended_order_in_wave"] == 1
    assert beta["meta"]["recommended_wave"] == "W1"
    assert beta["meta"]["execution_priority"] == 101
    assert beta["meta"]["depends_on"] == [{"id": "IS-A01", "kind": "hard"}]

    d = ledger.get_sheet_dir(by_title["Beta Policy"]["uid"])
    import_record = ledger_storage.read_json(d / "artifacts" / "import_record.json")
    assert import_record["manifest_entry"]["path"] == "portfolio_a/IS-A02_beta.md"
    assert import_record["source_member"].endswith("/portfolio_a/IS-A02_beta.md")


def test_import_bundle_skips_already_imported_members(tmp_path: Path) -> None:
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
