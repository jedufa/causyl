"""D2K3: Continuous self-check + degraded-state semantics.

This module provides a minimal, durable self-check contract for the Lab
Assistant v1 stack. It intentionally stays within D2K3 boundaries:

- Owns only self-check receipts, snapshot projection, and degraded/unknown
  classification based on required checks.
- Does not re-own product/runtime surfaces from neighboring PIS work.
- Fails closed: unknown cannot project as healthy; missing/failed/stale checks
  yield degraded or blocked status with explicit reasons.

The default implementation includes one storage write/read probe that
operates strictly under this module's own state directory. Scheduling is
out of scope; callers can invoke `run_default_checks` on their cadence.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

_SCHEMA = "lab_assistant_self_check_v1"
_DEFAULT_CHECK_MAX_AGE_S = 900
_REFRESH_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _utc_compact_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_iso_utc(value: Any) -> _dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _age_s(*, now: _dt.datetime, value: Any) -> int | None:
    parsed = _parse_iso_utc(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime/state/lab_assistant/self_check").resolve()


def _receipts_dir(repo_root: Path) -> Path:
    return (_root(repo_root) / "receipts").resolve()


def _latest_pointer_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "latest.json").resolve()


def _latest_snapshot_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "latest_snapshot.json").resolve()


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _default_required_checks() -> List[str]:
    # Minimal but non-empty: prove we can write/read within our own state root.
    return ["storage_write_probe"]


def _config_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "config.json").resolve()


def _read_config(repo_root: Path) -> Dict[str, Any]:
    return _read_json(_config_path(repo_root))


def _read_required_checks(repo_root: Path) -> List[str]:
    cfg = _read_config(repo_root)
    rows = list(cfg.get("required_checks") or [])
    out: List[str] = []
    for v in rows:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out or _default_required_checks()


def _default_max_age_seconds(repo_root: Path) -> int:
    cfg = _read_config(repo_root)
    raw = cfg.get("default_check_max_age_seconds", _DEFAULT_CHECK_MAX_AGE_S)
    try:
        value = int(raw)
    except Exception:
        value = _DEFAULT_CHECK_MAX_AGE_S
    return max(1, value)


def _check_max_age_seconds(repo_root: Path) -> Dict[str, int]:
    cfg = _read_config(repo_root)
    rows = cfg.get("check_max_age_seconds") if isinstance(cfg.get("check_max_age_seconds"), dict) else {}
    default_value = _default_max_age_seconds(repo_root)
    out: Dict[str, int] = {}
    for key, value in dict(rows or {}).items():
        check_key = str(key or "").strip()
        if not check_key:
            continue
        try:
            parsed = int(value)
        except Exception:
            parsed = default_value
        out[check_key] = max(1, parsed)
    return out


def write_self_check_receipt(
    repo_root: Path,
    *,
    check_key: str,
    status: str,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Write one durable self-check receipt and update the latest pointer.

    - check_key: machine key for the check (e.g., "storage_write_probe").
    - status: one of {"pass", "fail", "unknown"}.
    - details: optional diagnostic payload.
    """
    repo_root = Path(repo_root).resolve()
    k = str(check_key or "").strip()
    st = str(status or "").strip().lower()
    if not k:
        raise ValueError("check_key is required")
    if st not in {"pass", "fail", "unknown"}:
        raise ValueError("status must be one of: pass, fail, unknown")

    receipts = _receipts_dir(repo_root)
    ts = _utc_compact_stamp()
    path = (receipts / f"{ts}__{k}.json").resolve()
    payload = {
        "schema_version": _SCHEMA,
        "utc": _utc_now_iso(),
        "check_key": k,
        "status": st,
        "details": dict(details or {}),
    }
    _write_json_atomic(path, payload)

    # Update latest pointer map (check_key -> relpath) atomically.
    latest = _read_json(_latest_pointer_path(repo_root))
    latest.setdefault("schema_version", _SCHEMA)
    latest.setdefault("utc", _utc_now_iso())
    latest_map = latest.setdefault("latest", {})
    if not isinstance(latest_map, dict):
        latest_map = {}
        latest["latest"] = latest_map
    rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
    latest_map[k] = {"relpath": rel, "utc": payload["utc"]}
    latest["utc"] = payload["utc"]
    _write_json_atomic(_latest_pointer_path(repo_root), latest)
    return {"ok": True, "receipt_path": str(path), "utc": payload["utc"], "check_key": k, "status": st}


