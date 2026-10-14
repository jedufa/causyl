"""Durable scheduled agent jobs for the control plane.

This module keeps scheduled-job truth in canonical JSON specs on disk and
derives mutable execution state, receipts, and reports around those specs.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import socket
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from infra.root_tools import load_tool_module as _load_root_tool_module

_runtime_policy = _load_root_tool_module("runtime_policy", "tools/runtime_policy.py")
_fsio = _load_root_tool_module("shared_fsio", "tools/shared/fsio.py")
_commit_journal = _load_root_tool_module("git_support_commit_journal", "tools/git/support/commit_journal.py")
from infra.controlplane import compiled_artifacts as _compiled_artifacts
from infra.controlplane import compiled_execution_contract as _compiled_execution_contract
from infra.controlplane import compiled_execution_ir as _compiled_execution_ir
from infra.controlplane import compiled_execution_integration as _compiled_execution_integration
from infra.controlplane import compiled_execution_manifest as _compiled_execution_manifest
from infra.controlplane import compiled_failure_policy as _compiled_failure_policy
from infra.controlplane import compiled_output_classifier as _compiled_output_classifier
from infra.controlplane import compiled_prompt_renderer as _compiled_prompt_renderer
from infra.controlplane import continuation_context_attachment as _continuation_context_attachment
from infra.controlplane import context_bundles as _context_bundles
from infra.controlplane import job_budget_cost_ledger_store as _job_budget_cost_ledger_store
from infra.controlplane import scheduler_projection_contract as _scheduler_projection_contract
from infra.controlplane import job_shape_context_contract as _job_shape_context_contract
from infra.controlplane import job_shape_contract as _job_shape_contract
from infra.controlplane import job_shape_lifecycle_contract as _job_shape_lifecycle_contract
from infra.controlplane import job_shape_relationship_contract as _job_shape_relationship_contract
from infra.controlplane.scheduled_jobs_types import JobReceipt, JobSpec
from infra.controlplane import scheduled_jobs_parsing as _scheduled_jobs_parsing
from infra.webui import server_agents as _agents
from infra.webui import server_agents_swarm as _agent_swarm
from infra.webui.ledger import ledger_storage as _ledger_storage

try:
    from infra.webui import server_execution_queue as _execution_queue
except Exception:  # pragma: no cover - optional guard for limited environments
    _execution_queue = None


SCHEMA_VERSION = "scheduled_job_v1"
LEDGER_SCHEMA_VERSION = "scheduled_job_execution_event_v1"
RECEIPT_SCHEMA_VERSION = "scheduled_job_receipt_v1"
LAB_TASK_PROMPT_PATH = "docs/prompts/lab_task_prompts.md"
JOB_KIND_SET = {"single_turn", "build_verify_loop", "swarm_launch", "project_runner"}
LOOP_POLICY_SET = {"default_conditional_repair_loop", "full_build_verify_loop"}
PIPELINE_EXECUTOR_MODE_SET = {"stage8_backend", "full_auto"}
PIPELINE_STAGE8_BACKEND_MODE = "stage8_backend"
PIPELINE_FULL_AUTO_MODE = "full_auto"
DEFAULT_BUILD_VERIFY_LOOP_POLICY = "default_conditional_repair_loop"
BUILD_VERIFY_REPAIR_LOOP_POLICY = DEFAULT_BUILD_VERIFY_LOOP_POLICY
BUILD_VERIFY_REPAIR_OPERATOR_LABEL = "build-verify-repair"
ONE_POINT_FIVE_ITERATIONS_OPERATOR_LABEL = "1.5 iterations"
LOOP_POLICY_ALIASES = {
    "build_verify_repair": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "build-verify-repair": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "bvr": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "1.5_iterations": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "1.5-iterations": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "one_point_five_iterations": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "one-point-five-iterations": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
    "conditional_pass": DEFAULT_BUILD_VERIFY_LOOP_POLICY,
}
CONDITIONAL_PASS_TERMINAL_STATUS = "conditional_pass_repair_applied"
DELIVERY_GUARANTEED_POLICY = "delivery_guaranteed"
STRICT_DELIVERY_POLICY = "strict"
DELIVERY_POLICY_SET = {STRICT_DELIVERY_POLICY, DELIVERY_GUARANTEED_POLICY}
EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS = "executed_with_closeout_integrity_defect"
DELIVERY_TERMINAL_STATUSES = {
    "delivered_unverified",
    "delivered_with_findings",
    "delivered_builder_only",
}
SUCCESS_TERMINAL_STATUSES = {
    "ok",
    CONDITIONAL_PASS_TERMINAL_STATUS,
    EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS,
    *DELIVERY_TERMINAL_STATUSES,
}
PASS_PENDING_ACTIONS_VERDICT = "PASS_PENDING_ACTIONS"
AGENT_TECH_SET = {"codex", "openai", "claude", "gemini", "xai", "ollama", "deepseek", "pipeline"}
_OPENAI_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
_DEFAULT_REASONING_EFFORTS = ("low", "medium", "high")
ROLE_TARGETS = {
    "builder": {"runtime_role": "builder", "prompt_file": ""},    "assurer": {"runtime_role": "assurer", "prompt_file": ""},
    "verifier": {"runtime_role": "assurer", "prompt_file": "role__verifier_mobile.md"},    "generalist": {"runtime_role": "generalist", "prompt_file": ""},
    "orchestrator": {"runtime_role": "generalist", "prompt_file": "role__orchestrator_mobile.md"},
}
ROLE_SET = set(ROLE_TARGETS.keys())
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
DEFAULT_SCHEDULER_INTERVAL_S = 30
_PENDING_ACTION_VAGUE_PHRASES = {
    "improve reasoning",
    "tighten evidence",
    "clean up issues",
    "review dependencies more carefully",
    "clean up",
    "tighten",
    "improve",
    "review more carefully",
}
SWARM_MISSION_TYPE_ALLOWLIST = {"delivery_patch"}
SWARM_SUCCESS_STATUS_SET = {"finished", "partial", "failed", "cancelled", "timed_out", "interrupted"}
SWARM_WRITE_POLICY_SET = {"bounded_patch"}
JOB_KIND_TO_CANONICAL_JOB_SHAPE = {
    "single_turn": "verification_loop_job",
    "build_verify_loop": "verification_loop_job",
    "swarm_launch": "fanout_job",
    "project_runner": "experiment_run_job",
}
JOB_SHAPE_MIGRATION_STATUS_SET = {
    "explicit",
    "legacy_migration_required",
    "legacy_pending_backfill",
    "legacy_backfilled",
    "legacy_incompatible",
}
JOB_SHAPE_SOURCE_SET = {
    "",
    "explicit",
    "compatibility_inferred_from_job_kind",
    "backfilled_from_job_kind_contract",
}


def _runtime_policy_models(key: str, default_csv: str = "") -> List[str]:
    return _runtime_policy.get_runtime_policy_csv(key, default_csv)


def _format_allowed_values(values: Tuple[str, ...]) -> str:
    items = [str(x).strip() for x in values if str(x).strip()]
    if not items:
        return "empty"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", or {items[-1]}"


def _allowed_reasoning_efforts(*, model: str = "", agent_tech: str = "") -> Tuple[str, ...]:
    resolved_agent_tech = str(agent_tech or _infer_agent_tech_from_model(model) or "").strip().lower()
    if resolved_agent_tech in {"openai", "codex"}:
        return _OPENAI_REASONING_EFFORTS
    return _DEFAULT_REASONING_EFFORTS


def _normalize_reasoning_effort(
    value: Any,
    *,
    model: str = "",
    agent_tech: str = "",
    field_name: str = "runtime.reasoning_effort",
) -> str:
    effort = str(value or "").strip().lower()
    if not effort:
        return ""
    allowed = _allowed_reasoning_efforts(model=model, agent_tech=agent_tech)
    if effort not in allowed:
        raise ValueError(f"{field_name} must be {_format_allowed_values(allowed)}")
    return effort


def _scheduler_default_model(*, job_kind: str = "") -> str:
    kind = str(job_kind or "").strip().lower()
    if kind == "build_verify_loop":        return _runtime_policy.get_runtime_policy_text("scheduler.build_verify.default_model", "gpt-5.4")
    return _runtime_policy.get_runtime_policy_text("scheduler.default_model", "deepseek-reasoner")


def _scheduler_default_fallback_models(*, job_kind: str = "") -> List[str]:
    kind = str(job_kind or "").strip().lower()
    if kind == "build_verify_loop":
        return _runtime_policy_models("scheduler.build_verify.default_fallback_models", "deepseek-reasoner")
    return _runtime_policy_models("scheduler.default_fallback_models", "gpt-5.4")

_SCHEDULER_GUARD = threading.Lock()
_SCHEDULER_THREAD: Optional[threading.Thread] = None
_SCHEDULER_STOP: Optional[threading.Event] = None
_SCHEDULER_STATE: Dict[str, Any] = {
    "running": False,
    "interval_s": DEFAULT_SCHEDULER_INTERVAL_S,
    "started_utc": "",
    "last_tick_utc": "",
    "last_result": {},
    "last_error": "",
    "repo_root": "",
}
_WATCHDOG_GUARD = threading.Lock()
_WATCHDOG_THREAD: Optional[threading.Thread] = None
_WATCHDOG_STOP: Optional[threading.Event] = None
_WATCHDOG_STATE: Dict[str, Any] = {
    "running": False,
    "interval_s": 120,  # default watchdog interval: 2 minutes
    "started_utc": "",
    "last_receipt_utc": "",
    "last_receipt": {},
    "last_error": "",
    "repo_root": "",
}
_HEALER_GUARD = threading.Lock()
_HEALER_THREAD: Optional[threading.Thread] = None
_HEALER_STOP: Optional[threading.Event] = None
_HEALER_STATE: Dict[str, Any] = {
    "running": False,
    "interval_s": 300,  # default healer interval: 5 minutes
    "started_utc": "",
    "last_receipt_utc": "",
    "last_receipt": {},
    "last_error": "",
    "repo_root": "",
}

# Job dispatch thread pool and concurrency control
_DISPATCH_GUARD = threading.Lock()
_DISPATCH_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None
_DISPATCH_POOL_MAX_WORKERS = 0
_DISPATCH_ACTIVE_JOBS: Dict[str, concurrent.futures.Future] = {}  # job_id -> Future
_DISPATCH_ACTIVE_JOB_ROLES: Dict[str, str] = {}  # job_id -> normalized role
_DISPATCH_ACTIVE_JOB_META: Dict[str, Dict[str, Any]] = {}  # job_id -> dispatch bookkeeping metadata
_DISPATCH_ORPHAN_GRACE_SECONDS = 60
_DEFAULT_ROLE_CONCURRENCY_CAPS = {"builder": 1, "assurer": 1, "verifier": 1, "generalist": 1, "orchestrator": 1}
_DISPATCH_CONFIG = {
    "global_concurrency_cap": 1,  # max concurrent jobs
    "per_role_cap": dict(_DEFAULT_ROLE_CONCURRENCY_CAPS),
    "resource_class_caps": {},
    "policy_source": "defaults",
    "policy_valid": True,
    "validation_errors": [],
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _root(repo_root: Optional[Path] = None) -> Path:
    base = Path(repo_root or _repo_root()).resolve()
    out = (base / "governance" / "agents" / "runtime" / "scheduled_jobs").resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def jobs_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "inbox").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "archive").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "state").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def receipts_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "receipts").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def reports_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "reports").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def pipeline_backend_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "pipeline_backend").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def prompts_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "prompts").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def job_prompts_dir(job_id: str, repo_root: Optional[Path] = None) -> Path:
    p = (prompts_dir(repo_root) / "jobs" / _safe_name(job_id)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def ledger_file(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "ledger" / "executions.jsonl").resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def locks_dir(repo_root: Optional[Path] = None) -> Path:
    p = (_root(repo_root) / "locks").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[_dt.datetime] = None) -> str:
    when = dt or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact(dt: Optional[_dt.datetime] = None) -> str:
    return (dt or _utc_now()).astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_utc(raw: Any) -> _dt.datetime:
    txt = str(raw or "").strip()
    if not txt:
        raise ValueError("missing UTC timestamp")
    return _dt.datetime.fromisoformat(txt.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _rel_repo_path(path: Path, *, repo_root: Optional[Path] = None) -> str:
    root = Path(repo_root or _repo_root()).resolve()
    return str(path.resolve().relative_to(root)).replace(os.sep, "/")


def _normalize_prompt_preview(text: str, *, limit: int = 180) -> str:
    flat = " ".join(str(text or "").strip().split())
    return flat[:limit]


def _normalize_display_title(text: str) -> str:
    return " ".join(str(text or "").strip().split())[:160]


def _humanize_job_id(job_id: str) -> str:
    raw = str(job_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("recurring_"):
        return raw.replace("_", " ")
    parts = raw.split("_")
    if "final" in parts and "confidence" in parts:
        return "final confidence"
    if "before" in parts and "after" in parts:
        return "before after comparison"
    if "rollup" in parts:
        return "rollup"
    for idx, part in enumerate(parts):
        if part == "rw" and idx + 1 < len(parts):
            current = parts[idx + 1]
            total = ""
            if idx + 2 < len(parts) and parts[idx + 2].isdigit():
                total = parts[idx + 2]
            if current.isdigit() and total:
                return f"rw step {current}/{total}"
            if current.isdigit():
                return f"rw step {current}"
    trimmed: List[str] = []
    for part in parts:
        if part.isdigit() and len(part) == 8 and part.startswith("20"):
            continue
        if len(part) == 8 and all(c in "0123456789abcdef" for c in part.lower()):
            continue
        trimmed.append(part)
    text = " ".join(trimmed).strip()
    return text.replace("_", " ")


def job_display_title(spec: Dict[str, Any]) -> str:
    explicit = _normalize_display_title(str(spec.get("display_title") or ""))
    if explicit:
        return explicit
    return _normalize_display_title(_humanize_job_id(str(spec.get("job_id") or "")))


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-") or "x"


def _file_lock(path: Path) -> threading.RLock:
    return _fsio.file_lock(path)


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    _fsio.write_json_atomic(path, obj)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    _fsio.append_jsonl(path, obj)


def _read_json(path: Path) -> Dict[str, Any]:
    return _fsio.read_json_dict(path)


def _read_jsonl(path: Path, *, limit: int = 0) -> List[Dict[str, Any]]:
    rows = _fsio.read_jsonl(path)
    if int(limit) > 0 and len(rows) > int(limit):
        return rows[-int(limit):]
    return rows


def _state_path(job_id: str, repo_root: Optional[Path] = None) -> Path:
    return (state_dir(repo_root) / f"{_safe_name(job_id)}.json").resolve()


def _job_path(job_id: str, repo_root: Optional[Path] = None) -> Path:
    return (jobs_dir(repo_root) / f"{_safe_name(job_id)}.json").resolve()


def _claim_path(job_id: str, repo_root: Optional[Path] = None) -> Path:
    return (locks_dir(repo_root) / f"{_safe_name(job_id)}.lock.json").resolve()


def _normalize_context_refs(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        txt = str(item or "").strip()
        if txt and txt not in out:
            out.append(txt)
    return out


def _normalize_context_bundle(raw: Any) -> str:
    bundle = str(raw or "").strip()
    if not bundle:
        return ""
    if bundle not in _context_bundles.CONTEXT_BUNDLES:
        allowed = ", ".join(_context_bundles.available_bundle_ids())
        raise ValueError(f"required_context_bundle must be one of: {allowed}")
    return bundle


def _effective_context_refs(spec: Dict[str, Any]) -> List[str]:
    obligation_result = _resolved_job_shape_context_obligations(spec)
    refs = list(obligation_result.get("effective_context_refs") or [])
    forbidden = {str(x or "").strip() for x in list(spec.get("forbidden_context_refs") or []) if str(x or "").strip()}
    if forbidden:
        refs = [ref for ref in refs if ref not in forbidden]
    return refs


def _resolved_job_shape_context_obligations(
    spec: Dict[str, Any],
    *,
    transcript_summary_refs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    shape_fields = _resolve_job_shape_fields(spec)
    attachment_ref = _continuation_context_attachment.context_attachment_ref_for_job_spec(
        spec,
        repo_root=_repo_root(),
    )
    durable_delta_refs = (
        _normalize_context_refs(spec.get("context_refs"))
        + _normalize_context_refs(spec.get("additional_context_refs"))
        + ([attachment_ref] if attachment_ref else [])
    )
    return _job_shape_context_contract.resolve_context_obligations_for_shape(
        str(shape_fields.get("job_shape") or ""),
        durable_delta_refs=durable_delta_refs,
        transcript_summary_refs=transcript_summary_refs,
    )


def _job_shape_context_obligation_projection(
    spec: Dict[str, Any],
    *,
    transcript_summary_refs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    obligations = _resolved_job_shape_context_obligations(
        spec,
        transcript_summary_refs=transcript_summary_refs,
    )
    return {
        "job_shape": str(obligations.get("job_shape") or ""),
        "required_context_bundle": str(obligations.get("required_context_bundle") or ""),
        "additional_context_refs_allowed": bool(obligations.get("additional_context_refs_allowed")),
        "allowed_durable_delta_ref_kinds": list(obligations.get("allowed_durable_delta_ref_kinds") or []),
        "forbidden_context_refs": list(obligations.get("forbidden_context_refs") or []),
        "forbidden_context_ref_prefixes": list(obligations.get("forbidden_context_ref_prefixes") or []),
        "transcript_summary_policy": str(obligations.get("transcript_summary_policy") or ""),
        "reentry_precedence": str(obligations.get("reentry_precedence") or ""),
        "durable_delta_refs": list(obligations.get("durable_delta_refs") or []),
        "effective_context_refs": list(obligations.get("effective_context_refs") or []),
        "ignored_transcript_summary_refs": list(obligations.get("ignored_transcript_summary_refs") or []),
        "omitted_transcript_summary_refs": list(obligations.get("omitted_transcript_summary_refs") or []),
    }


def _prompt_path_from_ref(prompt_ref: str, *, repo_root: Optional[Path] = None) -> Path:
    ref = str(prompt_ref or "").strip()
    if not ref:
        raise ValueError("prompt_ref is required")
    root = Path(repo_root or _repo_root()).resolve()
    path = (root / ref).resolve() if not Path(ref).is_absolute() else Path(ref).resolve()
    prompts_root = prompts_dir(root)
    try:
        path.relative_to(root)
    except Exception as exc:
        raise ValueError("prompt_ref must stay within the repository root") from exc
    try:
        path.relative_to(prompts_root)
    except Exception as exc:
        raise ValueError(f"prompt_ref must live under {prompts_root}") from exc
    return path


def _read_prompt_text_from_ref(prompt_ref: str, *, repo_root: Optional[Path] = None) -> str:
    path = _prompt_path_from_ref(prompt_ref, repo_root=repo_root)
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"prompt file not found: {prompt_ref}")
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _resolved_prompt_text(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> str:
    inline = str(spec.get("prompt") or "").strip()
    if inline:
        return inline
    prompt_ref = str(spec.get("prompt_ref") or "").strip()
    if prompt_ref:
        return _read_prompt_text_from_ref(prompt_ref, repo_root=repo_root)
    if _normalize_job_kind(spec.get("job_kind")) == "swarm_launch":
        return _render_swarm_launch_prompt(_normalize_swarm_launch(spec.get("swarm")))
    if _normalize_job_kind(spec.get("job_kind")) == "project_runner":
        project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
        project_id = str(project_run.get("project_id") or "").strip()
        runner_id = str(project_run.get("runner_id") or "").strip()
        return f"Run project adapter {project_id}/{runner_id}".strip()
    return ""


def _write_prompt_revision(
    *,
    job_id: str,
    prompt_text: str,
    prompt_meta: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    body = str(prompt_text or "").strip()
    if not body:
        raise ValueError("prompt is required")
    sha = _sha256_text(body)
    stamp = _utc_compact().lower()
    filename = f"prompt__{stamp}__{sha[:8]}.md"
    path = (job_prompts_dir(job_id, repo_root) / filename).resolve()
    if not path.exists():
        path.write_text(body + "\n", encoding="utf-8", newline="\n")
    meta: Dict[str, Any] = {}
    raw_meta = prompt_meta if isinstance(prompt_meta, dict) else {}
    for key in ("producer_job_id", "producer_run_id", "producer_role", "note"):
        value = str(raw_meta.get(key) or "").strip()
        if value:
            meta[key] = value
    meta["job_id"] = str(job_id or "").strip()
    meta["prompt_ref"] = _rel_repo_path(path, repo_root=repo_root)
    meta["prompt_sha256"] = sha
    meta["written_utc"] = _utc_iso()
    meta_path = path.with_suffix(".json")
    _write_json_atomic(meta_path, meta)
    return {
        "prompt_ref": meta["prompt_ref"],
        "prompt_sha256": sha,
        "prompt_preview": _normalize_prompt_preview(body),
        "prompt_meta": meta,
    }


def _materialize_prompt_spec(
    spec: Dict[str, Any],
    *,
    existing: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    out = dict(spec or {})
    existing_obj = existing if isinstance(existing, dict) else {}
    job_kind = _normalize_job_kind(out.get("job_kind") or existing_obj.get("job_kind"))
    prompt_meta_input = out.get("prompt_meta") if isinstance(out.get("prompt_meta"), dict) else {}
    inline_prompt = str(out.get("prompt") or "").strip()
    prompt_ref = (
        str(out.get("prompt_ref") or "").strip()
        if "prompt_ref" in out
        else str(existing_obj.get("prompt_ref") or "").strip()
    )
    prompt_sha = (
        str(out.get("prompt_sha256") or "").strip()
        if "prompt_sha256" in out
        else str(existing_obj.get("prompt_sha256") or "").strip()
    )
    prompt_meta = dict(existing_obj.get("prompt_meta") or {})
    prompt_meta.update(prompt_meta_input)
    if inline_prompt:
        inline_sha = _sha256_text(inline_prompt)
        if prompt_ref and prompt_sha == inline_sha:
            resolved = _read_prompt_text_from_ref(prompt_ref, repo_root=repo_root)
            if _sha256_text(resolved) != inline_sha:
                prompt_ref = ""
        if not prompt_ref:
            stored = _write_prompt_revision(
                job_id=str(out.get("job_id") or existing_obj.get("job_id") or ""),
                prompt_text=inline_prompt,
                prompt_meta=prompt_meta if isinstance(prompt_meta, dict) else {},
                repo_root=repo_root,
            )
            prompt_ref = str(stored.get("prompt_ref") or "")
            prompt_sha = str(stored.get("prompt_sha256") or "")
            stored_meta = dict(stored.get("prompt_meta") or {})
            stored_meta.update(prompt_meta_input)
            prompt_meta = stored_meta
        out["prompt_preview"] = _normalize_prompt_preview(inline_prompt)
    elif prompt_ref:
        resolved = _read_prompt_text_from_ref(prompt_ref, repo_root=repo_root)
        resolved_sha = _sha256_text(resolved)
        if prompt_sha and prompt_sha != resolved_sha:
            raise ValueError(f"prompt_sha256 mismatch for {prompt_ref}")
        prompt_sha = resolved_sha
        out["prompt_preview"] = _normalize_prompt_preview(resolved)
    elif job_kind == "swarm_launch":
        derived = _render_swarm_launch_prompt(_normalize_swarm_launch(out.get("swarm") or existing_obj.get("swarm")))
        out["prompt_preview"] = _normalize_prompt_preview(derived)
        prompt_sha = _sha256_text(derived)
        prompt_ref = ""
    elif job_kind == "project_runner":
        out["prompt_preview"] = _normalize_prompt_preview(_resolved_prompt_text(out or existing_obj))
        prompt_sha = ""
        prompt_ref = ""
    else:
        raise ValueError("prompt is required")
    out["prompt_ref"] = prompt_ref
    out["prompt_sha256"] = prompt_sha
    if isinstance(prompt_meta, dict) and prompt_meta:
        out["prompt_meta"] = dict(prompt_meta)
    out.pop("prompt", None)
    return out


def _normalize_depends_on(raw: Any, *, job_id: str) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        dep = str(item or "").strip()
        if not dep:
            continue
        if not JOB_ID_RE.match(dep):
            raise ValueError("depends_on entries must match ^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
        if dep == job_id:
            raise ValueError("depends_on may not include the current job_id")
        if dep not in out:
            out.append(dep)
    return out


def _canonical_job_shape_for_job_kind(job_kind: Any) -> str:
    normalized_job_kind = _normalize_job_kind(job_kind)
    shape_name = JOB_KIND_TO_CANONICAL_JOB_SHAPE.get(normalized_job_kind)
    if not shape_name:
        raise ValueError(f"no canonical job_shape mapping exists for job_kind={normalized_job_kind}")
    return _job_shape_contract.canonical_job_shape_name(shape_name)


def _job_shape_candidate_records(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()

    def _append(value: Any) -> None:
        if not isinstance(value, dict):
            return
        marker = id(value)
        if marker in seen_ids:
            return
        seen_ids.add(marker)
        candidates.append(value)

    _append(record)
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    _append(metadata)
    raw_request = record.get("raw_request") if isinstance(record.get("raw_request"), dict) else {}
    _append(raw_request)
    _append(raw_request.get("metadata") if isinstance(raw_request.get("metadata"), dict) else {})
    work_order = record.get("work_order") if isinstance(record.get("work_order"), dict) else {}
    _append(work_order)
    _append(work_order.get("metadata") if isinstance(work_order.get("metadata"), dict) else {})
    compiled_execution = record.get("compiled_execution") if isinstance(record.get("compiled_execution"), dict) else {}
    _append(compiled_execution)
    compiled_ir = compiled_execution.get("ir") if isinstance(compiled_execution.get("ir"), dict) else {}
    _append(compiled_ir)
    normalized_ir = compiled_ir.get("normalized_ir") if isinstance(compiled_ir.get("normalized_ir"), dict) else {}
    _append(normalized_ir)
    _append(normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), dict) else {})
    spec_snapshot = record.get("spec_snapshot") if isinstance(record.get("spec_snapshot"), dict) else {}
    _append(spec_snapshot)
    execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    _append(execution)
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    _append(job)
    receipt = record.get("receipt") if isinstance(record.get("receipt"), dict) else {}
    _append(receipt)
    return candidates


def _resolve_job_shape_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    job_kind = ""
    raw_shape = ""
    raw_source = ""
    raw_compatibility_mode = False
    raw_migration_status = ""
    raw_migration_reason = ""
    for candidate in _job_shape_candidate_records(record):
        if not job_kind:
            candidate_job_kind = str(candidate.get("job_kind") or "").strip()
            if candidate_job_kind:
                job_kind = candidate_job_kind
        if not raw_shape:
            candidate_shape = str(candidate.get("job_shape") or "").strip()
            if candidate_shape:
                raw_shape = candidate_shape
                raw_source = str(candidate.get("job_shape_source") or "").strip()
                raw_compatibility_mode = bool(candidate.get("job_shape_compatibility_mode"))
        if not raw_migration_status:
            candidate_status = str(candidate.get("job_shape_migration_status") or "").strip()
            if candidate_status:
                raw_migration_status = candidate_status
                raw_migration_reason = str(candidate.get("job_shape_migration_reason") or "").strip()

    expected_shape = _canonical_job_shape_for_job_kind(job_kind) if job_kind else ""
    if raw_migration_status and raw_migration_status not in JOB_SHAPE_MIGRATION_STATUS_SET:
        raise ValueError(
            "job_shape_migration_status must be one of "
            + ", ".join(sorted(JOB_SHAPE_MIGRATION_STATUS_SET))
        )
    if raw_shape:
        try:
            canonical_shape = _job_shape_contract.canonical_job_shape_name(raw_shape)
        except KeyError as exc:
            raise ValueError(
                "job_shape must be one of " + ", ".join(_job_shape_contract.CANONICAL_JOB_SHAPES)
            ) from exc
        if expected_shape and canonical_shape != expected_shape:
            raise ValueError(
                f"job_shape {canonical_shape} does not match expected shape {expected_shape} "
                f"for job_kind={_normalize_job_kind(job_kind)}"
            )
        if raw_compatibility_mode or raw_source == "compatibility_inferred_from_job_kind":
            return {
                "job_shape": canonical_shape,
                "job_shape_source": "compatibility_inferred_from_job_kind",
                "job_shape_compatibility_mode": True,
                "job_shape_migration_status": raw_migration_status or "legacy_pending_backfill",
                "job_shape_migration_reason": raw_migration_reason,
            }
        return {
            "job_shape": canonical_shape,
            "job_shape_source": raw_source or "explicit",
            "job_shape_compatibility_mode": False,
            "job_shape_migration_status": (
                raw_migration_status
                or ("legacy_backfilled" if (raw_source or "").strip() == "backfilled_from_job_kind_contract" else "explicit")
            ),
            "job_shape_migration_reason": raw_migration_reason,
        }
    if expected_shape:
        migration_status = raw_migration_status or "legacy_pending_backfill"
        return {
            "job_shape": expected_shape,
            "job_shape_source": "compatibility_inferred_from_job_kind",
            "job_shape_compatibility_mode": True,
            "job_shape_migration_status": migration_status,
            "job_shape_migration_reason": raw_migration_reason,
        }
    raise ValueError("job_shape could not be resolved from record; missing job_kind and persisted job_shape")


def _job_shape_semantics_projection(
    record: Dict[str, Any],
    *,
    raw_state: str = "",
    active_run: bool = False,
    active_loop_stage: str = "",
) -> Dict[str, Any]:
    try:
        shape_fields = _resolve_job_shape_fields(record)
    except Exception as exc:
        return {
            "recognized": False,
            "job_shape": "",
            "job_shape_source": "",
            "job_shape_compatibility_mode": False,
            "job_shape_migration_status": "",
            "job_shape_migration_reason": "",
            "lifecycle_projection": {
                "job_shape": "",
                "raw_state": str(raw_state or ""),
                "canonical_state": "",
                "summary_bucket": "unknown",
                "terminal_summary_class": "",
                "recovery_class": "",
                "interruption_state": "",
                "recognized": False,
                "active_run": bool(active_run),
            },
            "operator_summary": "job shape missing or invalid; scheduler summaries must not guess from prose",
            "progress_semantics": "unknown until durable job_shape is present",
            "recovery_semantics": "unknown until durable job_shape is present",
            "child_launch_semantics": "unknown until durable job_shape is present",
            "integrator_owner": "",
            "legal_child_shapes": [],
            "future_child_shapes": [],
            "error": str(exc),
        }

    shape_name = str(shape_fields.get("job_shape") or "")
    lifecycle_projection = dict(
        _job_shape_lifecycle_contract.lifecycle_projection_for_shape_record(
            shape_name,
            str(raw_state or ""),
            active_run=bool(active_run),
            active_loop_stage=str(active_loop_stage or ""),
        )
    )
    legal_children = list(_job_shape_relationship_contract.legal_child_shapes_for_parent(shape_name))
    future_children = [
        child_shape
        for child_shape in _job_shape_relationship_contract.legal_child_shapes_for_parent(shape_name, include_future=True)
        if child_shape not in legal_children
    ]
    if shape_name == "verification_loop_job":
        progress_semantics = "bounded builder/verifier iteration state"
    elif shape_name == "fanout_job":
        progress_semantics = "parent launch and child reintegration state"
    elif shape_name == "experiment_run_job":
        progress_semantics = "experiment launch/checkpoint/result state"
    else:
        progress_semantics = "named future-facing shape; no current runtime progress semantics"

    recovery_class = str(lifecycle_projection.get("recovery_class") or "")
    recovery_semantics = str(_job_shape_lifecycle_contract.RECOVERY_CLASS_DEFINITIONS.get(recovery_class) or "")
    if legal_children:
        child_launch_semantics = "v1 legal child shapes: " + ", ".join(legal_children)
    elif future_children:
        child_launch_semantics = "no v1 child launches; future-only child shapes: " + ", ".join(future_children)
    else:
        child_launch_semantics = "no child launches in v1"
    integrator_owner = str(_job_shape_relationship_contract.load_job_shape_relationship_contract()["shapes"][shape_name]["default_integrator_owner"] or "")
    operator_summary = (
        f"{shape_name}: state={str(lifecycle_projection.get('canonical_state') or 'unknown')} | "
        f"progress={progress_semantics} | recovery={recovery_class or 'unknown'}"
    )
    if shape_fields.get("job_shape_compatibility_mode"):
        operator_summary += " | compatibility_mode=true"
    return {
        "recognized": bool(lifecycle_projection.get("recognized")),
        "job_shape": shape_name,
        "job_shape_source": str(shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(shape_fields.get("job_shape_compatibility_mode")),
        "job_shape_migration_status": str(shape_fields.get("job_shape_migration_status") or ""),
        "job_shape_migration_reason": str(shape_fields.get("job_shape_migration_reason") or ""),
        "lifecycle_projection": lifecycle_projection,
        "operator_summary": operator_summary,
        "progress_semantics": progress_semantics,
        "recovery_semantics": recovery_semantics,
        "child_launch_semantics": child_launch_semantics,
        "integrator_owner": integrator_owner,
        "legal_child_shapes": legal_children,
        "future_child_shapes": future_children,
        "error": "",
    }


def _normalize_binding(raw: Any) -> Dict[str, str]:
    obj = raw if isinstance(raw, dict) else {}
    bt = str(obj.get("type") or "task_id").strip().lower()
    bid = str(obj.get("id") or "").strip()
    if bt not in {"task_id", "ledger_uid", "none"}:
        raise ValueError("binding.type must be task_id, ledger_uid, or none")
    if bt == "none":
        return {"type": "none", "id": ""}
    if bt == "task_id":
        if not TASK_ID_RE.match(bid):
            raise ValueError("binding.id must match ^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$ for task_id")
    elif not bid:
        raise ValueError("binding.id is required for ledger_uid")
    return {"type": bt, "id": bid}


def _normalize_schedule(raw: Any) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    mode = str(obj.get("mode") or "").strip().lower()
    if mode not in {"once", "daily", "interval"}:
        raise ValueError("schedule.mode must be once, daily, or interval")
    if mode == "once":
        run_at = _utc_iso(_parse_utc(obj.get("run_at")))
        timezone = str(obj.get("timezone") or "UTC").strip() or "UTC"
        return {"mode": "once", "run_at": run_at, "timezone": timezone}
    if mode == "interval":
        try:
            every_minutes = int(obj.get("every_minutes") or 0)
        except Exception as exc:
            raise ValueError("schedule.every_minutes must be an integer") from exc
        if every_minutes < 5 or every_minutes > 1440:
            raise ValueError("schedule.every_minutes must be between 5 and 1440")
        start_at_raw = str(obj.get("start_at_utc") or "").strip()
        start_at_utc = _utc_iso(_parse_utc(start_at_raw)) if start_at_raw else ""
        return {
            "mode": "interval",
            "every_minutes": every_minutes,
            "start_at_utc": start_at_utc,
            "timezone": "UTC",
        }
    tod = str(obj.get("time_of_day") or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", tod):
        raise ValueError("schedule.time_of_day must be HH:MM for daily jobs")
    hh = int(tod[:2])
    mm = int(tod[3:5])
    if hh > 23 or mm > 59:
        raise ValueError("schedule.time_of_day must be a valid 24h time")
    tz_name = str(obj.get("timezone") or "").strip() or "UTC"
    ZoneInfo(tz_name)
    return {"mode": "daily", "time_of_day": tod, "timezone": tz_name}


def _normalize_policy(raw: Any, *, job_kind: Optional[str] = None) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    post_success_commit_tag_raw = obj.get("post_success_commit_tag")
    post_success_commit_tag = bool(post_success_commit_tag_raw) if post_success_commit_tag_raw is not None else False
    try:
        max_runtime = int(obj.get("max_runtime_seconds") or 1800)
    except Exception:
        max_runtime = 1800
    try:
        commit_retries = int(obj.get("post_success_commit_tag_retries") or 0)
    except Exception:
        commit_retries = 0
    try:
        commit_backoff_seconds = int(obj.get("post_success_commit_tag_backoff_seconds") or 60)
    except Exception:
        commit_backoff_seconds = 60
    try:
        turn_retries = int(obj.get("agent_turn_retries") or 0)
    except Exception:
        turn_retries = 0
    try:
        turn_backoff_seconds = int(obj.get("agent_turn_retry_backoff_seconds") or 5)
    except Exception:
        turn_backoff_seconds = 5
    try:
        job_transient_retries = int(obj.get("job_transient_retries") or 0)
    except Exception:
        job_transient_retries = 0
    try:
        job_transient_backoff_seconds = int(obj.get("job_transient_retry_backoff_seconds") or 30)
    except Exception:
        job_transient_backoff_seconds = 30
    try:
        scheduler_retry_backoff_seconds = int(obj.get("scheduler_retry_backoff_seconds") or 60)
    except Exception:
        scheduler_retry_backoff_seconds = 60
    explicit_repo_mode = "repo_change_mode" in obj
    repo_change_mode = str(obj.get("repo_change_mode") or "none").strip().lower()
    if repo_change_mode not in {"none", "strict"}:
        raise ValueError("policy.repo_change_mode must be none or strict")
    require_validation_evidence_raw = obj.get("require_validation_evidence")
    require_validation_evidence = (
        bool(require_validation_evidence_raw) if require_validation_evidence_raw is not None else repo_change_mode == "strict"
    )
    require_clean_worktree_raw = obj.get("require_clean_worktree")
    require_clean_worktree = bool(require_clean_worktree_raw) if require_clean_worktree_raw is not None else repo_change_mode == "strict"
    require_commit_journal_raw = obj.get("require_commit_journal")
    require_commit_journal = bool(require_commit_journal_raw) if require_commit_journal_raw is not None else repo_change_mode == "strict"
    require_budget_admission_raw = obj.get("require_budget_admission")
    require_budget_admission = bool(require_budget_admission_raw) if require_budget_admission_raw is not None else False
    delivery_policy = str(obj.get("delivery_policy") or STRICT_DELIVERY_POLICY).strip().lower() or STRICT_DELIVERY_POLICY
    if delivery_policy not in DELIVERY_POLICY_SET:
        raise ValueError("policy.delivery_policy must be strict or delivery_guaranteed")
    if str(job_kind or "").strip().lower() == "build_verify_loop":
        # Build/verify loops are always write-capable and therefore keep strict
        # proof requirements. Do not, however, force a clean worktree by
        # default; chained scheduled implementation jobs necessarily make the
        # tree dirty between runs.
        repo_change_mode = "strict"
        require_validation_evidence = True
        require_clean_worktree = False
        require_commit_journal = True
        if post_success_commit_tag_raw is None:
            post_success_commit_tag = True
    return {
        "allow_overlap": bool(obj.get("allow_overlap")),
        "max_runtime_seconds": max(30, min(86400, max_runtime)),
        "post_success_commit_tag": post_success_commit_tag,
        "post_success_commit_tag_retries": max(0, min(10, commit_retries)),
        "post_success_commit_tag_backoff_seconds": max(1, min(3600, commit_backoff_seconds)),
        "agent_turn_retries": max(0, min(8, turn_retries)),
        "agent_turn_retry_backoff_seconds": max(1, min(300, turn_backoff_seconds)),
        "job_transient_retries": max(0, min(6, job_transient_retries)),
        "job_transient_retry_backoff_seconds": max(1, min(3600, job_transient_backoff_seconds)),
        "scheduler_retry_backoff_seconds": max(30, min(86400, scheduler_retry_backoff_seconds)),
        "repo_change_mode": repo_change_mode,
        "require_validation_evidence": require_validation_evidence,
        "require_clean_worktree": require_clean_worktree,
        "require_commit_journal": require_commit_journal,
        "require_budget_admission": require_budget_admission,
        "delivery_policy": delivery_policy,
    }


def _normalize_runtime(raw: Any, *, job_kind: Optional[str] = None) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    kind = str(job_kind or "").strip().lower()
    explicit_advisory_agent_tech = str(obj.get("advisory_agent_tech") or obj.get("requested_agent_tech") or "").strip().lower()
    explicit_advisory_model = str(obj.get("advisory_model") or obj.get("requested_model") or "").strip()
    executor_agent_tech = str(obj.get("executor_agent_tech") or "").strip().lower()
    if executor_agent_tech and executor_agent_tech not in {"codex", "pipeline"}:
        raise ValueError("runtime.executor_agent_tech must be codex, pipeline, or empty")
    if kind in JOB_KIND_SET and not executor_agent_tech:
        executor_agent_tech = "codex"
    pipeline_executor_mode = str(obj.get("pipeline_executor_mode") or "").strip().lower()
    if pipeline_executor_mode and pipeline_executor_mode not in PIPELINE_EXECUTOR_MODE_SET:
        raise ValueError("runtime.pipeline_executor_mode must be stage8_backend, full_auto, or empty")
    if executor_agent_tech != "pipeline" and pipeline_executor_mode:
        raise ValueError("runtime.pipeline_executor_mode requires runtime.executor_agent_tech=pipeline")
    if executor_agent_tech == "pipeline" and not pipeline_executor_mode:
        pipeline_executor_mode = PIPELINE_FULL_AUTO_MODE if kind == "build_verify_loop" else PIPELINE_STAGE8_BACKEND_MODE
    if kind == "build_verify_loop" and executor_agent_tech == "pipeline" and pipeline_executor_mode != PIPELINE_FULL_AUTO_MODE:
        raise ValueError("build_verify_loop pipeline executor requires runtime.pipeline_executor_mode=full_auto")
    advisory_agent_tech = explicit_advisory_agent_tech
    legacy_agent_tech = str(obj.get("agent_tech") or "").strip().lower()
    if advisory_agent_tech and legacy_agent_tech and advisory_agent_tech != legacy_agent_tech:
        raise ValueError("runtime.advisory_agent_tech conflicts with runtime.agent_tech")
    agent_tech = advisory_agent_tech or legacy_agent_tech
    if agent_tech and agent_tech not in AGENT_TECH_SET:
        raise ValueError("runtime.advisory_agent_tech must be codex, openai, claude, gemini, xai, ollama, deepseek, pipeline, or empty")
    default_model = ""
    if kind == "build_verify_loop" and agent_tech in {"", "codex"}:
        default_model = _scheduler_default_model(job_kind=kind)
    elif agent_tech == "gemini":
        default_model = "gemini-2.5-pro"
    elif agent_tech == "claude":
        default_model = "claude-haiku-4-5"
    elif agent_tech == "xai":
        default_model = "grok-code-fast-1"
    elif agent_tech == "ollama":
        default_model = "qwen2.5:3b"
    elif agent_tech == "deepseek":
        default_model = "deepseek-chat"
    elif agent_tech == "pipeline":
        default_model = "pipeline"
    advisory_model = explicit_advisory_model
    legacy_model = str(obj.get("model") or "").strip()
    if advisory_model and legacy_model and advisory_model != legacy_model:
        raise ValueError("runtime.advisory_model conflicts with runtime.model")
    model = advisory_model or legacy_model or default_model
    agent_tech = _resolved_agent_tech_for_model(agent_tech, model)
    requested_provider = str(obj.get("provider") or "").strip().lower()
    if requested_provider and requested_provider not in {"openai", "claude", "gemini", "xai", "ollama", "deepseek"}:
        raise ValueError("runtime.provider must be openai, claude, gemini, xai, ollama, deepseek, or empty")
    provider = _resolved_provider_for_model(requested_provider, model=model, agent_tech=agent_tech, default_provider="")
    fallback_model = str(obj.get("fallback_model") or "").strip()
    raw_fallback_models = obj.get("fallback_models")
    fallback_models: List[str] = []
    if isinstance(raw_fallback_models, list):
        for item in raw_fallback_models:
            txt = str(item or "").strip()
            if txt and txt not in fallback_models:
                fallback_models.append(txt)
    elif isinstance(raw_fallback_models, str):
        for part in str(raw_fallback_models).split(","):
            txt = str(part or "").strip()
            if txt and txt not in fallback_models:
                fallback_models.append(txt)
    if fallback_model and fallback_model not in fallback_models:
        fallback_models.append(fallback_model)
    if kind == "build_verify_loop" and agent_tech in {"", "codex"}:
        for candidate in _scheduler_default_fallback_models(job_kind=kind):
            if candidate not in fallback_models and model != candidate:
                fallback_models.append(candidate)
    if agent_tech == "claude" and "claude-sonnet-4-6" not in fallback_models and model != "claude-sonnet-4-6":
        fallback_models.append("claude-sonnet-4-6")
    if agent_tech == "gemini" and "gemini-2.5-pro" not in fallback_models and model != "gemini-2.5-pro":
        fallback_models.append("gemini-2.5-pro")
    if agent_tech == "xai" and "grok-code-fast-1" not in fallback_models and model != "grok-code-fast-1":
        fallback_models.append("grok-code-fast-1")
    if agent_tech == "ollama" and "qwen2.5:3b" not in fallback_models and model != "qwen2.5:3b":
        fallback_models.append("qwen2.5:3b")
    explicit_model = bool(str(obj.get("model") or "").strip())
    explicit_fallbacks = bool(fallback_model or fallback_models)
    if agent_tech == "deepseek" and not (explicit_model and explicit_fallbacks):
        for candidate in ("deepseek-chat", "deepseek-reasoner"):
            if candidate not in fallback_models and model != candidate:
                fallback_models.append(candidate)
    if agent_tech == "pipeline" and "pipeline" not in fallback_models and model != "pipeline":
        fallback_models.append("pipeline")
    effort = _normalize_reasoning_effort(
        obj.get("reasoning_effort"),
        model=model,
        agent_tech=agent_tech,
        field_name="runtime.reasoning_effort",
    )
    execution_mode = str(obj.get("execution_mode") or "").strip().lower()
    if execution_mode and execution_mode not in {"advisory_then_controller", "advisory_only", "controller_only"}:
        raise ValueError("runtime.execution_mode must be advisory_then_controller, advisory_only, controller_only, or empty")
    compiled_execution_mode = str(obj.get("compiled_execution_mode") or "").strip().lower()
    if compiled_execution_mode and compiled_execution_mode not in {
        _compiled_execution_contract.ROLLOUT_LEGACY,
        _compiled_execution_contract.ROLLOUT_SHADOW_COMPILE,
        _compiled_execution_contract.ROLLOUT_SOFT_GATE,
        _compiled_execution_contract.ROLLOUT_HARD_GATE,
    }:
        raise ValueError("runtime.compiled_execution_mode must be legacy, shadow_compile, soft_gate, hard_gate, or empty")
    if execution_mode == "controller_only" and advisory_agent_tech and advisory_agent_tech != "codex":
        raise ValueError("runtime.execution_mode=controller_only cannot also specify advisory_agent_tech")
    if execution_mode == "advisory_only" and not agent_tech:
        raise ValueError("runtime.execution_mode=advisory_only requires runtime.advisory_agent_tech")
    output_advisory_agent_tech = advisory_agent_tech
    output_advisory_model = advisory_model
    if execution_mode == "controller_only" and not advisory_agent_tech:
        output_advisory_agent_tech = ""
        output_advisory_model = ""
    elif execution_mode in {"advisory_then_controller", "advisory_only"}:
        output_advisory_agent_tech = agent_tech
        output_advisory_model = model
    fallback_agent_tech = str(obj.get("fallback_agent_tech") or "").strip().lower()
    if fallback_agent_tech and fallback_agent_tech not in AGENT_TECH_SET:
        raise ValueError("runtime.fallback_agent_tech must be codex, openai, claude, gemini, xai, ollama, deepseek, pipeline, or empty")
    raw_fallback_agent_techs = obj.get("fallback_agent_techs")
    fallback_agent_techs: List[str] = []
    if isinstance(raw_fallback_agent_techs, list):
        for item in raw_fallback_agent_techs:
            txt = str(item or "").strip().lower()
            if not txt:
                continue
            if txt not in AGENT_TECH_SET:
                raise ValueError("runtime.fallback_agent_techs entries must be codex, openai, claude, gemini, xai, ollama, deepseek, or pipeline")
            if txt not in fallback_agent_techs:
                fallback_agent_techs.append(txt)
    elif isinstance(raw_fallback_agent_techs, str):
        for part in str(raw_fallback_agent_techs).split(","):
            txt = str(part or "").strip().lower()
            if not txt:
                continue
            if txt not in AGENT_TECH_SET:
                raise ValueError("runtime.fallback_agent_techs entries must be codex, openai, claude, gemini, xai, ollama, deepseek, or pipeline")
            if txt not in fallback_agent_techs:
                fallback_agent_techs.append(txt)
    if fallback_agent_tech and fallback_agent_tech not in fallback_agent_techs:
        fallback_agent_techs.append(fallback_agent_tech)
    if kind == "build_verify_loop" and agent_tech == "gemini" and not fallback_agent_techs:
        fallback_agent_techs = ["claude", "codex"]
    if agent_tech and agent_tech in fallback_agent_techs:
        fallback_agent_techs = [x for x in fallback_agent_techs if x != agent_tech]
    raw_tech_model_sequence = obj.get("tech_model_sequence")
    tech_model_sequence: List[Dict[str, str]] = []
    if isinstance(raw_tech_model_sequence, list):
        for item in raw_tech_model_sequence:
            if not isinstance(item, dict):
                continue
            seq_tech = str(item.get("agent_tech") or "").strip().lower()
            seq_model = str(item.get("model") or "").strip()
            if seq_tech and seq_tech not in AGENT_TECH_SET:
                raise ValueError("runtime.tech_model_sequence agent_tech must be codex, openai, claude, gemini, xai, ollama, deepseek, or pipeline")
            if not seq_tech:
                continue
            tech_model_sequence.append({"agent_tech": seq_tech, "model": seq_model})
    return {
        "executor_agent_tech": executor_agent_tech,
        "pipeline_executor_mode": pipeline_executor_mode,
        "agent_tech": agent_tech,
        "advisory_agent_tech": output_advisory_agent_tech,
        "fallback_agent_tech": fallback_agent_tech,
        "fallback_agent_techs": fallback_agent_techs,
        "provider": provider,
        "model": model,
        "advisory_model": output_advisory_model,
        "fallback_model": fallback_model,
        "fallback_models": fallback_models,
        "fallback_on_quota": bool(obj.get("fallback_on_quota", True)),
        "reasoning_effort": effort,
        "execution_mode": execution_mode,
        "compiled_execution_mode": compiled_execution_mode,
        "tech_model_sequence": tech_model_sequence,
    }


def _normalize_loop_policy(raw: Any) -> str:
    policy = str(raw or DEFAULT_BUILD_VERIFY_LOOP_POLICY).strip().lower() or DEFAULT_BUILD_VERIFY_LOOP_POLICY
    policy = LOOP_POLICY_ALIASES.get(policy, policy)
    if policy not in LOOP_POLICY_SET:
        raise ValueError(
            "loop.loop_policy must be default_conditional_repair_loop "
            f"({BUILD_VERIFY_REPAIR_OPERATOR_LABEL}, {ONE_POINT_FIVE_ITERATIONS_OPERATOR_LABEL}) "
            "or full_build_verify_loop"
        )
    return policy


def _loop_policy_operator_label(raw: Any) -> str:
    policy = _normalize_loop_policy(raw)
    if policy == DEFAULT_BUILD_VERIFY_LOOP_POLICY:
        return f"{BUILD_VERIFY_REPAIR_OPERATOR_LABEL} ({ONE_POINT_FIVE_ITERATIONS_OPERATOR_LABEL})"
    return policy


def _normalize_job_kind(raw: Any) -> str:
    kind = str(raw or "single_turn").strip().lower() or "single_turn"
    if kind not in JOB_KIND_SET:
        raise ValueError("job_kind must be single_turn, build_verify_loop, swarm_launch, or project_runner")
    return kind


def _is_success_status(raw: Any) -> bool:
    return str(raw or "").strip().lower() in SUCCESS_TERMINAL_STATUSES


def _normalize_expected_output(raw: Any) -> Dict[str, str]:
    obj = raw if isinstance(raw, dict) else {}
    kind = str(obj.get("kind") or "report").strip() or "report"
    fmt = str(obj.get("format") or "markdown").strip() or "markdown"
    return {"kind": kind, "format": fmt}


def _normalize_relpath_list(raw: Any, *, field_name: str) -> List[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be an array when provided")
    out: List[str] = []
    for item in raw:
        path = str(item or "").strip().strip("/")
        if not path:
            continue
        if path.startswith(".."):
            raise ValueError(f"{field_name} entries must stay within the repo root")
        if path not in out:
            out.append(path)
    return out


def _normalize_string_list(raw: Any, *, field_name: str) -> List[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be an array when provided")
    out: List[str] = []
    for item in raw:
        txt = str(item or "").strip()
        if txt and txt not in out:
            out.append(txt)
    return out
