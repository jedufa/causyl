#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "runtime" / "state" / "plane_intent_mining"


@dataclass(frozen=True)
class IntentRule:
    intent: str
    handling_mode: str
    rationale: str
    patterns: tuple[re.Pattern[str], ...]


INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        intent="direct_status_queue",
        handling_mode="direct_status",
        rationale="Queue/next-job questions should answer from scheduler truth immediately.",
        patterns=(
            re.compile(r"\bhow many jobs\b.*\bqueue\b", re.I),
            re.compile(r"\bjobs in the queue\b", re.I),
            re.compile(r"\bnext scheduled job\b", re.I),
            re.compile(r"\bwhat would be the next scheduled job to execute\b", re.I),
            re.compile(r"\bqueue and estimated execution start times\b", re.I),
        ),
    ),
    IntentRule(
        intent="direct_status_snapshot",
        handling_mode="direct_status",
        rationale="Snapshot/freshness questions should resolve from durable operator snapshot truth.",
        patterns=(
            re.compile(r"\bsnapshot\b", re.I),
            re.compile(r"\bstale\b", re.I),
            re.compile(r"\brecovery in progress\b", re.I),
            re.compile(r"\brunning jobs\b", re.I),
            re.compile(r"\bblocked\b", re.I),
        ),
    ),
    IntentRule(
        intent="direct_status_health",
        handling_mode="direct_status",
        rationale="Health/liveness checks should use direct plane/runtime status, not Codex reasoning.",
        patterns=(
            re.compile(r"\bplane is responding\b", re.I),
            re.compile(r"\byou see the server, all is ok\b", re.I),
            re.compile(r"\bserver is online\b", re.I),
            re.compile(r"\bserver ok\b", re.I),
            re.compile(r"\bhealthy\b", re.I),
            re.compile(r"\bping\b", re.I),
        ),
    ),
    IntentRule(
        intent="direct_control_server",
        handling_mode="direct_control",
        rationale="Start/stop/restart actions should invoke bounded plane operations.",
        patterns=(
            re.compile(r"\brestart\b.*\bserver\b", re.I),
            re.compile(r"\bserver_start\b", re.I),
            re.compile(r"\bserver_stop\b", re.I),
            re.compile(r"\bstart_server\b", re.I),
            re.compile(r"\bstop_server\b", re.I),
        ),
    ),
    IntentRule(
        intent="direct_control_scheduler",
        handling_mode="direct_control",
        rationale="Scheduler control requests are explicit operational mutations.",
        patterns=(
            re.compile(r"\bresume the scheduled jobs\b", re.I),
            re.compile(r"\breschedule\b", re.I),
            re.compile(r"\bdelay all by\b", re.I),
            re.compile(r"\brun now\b", re.I),
            re.compile(r"\benable\b.*\bjobs?\b", re.I),
            re.compile(r"\bdisable\b.*\bjobs?\b", re.I),
        ),
    ),
    IntentRule(
        intent="schedule_work",
        handling_mode="schedule_work",
        rationale="Scheduling batches or PIS runs should produce managed scheduler jobs.",
        patterns=(
            re.compile(r"\bschedule\b.*\bpis\b", re.I),
            re.compile(r"\bschedule all the remaining pises\b", re.I),
            re.compile(r"\bschedule the execution\b", re.I),
            re.compile(r"\bnext few time slot\b", re.I),
            re.compile(r"\biterations\b", re.I),
        ),
    ),
    IntentRule(
        intent="ledger_query",
        handling_mode="direct_status",
        rationale="Ledger/PIS status questions should answer from ledger truth and links.",
        patterns=(
            re.compile(r"\bledger\b", re.I),
            re.compile(r"\bpis-[0-9]{8}-", re.I),
            re.compile(r"\bpis\b.*\b(status|verified|deferred|active|close|implement)\b", re.I),
            re.compile(r"\bverified\b.*\bpis\b", re.I),
            re.compile(r"\bdeferred\b.*\bpis\b", re.I),
        ),
    ),
    IntentRule(
        intent="diagnostic_review",
        handling_mode="codex_reasoning",
        rationale="Review/audit requests still need full reasoning and code inspection.",
        patterns=(
            re.compile(r"\baudit\b", re.I),
            re.compile(r"\breview\b", re.I),
            re.compile(r"\bfindings\b", re.I),
            re.compile(r"\bwhy\b", re.I),
            re.compile(r"\bcheck\b.*\bwhy\b", re.I),
        ),
    ),
    IntentRule(
        intent="implementation_request",
        handling_mode="codex_reasoning",
        rationale="Implementation/build requests still belong to Codex execution rather than direct status.",
        patterns=(
            re.compile(r"\bimplement\b", re.I),
            re.compile(r"\bbuild\b", re.I),
            re.compile(r"\bfix\b", re.I),
            re.compile(r"\bdo it\b", re.I),
        ),
    ),
)


