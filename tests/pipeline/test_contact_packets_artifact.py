from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

from infra.webui.ledger.ledger_manager import ChangeLedger

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = (
    ChangeLedger(repo_root=REPO_ROOT).get_item_dir("PIS-20260502-141933Z-B1D5") / "artifacts" / "contact_prep"
)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_csv_export(path: Path, packets: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["property_name"])
        writer.writeheader()
        for packet in packets:
            assert isinstance(packet, dict)
            writer.writerow({"property_name": packet["property_name"]})


def test_builder_output_validates(tmp_path: Path, monkeypatch) -> None:
    builder = _load_module(ARTIFACT_DIR / "build_contact_packets.py", "pis_b1d5_builder")
    validator = _load_module(ARTIFACT_DIR / "validate_contact_packets.py", "pis_b1d5_validator")

    artifact = builder.build_artifact()
    csv_path = tmp_path / "contact_packets.csv"
    _write_csv_export(csv_path, artifact["contact_packets"])
    artifact["csv_export"]["path"] = str(csv_path)
    monkeypatch.setattr(validator, "CSV_OUTPUT_PATH", csv_path)

    assert artifact["coverage_summary"]["shortlist_entries_needing_contact"] == 1
    assert artifact["coverage_summary"]["packets_with_missing_direct_channel"] == 1
    assert artifact["contact_packets"][0]["recommended_subject"].endswith("Hebergement Temilac")
    assert "best contact path or next step" in artifact["contact_packets"][0]["customized_message_draft"]
    assert validator.validate_artifact(artifact) == []


def test_validator_rejects_missing_contact_required_packet() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_contact_packets.py", "pis_b1d5_validator_missing")
    artifact = json.loads((Path("governance/evidence/contact_prep/contact_packets.json")).read_text(encoding="utf-8"))
    artifact["contact_packets"] = []
    artifact["coverage_summary"]["packets_created"] = 0
    artifact["coverage_summary"]["packets_with_missing_direct_channel"] = 0

    issues = validator.validate_artifact(artifact)

    assert any(
        "CONTACT_REQUIRED shortlist item Hebergement Temilac has no corresponding packet and no documented channel gap"
        in issue
        for issue in issues
    )


def test_validator_rejects_missing_channel_gap_evidence() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_contact_packets.py", "pis_b1d5_validator_channel_gap")
    artifact = json.loads((Path("governance/evidence/contact_prep/contact_packets.json")).read_text(encoding="utf-8"))
    artifact["coverage_summary"]["packets_with_missing_direct_channel"] = 0
    artifact["contact_packets"][0]["contact_channel"]["source_urls_checked"] = []

    issues = validator.validate_artifact(artifact)

    assert "coverage_summary.packets_with_missing_direct_channel drifted from packet truth" in issues
    assert any(
        "Hebergement Temilac packet is missing source_urls_checked for its direct-channel gap" in issue
        for issue in issues
    )


def test_validator_rejects_missing_contact_path_close() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_contact_packets.py", "pis_b1d5_validator_contact_path")
    artifact = json.loads((Path("governance/evidence/contact_prep/contact_packets.json")).read_text(encoding="utf-8"))
    artifact["contact_packets"][0]["customized_message_draft"] = artifact["contact_packets"][0][
        "customized_message_draft"
    ].replace(
        "If email is not the best way to handle this, please let me know the best contact path or next step.\n\n",
        "",
    )

    issues = validator.validate_artifact(artifact)

    assert any(
        "Hebergement Temilac packet customized_message_draft is missing the contact-path close" in issue
        for issue in issues
    )