def _read_latest_receipts(repo_root: Path) -> Dict[str, Dict[str, Any]]:
    latest = _read_json(_latest_pointer_path(repo_root))
    latest_map = latest.get("latest") if isinstance(latest.get("latest"), dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, meta in list(latest_map.items()):
        try:
            rel = str(dict(meta or {}).get("relpath") or "").strip()
            if not rel:
                continue
            path = (Path(repo_root).resolve() / rel).resolve()
            obj = _read_json(path)
            if obj:
                out[str(k)] = obj
        except Exception:
            continue
    return out


def _host_readiness(repo_root: Path) -> Dict[str, Any]:
    try:
        from infra.webui import server_lab_assistant_host as _host

        health = _host.host_health_snapshot(repo_root, initialize_if_missing=False)
        return {
            "status": str(health.get("status") or "").strip().lower(),
            "mode": str(health.get("mode") or "").strip().lower(),
            "summary": str(health.get("summary") or "").strip(),
            "reasons": [str(row).strip() for row in list(health.get("reasons") or []) if str(row).strip()],
        }
    except Exception as exc:
        return {
            "status": "blocked",
            "mode": "host_health_error",
            "summary": f"Host readiness could not be evaluated: {type(exc).__name__}",
            "reasons": [f"host_health_error:{type(exc).__name__}"],
        }


def _project_check(
    *,
    check_key: str,
    receipt: Dict[str, Any],
    now: _dt.datetime,
    max_age_s: int,
) -> Dict[str, Any]:
    row = dict(receipt or {})
    recorded_status = str(row.get("status") or "").strip().lower()
    age_s = _age_s(now=now, value=row.get("utc"))
    stale = bool(age_s is None or age_s > max_age_s)
    reason_tags: List[str] = []

    if not row:
        effective_status = "unknown"
        reason_tags.append("missing_receipt")
    elif recorded_status == "fail":
        effective_status = "fail"
    elif recorded_status == "pass":
        effective_status = "pass"
    elif recorded_status == "unknown":
        effective_status = "unknown"
        reason_tags.append("recorded_unknown")
    else:
        effective_status = "unknown"
        reason_tags.append("invalid_status")

    if age_s is None:
        reason_tags.append("missing_or_invalid_utc")
    if stale:
        reason_tags.append("stale_receipt")
        if effective_status == "pass":
            effective_status = "unknown"

    return {
        **row,
        "check_key": check_key,
        "recorded_status": recorded_status,
        "status": effective_status,
        "age_s": age_s,
        "max_age_s": max_age_s,
        "stale": stale,
        "reason_tags": reason_tags,
    }


def _project_overall(
    *,
    host_readiness: Dict[str, Any],
    required: List[str],
    checks: Dict[str, Dict[str, Any]],
) -> Tuple[str, str, List[str], List[str], List[str], List[str]]:
    missing: List[str] = []
    failed: List[str] = []
    stale: List[str] = []
    reasons: List[str] = []

    for key in required:
        row = checks.get(key) or {}
        status = str(row.get("status") or "").strip().lower()
        if bool(row.get("stale")):
            stale.append(key)
        if status == "fail":
            failed.append(key)
        elif status in {"", "unknown"}:
            missing.append(key)

    host_status = str(host_readiness.get("status") or "").strip().lower()
    host_mode = str(host_readiness.get("mode") or "").strip().lower()
    host_reasons = [str(row).strip() for row in list(host_readiness.get("reasons") or []) if str(row).strip()]

    if host_status == "blocked":
        reasons.append(f"blocked_host:{host_mode or 'unknown'}")
        reasons.extend([f"blocked_host_reason:{reason}" for reason in host_reasons])
        return "blocked", "Self-check is blocked until host baseline readiness is restored.", reasons, failed, missing, stale

    if failed:
        reasons.append("failed:" + ",".join(sorted(set(failed))))
    if missing:
        reasons.append("missing:" + ",".join(sorted(set(missing))))
    if stale:
        reasons.append("stale:" + ",".join(sorted(set(stale))))
    if host_status == "degraded":
        reasons.append(f"degraded_host:{host_mode or 'unknown'}")
        reasons.extend([f"degraded_host_reason:{reason}" for reason in host_reasons])

    if failed:
        return "degraded", "One or more required self-checks failed.", reasons, failed, missing, stale
    if missing or stale:
        return "unknown", "Self-check coverage is missing or stale, so readiness remains unproven.", reasons, failed, missing, stale
    if host_status == "degraded":
        return "degraded", "Host baseline is degraded even though current self-check receipts are passing.", reasons, failed, missing, stale
    return "ok", "Required self-check receipts are present, fresh, and passing.", reasons, failed, missing, stale


def self_check_snapshot(repo_root: Path, *, persist_snapshot: bool = True) -> Dict[str, Any]:
    """Return the durable self-check snapshot with explicit degraded semantics.

    Output fields:
    - schema_version: fixed identifier
    - utc: snapshot time
    - required_checks: list[str]
    - checks: dict[check_key] -> projected latest receipt
    - mode: one of {"self_check_ok","self_check_degraded","self_check_unknown","self_check_blocked"}
    - status: "ok", "degraded", or "blocked"
    - reasons: machine-readable reason tags
    """
    repo_root = Path(repo_root).resolve()
    now = _dt.datetime.now(_dt.timezone.utc)
    required = _read_required_checks(repo_root)
    latest = _read_latest_receipts(repo_root)
    max_age_map = _check_max_age_seconds(repo_root)
    default_max_age = _default_max_age_seconds(repo_root)
    projected_checks = {
        key: _project_check(
            check_key=key,
            receipt=dict(latest.get(key) or {}),
            now=now,
            max_age_s=int(max_age_map.get(key, default_max_age)),
        )
        for key in required
    }
    host_readiness = _host_readiness(repo_root)
    overall, summary, reasons, failed, missing, stale = _project_overall(
        host_readiness=host_readiness,
        required=required,
        checks=projected_checks,
    )
    mode = {
        "ok": "self_check_ok",
        "degraded": "self_check_degraded",
        "unknown": "self_check_unknown",
        "blocked": "self_check_blocked",
    }.get(overall, "self_check_unknown")
    status = overall if overall == "blocked" else ("ok" if overall == "ok" else "degraded")
    snapshot = {
        "schema_version": _SCHEMA,
        "utc": _utc_now_iso(),
        "required_checks": list(required),
        "checks": projected_checks,
        "mode": mode,
        "status": status,
        "summary": summary,
        "reasons": reasons,
        "failed_checks": sorted(set(failed)),
        "missing_checks": sorted(set(missing)),
        "stale_checks": sorted(set(stale)),
        "host_readiness": host_readiness,
        "default_check_max_age_seconds": int(default_max_age),
    }
    if persist_snapshot:
        _write_json_atomic(_latest_snapshot_path(repo_root), snapshot)
    return snapshot


def run_default_checks(repo_root: Path) -> Dict[str, Any]:
    """Run the minimal built-in checks and persist receipts.

    Currently implements only a local storage write/read probe under this
    module's root directory. Returns the post-run snapshot.
    """
    repo_root = Path(repo_root).resolve()
    probe_dir = (_root(repo_root) / "probes").resolve()
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_path = (probe_dir / "write_probe.txt").resolve()
    ok = False
    error = ""
    try:
        with open(probe_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("ok\n")
            f.flush()
            os.fsync(f.fileno())
        text = probe_path.read_text(encoding="utf-8", errors="replace")
        ok = text.strip() == "ok"
    except Exception as exc:
        error = str(exc)
        ok = False
    status = "pass" if ok else "fail"
    details: Dict[str, Any] = {"probe_path": str(probe_path)}
    if not ok and error:
        details["error"] = error
    write_self_check_receipt(repo_root, check_key="storage_write_probe", status=status, details=details)
    return self_check_snapshot(repo_root)


def refresh_default_checks_if_needed(repo_root: Path) -> Dict[str, Any]:
    """Refresh built-in receipts when coverage is missing or stale.

    This keeps the self-check surface from degrading permanently just because
    nothing ever invokes the built-in probe runner. Failed receipts are left
    alone so we do not auto-whitewash a real fault.
    """
    repo_root = Path(repo_root).resolve()
    snapshot = self_check_snapshot(repo_root)
    host_status = str(dict(snapshot.get("host_readiness") or {}).get("status") or "").strip().lower()
    missing = list(snapshot.get("missing_checks") or [])
    stale = list(snapshot.get("stale_checks") or [])
    failed = list(snapshot.get("failed_checks") or [])
    if host_status == "blocked" or failed or (not missing and not stale):
        return snapshot
    with _REFRESH_LOCK:
        snapshot = self_check_snapshot(repo_root)
        host_status = str(dict(snapshot.get("host_readiness") or {}).get("status") or "").strip().lower()
        missing = list(snapshot.get("missing_checks") or [])
        stale = list(snapshot.get("stale_checks") or [])
        failed = list(snapshot.get("failed_checks") or [])
        if host_status == "blocked" or failed or (not missing and not stale):
            return snapshot
        return run_default_checks(repo_root)


__all__ = [
    "refresh_default_checks_if_needed",
    "self_check_snapshot",
    "run_default_checks",
    "write_self_check_receipt",
]
