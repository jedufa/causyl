#!/usr/bin/env python3
"""Interactive helper for inspecting and updating a scheduled job's agent model.

Usage:
  scheduler_change_job_model.py [job_id] [--model MODEL] [--builder-model MODEL] [--verifier-model MODEL] [--fallback-models MODELS] [--list] [--yes] [--all]

Arguments:
  job_id           The scheduled job ID to update (optional when picking from pending list).
  --model MODEL    Set the job's primary model non-interactively. For build-verify jobs, this applies to both builder and verifier unless stage-specific flags are used.
  --builder-model MODEL
                   Set only the builder model for build-verify jobs.
  --verifier-model MODEL
                   Set only the verifier model for build-verify jobs.
  --fallback-models MODELS
                   Set the job's top-level fallback model list non-interactively as a comma-separated list.
                   Use an empty string to clear explicit fallbacks.
  --list           Just list available model candidates for the target job and exit.
  --yes            Skip the confirmation prompt before persisting the new model (default: prompt).
  --all            When no job_id is provided, include every job instead of only jobs due within 2 days
                   (default behavior shows only the jobs scheduled within the next 48 hours).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWN_MODEL_CANDIDATES = (
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "deepseek-chat",
    "deepseek-reasoner",
)


def _ensure_repo_in_path() -> None:
    sys.path.insert(0, str(REPO_ROOT))


_ensure_repo_in_path()

from tools.bin.scheduler_tty_ui import prompt_menu, prompt_yes_no
from infra.controlplane.sched import operator_backend as sched_backend
from infra.webui.ledger import ledger_storage


def _parse_runtime_policy_models() -> List[str]:
    path = REPO_ROOT / "config" / "runtime_policy.properties"
    models: List[str] = []

    def _add(entry: Optional[str]) -> None:
        if not entry:
            return
        if entry not in models:
            models.append(entry)

    if not path.exists():
        return models

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            continue
        if "model" not in key:
            continue
        for candidate in value.split(","):
            _add(candidate.strip())
    return models


def _pis_title_from_binding(binding_id: str) -> str:
    if not binding_id:
        return ""
    item_dir = ledger_storage.item_dir_for_uid(REPO_ROOT / "governance" / "ledger" / "items", binding_id)
    if item_dir is None:
        return ""
    content = (item_dir / "pis.md").read_text(encoding="utf-8", errors="replace").splitlines()
    in_title = False
    for line in content:
        stripped = line.strip()
        if stripped.startswith("## 1. Title"):
            in_title = True
            continue
        if stripped.startswith("## ") and in_title:
            break
        if in_title and stripped:
            return stripped
    return ""


def _reason_display(reason: str, rows_by_job_id: Dict[str, Dict[str, Any]]) -> str:
    txt = str(reason or "").strip()
    if not txt:
        return ""
    if txt.startswith("dependency_not_ok:"):
        dep_id = txt.split(":", 1)[1].strip()
        dep_row = rows_by_job_id.get(dep_id) or {}
        dep_title = str(dep_row.get("display_title") or dep_id).strip()
        return f"blocked by {dep_title}"
    return txt.replace("_", " ")


def _disabled_reason_display(effective_due: Dict[str, Any], rows_by_job_id: Dict[str, Dict[str, Any]]) -> str:
    reason = str(effective_due.get("reason") or "").strip().lower()
    if reason.startswith("dependency_not_ok:"):
        return f"disabled; would be {_reason_display(reason, rows_by_job_id)}"
    if reason in {"due", "due_failed_slot_reusable"}:
        return "disabled; would be due"
    if reason == "not_due_yet":
        due_utc = str(effective_due.get("due_utc") or "").strip()
        return f"disabled; due {due_utc}" if due_utc else "disabled; due soon"
    if reason == "active_run":
        return "disabled; blocked by active run"
    if reason:
        return f"disabled; would be {_reason_display(reason, rows_by_job_id)}"
    return "disabled"


def _gather_available_models(spec: Dict[str, Any]) -> List[str]:
    runtime = spec.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    models: List[str] = []

    def _push(entry: Optional[str]) -> None:
        if not entry:
            return
        entry = str(entry).strip()
        if entry and entry not in models:
            models.append(entry)

    _push(runtime.get("model"))
    for fallback in runtime.get("fallback_models") or []:
        _push(str(fallback))
    tech_sequence = runtime.get("tech_model_sequence") or []
    if isinstance(tech_sequence, Iterable):
        for entry in tech_sequence:
            if isinstance(entry, dict):
                _push(entry.get("model"))
    for entry in _parse_runtime_policy_models():
        _push(entry)
    for entry in KNOWN_MODEL_CANDIDATES:
        _push(entry)
    _push("pipeline")
    if not models:
        _push("gpt-5.4")
    return models

def _format_models(models: List[str], current: Optional[str]) -> str:
    lines = []
    for idx, model in enumerate(models, start=1):
        marker = " (current)" if current and model == current else ""
        lines.append(f"  {idx}. {model}{marker}")
    if not lines:
        return "  (none)"
    return "\n".join(lines)


def _parse_csv_models(raw: str) -> List[str]:
    values: List[str] = []
    for part in str(raw or "").split(","):
        candidate = str(part or "").strip()
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def _shared_value(values: Sequence[str]) -> Optional[str]:
    normalized = [str(value or "").strip() for value in values]
    unique = {value for value in normalized}
    if len(unique) == 1:
        return normalized[0]
    return None


def _shared_model_list(specs: Sequence[Dict[str, Any]]) -> Optional[List[str]]:
    current_lists: List[List[str]] = []
    for spec in specs:
        runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
        current_lists.append([str(x).strip() for x in list(runtime.get("fallback_models") or []) if str(x).strip()])
    if not current_lists:
        return []
    first = current_lists[0]
    if all(items == first for items in current_lists[1:]):
        return first
    return None


def _parse_selection_ranges(raw: str, *, count: int) -> List[int]:
    selection = str(raw or "").strip()
    if not selection:
        return [0]
    lowered = selection.lower()
    if lowered in {"all", "*"}:
        return list(range(count))

    selected: List[int] = []
    seen: set[int] = set()
    for token in selection.split(","):
        item = token.strip()
        if not item:
            continue
        if "-" in item:
            left, right = item.split("-", 1)
            if not left.strip().isdigit() or not right.strip().isdigit():
                raise ValueError(f"invalid range '{item}'")
            start = int(left.strip())
            end = int(right.strip())
            if start > end:
                raise ValueError(f"invalid range '{item}'")
            indexes = range(start, end + 1)
        else:
            if not item.isdigit():
                raise ValueError(f"invalid selection '{item}'")
            indexes = [int(item)]
        for index in indexes:
            zero_based = index - 1
            if zero_based < 0 or zero_based >= count:
                raise ValueError(f"selection '{index}' is out of range 1..{count}")
            if zero_based not in seen:
                seen.add(zero_based)
                selected.append(zero_based)
    if not selected:
        raise ValueError("selection is empty")
    return selected


def _select_model(
    models: List[str], current: Optional[str], args: argparse.Namespace
) -> Optional[str]:
    if args.model:
        candidate = args.model.strip()
        return candidate if candidate else None

    if not models:
        print("No available model candidates.", file=sys.stderr)
        return None

    return prompt_menu(
        "Available models",
        choices=models,
        render=lambda model: f"{model}{' (current)' if current and model == current else ''}",
        default_index=models.index(current) if current in models else 0,
        fallback_reader=input,
    )


def _select_fallback_models(models: List[str], current: Optional[List[str]]) -> Optional[List[str]]:
    if not models:
        print("No available model candidates.", file=sys.stderr)
        return None

    current_set = {item for item in list(current or []) if item}
    print("Available fallback models:")
    for idx, model in enumerate(models, start=1):
        marker = " (current)" if model in current_set else ""
        print(f"  {idx}. {model}{marker}")
    prompt = "Fallback selection (e.g. 1,2,4-6, 'all', 'none'"
    if current is not None:
        prompt += ", Enter=keep current"
    prompt += "): "
    while True:
        raw = str(input(prompt) or "").strip()
        lowered = raw.lower()
        if not raw and current is not None:
            return list(current)
        if lowered in {"none", "clear"}:
            return []
        try:
            indexes = _parse_selection_ranges(raw, count=len(models))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            continue
        return [models[index] for index in indexes]


def _confirm(new_model: str) -> bool:
    return prompt_yes_no(f"Apply model '{new_model}'", default=False, fallback_reader=input)


def _infer_agent_tech_from_model(model: str) -> str:
    m = str(model or "").strip().lower()
    if not m:
        return ""
    if m == "pipeline" or m.startswith("pipeline:"):
        return "pipeline"
    if m.startswith("claude"):
        return "claude"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("grok"):
        return "xai"
    if "codex" in m:
        return "codex"
    if m.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith(("qwen", "llama", "mistral", "phi", "gemma")):
        return "ollama"
    return ""


def _clear_runtime_model_routing(runtime: Dict[str, Any]) -> None:
    for key in ("fallback_agent_tech", "fallback_agent_techs", "fallback_model", "fallback_models", "tech_model_sequence"):
        runtime.pop(key, None)


def _runtime_would_change(spec: Dict[str, Any], new_model: str) -> bool:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    current_model = str(runtime.get("advisory_model") or runtime.get("model") or "").strip()
    current_tech = str(runtime.get("advisory_agent_tech") or runtime.get("agent_tech") or "").strip().lower()
    inferred = _infer_agent_tech_from_model(new_model)
    if current_model != new_model:
        return True
    if inferred and inferred != current_tech:
        return True
    return False


def _stage_runtime_would_change(spec: Dict[str, Any], stage: str, new_model: str) -> bool:
    loop = spec.get("loop") if isinstance(spec.get("loop"), dict) else {}
    stage_runtime = loop.get(f"{stage}_runtime") if isinstance(loop.get(f"{stage}_runtime"), dict) else {}
    current_model = str(stage_runtime.get("advisory_model") or stage_runtime.get("model") or "").strip()
    current_tech = str(stage_runtime.get("advisory_agent_tech") or stage_runtime.get("agent_tech") or "").strip().lower()
    inferred = _infer_agent_tech_from_model(new_model)
    if current_model != new_model:
        return True
    if inferred and inferred != current_tech:
        return True
    return False


def _fallback_models_would_change(spec: Dict[str, Any], new_fallback_models: Sequence[str]) -> bool:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    current = [str(x).strip() for x in list(runtime.get("fallback_models") or []) if str(x).strip()]
    desired = [str(x).strip() for x in list(new_fallback_models or []) if str(x).strip()]
    return current != desired


def _safe_update_job(
    job_id: str,
    *,
    model: str = "",
    builder_model: str = "",
    verifier_model: str = "",
    fallback_models: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    from infra.controlplane import scheduled_jobs as sched

    spec = sched.load_job(job_id, repo_root=REPO_ROOT)
    normalized_model = str(model or "").strip()
    normalized_builder_model = str(builder_model or "").strip()
    normalized_verifier_model = str(verifier_model or "").strip()
    normalized_fallback_models = None
    if fallback_models is not None:
        normalized_fallback_models = []
        for item in fallback_models:
            txt = str(item or "").strip()
            if txt and txt not in normalized_fallback_models:
                normalized_fallback_models.append(txt)
    if normalized_model:
        runtime = spec.setdefault("runtime", {})
        _clear_runtime_model_routing(runtime)
        runtime["model"] = normalized_model
        runtime["advisory_model"] = normalized_model
        inferred_tech = _infer_agent_tech_from_model(normalized_model)
        if inferred_tech:
            runtime["agent_tech"] = inferred_tech
            runtime["advisory_agent_tech"] = inferred_tech
        if str(spec.get("job_kind") or "") == "build_verify_loop":
            loop = spec.setdefault("loop", {})
            for key in ("builder_runtime", "verifier_runtime"):
                stage_runtime = dict(loop.get(key) or {})
                _clear_runtime_model_routing(stage_runtime)
                stage_runtime["model"] = normalized_model
                stage_runtime["advisory_model"] = normalized_model
                if inferred_tech:
                    stage_runtime["agent_tech"] = inferred_tech
                    stage_runtime["advisory_agent_tech"] = inferred_tech
                loop[key] = stage_runtime
    elif str(spec.get("job_kind") or "") == "build_verify_loop":
        loop = spec.setdefault("loop", {})
        if normalized_builder_model:
            stage_runtime = dict(loop.get("builder_runtime") or {})
            _clear_runtime_model_routing(stage_runtime)
            stage_runtime["model"] = normalized_builder_model
            stage_runtime["advisory_model"] = normalized_builder_model
            inferred_builder_tech = _infer_agent_tech_from_model(normalized_builder_model)
            if inferred_builder_tech:
                stage_runtime["agent_tech"] = inferred_builder_tech
                stage_runtime["advisory_agent_tech"] = inferred_builder_tech
            loop["builder_runtime"] = stage_runtime
        if normalized_verifier_model:
            stage_runtime = dict(loop.get("verifier_runtime") or {})
            _clear_runtime_model_routing(stage_runtime)
            stage_runtime["model"] = normalized_verifier_model
            stage_runtime["advisory_model"] = normalized_verifier_model
            inferred_verifier_tech = _infer_agent_tech_from_model(normalized_verifier_model)
            if inferred_verifier_tech:
                stage_runtime["agent_tech"] = inferred_verifier_tech
                stage_runtime["advisory_agent_tech"] = inferred_verifier_tech
            loop["verifier_runtime"] = stage_runtime
    if normalized_fallback_models is not None:
        runtime = spec.setdefault("runtime", {})
        for key in ("fallback_agent_tech", "fallback_agent_techs", "fallback_model", "fallback_models", "tech_model_sequence"):
            runtime.pop(key, None)
        runtime["fallback_models"] = list(normalized_fallback_models)
        runtime["fallback_model"] = normalized_fallback_models[0] if normalized_fallback_models else ""
    saved = sched.save_job(spec, repo_root=REPO_ROOT)
    return saved


def _parse_due_timestamp(row: Dict[str, Any]) -> Optional[datetime]:
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    raw = str(due.get("due_utc") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _hypothetical_due_for_disabled(row: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
    from infra.controlplane import scheduled_jobs as sched

    if bool(row.get("enabled")):
        return dict(row.get("due") or {})
    job_id = str(row.get("job_id") or "")
    if not job_id:
        return {}
    spec = sched.load_job(job_id, repo_root=REPO_ROOT)
    spec["enabled"] = True
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    return sched.due_status(spec, state=state, now=now, repo_root=REPO_ROOT)


def _row_effective_due(row: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
    if bool(row.get("enabled")):
        return dict(row.get("due") or {})
    hypothetical = _hypothetical_due_for_disabled(row, now=now)
    out = dict(hypothetical)
    out["_hypothetical"] = True
    return out


def _should_include_row(row: Dict[str, Any], *, include_all: bool, now: datetime, target: datetime) -> bool:
    effective_due = _row_effective_due(row, now=now)
    reason = str(effective_due.get("reason") or "").lower()
    if reason == "already_executed":
        return False
    if include_all:
        return True
    ts = _parse_due_timestamp({"due": effective_due})
    if ts and ts <= target:
        return True
    return reason in {"due", "due_failed_slot_reusable", "active_run"} or reason.startswith("dependency_not_ok:")


def _display_sort_key(job_id: str, rows_by_job_id: Dict[str, Dict[str, Any]], effective_due: Dict[str, Dict[str, Any]]) -> Tuple[str, str, str]:
    row = rows_by_job_id.get(job_id) or {}
    due = effective_due.get(job_id) or {}
    return (
        str(due.get("due_utc") or ""),
        str(row.get("display_title") or ""),
        job_id,
    )


def _order_rows_by_dependencies(rows: List[Dict[str, Any]], *, now: datetime) -> List[Dict[str, Any]]:
    rows_by_job_id = {str(row.get("job_id") or ""): row for row in rows}
    effective_due = {job_id: _row_effective_due(row, now=now) for job_id, row in rows_by_job_id.items()}
    indegree: Dict[str, int] = {job_id: 0 for job_id in rows_by_job_id}
    children: Dict[str, List[str]] = {job_id: [] for job_id in rows_by_job_id}
    for job_id, row in rows_by_job_id.items():
        for dep in list(row.get("depends_on") or []):
            dep_id = str(dep or "").strip()
            if dep_id in rows_by_job_id:
                indegree[job_id] += 1
                children.setdefault(dep_id, []).append(job_id)
    heap: List[Tuple[Tuple[str, str, str], str]] = []
    for job_id, degree in indegree.items():
        if degree == 0:
            heappush(heap, (_display_sort_key(job_id, rows_by_job_id, effective_due), job_id))
    ordered: List[Dict[str, Any]] = []
    seen: set[str] = set()
    while heap:
        _key, job_id = heappop(heap)
        if job_id in seen:
            continue
        seen.add(job_id)
        ordered.append(rows_by_job_id[job_id])
        for child_id in sorted(children.get(job_id) or [], key=lambda x: _display_sort_key(x, rows_by_job_id, effective_due)):
            indegree[child_id] -= 1
            if indegree[child_id] <= 0:
                heappush(heap, (_display_sort_key(child_id, rows_by_job_id, effective_due), child_id))
    for job_id in sorted((jid for jid in rows_by_job_id if jid not in seen), key=lambda x: _display_sort_key(x, rows_by_job_id, effective_due)):
        ordered.append(rows_by_job_id[job_id])
    return ordered


def _list_pending_jobs(include_all: bool) -> List[Dict[str, Any]]:
    from infra.controlplane import scheduled_jobs as sched

    now = datetime.now(timezone.utc)
    target = now + timedelta(days=2)
    rows = sched.list_jobs(repo_root=REPO_ROOT)
    pending = [row for row in rows if _should_include_row(row, include_all=include_all, now=now, target=target)]
    return _order_rows_by_dependencies(pending, now=now)


def _choose_job_from_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        print("No pending jobs available.", file=sys.stderr)
        return []

    rows_by_job_id = {str(row.get("job_id") or ""): row for row in rows}
    print("Pending jobs:")
    for idx, row in enumerate(rows, start=1):
        runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
        model = str(runtime.get("model") or "").strip() or "(unspecified)"
        binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
        binding_id = str(binding.get("id") or "").strip()
        tail = binding_id[-4:] if binding_id else row.get("job_id", "")[-4:]
        pis_title = _pis_title_from_binding(binding_id) or ""
        job_display = str(row.get("display_title") or row.get("job_id", "")).strip()
        jid = str(row.get("job_id") or "")
        effective_due = _row_effective_due(row, now=datetime.now(timezone.utc))
        if not bool(row.get("enabled")):
            reason = _disabled_reason_display(effective_due, rows_by_job_id)
        else:
            reason = _reason_display(str(effective_due.get("reason") or ""), rows_by_job_id)
        due_txt = f"({reason})" if reason else ""
        if pis_title:
            print(f"  {idx}. [{model}] {tail} {pis_title} - {job_display} [{jid}] {due_txt}")
        else:
            print(f"  {idx}. [{model}] {job_display} [{jid}] {due_txt}")
    while True:
        raw = str(input("Select jobs (e.g. 1,2,3-9,12; Enter=1): ") or "").strip()
        try:
            indexes = _parse_selection_ranges(raw, count=len(rows))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            continue
        return [rows[index] for index in indexes]


def _prompt_change_target(specs: Sequence[Dict[str, Any]]) -> str:
    has_build_verify = any(str(spec.get("job_kind") or "") == "build_verify_loop" for spec in specs)
    choices = ["primary", "fallback"]
    if has_build_verify:
        choices.extend(["builder", "verifier"])
    return prompt_menu(
        "What do you want to change",
        choices=choices,
        render=lambda value: {
            "primary": "Primary model",
            "builder": "Builder model only",
            "verifier": "Verifier model only",
            "fallback": "Fallback models",
        }.get(value, str(value)),
        default_index=0,
        fallback_reader=input,
    )


def main() -> int:
    try:
        _ensure_repo_in_path()
        from infra.controlplane import scheduled_jobs as sched

        parser = argparse.ArgumentParser(description="Change a scheduled job's primary agent model.")
        parser.add_argument("job_id", nargs="?", help="The scheduled job ID to update.")
        parser.add_argument("--model", help="Non-interactive model name to set.")
        parser.add_argument("--builder-model", help="Set only the builder model for build-verify jobs.")
        parser.add_argument("--verifier-model", help="Set only the verifier model for build-verify jobs.")
        parser.add_argument(
            "--fallback-models",
            help="Set top-level fallback models as a comma-separated list. Use an empty string to clear explicit fallbacks.",
        )
        parser.add_argument("--list", action="store_true", help="Only list the available models for this job.")
        parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
        parser.add_argument("--all", action="store_true", help="Show every job instead of just those due within two days.")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args()
        sched_backend.require_legacy_for_mutation("change scheduled job model", backend=args.backend or None)

        job_ids: List[str] = []
        selected_rows: List[Dict[str, Any]] = []
        if args.job_id:
            job_ids = [args.job_id]
        else:
            pending = _list_pending_jobs(include_all=args.all)
            selected_rows = _choose_job_from_list(pending)
            if not selected_rows:
                print("No job selected; exiting.")
                return 1
            job_ids = [str(row.get("job_id") or "") for row in selected_rows if row.get("job_id")]
            job_ids = [jid for jid in job_ids if jid]
            if not job_ids:
                print("Selected job(s) have no job_id; exiting.")
                return 1

        specs: List[Dict[str, Any]] = []
        for jid in job_ids:
            try:
                spec = sched.load_job(jid, repo_root=REPO_ROOT)
            except FileNotFoundError:
                print(f"Job '{jid}' not found.", file=sys.stderr)
                return 1
            specs.append(spec)

        shared_current_models = set()
        build_verify_specs = [spec for spec in specs if str(spec.get("job_kind") or "") == "build_verify_loop"]
        shared_builder_model = None
        shared_verifier_model = None
        if build_verify_specs:
            shared_builder_model = _shared_value(
                [str(((spec.get("loop") if isinstance(spec.get("loop"), dict) else {}).get("builder_runtime") or {}).get("model") or "") for spec in build_verify_specs]
            )
            shared_verifier_model = _shared_value(
                [str(((spec.get("loop") if isinstance(spec.get("loop"), dict) else {}).get("verifier_runtime") or {}).get("model") or "") for spec in build_verify_specs]
            )
        shared_fallback_models = _shared_model_list(specs)
        for spec in specs:
            runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
            loop = spec.get("loop") if isinstance(spec.get("loop"), dict) else {}
            current_model = str(runtime.get("advisory_model") or runtime.get("model") or "").strip()
            agent_tech = str(runtime.get("advisory_agent_tech") or runtime.get("agent_tech") or "").strip() or "(unspecified)"
            executor_tech = str(runtime.get("executor_agent_tech") or "codex").strip() or "codex"
            print(f"Job: {spec.get('job_id')}")
            print(f"  Executor tech: {executor_tech}")
            print(f"  Advisory tech: {agent_tech}")
            print(f"  Current advisory model: {current_model or '(none)'}")
            if str(spec.get("job_kind") or "") == "build_verify_loop":
                builder_runtime = loop.get("builder_runtime") if isinstance(loop.get("builder_runtime"), dict) else {}
                verifier_runtime = loop.get("verifier_runtime") if isinstance(loop.get("verifier_runtime"), dict) else {}
                print(f"  Builder advisory model: {str(builder_runtime.get('advisory_model') or builder_runtime.get('model') or '(none)')}")
                print(f"  Verifier advisory model: {str(verifier_runtime.get('advisory_model') or verifier_runtime.get('model') or '(none)')}")
            fallback = runtime.get("fallback_models") or []
            if fallback:
                print(f"  Fallback models: {', '.join(str(x) for x in fallback)}")
            if current_model:
                shared_current_models.add(current_model)
        shared_current_model = next(iter(shared_current_models)) if len(shared_current_models) == 1 else None
        models: List[str] = []
        for spec in specs:
            for candidate in _gather_available_models(spec):
                if candidate not in models:
                    models.append(candidate)

        if args.list:
            print("Model candidates:")
            print(_format_models(models, shared_current_model))
            return 0

        builder_model = str(args.builder_model or "").strip()
        verifier_model = str(args.verifier_model or "").strip()
        new_model = str(args.model or "").strip()
        fallback_models_arg = args.fallback_models
        fallback_models = _parse_csv_models(fallback_models_arg) if fallback_models_arg is not None else None
        active_flag_count = sum(
            1 for value in (bool(new_model), bool(builder_model), bool(verifier_model), fallback_models_arg is not None) if value
        )
        if active_flag_count > 1:
            print("Use only one of --model, --builder-model, --verifier-model, or --fallback-models at a time.", file=sys.stderr)
            return 1

        if not new_model and not builder_model and not verifier_model and fallback_models is None:
            target = _prompt_change_target(specs)
            if target == "primary":
                new_model = _select_model(models, shared_current_model, args) or ""
                if not new_model:
                    print("No selection made; aborting.")
                    return 1
            elif target == "builder":
                builder_model = _select_model(models, shared_builder_model, args) or ""
                if not builder_model:
                    print("No selection made; aborting.")
                    return 1
            elif target == "verifier":
                verifier_model = _select_model(models, shared_verifier_model, args) or ""
                if not verifier_model:
                    print("No selection made; aborting.")
                    return 1
            elif target == "fallback":
                fallback_models = _select_fallback_models(models, shared_fallback_models)
                if fallback_models is None:
                    print("No selection made; aborting.")
                    return 1

        if new_model:
            if not any(_runtime_would_change(spec, new_model) for spec in specs):
                print("Runtime already matches requested model/provider mapping; nothing to do.")
                return 0
            confirm_label = new_model
        elif fallback_models is not None:
            if not any(_fallback_models_would_change(spec, fallback_models) for spec in specs):
                print("Selected fallback models already match; nothing to do.")
                return 0
            confirm_label = f"fallbacks={','.join(fallback_models) if fallback_models else '(clear)'}"
        else:
            if not any(
                (builder_model and _stage_runtime_would_change(spec, "builder", builder_model))
                or (verifier_model and _stage_runtime_would_change(spec, "verifier", verifier_model))
                for spec in specs
            ):
                print("Selected stage runtime(s) already match; nothing to do.")
                return 0
            label_bits: List[str] = []
            if builder_model:
                label_bits.append(f"builder={builder_model}")
            if verifier_model:
                label_bits.append(f"verifier={verifier_model}")
            confirm_label = ", ".join(label_bits)

        if not args.yes and not _confirm(confirm_label):
            print("Aborted by user.")
            return 0

        for jid in job_ids:
            saved = _safe_update_job(
                jid,
                model=new_model,
                builder_model=builder_model,
                verifier_model=verifier_model,
                fallback_models=fallback_models,
            )
            saved_runtime = saved.get("runtime") if isinstance(saved.get("runtime"), dict) else {}
            loop = saved.get("loop") if isinstance(saved.get("loop"), dict) else {}
            builder_runtime = loop.get("builder_runtime") if isinstance(loop.get("builder_runtime"), dict) else {}
            verifier_runtime = loop.get("verifier_runtime") if isinstance(loop.get("verifier_runtime"), dict) else {}
            message = (
                f"Update applied to {jid}: advisory_model={saved_runtime.get('advisory_model') or saved_runtime.get('model')} "
                f"advisory_agent_tech={saved_runtime.get('advisory_agent_tech') or saved_runtime.get('agent_tech')} "
                f"executor_agent_tech={saved_runtime.get('executor_agent_tech') or 'codex'}"
            )
            message += f" fallback_models={','.join(str(x) for x in list(saved_runtime.get('fallback_models') or [])) or '(none)'}"
            if str(saved.get("job_kind") or "") == "build_verify_loop":
                message += (
                    f" builder_model={builder_runtime.get('model')}"
                    f" verifier_model={verifier_runtime.get('model')}"
                )
            print(message)
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
