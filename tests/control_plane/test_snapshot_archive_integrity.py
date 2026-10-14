from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_kv_block(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = str(raw or "").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


@pytest.mark.skipif(shutil.which("zip") is None, reason="zip binary is required for archive snapshot test")
def test_archive_snapshot_manifest_and_validation_summary_share_the_same_identity(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools" / "bin").mkdir(parents=True, exist_ok=True)
    (repo_root / "config").mkdir(parents=True, exist_ok=True)
    (repo_root / "README.md").write_text("snapshot test repo\n", encoding="utf-8")

    shutil.copy2(REPO_ROOT / "tools" / "bin" / "archive_complete_repo_snapshot.sh", repo_root / "tools" / "bin" / "archive_complete_repo_snapshot.sh")
    shutil.copy2(REPO_ROOT / "config" / "archive_profiles.toml", repo_root / "config" / "archive_profiles.toml")

    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "snapshot@example.com"], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Snapshot Test"], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "bootstrap snapshot test repo"], cwd=repo_root, check=True, capture_output=True, text=True)

    cp = subprocess.run(
        ["bash", "infra/bin/archive_complete_repo_snapshot.sh", "--debug"],
        cwd=repo_root,
        env={**os.environ, "EIEL_COLOR": "0"},
        check=True,
        capture_output=True,
        text=True,
    )
    marker = "SNAPSHOT_ZIP_PATH="
    zip_line = next((line for line in cp.stdout.splitlines() if line.startswith(marker)), "")
    assert zip_line, cp.stdout
    zip_path = (repo_root / zip_line.split("=", 1)[1].strip()).resolve()
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "SNAPSHOT_MANIFEST.txt" in names
        assert "SNAPSHOT_VALIDATION_SUMMARY.txt" in names
        manifest = zf.read("SNAPSHOT_MANIFEST.txt").decode("utf-8")
        summary = zf.read("SNAPSHOT_VALIDATION_SUMMARY.txt").decode("utf-8")

    manifest_kv = _parse_kv_block(manifest)
    summary_kv = _parse_kv_block(summary)
    zip_basename = zip_path.name

    assert manifest_kv["snapshot_identity_version"] == "causyl_snapshot_identity_v1"
    assert manifest_kv["zip_filename"] == zip_basename
    assert manifest_kv["expected_zip_filename"] == zip_basename
    assert manifest_kv["validation_summary_filename"] == "SNAPSHOT_VALIDATION_SUMMARY.txt"
    assert manifest_kv["created_timezone"] == "Europe/Vienna"
    assert manifest_kv["archive_profile"] == "debug"

    assert summary_kv["snapshot_identity_version"] == "causyl_snapshot_identity_v1"
    assert summary_kv["zip_filename"] == zip_basename
    assert summary_kv["expected_zip_filename"] == zip_basename
    assert summary_kv["manifest_filename"] == "SNAPSHOT_MANIFEST.txt"
    assert summary_kv["validation_summary_filename"] == "SNAPSHOT_VALIDATION_SUMMARY.txt"
    assert summary_kv["created_timezone"] == "Europe/Vienna"
    assert summary_kv["archive_profile"] == "debug"
    assert summary_kv["identity_match"] == "true"

    assert manifest_kv["zip_filename"] == summary_kv["zip_filename"]
    assert manifest_kv["expected_zip_filename"] == summary_kv["expected_zip_filename"]
    assert manifest_kv["created_at_local"] == summary_kv["created_at_local"]
    assert manifest_kv["created_at_utc"] == summary_kv["created_at_utc"]
