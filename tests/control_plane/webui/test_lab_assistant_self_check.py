from __future__ import annotations

import json
from pathlib import Path

from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_self_check as sc


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _init_repo(root: Path, *, bootstrap_host: bool = True) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant").mkdir(parents=True, exist_ok=True)
    if bootstrap_host:
        host.bootstrap_host_baseline(root)
    else:
        host.ensure_host_storage(root)


def test_self_check_persists_and_survives_restart(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    snap1 = sc.run_default_checks(tmp_path)
    assert snap1["schema_version"] == "lab_assistant_self_check_v1"
    assert snap1["status"] == "ok"
    assert snap1["mode"] == "self_check_ok"

    snap2 = sc.self_check_snapshot(tmp_path)
    assert set(snap1["required_checks"]) == set(snap2["required_checks"])
    assert snap2["status"] == "ok"
    assert snap2["mode"] == "self_check_ok"
    latest_snapshot_path = tmp_path / "runtime" / "state" / "lab_assistant" / "self_check" / "latest_snapshot.json"
    assert latest_snapshot_path.exists() is True
    latest_snapshot = json.loads(latest_snapshot_path.read_text(encoding="utf-8"))
    assert latest_snapshot["mode"] == "self_check_ok"


def test_unknown_state_is_not_projected_as_healthy(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    snap = sc.self_check_snapshot(tmp_path)
    assert snap["mode"] == "self_check_unknown"
    assert snap["status"] == "degraded"
    assert snap["host_readiness"]["status"] == "ok"
    assert "storage_write_probe" in snap["missing_checks"]


def test_degraded_when_required_check_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    sc.write_self_check_receipt(tmp_path, check_key="storage_write_probe", status="fail", details={"forced": True})
    snap = sc.self_check_snapshot(tmp_path)
    assert snap["status"] == "degraded"
    assert snap["mode"] == "self_check_degraded"
    assert snap["failed_checks"] == ["storage_write_probe"]
    assert any(tag.startswith("failed:") for tag in snap["reasons"])


def test_self_check_blocks_when_host_baseline_is_uninitialized(tmp_path: Path) -> None:
    _init_repo(tmp_path, bootstrap_host=False)

    snap = sc.self_check_snapshot(tmp_path)
    assert snap["status"] == "blocked"
    assert snap["mode"] == "self_check_blocked"
    assert snap["host_readiness"]["mode"] == "host_baseline_uninitialized"


def test_stale_pass_receipt_downgrades_to_unknown(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config_path = tmp_path / "runtime" / "state" / "lab_assistant" / "self_check" / "config.json"
    _write_json(
        config_path,
        {
            "default_check_max_age_seconds": 1,
            "check_max_age_seconds": {"storage_write_probe": 1},
        },
    )
    receipt = sc.write_self_check_receipt(tmp_path, check_key="storage_write_probe", status="pass", details={"forced": True})
    receipt_path = Path(str(receipt["receipt_path"]))
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["utc"] = "2000-01-01T00:00:00Z"
    receipt_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    snap = sc.self_check_snapshot(tmp_path)
    assert snap["status"] == "degraded"
    assert snap["mode"] == "self_check_unknown"
    assert snap["stale_checks"] == ["storage_write_probe"]
    assert snap["checks"]["storage_write_probe"]["status"] == "unknown"


def test_refresh_if_needed_runs_probe_when_coverage_is_missing(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    snap = sc.refresh_default_checks_if_needed(tmp_path)

    assert snap["status"] == "ok"
    assert snap["mode"] == "self_check_ok"
    assert snap["missing_checks"] == []
    assert snap["stale_checks"] == []
    assert snap["checks"]["storage_write_probe"]["status"] == "pass"


def test_refresh_if_needed_renews_stale_pass_receipt(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config_path = tmp_path / "runtime" / "state" / "lab_assistant" / "self_check" / "config.json"
    _write_json(
        config_path,
        {
            "default_check_max_age_seconds": 1,
            "check_max_age_seconds": {"storage_write_probe": 1},
        },
    )
    receipt = sc.write_self_check_receipt(tmp_path, check_key="storage_write_probe", status="pass", details={"forced": True})
    receipt_path = Path(str(receipt["receipt_path"]))
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["utc"] = "2000-01-01T00:00:00Z"
    receipt_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    snap = sc.refresh_default_checks_if_needed(tmp_path)

    assert snap["status"] == "ok"
    assert snap["mode"] == "self_check_ok"
    assert snap["stale_checks"] == []
    assert snap["checks"]["storage_write_probe"]["status"] == "pass"


def test_refresh_if_needed_does_not_overwrite_failed_receipt(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    sc.write_self_check_receipt(tmp_path, check_key="storage_write_probe", status="fail", details={"forced": True})

    snap = sc.refresh_default_checks_if_needed(tmp_path)

    assert snap["status"] == "degraded"
    assert snap["mode"] == "self_check_degraded"
    assert snap["failed_checks"] == ["storage_write_probe"]


def test_self_check_snapshot_is_persisted_for_unknown_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    snap = sc.self_check_snapshot(tmp_path)

    latest_snapshot_path = tmp_path / "runtime" / "state" / "lab_assistant" / "self_check" / "latest_snapshot.json"
    persisted = json.loads(latest_snapshot_path.read_text(encoding="utf-8"))
    assert persisted["mode"] == "self_check_unknown"
    assert persisted["missing_checks"] == ["storage_write_probe"]
