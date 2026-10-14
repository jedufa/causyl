"""Deterministic operator monitoring and reporting digest.

Last modified: 2026-03-31 00:00 Europe/Zurich
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Sequence

from tools.deterministic_tooling.common import build_envelope, canonical_source, ensure_runtime_dir, hash_object, load_cache, normalized_list, parse_run_timestamp, read_text, repo_rel, store_cache, write_output

SCHEMA_VERSION = "operator_digest_v1"
TOOL_NAME = "operator_monitoring_reporting_digest"
CACHE_TTL_S = 600.0
_STATUS_SEVERITY = {"blocked": 4, "error": 4, "failed": 4, "not_launched": 3, "in_progress": 2, "pending": 2, "ok": 1, "done": 1, "verified": 1}


def _report_paths(repo_root: Path, job_ids: Sequence[str]) -> list[Path]:
    root = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "reports"
    out: list[Path] = []
    for job_id in normalized_list(job_ids):
        report_dir = root / job_id
        if report_dir.exists():
            out.extend(sorted(report_dir.glob("*.md")))
    return out


def _parse_report(path: Path, repo_root: Path) -> dict[str, Any]:
    text = read_text(path)
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("- "):
            if meta:
                break
            continue
        body = line[2:]
        key, sep, value = body.partition(":")
        if not sep:
            continue
        meta[key.strip()] = value.strip()
    status = str(meta.get("status") or "unknown").lower()
    severity = _STATUS_SEVERITY.get(status, 2)
    run_id = str(meta.get("run_id") or path.stem)
    timestamp = parse_run_timestamp(run_id) or parse_run_timestamp(path.name)
    return {
        "job_id": path.parent.name,
        "run_id": run_id,
        "status": status,
        "severity": severity,
        "timestamp_utc": timestamp,
        "reason": str(meta.get("reason") or ""),
        "path": repo_rel(repo_root, path),
        "content_hash": canonical_source(repo_root, path)["content_hash"],
    }


def build_operator_digest(*, repo_root: Path, job_ids: Sequence[str], runtime_policy: Mapping[str, Any] | None = None, context_bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    cache_dir = ensure_runtime_dir(repo_root, "operator_digests")
    key = hash_object({"job_ids": normalized_list(job_ids), "context_bundle": context_bundle or {}}).split(":", 1)[1]
    cache_path = cache_dir / f"{key}.json"
    cached = load_cache(cache_path, ttl_s=CACHE_TTL_S)
    if cached is not None:
        return cached

    warnings: list[dict[str, Any]] = []
    reports = [_parse_report(path, repo_root) for path in _report_paths(repo_root, job_ids)]
    reports.sort(key=lambda row: (-int(row["severity"]), str(row["timestamp_utc"] or ""), str(row["run_id"])))
    if not reports:
        warnings.append({"code": "no_reports", "severity": "warning", "message": "No report files were found for the requested jobs."})
    risk_list = [
        {"job_id": row["job_id"], "run_id": row["run_id"], "status": row["status"], "reason": row["reason"]}
        for row in reports
        if int(row["severity"]) >= 3
    ]
    required_actions = []
    if risk_list:
        required_actions.append("Inspect the highest-severity blocked or failed job receipts first.")
    proof_links = [{"job_id": row["job_id"], "path": row["path"], "content_hash": row["content_hash"]} for row in reports]
    outputs = {
        "timeline": reports,
        "risk_list": risk_list,
        "required_actions": required_actions,
        "proof_links": proof_links,
    }
    envelope = build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={"repo_root": str(repo_root), "job_ids": normalized_list(job_ids)},
        outputs=outputs,
        warnings=warnings,
        provenance=[canonical_source(repo_root, repo_root / row["path"]) for row in reports[:20]],
        artifacts=[{"path": repo_rel(repo_root, cache_path), "kind": "cache_entry"}],
    )
    store_cache(cache_path, envelope)
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an operator monitoring digest")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--jobs", nargs="*", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = build_operator_digest(repo_root=Path(args.repo_root), job_ids=args.jobs)
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
