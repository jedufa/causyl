#!/usr/bin/env python3
"""Interactive authoring and execution CLI for scheduled agent jobs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import job_shape_context_contract
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from tools.bin.scheduler_tty_ui import prompt_menu, prompt_yes_no

ROLE_CHOICES = tuple(sorted(sched.ROLE_SET))
BINDING_CHOICES = ("task_id", "ledger_uid")
MODE_CHOICES = ("once", "daily", "interval")
JOB_KIND_CHOICES = tuple(sorted(sched.JOB_KIND_SET))
EXECUTOR_CHOICES = ("codex", "pipeline")
EXECUTION_MODE_CHOICES = ("advisory_then_controller", "controller_only", "advisory_only")
KNOWN_MODEL_CANDIDATES = (
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gemini-2.5-pro",
    "grok-code-fast-1",
    "deepseek-chat",
    "deepseek-reasoner",
    "qwen2.5:3b",
    "pipeline",
)


class UserAbort(Exception):
    """Raised when the interactive wizard is canceled by the operator."""


def _read_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise UserAbort("input stream closed") from exc
    except KeyboardInterrupt as exc:
        raise UserAbort("canceled by user") from exc


def _require_tty() -> None:
    if not sys.stdin.isatty():
        raise UserAbort("interactive commands require a TTY")


def _prompt(
    label: str,
    *,
    default: str = "",
    required: bool = False,
    validator: Callable[[str], str] | None = None,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = _read_input(f"{label}{suffix}: ").strip()
        if raw:
            try:
                return validator(raw) if validator else raw
            except ValueError as exc:
                print(f"Invalid value: {exc}", file=sys.stderr)
                continue
        if default:
            try:
                return validator(default) if validator else default
            except ValueError as exc:
                print(f"Default is invalid: {exc}", file=sys.stderr)
                continue
        if not required:
            return ""
        print("Value is required.", file=sys.stderr)


def _prompt_bool(label: str, *, default: bool = True) -> bool:
    return prompt_yes_no(label, default=bool(default), fallback_reader=_read_input)


def _prompt_menu(label: str, *, choices: tuple[str, ...], default: str) -> str:
    items = list(choices)
    return prompt_menu(
        label,
        choices=items,
        default_index=items.index(default) if default in items else 0,
        fallback_reader=_read_input,
    )


def _prompt_multiline(label: str, *, default: str = "") -> str:
    print(f"{label} (finish with a single '.' line)")
    if default:
        print("Current value:")
        print(default)
    lines: List[str] = []
    while True:
        raw = _read_input()
        if raw.strip() == ".":
            break
        lines.append(raw)
    text = "\n".join(lines).strip()
    return text or default


def _split_csv(raw: str) -> List[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _trim(text: str, limit: int = 96) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 3)].rstrip() + "..."


def _backend(args: argparse.Namespace) -> str:
    return sched_backend.selected_backend(getattr(args, "backend", "") or None)


def _v2_watch_rows() -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    bundle = dict(sched_backend.v2_surface_bundle())
    surfaces = bundle.get("surfaces") if isinstance(bundle.get("surfaces"), dict) else {}
    rows = [dict(row) for row in list(surfaces.get("watch") or []) if isinstance(row, dict)]
    return bundle, rows


def _print_v2_rows(rows: List[Dict[str, Any]], *, limit: int = 100) -> None:
    for row in rows[: max(1, int(limit))]:
        print(
            f"{str(row.get('child_uid') or '-'):24} "
            f"decision={str(row.get('decision') or '-'):8} "
            f"kind={str(row.get('node_kind') or '-'):14} "
            f"gate={str(row.get('gate') or '-'):10} "
            f"reason={str(row.get('reason') or '-')}"
        )
        print(f"  title={_trim(str(row.get('title') or ''))}")


def _parse_runtime_policy_models() -> List[str]:
    path = Path(__file__).resolve().parents[2] / "config" / "runtime_policy.properties"
    models: List[str] = []

    def _add(entry: str) -> None:
        value = str(entry or "").strip()
        if value and value not in models:
            models.append(value)

    if not path.exists():
        return models
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not value or "model" not in key:
            continue
        for candidate in value.split(","):
            _add(candidate)
    return models


def _available_model_choices(current: str = "") -> List[str]:
    models: List[str] = []

    def _add(entry: str) -> None:
        value = str(entry or "").strip()
        if value and value not in models:
            models.append(value)

    _add(current)
    for entry in _parse_runtime_policy_models():
        _add(entry)
    for entry in KNOWN_MODEL_CANDIDATES:
        _add(entry)
    return models


def _inline_choice_hint(choices: List[str], *, prefix: str = "available") -> str:
    if not choices:
        return ""
    return f"{prefix}: {', '.join(choices)}"


def _default_pipeline_executor_mode(*, executor_agent_tech: str, job_kind: str, current: str = "") -> str:
    current_value = str(current or "").strip().lower()
    if current_value in {"stage8_backend", "full_auto"}:
        return current_value
    if str(executor_agent_tech or "").strip().lower() != "pipeline":
        return ""
    return "full_auto" if str(job_kind or "").strip().lower() == "build_verify_loop" else "stage8_backend"


def _warn_if_pipeline_executor(*, executor_agent_tech: str, job_kind: str, pipeline_executor_mode: str = "") -> None:
    if str(executor_agent_tech or "").strip().lower() != "pipeline":
        return
    mode = str(pipeline_executor_mode or _default_pipeline_executor_mode(executor_agent_tech=executor_agent_tech, job_kind=job_kind)).strip().lower()
    print()
    print("Warning: pipeline executor does not run tools through the scheduler's native controller loop.")
    if str(job_kind or "").strip().lower() == "build_verify_loop" and mode == "full_auto":
        print("Note: build_verify_loop will use pipeline full-auto as the execution owner for stages 1-16, verification, retry, and finalization.")
    elif str(job_kind or "").strip().lower() == "build_verify_loop":
        print("Warning: verification/proof quality will be limited for build_verify_loop jobs.")
    else:
        print("Note: single_turn pipeline executor uses the bounded stage-8 backend path.")
    print()

def _default_execution_mode(*, executor_agent_tech: str, model: str, current: str = "") -> str:
    current_value = str(current or "").strip().lower()
    if current_value in EXECUTION_MODE_CHOICES:
        return current_value
    executor = str(executor_agent_tech or "").strip().lower()
    if executor == "pipeline":
        return "controller_only"
    inferred = str(sched._infer_agent_tech_from_model(str(model or "").strip()) or "").strip().lower()
    if executor == "codex" and inferred and inferred != "codex":
        return "advisory_then_controller"
    return "controller_only"


def _warn_if_execution_mode(*, execution_mode: str, executor_agent_tech: str) -> None:
    mode = str(execution_mode or "").strip().lower()
    if mode == "advisory_then_controller":
        print()
        print("Note: advisory_then_controller makes two model calls per logical stage.")
        print("Use controller_only when you want the cheaper single-call comparison.")
        print()
    elif mode == "advisory_only" and str(executor_agent_tech or "").strip().lower() == "codex":
        print()
        print("Warning: advisory_only skips the controller layer entirely.")
        print("Use it only when you explicitly want raw advisory output without controller normalization.")
        print()


def _validate_choice(raw: str, *, choices: tuple[str, ...], field: str) -> str:
    value = str(raw or "").strip().lower()
    if value not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(choices)}")
    return value


def _validate_int(raw: str, *, field: str, minimum: int, maximum: int) -> str:
    try:
        value = int(str(raw or "").strip())
    except Exception as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return str(value)


def _validate_task_id(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not sched.TASK_ID_RE.match(value):
        raise ValueError("task_id must be exactly 3 lowercase letters or digits")
    return value


def _validate_nonempty(raw: str, *, field: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _validate_timezone(raw: str) -> str:
    value = _validate_nonempty(raw, field="timezone")
    try:
        sched.ZoneInfo(value)
    except Exception as exc:
        raise ValueError(f"unknown timezone: {value}") from exc
    return value


def _validate_once_utc(raw: str) -> str:
    return sched._utc_iso(sched._parse_utc(raw))


def _validate_time_of_day(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("time_of_day is required")
    sched._normalize_schedule({"mode": "daily", "time_of_day": value, "timezone": "UTC"})
    return value


def _validate_every_minutes(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("every_minutes is required")
    sched._normalize_schedule({"mode": "interval", "every_minutes": value})
    return value


def _prompt_context_refs(default: List[str]) -> List[str]:
    raw = _prompt(
        "context refs (comma-separated repo paths, optional)",
        default=",".join(default),
        required=False,
    )
    refs = _split_csv(raw)
    missing: List[str] = []
    repo_root = sched._repo_root()
    for ref in refs:
        path = (repo_root / ref).resolve() if not Path(ref).is_absolute() else Path(ref).resolve()
        if not path.exists():
            missing.append(ref)
    if missing:
        print("Warning: these context refs do not exist right now:", file=sys.stderr)
        for ref in missing:
            print(f"  - {ref}", file=sys.stderr)
        if not _prompt_bool("Keep them anyway", default=False):
            return _prompt_context_refs(default)
    return refs


def _print_spec_summary(spec: Dict[str, Any]) -> None:
    print()
    print("Planned scheduled job:")
    print(json.dumps(sched.job_summary(spec), indent=2, ensure_ascii=False))
    print()


def _interactive_spec(existing: Dict[str, Any] | None = None, *, editing: bool = False) -> Dict[str, Any]:
    cur = dict(existing or {})
    schedule = cur.get("schedule") if isinstance(cur.get("schedule"), dict) else {}
    binding = cur.get("binding") if isinstance(cur.get("binding"), dict) else {}
    expected = cur.get("expected_output") if isinstance(cur.get("expected_output"), dict) else {}
    policy = cur.get("policy") if isinstance(cur.get("policy"), dict) else {}
    runtime = cur.get("runtime") if isinstance(cur.get("runtime"), dict) else {}
    loop = cur.get("loop") if isinstance(cur.get("loop"), dict) else {}
    swarm = cur.get("swarm") if isinstance(cur.get("swarm"), dict) else {}

    if editing:
        job_id = str(cur.get("job_id") or "").strip()
        print(f"Editing job_id: {job_id}")
    else:
        while True:
            job_id = _prompt("job_id", default=str(cur.get("job_id") or ""), required=True)
            if not sched.JOB_ID_RE.match(job_id):
                print("Invalid value: job_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$", file=sys.stderr)
                continue
            path = sched.jobs_dir() / f"{sched._safe_name(job_id)}.json"
            if path.exists():
                print(f"Job already exists: {job_id}. Use `edit` to modify it.", file=sys.stderr)
                continue
            break
    enabled = _prompt_bool("enabled", default=bool(cur.get("enabled", True)))
    job_kind = _prompt_menu("Job kind", choices=JOB_KIND_CHOICES, default=str(cur.get("job_kind") or "single_turn"))
    role = ""
    if job_kind == "single_turn":
        role = _prompt_menu("Role", choices=ROLE_CHOICES, default=str(cur.get("role") or "generalist"))
    binding_type = _prompt_menu("Binding type", choices=BINDING_CHOICES, default=str(binding.get("type") or "task_id"))
    binding_validator = _validate_task_id if binding_type == "task_id" else (lambda raw: _validate_nonempty(raw, field="binding id"))
    binding_id = _prompt("binding id", default=str(binding.get("id") or ""), required=True, validator=binding_validator)
    depends_on = _split_csv(
        _prompt(
            "depends_on job ids (comma-separated, optional)",
            default=",".join(list(cur.get("depends_on") or [])),
            required=False,
        )
    )
    mode = _prompt_menu("Schedule mode", choices=MODE_CHOICES, default=str(schedule.get("mode") or "once"))
    timezone = _prompt("timezone", default=str(schedule.get("timezone") or "UTC"), required=True, validator=_validate_timezone)
    run_at = str(schedule.get("run_at") or "")
    time_of_day = str(schedule.get("time_of_day") or "03:30")
    every_minutes = str(schedule.get("every_minutes") or 30)
    start_at_utc = str(schedule.get("start_at_utc") or "")
    if mode == "once":
        run_at = _prompt("run_at UTC (YYYY-MM-DDTHH:MM:SSZ)", default=run_at, required=True, validator=_validate_once_utc)
    elif mode == "daily":
        time_of_day = _prompt("time_of_day local (HH:MM)", default=time_of_day, required=True, validator=_validate_time_of_day)
    else:
        every_minutes = _prompt(
            "every_minutes (5..1440)",
            default=every_minutes,
            required=True,
            validator=_validate_every_minutes,
        )
        start_at_utc = _prompt(
            "start_at_utc (optional, YYYY-MM-DDTHH:MM:SSZ; empty=created_utc)",
            default=start_at_utc,
            required=False,
            validator=lambda raw: _validate_once_utc(raw) if str(raw or "").strip() else "",
        )
    prompt = _prompt_multiline("prompt", default=str(cur.get("prompt") or ""))
    while job_kind != "swarm_launch" and not prompt.strip():
        print("Prompt is required.", file=sys.stderr)
        prompt = _prompt_multiline("prompt", default=str(cur.get("prompt") or ""))
    context_refs = _prompt_context_refs(list(cur.get("context_refs") or []))
    context_obligations = job_shape_context_contract.resolve_context_obligations_for_shape(
        sched._canonical_job_shape_for_job_kind(job_kind),
        durable_delta_refs=context_refs,
    )
    out_kind = _prompt("expected output kind", default=str(expected.get("kind") or "report"))
    out_fmt = _prompt("expected output format", default=str(expected.get("format") or "markdown"))
    max_runtime_seconds = _prompt(
        "max runtime seconds",
        default=str(policy.get("max_runtime_seconds") or 1800),
        required=True,
        validator=lambda raw: _validate_int(raw, field="max runtime seconds", minimum=30, maximum=86400),
    )
    post_success_commit_tag = _prompt_bool(
        "run tools/git/git-commit-tag.sh after successful completion",
        default=bool(policy.get("post_success_commit_tag", False)),
    )
    post_success_commit_tag_retries = _prompt(
        "post-success commit/tag retries",
        default=str(policy.get("post_success_commit_tag_retries") or 0),
        required=True,
        validator=lambda raw: _validate_int(raw, field="post-success commit/tag retries", minimum=0, maximum=10),
    )
    post_success_commit_tag_backoff_seconds = _prompt(
        "post-success commit/tag backoff seconds",
        default=str(policy.get("post_success_commit_tag_backoff_seconds") or 60),
        required=True,
        validator=lambda raw: _validate_int(raw, field="post-success commit/tag backoff seconds", minimum=1, maximum=3600),
    )
    agent_turn_retries = _prompt(
        "agent turn retries (transient/runtime failures)",
        default=str(policy.get("agent_turn_retries") or 0),
        required=True,
        validator=lambda raw: _validate_int(raw, field="agent turn retries", minimum=0, maximum=8),
    )
    agent_turn_retry_backoff_seconds = _prompt(
        "agent turn retry backoff seconds",
        default=str(policy.get("agent_turn_retry_backoff_seconds") or 5),
        required=True,
        validator=lambda raw: _validate_int(raw, field="agent turn retry backoff seconds", minimum=1, maximum=300),
    )
    build_verify_default_model = sched._scheduler_default_model(job_kind="build_verify_loop")
    executor_agent_tech = _prompt_menu(
        "Job executor",
        choices=EXECUTOR_CHOICES,
        default=str(runtime.get("executor_agent_tech") or "codex"),
    )
    _warn_if_pipeline_executor(executor_agent_tech=executor_agent_tech, job_kind=job_kind, pipeline_executor_mode=_default_pipeline_executor_mode(executor_agent_tech=executor_agent_tech, job_kind=job_kind, current=str(runtime.get("pipeline_executor_mode") or "")))
    model_choices = _available_model_choices(str(runtime.get("model") or (build_verify_default_model if job_kind == "build_verify_loop" else "")))
    model_name = _prompt(
        f"runtime model (optional; {_inline_choice_hint(model_choices)})",        default=str(runtime.get("model") or (build_verify_default_model if job_kind == "build_verify_loop" else "")),
        required=False,
    )
    execution_mode = _prompt_menu(
        "execution mode",
        choices=EXECUTION_MODE_CHOICES,
        default=_default_execution_mode(
            executor_agent_tech=executor_agent_tech,
            model=str(model_name or runtime.get("model") or ""),
            current=str(runtime.get("execution_mode") or ""),
        ),
    )
    _warn_if_execution_mode(execution_mode=execution_mode, executor_agent_tech=executor_agent_tech)
    pipeline_executor_mode = _default_pipeline_executor_mode(
        executor_agent_tech=executor_agent_tech,
        job_kind=job_kind,
        current=str(runtime.get("pipeline_executor_mode") or ""),
    )
    fallback_model_name = _prompt(
        "fallback model on quota/model availability error (optional, e.g. gpt-5.1)",
        default=str(runtime.get("fallback_model") or ""),        required=False,
    )
    fallback_on_quota = _prompt_bool(
        "allow fallback model on quota/model-availability failures",
        default=bool(runtime.get("fallback_on_quota", True)),
    )
    allowed_reasoning_efforts = sched._allowed_reasoning_efforts(
        model=str(model_name or runtime.get("model") or ""),
        agent_tech=executor_agent_tech,
    )
    reasoning_effort = _prompt(
        f"reasoning effort (optional: {'/'.join(allowed_reasoning_efforts)})",
        default=str(runtime.get("reasoning_effort") or ""),
        required=False,
        validator=lambda raw: sched._normalize_reasoning_effort(
            raw,
            model=str(model_name or runtime.get("model") or ""),
            agent_tech=executor_agent_tech,
            field_name="reasoning effort",
        ),
    )
    spec: Dict[str, Any] = {        "job_id": job_id,
        "job_kind": job_kind,
        "enabled": enabled,
        "binding": {"type": binding_type, "id": binding_id},
        "depends_on": depends_on,
        "context_refs": context_refs,
        "required_context_bundle": context_obligations["required_context_bundle"],
        "expected_output": {"kind": out_kind, "format": out_fmt},
        "policy": {
            "allow_overlap": False,
            "max_runtime_seconds": int(max_runtime_seconds),
            "post_success_commit_tag": post_success_commit_tag,
            "post_success_commit_tag_retries": int(post_success_commit_tag_retries),
            "post_success_commit_tag_backoff_seconds": int(post_success_commit_tag_backoff_seconds),
            "agent_turn_retries": int(agent_turn_retries),
            "agent_turn_retry_backoff_seconds": int(agent_turn_retry_backoff_seconds),
        },
        "runtime": {
            "executor_agent_tech": str(executor_agent_tech or "codex").strip().lower(),
            "pipeline_executor_mode": str(pipeline_executor_mode or "").strip().lower(),
            "execution_mode": str(execution_mode or "").strip().lower(),
            "model": str(model_name or "").strip(),
            "fallback_model": str(fallback_model_name or "").strip(),            "fallback_on_quota": bool(fallback_on_quota),
            "reasoning_effort": str(reasoning_effort or "").strip().lower(),
        },
        "schedule": {"mode": mode, "timezone": timezone},
    }
    if prompt.strip():
        spec["prompt"] = prompt
    if job_kind == "single_turn":
        spec["role"] = role
    elif job_kind == "swarm_launch":
        success_contract = swarm.get("success_contract") if isinstance(swarm.get("success_contract"), dict) else {}
        evidence_requirements = swarm.get("evidence_requirements") if isinstance(swarm.get("evidence_requirements"), dict) else {}
        write_policy = swarm.get("write_policy") if isinstance(swarm.get("write_policy"), dict) else {}
        launch_options = swarm.get("launch_options") if isinstance(swarm.get("launch_options"), dict) else {}
        mission_type = _prompt(
            f"swarm mission type ({'/'.join(sorted(sched.SWARM_MISSION_TYPE_ALLOWLIST))})",
            default=str(swarm.get("mission_type") or "delivery_patch"),
            required=True,
            validator=lambda raw: _validate_choice(raw, choices=tuple(sorted(sched.SWARM_MISSION_TYPE_ALLOWLIST)), field="swarm mission type"),
        )
        objective = _prompt("swarm objective", default=str(swarm.get("objective") or ""), required=True)
        scope = _prompt("swarm scope (optional)", default=str(swarm.get("scope") or ""), required=False)
        expected_outputs_csv = _prompt(
            "swarm expected outputs (comma-separated, optional)",
            default=",".join(list(swarm.get("expected_outputs") or [])),
            required=False,
        )
        accepted_statuses_csv = _prompt(
            "accepted terminal statuses (comma-separated)",
            default=",".join(list(success_contract.get("accepted_statuses") or ["finished"])),
            required=True,
        )
        require_changed_files = _prompt_bool(
            "require changed files in final swarm result",
            default=bool(success_contract.get("require_changed_files", True)),
        )
        require_summary_path = _prompt_bool(
            "require swarm summary artifact path",
            default=bool(evidence_requirements.get("require_summary_path", True)),
        )
        require_worker_artifacts = _prompt_bool(
            "require worker summary artifacts",
            default=bool(evidence_requirements.get("require_worker_artifacts", False)),
        )
        allowed_paths_csv = _prompt(
            "allowed changed paths (comma-separated, optional)",
            default=",".join(list(write_policy.get("allowed_paths") or [])),
            required=False,
        )
        max_changed_files = _prompt(
            "maximum changed files",
            default=str(write_policy.get("max_changed_files") or 25),
            required=True,
            validator=lambda raw: _validate_int(raw, field="maximum changed files", minimum=1, maximum=500),
        )
        run_async = _prompt_bool(
            "launch swarm asynchronously",
            default=bool(launch_options.get("run_async", False)),
        )
        started_by_role = _prompt_menu(
            "swarm started_by_role",
            choices=ROLE_CHOICES,
            default=str(launch_options.get("started_by_role") or "generalist"),
        )
        spec["swarm"] = {
            "mission_type": mission_type,
            "objective": objective,
            "scope": scope,
            "expected_outputs": _split_csv(expected_outputs_csv),
            "success_contract": {
                "accepted_statuses": _split_csv(accepted_statuses_csv),
                "require_changed_files": bool(require_changed_files),
            },
            "evidence_requirements": {
                "require_summary_path": bool(require_summary_path),
                "require_worker_artifacts": bool(require_worker_artifacts),
            },
            "write_policy": {
                "mode": "bounded_patch",
                "allowed_paths": _split_csv(allowed_paths_csv),
                "max_changed_files": int(max_changed_files),
            },
            "launch_options": {
                "run_async": bool(run_async),
                "started_by_role": started_by_role,
            },
        }
    else:
        builder_role = _prompt_menu("Builder role", choices=ROLE_CHOICES, default=str(loop.get("builder_role") or "builder"))
        verifier_role = _prompt_menu("Verifier role", choices=ROLE_CHOICES, default=str(loop.get("verifier_role") or "verifier"))
        max_iterations = _prompt(
            "max iterations",
            default=str(loop.get("max_iterations") or 3),
            required=True,
            validator=lambda raw: _validate_int(raw, field="max iterations", minimum=1, maximum=10),
        )
        pass_marker = _prompt("pass marker", default=str(loop.get("pass_marker") or "VERDICT: PASS"), required=True)
        spec["loop"] = {
            "builder_role": builder_role,
            "verifier_role": verifier_role,
            "max_iterations": int(max_iterations),
            "pass_marker": pass_marker,
        }
        spec["loop"]["builder_runtime"] = {
            "executor_agent_tech": str(executor_agent_tech or "codex").strip().lower(),
            "pipeline_executor_mode": str(pipeline_executor_mode or "").strip().lower(),
            "execution_mode": str(execution_mode or "").strip().lower(),
            "model": str(model_name or "").strip(),
            "fallback_model": str(fallback_model_name or "").strip(),            "fallback_on_quota": bool(fallback_on_quota),
            "reasoning_effort": str(reasoning_effort or "").strip().lower(),
        }
        spec["loop"]["verifier_runtime"] = dict(spec["loop"]["builder_runtime"])
    if str(executor_agent_tech or "").strip().lower() != "pipeline":
        spec["runtime"].pop("pipeline_executor_mode", None)
        if job_kind != "single_turn":
            spec["loop"]["builder_runtime"].pop("pipeline_executor_mode", None)
            spec["loop"]["verifier_runtime"].pop("pipeline_executor_mode", None)
    if mode == "once":
        spec["schedule"]["run_at"] = run_at
    elif mode == "daily":        spec["schedule"]["time_of_day"] = time_of_day
    else:
        spec["schedule"]["every_minutes"] = int(every_minutes)
        if str(start_at_utc or "").strip():
            spec["schedule"]["start_at_utc"] = str(start_at_utc).strip()
    return spec


def _save_interactive(*, existing: Dict[str, Any] | None = None, editing: bool = False) -> Dict[str, Any]:
    _require_tty()
    spec = _interactive_spec(existing, editing=editing)
    canonical = sched.validate_job_spec(spec)
    _print_spec_summary(canonical)
    if not _prompt_bool("Save this job", default=True):
        raise UserAbort("save canceled")
    return sched.save_job(canonical)


def _cmd_new(args: argparse.Namespace) -> int:
    sched_backend.require_legacy_for_mutation("new scheduled job", backend=getattr(args, "backend", "") or None)
    spec = _save_interactive()
    print(json.dumps(sched.job_summary(spec), indent=2, ensure_ascii=False))
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    sched_backend.require_legacy_for_mutation("edit scheduled job", backend=getattr(args, "backend", "") or None)
    spec = sched.load_job(args.job_id)
    updated = _save_interactive(existing=spec, editing=True)
    print(json.dumps(sched.job_summary(updated), indent=2, ensure_ascii=False))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    if _backend(args) == "v2":
        bundle, rows = _v2_watch_rows()
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "backend": "v2",
                        "surface_status": str(bundle.get("status") or ""),
                        "parent_uid": str(bundle.get("parent_uid") or ""),
                        "jobs": rows,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        print(f"backend=v2 surface_status={bundle.get('status') or ''} parent={bundle.get('parent_uid') or ''}")
        _print_v2_rows(rows, limit=getattr(args, "limit", 100))
        return 0
    rows = sched.list_jobs()
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    for row in rows:
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        print(
            f"{row['job_id']:24} enabled={int(bool(row['enabled']))} role={row['role']:10} "
            f"due={int(bool(due.get('eligible')))} next={due.get('due_utc') or '-'} "
            f"last={state.get('last_status') or '-'}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if _backend(args) == "v2":
        bundle, rows = _v2_watch_rows()
        wanted = str(args.job_id or "").strip()
        matches = [row for row in rows if str(row.get("child_uid") or "").strip() == wanted or str(row.get("node_id") or "").strip() == wanted]
        if not matches:
            raise FileNotFoundError(f"v2 job not found: {wanted} (use --backend legacy for legacy scheduled_jobs)")
        print(
            json.dumps(
                {
                    "backend": "v2",
                    "surface_status": str(bundle.get("status") or ""),
                    "parent_uid": str(bundle.get("parent_uid") or ""),
                    "job": matches[0],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    spec = sched.load_job(args.job_id)
    state = sched.load_state(args.job_id)
    receipts = sched.list_receipts(args.job_id, limit=5)
    print(
        json.dumps(
            {
                "job": sched.job_summary(spec, state=state),
                "latest_receipts": receipts,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_enable(args: argparse.Namespace) -> int:
    sched_backend.require_legacy_for_mutation("enable scheduled job", backend=getattr(args, "backend", "") or None)
    print(json.dumps(sched.set_job_enabled(args.job_id, True), indent=2, ensure_ascii=False))
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    sched_backend.require_legacy_for_mutation("disable scheduled job", backend=getattr(args, "backend", "") or None)
    print(json.dumps(sched.set_job_enabled(args.job_id, False), indent=2, ensure_ascii=False))
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    sched_backend.require_legacy_for_mutation("delete scheduled job", backend=getattr(args, "backend", "") or None)
    job_id = str(args.job_id or "").strip()
    if not job_id:
        _require_tty()
        rows = sched.list_jobs()
        if not rows:
            print("No scheduled jobs found.", file=sys.stderr)
            return 1
        selected = prompt_menu(
            "Select job to delete",
            choices=rows,
            render=lambda row: (
                f"{row['display_title']} [{row['job_id']}] "
                f"enabled={int(bool(row.get('enabled')))} "
                f"last={str((row.get('state') or {}).get('last_status') or '-')}"
            ),
            default_index=0,
            fallback_reader=_read_input,
        )
        job_id = str(selected.get("job_id") or "").strip()
    if not args.yes:
        suffix = " and purge its stored history" if args.purge_history else ""
        confirmed = prompt_yes_no(
            f"Delete scheduled job '{job_id}'{suffix}",
            default=False,
            fallback_reader=_read_input,
        )
        if not confirmed:
            raise UserAbort("delete canceled")
    print(
        json.dumps(
            sched.delete_job(job_id, purge_history=bool(args.purge_history)),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_run_now(args: argparse.Namespace) -> int:
    sched_backend.require_legacy_for_mutation("run scheduled job now", backend=getattr(args, "backend", "") or None)
    print(json.dumps(sched.request_run_now(args.job_id, request_source="manual"), indent=2, ensure_ascii=False))
    return 0


def _cmd_tick(args: argparse.Namespace) -> int:
    if _backend(args) == "v2":
        print(
            json.dumps(
                {
                    "ok": False,
                    "backend": "v2",
                    "skipped": True,
                    "reason": "v2_has_no_standalone_tick; server owns v2 scheduling through the intent_family_v2 surface",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(sched.scheduler_tick(limit=args.limit), indent=2, ensure_ascii=False))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if _backend(args) == "v2":
        print(json.dumps(sched_backend.v2_surface_bundle(), indent=2, ensure_ascii=False))
        return 0
    print(json.dumps(sched.scheduler_service_status(), indent=2, ensure_ascii=False))
    return 0


def _cmd_daemon(args: argparse.Namespace) -> int:
    if _backend(args) == "v2":
        print(
            json.dumps(
                {
                    "ok": False,
                    "backend": "v2",
                    "skipped": True,
                    "reason": "v2_has_no_standalone_daemon; run the plane server and v2 scheduler surface instead",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2
    interval = max(5, int(args.interval_s))
    print(f"scheduled job daemon polling every {interval}s")
    try:
        while True:
            result = sched.scheduler_tick(limit=args.limit)
            print(json.dumps(result, ensure_ascii=False))
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Interactive authoring and execution for scheduled agent jobs.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_backend_arg(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")

    p_new = sub.add_parser("new", help="Create a scheduled job interactively")
    add_backend_arg(p_new)
    p_new.set_defaults(func=_cmd_new)

    p_edit = sub.add_parser("edit", help="Edit an existing scheduled job interactively")
    p_edit.add_argument("job_id")
    add_backend_arg(p_edit)
    p_edit.set_defaults(func=_cmd_edit)

    p_list = sub.add_parser("list", help="List scheduled jobs")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--limit", type=int, default=100)
    add_backend_arg(p_list)
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="Show one scheduled job and its latest receipts")
    p_show.add_argument("job_id")
    add_backend_arg(p_show)
    p_show.set_defaults(func=_cmd_show)

    p_enable = sub.add_parser("enable", help="Enable a scheduled job")
    p_enable.add_argument("job_id")
    add_backend_arg(p_enable)
    p_enable.set_defaults(func=_cmd_enable)

    p_disable = sub.add_parser("disable", help="Disable a scheduled job")
    p_disable.add_argument("job_id")
    add_backend_arg(p_disable)
    p_disable.set_defaults(func=_cmd_disable)

    p_delete = sub.add_parser("delete", help="Delete a scheduled job")
    p_delete.add_argument("job_id", nargs="?")
    p_delete.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_delete.add_argument("--purge-history", action="store_true", help="Also remove state, receipts, and stored prompt files")
    add_backend_arg(p_delete)
    p_delete.set_defaults(func=_cmd_delete)

    p_run = sub.add_parser("run-now", help="Execute one scheduled job immediately")
    p_run.add_argument("job_id")
    add_backend_arg(p_run)
    p_run.set_defaults(func=_cmd_run_now)

    p_tick = sub.add_parser("tick", help="Execute all due scheduled jobs once")
    p_tick.add_argument("--limit", type=int, default=10)
    add_backend_arg(p_tick)
    p_tick.set_defaults(func=_cmd_tick)

    p_status = sub.add_parser("status", help="Show scheduler service status")
    add_backend_arg(p_status)
    p_status.set_defaults(func=_cmd_status)

    p_daemon = sub.add_parser("daemon", help="Poll and execute due scheduled jobs")
    p_daemon.add_argument("--interval-s", type=int, default=30)
    p_daemon.add_argument("--limit", type=int, default=10)
    add_backend_arg(p_daemon)
    p_daemon.set_defaults(func=_cmd_daemon)
    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except UserAbort as exc:
        print(f"Canceled: {exc}", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