def _normalize_prompt(text: str) -> str:
    txt = re.sub(r"\s+", " ", text.strip().lower())
    txt = txt.replace("what’s", "what's")
    return txt


def _looks_like_user_prompt(text: str) -> bool:
    txt = text.strip()
    if not txt:
        return False
    if len(txt) > 3000:
        return False
    if "\x00" in txt:
        return False
    if txt.count("\n") > 40:
        return False
    if txt.startswith("{") or txt.startswith("["):
        return False
    shell_markers = ("$ ", "Traceback (most recent call last)", "Command:", "Output:")
    if any(marker in txt for marker in shell_markers):
        return False
    return True


def _source_rows() -> Iterator[dict]:
    for path in REPO_ROOT.joinpath("runtime", "state", "codex_terminal_slots").rglob("history.jsonl"):
        slot = path.parts[-3] if len(path.parts) >= 3 else ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                text = str(obj.get("text") or "").strip()
                if not _looks_like_user_prompt(text):
                    continue
                yield {
                    "text": text,
                    "source_file": str(path.relative_to(REPO_ROOT)),
                    "source_kind": "codex_history",
                    "session_id": str(obj.get("session_id") or ""),
                    "timestamp": int(obj.get("ts") or 0),
                    "slot": slot,
                    "surface": "codex_terminal",
                    "binding_id": "",
                }

    mobile_path = REPO_ROOT / "runtime" / "state" / "webui" / "mobile_chat_sessions.json"
    if mobile_path.exists():
        try:
            mobile_obj = json.loads(mobile_path.read_text(encoding="utf-8"))
        except Exception:
            mobile_obj = {}
        sessions = mobile_obj.get("sessions") if isinstance(mobile_obj, dict) else {}
        if isinstance(sessions, dict):
            for thread_id, rows in sessions.items():
                if not isinstance(rows, list):
                    continue
                for idx, row in enumerate(rows):
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("role") or "").strip().lower() != "user":
                        continue
                    text = str(row.get("content") or "").strip()
                    if not _looks_like_user_prompt(text):
                        continue
                    binding_id = str(thread_id or "").strip()
                    surface = "mobile_plane" if binding_id == "mpl" else "mobile_chat"
                    yield {
                        "text": text,
                        "source_file": str(mobile_path.relative_to(REPO_ROOT)),
                        "source_kind": "mobile_chat_sessions",
                        "session_id": binding_id,
                        "timestamp": idx,
                        "slot": "",
                        "surface": surface,
                        "binding_id": binding_id,
                    }


def _worker_outcome_index() -> dict[str, dict[str, int]]:
    path = REPO_ROOT / "governance" / "agents" / "runtime" / "workers_events.jsonl"
    outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    if not path.exists():
        return {}
    terminal_map = {
        "turn_ok": "turn_ok",
        "turn_error": "turn_error",
        "turn_timeout": "turn_timeout",
        "turn_cancelled": "turn_cancelled",
        "process_exit": "process_exit",
        "worker_gc_timeout": "worker_gc_timeout",
    }
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            worker = obj.get("worker") if isinstance(obj.get("worker"), dict) else {}
            binding_id = str(worker.get("binding_id") or "").strip()
            if not binding_id:
                continue
            event = str(obj.get("event") or "").strip()
            mapped = terminal_map.get(event)
            if mapped:
                outcomes[binding_id][mapped] += 1
    return {key: dict(value) for key, value in outcomes.items()}


def _classify(text: str) -> tuple[str, str, str]:
    for rule in INTENT_RULES:
        if any(pattern.search(text) for pattern in rule.patterns):
            return rule.intent, rule.handling_mode, rule.rationale
    return (
        "other",
        "codex_reasoning",
        "Fallback bucket: keep as reasoning-heavy until a narrower plane intent exists.",
    )


