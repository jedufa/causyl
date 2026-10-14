from pathlib import Path
import json
import os
from infra.webui import server_routes_and_main as routes


def _write_apk(root: Path, channel: str, name: str, content: bytes = b"dummy-apk-bytes") -> Path:
    target = root / "runtime" / "artifacts" / "mobile_apk" / channel
    target.mkdir(parents=True, exist_ok=True)
    apk_path = target / name
    apk_path.write_bytes(content)
    return apk_path


def test_mobile_apk_release_requires_meta_sidecar(tmp_path: Path):
    # Arrange: create an APK without a meta sidecar
    apk_name = routes._MOBILE_APK_CHANNEL_META["debug"]["apk_name"]
    _write_apk(tmp_path, "debug", apk_name)

    # Act
    found = routes._mobile_apk_find_release(tmp_path, "debug")

    # Assert: gate must fail-closed without meta
    assert found is None


def test_mobile_apk_release_with_valid_meta_is_detected(tmp_path: Path):
    # Arrange: create an APK with a valid meta sidecar
    channel = "debug"
    apk_name = routes._MOBILE_APK_CHANNEL_META[channel]["apk_name"]
    apk_path = _write_apk(tmp_path, channel, apk_name)
    meta = {
        "schema_version": "mobile_apk_release_meta_v1",
        "channel": channel,
        "apk_name": apk_name,
        "version_code": 42,
        "version_name": "0.2.3-debug",
        "built_at": "2026-03-13T12:34:56Z",
    }
    meta_path = routes._mobile_apk_meta_path(apk_path)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # Act
    found = routes._mobile_apk_find_release(tmp_path, channel)

    # Assert
    assert isinstance(found, dict)
    assert found["channel"] == channel
    assert found["apk_name"] == apk_name
    # spot-check that version/built_at come from meta (not default fallback)
    assert found["version_name"] == meta["version_name"]
    assert found["built_at"] == meta["built_at"]


def test_mobile_apk_release_prefers_newest_versioned_causyl_apk(tmp_path: Path):
    channel = "debug"
    older_name = routes._MOBILE_APK_CHANNEL_META[channel]["apk_name"]
    newer_name = "causyl-debug-0.2.4-debug-20260520.apk"
    older_path = _write_apk(tmp_path, channel, older_name)
    newer_path = _write_apk(tmp_path, channel, newer_name)
    older_meta = {
        "schema_version": "mobile_apk_release_meta_v1",
        "channel": channel,
        "apk_name": older_name,
        "version_code": 41,
        "version_name": "0.2.3-debug",
        "built_at": "2026-03-13T12:34:56Z",
    }
    newer_meta = {
        "schema_version": "mobile_apk_release_meta_v1",
        "channel": channel,
        "apk_name": newer_name,
        "version_code": 42,
        "version_name": "0.2.4-debug",
        "built_at": "2026-05-20T12:34:56Z",
    }
    routes._mobile_apk_meta_path(older_path).write_text(json.dumps(older_meta), encoding="utf-8")
    routes._mobile_apk_meta_path(newer_path).write_text(json.dumps(newer_meta), encoding="utf-8")
    os.utime(older_path, (1, 1))
    os.utime(newer_path, (2, 2))

    found = routes._mobile_apk_find_release(tmp_path, channel)

    assert isinstance(found, dict)
    assert found["apk_name"] == newer_name
    assert found["source_path"] == f"runtime/artifacts/mobile_apk/{channel}/{newer_name}"


def test_mobile_apk_release_integrity_reports_validation_scope_and_lineage(tmp_path: Path):
    channel = "debug"
    apk_name = routes._MOBILE_APK_CHANNEL_META[channel]["apk_name"]
    apk_path = _write_apk(tmp_path, channel, apk_name)
    meta = {
        "schema_version": "mobile_apk_release_meta_v1",
        "channel": channel,
        "apk_name": apk_name,
        "version_code": 42,
        "version_name": "0.2.3-debug",
        "built_at": "2026-03-13T12:34:56Z",
    }
    routes._mobile_apk_meta_path(apk_path).write_text(json.dumps(meta), encoding="utf-8")
    receipt_dir = tmp_path / "runtime" / "state" / "mobile_apk" / "releases" / channel / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "rr_2026-03-13T12:35:00Z__debug.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "mobile_apk_release_receipt_v1",
                "utc": "2026-03-13T12:35:00Z",
                "channel": channel,
                "channel_publication_state": "published_local_artifact",
                "apk_name": apk_name,
                "apk_sha256": routes._sha256_file(apk_path),
                "version_code": 42,
                "version_name": "0.2.3-debug",
                "built_at": "2026-03-13T12:34:56Z",
                "source_path": f"runtime/artifacts/mobile_apk/{channel}/{apk_name}",
                "source_snapshot_identity": "abc123",
                "validation_scope": "artifact_smoke_only",
                "validation_commands": ["./gradlew testDebugUnitTest"],
                "proof_gap": False,
                "proof_gap_reason": "none",
                "artifact_lineage": {
                    "published_apk_path": f"runtime/artifacts/mobile_apk/{channel}/{apk_name}",
                    "meta_sidecar_path": f"runtime/artifacts/mobile_apk/{channel}/{apk_name}.meta.json",
                    "receipt_relpath": f"runtime/state/mobile_apk/releases/{channel}/receipts/{receipt_path.name}",
                },
            }
        ),
        encoding="utf-8",
    )

    payload = routes._mobile_apk_release_integrity(tmp_path, channel)

    assert payload["integrity_ok"] is True
    assert payload["status"] == "ok"
    assert payload["validation_scope"] == "artifact_smoke_only"
    assert payload["proof_gap"] is False
    assert payload["artifact_lineage"]["receipt_relpath"].endswith(receipt_path.name)


def test_mobile_apk_release_integrity_fail_closes_when_receipt_scope_fields_are_missing(tmp_path: Path):
    channel = "debug"
    apk_name = routes._MOBILE_APK_CHANNEL_META[channel]["apk_name"]
    apk_path = _write_apk(tmp_path, channel, apk_name)
    meta = {
        "schema_version": "mobile_apk_release_meta_v1",
        "channel": channel,
        "apk_name": apk_name,
        "version_code": 42,
        "version_name": "0.2.3-debug",
        "built_at": "2026-03-13T12:34:56Z",
    }
    routes._mobile_apk_meta_path(apk_path).write_text(json.dumps(meta), encoding="utf-8")
    receipt_dir = tmp_path / "runtime" / "state" / "mobile_apk" / "releases" / channel / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "rr_2026-03-13T12:35:00Z__debug.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "mobile_apk_release_receipt_v1",
                "utc": "2026-03-13T12:35:00Z",
                "channel": channel,
                "apk_name": apk_name,
                "apk_sha256": routes._sha256_file(apk_path),
                "version_code": 42,
                "version_name": "0.2.3-debug",
                "built_at": "2026-03-13T12:34:56Z",
                "source_path": f"runtime/artifacts/mobile_apk/{channel}/{apk_name}",
            }
        ),
        encoding="utf-8",
    )

    payload = routes._mobile_apk_release_integrity(tmp_path, channel)

    assert payload["status"] == "proof_gap"
    assert payload["integrity_ok"] is False
    assert "validation_scope" in payload["missing_receipt_fields"]
    assert payload["proof_gap"] is True
