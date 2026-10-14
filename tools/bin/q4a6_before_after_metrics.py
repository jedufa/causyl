#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SCHED_BASE = REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs"
INBOX_DIR = SCHED_BASE / "inbox"
STATE_DIR = SCHED_BASE / "state"
RECEIPTS_DIR = SCHED_BASE / "receipts"

SOURCE_RE = re.compile(r"previously one-iteration-accepted job `([^`]+)`", re.IGNORECASE)
ADDITIONS_RE = re.compile(r"write the final additions to `([^`]+)`", re.IGNORECASE)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _extract_source_and_additions(prompt: str) -> Tuple[str, str]:
    source = ""
    additions = ""
    m = SOURCE_RE.search(str(prompt or ""))
    if m:
        source = str(m.group(1) or "").strip()
    m2 = ADDITIONS_RE.search(str(prompt or ""))
    if m2:
        additions = str(m2.group(1) or "").strip()
    return source, additions


def _iterations_from_receipt(receipt: Dict[str, Any]) -> int:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    try:
        completed = int(loop.get("completed_iterations") or 0)
    except Exception:
        completed = 0
    if completed > 0:
        return completed
    rows = loop.get("iterations") if isinstance(loop.get("iterations"), list) else []
    return len(rows)


def _collect_rework_jobs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in sorted(INBOX_DIR.glob("overnight_20260314_q4a6[a-b]_rw_*.json")):
        spec = _read_json(p)
        if not spec:
            continue
        jid = str(spec.get("job_id") or p.stem)
        prompt = str(spec.get("prompt") or "")
        source, additions = _extract_source_and_additions(prompt)
        phase = "A" if "_q4a6a_" in jid else "B"
        out.append(
            {
                "job_id": jid,
                "phase": phase,
                "source_job_id": source,
                "additions_path": additions,
            }
        )
    return out


def _source_before_row(source_job_id: str) -> Dict[str, Any]:
    st = _read_json(STATE_DIR / f"{source_job_id}.json")
    run_id = str(st.get("last_run_id") or "").strip()
    receipt = _read_json(RECEIPTS_DIR / source_job_id / f"{run_id}.json") if run_id else {}
    return {
        "source_job_id": source_job_id,
        "source_last_status": str(st.get("last_status") or ""),
        "source_last_run_id": run_id,
        "source_iterations": _iterations_from_receipt(receipt) if receipt else 0,
    }


def _rework_after_row(job_id: str) -> Dict[str, Any]:
    st = _read_json(STATE_DIR / f"{job_id}.json")
    run_id = str(st.get("last_run_id") or "").strip()
    receipt = _read_json(RECEIPTS_DIR / job_id / f"{run_id}.json") if run_id else {}
    return {
        "rework_job_id": job_id,
        "rework_last_status": str(st.get("last_status") or ""),
        "rework_last_run_id": run_id,
        "rework_iterations": _iterations_from_receipt(receipt) if receipt else 0,
    }


def _compute_summary(rows: List[Dict[str, Any]], *, before: bool) -> Dict[str, Any]:
    if before:
        it = [int(r.get("source_iterations") or 0) for r in rows if int(r.get("source_iterations") or 0) > 0]
        ok = sum(1 for r in rows if str(r.get("source_last_status") or "").lower() == "ok")
    else:
        it = [int(r.get("rework_iterations") or 0) for r in rows if int(r.get("rework_iterations") or 0) > 0]
        ok = sum(1 for r in rows if str(r.get("rework_last_status") or "").lower() == "ok")
    total = len(rows)
    return {
        "count": total,
        "ok_count": ok,
        "ok_rate": (ok / total) if total else 0.0,
        "iterations_avg": (sum(it) / len(it)) if it else 0.0,
        "iterations_min": min(it) if it else 0,
        "iterations_max": max(it) if it else 0,
        "iterations_gt1_count": sum(1 for x in it if x > 1),
    }


def cmd_baseline(baseline_path: Path) -> int:
    jobs = _collect_rework_jobs()
    rows: List[Dict[str, Any]] = []
    for j in jobs:
        source = str(j.get("source_job_id") or "").strip()
        if not source:
            continue
        row = {}
        row.update(j)
        row.update(_source_before_row(source))
        rows.append(row)
    out = {
        "schema_version": "q4a6_before_after_baseline_v1",
        "utc": _utc_now(),
        "rows": rows,
        "summary_before": _compute_summary(rows, before=True),
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "baseline_path": str(baseline_path), "rows": len(rows)}, indent=2))
    return 0