def _dedupe(rows: Iterable[dict]) -> List[dict]:
    grouped: dict[str, dict] = {}
    worker_index = _worker_outcome_index()
    for row in rows:
        key = _normalize_prompt(str(row["text"]))
        if key not in grouped:
            grouped[key] = {
                "normalized_prompt": key,
                "canonical_prompt": str(row["text"]).strip(),
                "count": 0,
                "sources": set(),
                "slots": set(),
                "example_session_ids": set(),
                "surfaces": set(),
                "binding_ids": set(),
            }
        item = grouped[key]
        item["count"] += 1
        item["sources"].add(str(row["source_file"]))
        if row.get("slot"):
            item["slots"].add(str(row["slot"]))
        if row.get("session_id"):
            item["example_session_ids"].add(str(row["session_id"]))
        if row.get("surface"):
            item["surfaces"].add(str(row["surface"]))
        if row.get("binding_id"):
            item["binding_ids"].add(str(row["binding_id"]))
    out: List[dict] = []
    for item in grouped.values():
        intent, handling_mode, rationale = _classify(item["canonical_prompt"])
        binding_ids = sorted(item["binding_ids"])
        observed = {
            binding_id: worker_index.get(binding_id, {})
            for binding_id in binding_ids
            if worker_index.get(binding_id)
        }
        out.append(
            {
                "schema_version": "plane_intent_example_v1",
                "canonical_prompt": item["canonical_prompt"],
                "normalized_prompt": item["normalized_prompt"],
                "count": int(item["count"]),
                "intent": intent,
                "handling_mode": handling_mode,
                "rationale": rationale,
                "source_kind": "mixed_histories",
                "source_files": sorted(item["sources"]),
                "slots": sorted(item["slots"]),
                "surfaces": sorted(item["surfaces"]),
                "binding_ids": binding_ids,
                "example_session_ids": sorted(item["example_session_ids"])[:5],
                "observed_worker_outcomes": observed,
            }
        )
    out.sort(key=lambda row: (-int(row["count"]), str(row["intent"]), str(row["canonical_prompt"]).lower()))
    return out


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_report(path: Path, rows: List[dict]) -> None:
    intent_counts = Counter(str(row["intent"]) for row in rows)
    mode_counts = Counter(str(row["handling_mode"]) for row in rows)
    top_rows = [
        row
        for row in rows
        if len(str(row["canonical_prompt"]).strip()) >= 12 and str(row["intent"]) != "other"
    ][:40]
    lines = [
        "# Plane Intent Mining Report",
        "",
        "This is a first-pass mined routing/evaluation corpus for the mobile Plane tab.",
        "",
        "Source scope:",
        "- `runtime/state/codex_terminal_slots/*/history.jsonl`",
        "- `runtime/state/webui/mobile_chat_sessions.json`",
        "- `governance/agents/runtime/workers_events.jsonl` for observed worker outcomes by binding id",
        "- filtered to likely user-authored prompts",
        "- deduped by normalized prompt text",
        "",
        f"Unique prompts kept: `{len(rows)}`",
        "",
        "## Intent Counts",
    ]
    for intent, count in sorted(intent_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{intent}`: `{count}`")
    lines.extend(["", "## Handling Mode Counts"])
    for mode, count in sorted(mode_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{mode}`: `{count}`")
    lines.extend(
        [
            "",
            "## Routing Recommendation",
            "- `direct_status`: queue, scheduler-state, health, snapshot, freshness, and next-job prompts.",
            "- `direct_control`: explicit start/stop/restart/enable/disable/reschedule commands.",
            "- `schedule_work`: requests to create or modify scheduled implementation programs.",
            "- `codex_reasoning`: audit/review/implementation questions that need repo reasoning.",
            "",
            "## Added Fields",
            "- `surfaces`: where the prompt was observed, e.g. `mobile_plane`, `mobile_chat`, `codex_terminal`.",
            "- `binding_ids`: durable worker binding ids tied to the prompt when available.",
            "- `observed_worker_outcomes`: terminal worker event counts keyed by binding id from `workers_events.jsonl`.",
            "",
            "## Top Seed Examples",
        ]
    )
    for row in top_rows:
        lines.append(
            f"- `{row['intent']}` / `{row['handling_mode']}` / count `{row['count']}`: "
            f"{row['canonical_prompt']}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mine a first-pass Plane intent dataset from stored histories.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    rows = list(_source_rows())
    deduped = _dedupe(rows)
    out_dir = Path(args.out_dir).resolve()
    dataset_path = out_dir / "plane_intent_dataset_seed.jsonl"
    direct_eval_path = out_dir / "plane_direct_eval_seed.jsonl"
    report_path = out_dir / "plane_intent_report.md"
    _write_jsonl(dataset_path, deduped)
    _write_jsonl(
        direct_eval_path,
        [
            row
            for row in deduped
            if str(row["handling_mode"]) in {"direct_status", "direct_control", "schedule_work"}
        ],
    )
    _write_report(report_path, deduped)
    summary = {
        "schema_version": "plane_intent_mining_summary_v1",
        "dataset_path": str(dataset_path),
        "direct_eval_path": str(direct_eval_path),
        "report_path": str(report_path),
        "raw_rows": len(rows),
        "unique_rows": len(deduped),
        "intent_counts": dict(Counter(str(row["intent"]) for row in deduped)),
        "handling_mode_counts": dict(Counter(str(row["handling_mode"]) for row in deduped)),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
