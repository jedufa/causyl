#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from tools.bin.scheduler_tty_ui import prompt_menu
from infra.webui.ledger import ledger_storage


def _parse_utc(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _estimate_tokens_from_text(text: Any) -> int:
    payload = str(text or "")
    if not payload:
        return 0
    return max(1, int(round(len(payload.encode("utf-8")) / 4.0)))


def _estimate_tokens_from_bytes(raw: Any) -> int:
    try:
        count = int(raw or 0)
    except Exception:
        count = 0
    if count <= 0:
        return 0
    return max(1, int(round(count / 4.0)))


def _recent_sort_key(row: Dict[str, Any]) -> tuple[int, float, str]:
    state = dict(row.get("state") or {})
    stamp = (
        state.get("active_started_utc")
        or state.get("last_started_utc")
        or state.get("last_finished_utc")
        or state.get("last_nonlaunch_utc")
        or row.get("created_utc")
        or ""
    )
    parsed = _parse_utc(stamp)
    return (0 if state.get("active_run_id") else 1, -(parsed.timestamp() if parsed else 0.0), str(row.get("job_id") or ""))


def _choose_job(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return prompt_menu(
        "Recent scheduled jobs",
        choices=rows,
        render=lambda row: (
            f"{row['display_title']} [{row['job_id']}] "
            f"{str((dict(row.get('state') or {}).get('last_status') or dict(row.get('due') or {}).get('reason') or '')).strip()}"
            f"{' (active)' if dict(row.get('state') or {}).get('active_run_id') else ''}"
        ),
        default_index=0,
        fallback_reader=input,
    )


def _ledger_item_dir_from_binding(binding: Dict[str, Any]) -> Optional[Path]:
    if str(binding.get("type") or "") != "ledger_uid":
        return None
    uid = str(binding.get("id") or "").strip()
    if not uid:
        return None
    return ledger_storage.item_dir_for_uid(REPO_ROOT / "governance" / "ledger" / "items", uid)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _session_rows_for_receipt(receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    binding = dict(receipt.get("binding") or {})
    item_dir = _ledger_item_dir_from_binding(binding)
    if not item_dir:
        return []
    started = _parse_utc(receipt.get("started_utc"))
    finished = _parse_utc(receipt.get("finished_utc")) or _parse_utc(receipt.get("started_utc"))
    if not started:
        return []
    # Use a small buffer because transcript events and receipt timestamps are not perfectly aligned.
    window_start = started - timedelta(minutes=2)
    window_end = (finished or started) + timedelta(minutes=2)
    rows: List[Dict[str, Any]] = []
    for path in sorted((item_dir / "artifacts" / "agent_chat").glob("session__*.jsonl")):
        for row in _load_jsonl(path):
            event_utc = _parse_utc(row.get("utc"))
            if event_utc is None:
                continue
            if window_start <= event_utc <= window_end:
                rows.append(row)
    rows.sort(key=lambda row: _parse_utc(row.get("utc")) or datetime.fromtimestamp(0, tz=timezone.utc))
    return rows


def _estimate_run_tokens(receipt: Dict[str, Any]) -> Dict[str, Any]:
    rows = _session_rows_for_receipt(receipt)
    inbound_prompt_tokens = 0
    inbound_message_tokens = 0
    outbound_tokens = 0
    user_messages = 0
    assistant_messages = 0
    models: List[str] = []

    for row in rows:
        sender = str(row.get("sender") or "").strip().lower()
        content = str(row.get("content") or "")
        meta = dict(row.get("meta") or {})
        selected_model = str(meta.get("selected_model") or meta.get("requested_model") or "").strip()
        if selected_model and selected_model not in models:
            models.append(selected_model)
        if sender == "assistant":
            assistant_messages += 1
            outbound_tokens += _estimate_tokens_from_text(content)
            inbound_prompt_tokens += _estimate_tokens_from_bytes(meta.get("request_payload_bytes"))
        elif sender == "user":
            user_messages += 1
            inbound_message_tokens += _estimate_tokens_from_text(content)

    inbound_tokens = inbound_prompt_tokens or inbound_message_tokens
    total_tokens = inbound_tokens + outbound_tokens
    return {
        "inbound_tokens_est": inbound_tokens,
        "outbound_tokens_est": outbound_tokens,
        "total_tokens_est": total_tokens,
        "inbound_prompt_tokens_est": inbound_prompt_tokens,
        "inbound_message_tokens_est": inbound_message_tokens,
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "models_seen": models,
        "transcript_rows": len(rows),
        "estimation_source": "request_payload_bytes" if inbound_prompt_tokens else ("message_text" if inbound_message_tokens or outbound_tokens else "none"),
    }


def _latest_receipt_for_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    state = dict(row.get("state") or {})
    active_run_id = str(state.get("active_run_id") or "").strip()
    if active_run_id:
        try:
            return sched.load_receipt(str(row.get("job_id") or ""), active_run_id, repo_root=REPO_ROOT)
        except Exception:
            pass
    receipts = sched.list_receipts(str(row.get("job_id") or ""), repo_root=REPO_ROOT, limit=1)
    return receipts[0] if receipts else None


def _collect_rows(job_id: str = "", *, limit: int = 50) -> List[Dict[str, Any]]:
    rows = sorted(sched.list_jobs(repo_root=REPO_ROOT), key=_recent_sort_key)
    if job_id:
        return [row for row in rows if str(row.get("job_id") or "") == job_id]
    return rows[: max(1, limit)]


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def _fmt_when(started_utc: str, finished_utc: str) -> str:
    if finished_utc:
        return f"finished {finished_utc}"
    if started_utc:
        return f"started {started_utc}"
    return "time unknown"


def _fmt_models(models: Iterable[str]) -> str:
    items = [str(x or "").strip() for x in models if str(x or "").strip()]
    if not items:
        return "unknown"
    if len(items) <= 2:
        return ", ".join(items)
    return ", ".join(items[:2]) + f" +{len(items) - 2} more"


def _render_table(results: List[Dict[str, Any]], *, group_by: str) -> str:
    lines: List[str] = []
    group_totals: Dict[str, Dict[str, int]] = defaultdict(lambda: {"jobs": 0, "inbound": 0, "outbound": 0, "total": 0})
    for row in results:
        key = str(row.get(group_by) or "unknown")
        group_totals[key]["jobs"] += 1
        group_totals[key]["inbound"] += int(row.get("inbound_tokens_est") or 0)
        group_totals[key]["outbound"] += int(row.get("outbound_tokens_est") or 0)
        group_totals[key]["total"] += int(row.get("total_tokens_est") or 0)

    total_inbound = sum(int(r.get("inbound_tokens_est") or 0) for r in results)
    total_outbound = sum(int(r.get("outbound_tokens_est") or 0) for r in results)
    total_all = sum(int(r.get("total_tokens_est") or 0) for r in results)
    lines.append("Scheduled Job Token Estimate")
    lines.append(f"Scope: {len(results)} latest run(s)")
    lines.append(f"Total estimated tokens: {_fmt_int(total_all)}")
    lines.append(f"Inbound: {_fmt_int(total_inbound)} | Outbound: {_fmt_int(total_outbound)}")
    for key in sorted(group_totals):
        bucket = group_totals[key]
        lines.append(
            f"- {key}: {bucket['jobs']} job(s), total {_fmt_int(bucket['total'])}, inbound {_fmt_int(bucket['inbound'])}, outbound {_fmt_int(bucket['outbound'])}"
        )

    lines.append("")
    lines.append("Job Breakdown")
    for row in sorted(results, key=lambda r: int(r.get("total_tokens_est") or 0), reverse=True):
        title = str(row.get("display_title") or row["job_id"]).strip()
        if len(title) > 72:
            title = title[:69].rstrip() + "..."
        lines.append(
            f"- {title} [{row['job_id']}]"
        )
        lines.append(
            f"  status={row['status']} kind={row['job_kind']} total={_fmt_int(row['total_tokens_est'])} "
            f"(in {_fmt_int(row['inbound_tokens_est'])} / out {_fmt_int(row['outbound_tokens_est'])})"
        )
        lines.append(
            f"  models={_fmt_models(row.get('models_seen') or [])} | source={row['estimation_source']} | {_fmt_when(str(row.get('started_utc') or ''), str(row.get('finished_utc') or ''))}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Estimate scheduled-job token utilization from receipts and transcripts.")
    parser.add_argument("job_id", nargs="?", help="Optional job_id to analyze.")
    parser.add_argument("--limit", type=int, default=25, help="How many recent jobs to analyze when no job_id is provided.")
    parser.add_argument("--group-by", choices=["job_kind", "status"], default="job_kind")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--pick", action="store_true", help="Pick one job interactively from recent jobs.")
    parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
    args = parser.parse_args(argv)
    if sched_backend.selected_backend(args.backend or None) == "v2":
        payload = {
            "ok": True,
            "backend": "v2",
            "skipped": True,
            "reason": "v2 token usage is not derived from legacy scheduled-job receipts",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else payload["reason"])
        return 0

    rows = _collect_rows(job_id=str(args.job_id or "").strip(), limit=max(1, int(args.limit)))
    if not rows:
        print("No scheduled jobs found.", file=sys.stderr)
        return 1
    if args.pick and not args.job_id:
        rows = [_choose_job(rows)]

    results: List[Dict[str, Any]] = []
    for row in rows:
        receipt = _latest_receipt_for_row(row)
        if not receipt:
            continue
        estimate = _estimate_run_tokens(receipt)
        results.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "display_title": str(row.get("display_title") or ""),
                "job_kind": str(row.get("job_kind") or ""),
                "status": str(receipt.get("status") or (dict(row.get("state") or {}).get("last_status") or "")),
                "run_id": str(receipt.get("run_id") or ""),
                "started_utc": str(receipt.get("started_utc") or ""),
                "finished_utc": str(receipt.get("finished_utc") or ""),
                **estimate,
            }
        )

    payload = {
        "results": results,
        "group_by": args.group_by,
        "totals": {
            "jobs": len(results),
            "inbound_tokens_est": sum(int(r.get("inbound_tokens_est") or 0) for r in results),
            "outbound_tokens_est": sum(int(r.get("outbound_tokens_est") or 0) for r in results),
            "total_tokens_est": sum(int(r.get("total_tokens_est") or 0) for r in results),
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(_render_table(results, group_by=args.group_by))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