def cmd_final(baseline_path: Path, report_path: Path) -> int:
    base = _read_json(baseline_path)
    if str(base.get("schema_version") or "") != "q4a6_before_after_baseline_v1":
        raise SystemExit(f"invalid baseline schema: {baseline_path}")
    rows = base.get("rows") if isinstance(base.get("rows"), list) else []

    merged: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out = dict(r)
        out.update(_rework_after_row(str(r.get("job_id") or "")))
        add_path = str(r.get("additions_path") or "").strip()
        out["additions_exists"] = bool(add_path and (REPO_ROOT / add_path).exists())
        merged.append(out)

    summary_before = base.get("summary_before") if isinstance(base.get("summary_before"), dict) else {}
    summary_after = _compute_summary(merged, before=False)

    lines: List[str] = []
    lines.append("# Q4A6 Before/After Campaign Comparison")
    lines.append("")
    lines.append(f"- generated: `{_utc_now()}`")
    lines.append(f"- baseline: `{baseline_path.relative_to(REPO_ROOT)}`")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- items compared: `{len(merged)}`")
    lines.append(f"- before ok-rate: `{summary_before.get('ok_rate',0):.2%}`")
    lines.append(f"- after ok-rate: `{summary_after.get('ok_rate',0):.2%}`")
    lines.append(f"- before avg iterations: `{summary_before.get('iterations_avg',0):.2f}`")
    lines.append(f"- after avg iterations: `{summary_after.get('iterations_avg',0):.2f}`")
    lines.append(
        f"- builder verifier-additions coverage: `{sum(1 for r in merged if r.get('additions_exists'))}/{len(merged)}`"
    )
    lines.append("")
    lines.append("## Aggregate Metrics")
    lines.append("")
    lines.append("| Metric | Before | After |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Count | {summary_before.get('count',0)} | {summary_after.get('count',0)} |")
    lines.append(f"| OK Count | {summary_before.get('ok_count',0)} | {summary_after.get('ok_count',0)} |")
    lines.append(f"| OK Rate | {summary_before.get('ok_rate',0):.2%} | {summary_after.get('ok_rate',0):.2%} |")
    lines.append(
        f"| Avg Iterations | {summary_before.get('iterations_avg',0):.2f} | {summary_after.get('iterations_avg',0):.2f} |"
    )
    lines.append(
        f"| Iterations > 1 | {summary_before.get('iterations_gt1_count',0)} | {summary_after.get('iterations_gt1_count',0)} |"
    )
    lines.append("")
    lines.append("## Per-Item Snapshot")
    lines.append("")
    lines.append("| Source Job | Phase | Before Iter | After Status | After Iter | Verifier Additions |")
    lines.append("|---|---:|---:|---|---:|---|")
    for r in merged:
        lines.append(
            f"| `{r.get('source_job_id','')}` | {r.get('phase','')} | {int(r.get('source_iterations') or 0)} | "
            f"{str(r.get('rework_last_status') or '')} | {int(r.get('rework_iterations') or 0)} | "
            f"{'yes' if r.get('additions_exists') else 'no'} |"
        )
    lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report_path": str(report_path), "rows": len(merged)}, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Q4A6 before/after campaign metrics.")
    p.add_argument("--mode", choices=("baseline", "final"), required=True)
    p.add_argument(
        "--baseline",
        default="docs/reports/q4a6/before_after_baseline_2026-03-14.json",
        help="Baseline snapshot path (repo-relative).",
    )
    p.add_argument(
        "--report",
        default="docs/reports/q4a6_before_after_comparison_2026-03-14.md",
        help="Final report path (repo-relative).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    baseline = (REPO_ROOT / str(args.baseline)).resolve()
    report = (REPO_ROOT / str(args.report)).resolve()
    if args.mode == "baseline":
        return cmd_baseline(baseline)
    return cmd_final(baseline, report)


if __name__ == "__main__":
    raise SystemExit(main())

