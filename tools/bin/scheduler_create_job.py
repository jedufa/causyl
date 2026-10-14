#!/usr/bin/env python3
"""Ledger-first helper for creating scheduled jobs bound to a PIS."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import string
from typing import Any, Dict, List, Optional

try:
    import termios
    import tty
except Exception:  # pragma: no cover
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

from tools import runtime_policy as _runtime_policy
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from infra.webui.ledger import ledger_storage

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_ROOT = (REPO_ROOT / "governance" / "ledger" / "items").resolve()
TEMPLATES_ROOT = (REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs" / "templates").resolve()
PROMPT_TEMPLATE_PATH = (REPO_ROOT / "docs" / "prompts" / "lab_task_prompts.md").resolve()
DEFAULT_ROLE = "generalist"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_TIME_OF_DAY = "03:30"
DEFAULT_RUNTIME_SECONDS = 1800
DEFAULT_EXPECTED_OUTPUT = {"kind": "report", "format": "markdown"}
PROMPT_MODES = {
    "implementation": {
        "heading": "Implement One Child PIS",
        "default_role": "generalist",
        "expected_output": {"kind": "report", "format": "markdown"},
    },
    "decompose_parent": {
        "heading": "Decompose A Parent Ledger Item",
        "default_role": "builder",
        "expected_output": {"kind": "governance_decomposition_patch", "format": "markdown"},
        "job_kind": "build_verify_loop",
    },
}

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
)
EXECUTOR_CHOICES = ("codex", "pipeline")
EXECUTION_MODE_CHOICES = ("advisory_then_controller", "controller_only", "advisory_only")


class UserAbort(Exception):
    pass


@dataclass(frozen=True)
class PisChoice:
    uid: str
    title: str
    status: str
    updated_utc: str
    created_utc: str
    slug: str
    dir_path: Path
    pis_path: Path


def _scheduled_job_template_paths() -> List[Path]:
    if not TEMPLATES_ROOT.exists():
        return []
    return sorted(path.resolve() for path in TEMPLATES_ROOT.glob("*.json") if path.is_file())


def _load_scheduled_job_template(template_id: str) -> Dict[str, Any]:
    wanted = str(template_id or "").strip()
    if not wanted:
        raise UserAbort("template_id is required")
    for path in _scheduled_job_template_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise UserAbort(f"failed to load scheduled template {path.name}: {exc}") from exc
        if str(data.get("template_id") or "").strip() != wanted:
            continue
        if str(data.get("schema_version") or "").strip() != "scheduled_job_template_v1":
            raise UserAbort(f"unsupported template schema for {wanted}")
        out = dict(data)
        out["_template_path"] = path
        return out
    raise UserAbort(f"scheduled template not found: {wanted}")


def _available_template_ids() -> List[str]:
    ids: List[str] = []
    for path in _scheduled_job_template_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        template_id = str(data.get("template_id") or "").strip()
        if template_id and template_id not in ids:
            ids.append(template_id)
    return ids


def _template_tokens(row: PisChoice, *, template_id: str) -> Dict[str, str]:
    m = re.match(r"^PIS-(\d{8})-", str(row.uid or ""))
    date_part = m.group(1) if m else ""
    return {
        "<LEDGER_UID>": row.uid,
        "<TITLE>": row.title,
        "<PIS_PATH>": str(row.pis_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "<UID_SUFFIX>": row.uid.rsplit("-", 1)[-1],
        "<SLUG>": row.slug,
        "<TEMPLATE_ID>": str(template_id or "").strip(),
        "<DATESTAMP>": date_part,
    }


def _apply_template_tokens(value: Any, tokens: Dict[str, str]) -> Any:
    if isinstance(value, str):
        out = value
        for needle, replacement in tokens.items():
            out = out.replace(needle, replacement)
        return out
    if isinstance(value, list):
        return [_apply_template_tokens(item, tokens) for item in value]
    if isinstance(value, dict):
        return {str(key): _apply_template_tokens(item, tokens) for key, item in value.items()}
    return value


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(dict(out.get(key) or {}), value)
        else:
            out[key] = value
    return out


def _default_spec_from_template(row: PisChoice, *, template_id: str) -> Dict[str, Any]:
    template = _load_scheduled_job_template(template_id)
    prompt_mode = str(template.get("prompt_mode") or "implementation").strip() or "implementation"
    spec = _default_spec(row, prompt_mode=prompt_mode)
    tokens = _template_tokens(row, template_id=template_id)
    template_path = Path(str(template.get("_template_path") or ""))
    ref = str(template_path.relative_to(REPO_ROOT)).replace("\\", "/") if template_path else ""
    job_title = str(template.get("job_title") or f"{template_id} {row.title}").strip()
    spec["job_id"] = _job_id_from_pis(row.uid, job_title)
    spec["display_title"] = _apply_template_tokens(
        str(template.get("display_title_template") or f"{template_id} {row.title}"),
        tokens,
    )
    for field in [
        "job_kind",
        "role",
        "prompt",
        "expected_output",
        "required_context_bundle",
        "depends_on",
        "additional_context_refs",
        "forbidden_context_refs",
    ]:
        if field in template:
            spec[field] = _apply_template_tokens(template.get(field), tokens)
    if "context_refs" in template:
        spec["context_refs"] = _apply_template_tokens(template.get("context_refs"), tokens)
    if isinstance(template.get("policy"), dict):
        spec["policy"] = _merge_dict(dict(spec.get("policy") or {}), _apply_template_tokens(dict(template.get("policy") or {}), tokens))
    if isinstance(template.get("runtime"), dict):
        spec["runtime"] = _merge_dict(dict(spec.get("runtime") or {}), _apply_template_tokens(dict(template.get("runtime") or {}), tokens))
    if isinstance(template.get("loop"), dict):
        spec["loop"] = _apply_template_tokens(dict(template.get("loop") or {}), tokens)
    prompt_meta = dict(spec.get("prompt_meta") or {})
    prompt_meta["scheduled_template"] = {
        "template_id": template_id,
        "template_ref": ref,
        "instantiated_for": row.uid,
    }
    if isinstance(template.get("prompt_meta"), dict):
        prompt_meta = _merge_dict(prompt_meta, _apply_template_tokens(dict(template.get("prompt_meta") or {}), tokens))
    spec["prompt_meta"] = prompt_meta
    return spec


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


def _parse_utc_maybe(raw: Any) -> datetime:
    text = str(raw or "").strip()
    if not text:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _slugify(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value[:32].strip("-") or "job"


def _job_id_from_pis(uid: str, title: str) -> str:
    suffix = uid.rsplit("-", 1)[-1].lower()
    date_part = ""
    m = re.match(r"^PIS-(\d{8})-", str(uid or ""))
    if m:
        date_part = m.group(1)
    pieces = ["pis", suffix]
    if date_part:
        pieces.append(date_part)
    pieces.append(_slugify(title))
    raw = "_".join([part for part in pieces if part])
    raw = raw[:78].rstrip("._-")
    if not sched.JOB_ID_RE.match(raw):
        raw = f"pis_{suffix}_{date_part or 'job'}"
    return raw


def _default_run_at(now: Optional[datetime] = None) -> str:
    base = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    candidate = base + timedelta(hours=1)
    return candidate.isoformat().replace("+00:00", "Z")


def _prompt(
    label: str,
    *,
    default: str = "",
    validator: Optional[callable] = None,
    required: bool = False,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = _read_input(f"{label}{suffix}: ").strip()
        value = raw or default
        if not value and not required:
            return ""
        if not value:
            print("Value is required.", file=sys.stderr)
            continue
        try:
            return validator(value) if validator else value
        except ValueError as exc:
            print(f"Invalid value: {exc}", file=sys.stderr)


def _prompt_bool(label: str, *, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    default_token = "y" if default else "n"
    if str(os.environ.get("SCHEDULER_SINGLE_KEY_MENUS") or "").strip().lower() in {"1", "true", "yes", "on"} and sys.stdin.isatty() and termios is not None and tty is not None:
        print(f"{label} [{hint}]: ", end="", flush=True)
        while True:
            raw = _read_menu_key()
            token = (raw or default_token).lower()
            if token == "y":
                return True
            if token == "n":
                return False
            print("Choose one key: y, n.", file=sys.stderr)
            print(f"{label} [{hint}]: ", end="", flush=True)
    while True:
        raw = _read_input(f"{label} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer yes or no.", file=sys.stderr)


def _prompt_choice(label: str, *, choices: List[str], default: str) -> str:
    options = "/".join(choices)
    while True:
        raw = _read_input(f"{label} [{default}] ({options}): ").strip().lower()
        value = raw or default
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(choices)}.", file=sys.stderr)


def _choice_default_index(choices: List[str], default: str) -> int:
    try:
        return choices.index(default)
    except ValueError:
        return 0


def _menu_tokens(count: int) -> List[str]:
    alphabet = list(string.digits[1:] + string.ascii_lowercase)
    if count > len(alphabet):
        raise UserAbort(f"menu too large for single-key selection: {count}")
    return alphabet[:count]


def _read_menu_key() -> str:
    if str(os.environ.get("SCHEDULER_SINGLE_KEY_MENUS") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return _read_input("").strip().lower()
    if not sys.stdin.isatty() or termios is None or tty is None:
        return _read_input("").strip().lower()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        chars = sys.stdin.read(1)
    except KeyboardInterrupt as exc:
        raise UserAbort("canceled by user") from exc
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if chars in {"\x03", "\x1b"}:
        raise UserAbort("canceled by user")
    if chars in {"\r", "\n"}:
        return ""
    if chars:
        print(chars)
    return chars.strip().lower()


def _prompt_menu(label: str, *, choices: List[str], default_index: int = 0) -> str:
    if not choices:
        raise UserAbort("menu choices are empty")
    if default_index < 0 or default_index >= len(choices):
        default_index = 0
    if str(os.environ.get("SCHEDULER_SINGLE_KEY_MENUS") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        print(label)
        for idx, choice in enumerate(choices, start=1):
            marker = " (default)" if idx - 1 == default_index else ""
            print(f"  {idx}. {choice}{marker}")
        while True:
            raw = _read_input(f"Selection [{default_index + 1}]: ").strip()
            if not raw:
                return choices[default_index]
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(choices):
                    return choices[idx]
            print(f"Choose a number between 1 and {len(choices)}.", file=sys.stderr)
    
    tokens = _menu_tokens(len(choices))
    token_to_choice = {token: choice for token, choice in zip(tokens, choices)}
    default_token = tokens[default_index]
    print(label)
    for token, choice in zip(tokens, choices):
        marker = " (default)" if token == default_token else ""
        print(f"  {token}. {choice}{marker}")
    if sys.stdin.isatty() and termios is not None and tty is not None:
        print(f"Select one key [{default_token}]: ", end="", flush=True)
    while True:
        raw = _read_menu_key()
        token = raw or default_token
        if token in token_to_choice:
            return token_to_choice[token]
        if not (sys.stdin.isatty() and termios is not None and tty is not None):
            print(f"Choose one of: {', '.join(tokens)}.", file=sys.stderr)
        else:
            print(f"Choose one key: {', '.join(tokens)}.", file=sys.stderr)
            print(f"Select one key [{default_token}]: ", end="", flush=True)


def _read_multiline_until_eof() -> str:
    lines: List[str] = []
    while True:
        raw = sys.stdin.readline()
        if raw == "":
            break
        lines.append(raw.rstrip("\n"))
    return "\n".join(lines).strip()


def _prompt_multiline(label: str, *, default: str) -> str:
    print(f"{label} (finish with EOF; Ctrl-D on Unix/WSL, Ctrl-Z then Enter on Windows)")
    if default:
        print("Default/current value:")
        print(default)
    text = _read_multiline_until_eof()
    return text or default


def _validate_job_id(raw: str) -> str:
    value = str(raw or "").strip()
    if not sched.JOB_ID_RE.match(value):
        raise ValueError("job_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
    if (sched.jobs_dir() / f"{sched._safe_name(value)}.json").exists():
        raise ValueError(f"job already exists: {value}")
    return value


def _validate_role(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value not in sched.ROLE_SET:
        raise ValueError(f"role must be one of: {', '.join(sorted(sched.ROLE_SET))}")
    return value


def _validate_role_in_choices(raw: str, *, choices: List[str], field: str) -> str:
    value = _validate_role(raw)
    if value not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(choices)}")
    return value


def _validate_model(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("model is required")
    return value


def _validate_once_utc(raw: str) -> str:
    return sched._utc_iso(sched._parse_utc(raw))


def _validate_timezone(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("timezone is required")
    try:
        sched.ZoneInfo(value)
    except Exception as exc:
        raise ValueError(f"unknown timezone: {value}") from exc
    return value


def _validate_runtime_seconds(raw: str) -> int:
    try:
        value = int(str(raw or "").strip())
    except Exception as exc:
        raise ValueError("max runtime seconds must be an integer") from exc
    if value < 30 or value > 86400:
        raise ValueError("max runtime seconds must be between 30 and 86400")
    return value


def _available_role_choices() -> List[str]:
    return sorted(str(role) for role in sched.ROLE_SET)


def _loop_builder_role_choices() -> List[str]:
    return ["builder", "generalist", "orchestrator"]


def _loop_verifier_role_choices() -> List[str]:
    return ["verifier", "assurer", "generalist"]


def _parse_runtime_policy_models() -> List[str]:
    models: List[str] = []

    def _add(entry: Optional[str]) -> None:
        value = str(entry or "").strip()
        if value and value not in models:
            models.append(value)

    for key, value in _runtime_policy.load_runtime_policy().items():
        if "model" not in str(key or ""):
            continue
        for candidate in str(value or "").split(","):
            _add(candidate)
    return models


def _scheduler_default_model() -> str:
    return _runtime_policy.get_runtime_policy_text("scheduler.default_model", "deepseek-reasoner")


def _scheduler_default_fallback_models() -> List[str]:
    return _runtime_policy.get_runtime_policy_csv("scheduler.default_fallback_models", "gpt-5.4")


def _runtime_spec_from_model(model: str, fallback_models: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "executor_agent_tech": "codex",
        "agent_tech": str(sched._infer_agent_tech_from_model(model) or "codex"),
        "model": model,
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
    }
    normalized = [str(item or "").strip() for item in list(fallback_models or []) if str(item or "").strip()]
    if normalized:
        out["fallback_models"] = normalized
    return out


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


def _decompose_runtime_defaults() -> Dict[str, Dict[str, Any]]:
    runtime_model = _runtime_policy.get_runtime_policy_text("scheduler.decompose.runtime_model", "gpt-5.4")
    builder_model = _runtime_policy.get_runtime_policy_text("scheduler.decompose.builder_model", "deepseek-reasoner")
    verifier_model = _runtime_policy.get_runtime_policy_text("scheduler.decompose.verifier_model", "gpt-5.4")

    return {
        "runtime": _runtime_spec_from_model(
            runtime_model,
            _runtime_policy.get_runtime_policy_csv("scheduler.decompose.runtime_fallback_models", "deepseek-reasoner"),
        ),
        "builder": _runtime_spec_from_model(
            builder_model,
            _runtime_policy.get_runtime_policy_csv("scheduler.decompose.builder_fallback_models", "gpt-5.4"),
        ),
        "verifier": _runtime_spec_from_model(
            verifier_model,
            _runtime_policy.get_runtime_policy_csv("scheduler.decompose.verifier_fallback_models", "deepseek-reasoner"),
        ),
    }


def _available_model_choices(current: str = "") -> List[str]:
    models: List[str] = []

    def _add(entry: Optional[str]) -> None:
        value = str(entry or "").strip()
        if value and value not in models:
            models.append(value)

    _add(current)
    _add(_scheduler_default_model())
    for entry in _parse_runtime_policy_models():
        _add(entry)
    for entry in KNOWN_MODEL_CANDIDATES:
        _add(entry)
    _add("pipeline")
    return models


def _inline_choice_hint(choices: List[str], *, prefix: str = "options") -> str:
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


def _model_prompt_label(*, executor_agent_tech: str, stage: str = "") -> str:
    executor = str(executor_agent_tech or "").strip().lower()
    prefix = f"{stage} " if stage else ""
    if executor == "pipeline":
        return f"{prefix}executor model"
    return f"{prefix}advisory model"


def _load_prompt_template(heading: str) -> str:
    text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    pattern = rf"^##\s+\d+\.\s+{re.escape(heading)}\s*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise UserAbort(f"prompt template heading not found: {heading}")
    remainder = text[match.end() :]
    fence_match = re.search(r"```text\n(.*?)\n```", remainder, flags=re.DOTALL)
    if not fence_match:
        raise UserAbort(f"prompt template body not found: {heading}")
    return fence_match.group(1).strip()


def _load_recent_pis_choices(limit: int) -> List[PisChoice]:
    out: List[PisChoice] = []
    if not LEDGER_ROOT.exists():
        return out
    for child in ledger_storage.list_item_dirs(LEDGER_ROOT):
        meta_path = (child / "meta.json").resolve()
        pis_path = (child / "pis.md").resolve()
        if not meta_path.exists() or not pis_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        uid = str(meta.get("uid") or child.name.split("__", 1)[0]).strip()
        title = str(meta.get("title") or "").strip() or uid
        status = str(meta.get("status") or "").strip() or "unknown"
        updated_utc = str(meta.get("updated_utc") or "").strip()
        created_utc = str(meta.get("created_utc") or "").strip()
        slug = str(meta.get("slug") or child.name.split("__", 1)[-1]).strip()
        out.append(
            PisChoice(
                uid=uid,
                title=title,
                status=status,
                updated_utc=updated_utc,
                created_utc=created_utc,
                slug=slug,
                dir_path=child.resolve(),
                pis_path=pis_path,
            )
        )
    out.sort(key=lambda row: (_parse_utc_maybe(row.updated_utc), _parse_utc_maybe(row.created_utc), row.uid), reverse=True)
    return out[: max(1, limit)]


def _display_pis_choices(rows: List[PisChoice]) -> None:
    print("Recent PIS items")
    for idx, row in enumerate(rows, start=1):
        stamp = row.updated_utc or row.created_utc or "-"
        print(f"  {idx}. {row.uid} [{row.status}] {stamp}")
        print(f"     {row.title}")


def _select_pis(limit: int, preselected_uid: str = "") -> PisChoice:
    rows = _load_recent_pis_choices(limit)
    if not rows:
        raise UserAbort("no ledger PIS items found")
    if preselected_uid:
        uid = str(preselected_uid).strip()
        for row in rows:
            if row.uid == uid:
                return row
        child = ledger_storage.item_dir_for_uid(LEDGER_ROOT, uid)
        if child is not None:
            meta_path = child / "meta.json"
            pis_path = child / "pis.md"
            if meta_path.exists() and pis_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return PisChoice(
                    uid=str(meta.get("uid") or uid),
                    title=str(meta.get("title") or uid),
                    status=str(meta.get("status") or "unknown"),
                    updated_utc=str(meta.get("updated_utc") or ""),
                    created_utc=str(meta.get("created_utc") or ""),
                    slug=str(meta.get("slug") or child.name.split("__", 1)[-1]),
                    dir_path=child.resolve(),
                    pis_path=pis_path.resolve(),
                )
        raise UserAbort(f"PIS not found: {uid}")
    print()
    print("Recent PIS items")
    if str(os.environ.get("SCHEDULER_SINGLE_KEY_MENUS") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        for idx, row in enumerate(rows, start=1):
            stamp = row.updated_utc or row.created_utc or "-"
            default_marker = " (default)" if idx == 1 else ""
            print(f"  {idx}. {row.uid} [{row.status}] {stamp}{default_marker}")
            print(f"     {row.title}")
        print("Choose a listed number, or type/paste a full PIS UID and press Enter.")
        while True:
            choice = _read_input("Selection [1]: ").strip()
            if not choice:
                return rows[0]
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(rows):
                    return rows[idx]
            for row in rows:
                if row.uid == choice:
                    return row
            print("Unknown selection. Use a shown number or paste a full PIS UID.", file=sys.stderr)
    tokens = _menu_tokens(len(rows))
    for token, row in zip(tokens, rows):
        stamp = row.updated_utc or row.created_utc or "-"
        default_marker = " (default)" if token == tokens[0] else ""
        print(f"  {token}. {row.uid} [{row.status}] {stamp}{default_marker}")
        print(f"     {row.title}")
    print("Press one key to select a listed PIS, or type/paste a full UID and press Enter.")
    if sys.stdin.isatty() and termios is not None and tty is not None:
        print(f"Select one key [{tokens[0]}]: ", end="", flush=True)
    while True:
        choice = _read_menu_key()
        if not choice:
            return rows[0]
        for token, row in zip(tokens, rows):
            if choice == token:
                return row
        for row in rows:
            if row.uid == choice:
                return row
        print("Unknown selection. Use a shown key or paste a full PIS UID.", file=sys.stderr)
        if sys.stdin.isatty() and termios is not None and tty is not None:
            print(f"Select one key [{tokens[0]}]: ", end="", flush=True)


def _default_prompt(row: PisChoice, *, prompt_mode: str) -> str:
    pis_rel = str(row.pis_path.relative_to(REPO_ROOT)).replace("\\", "/")
    if prompt_mode not in PROMPT_MODES:
        raise UserAbort(f"unsupported prompt mode: {prompt_mode}")
    template = _load_prompt_template(PROMPT_MODES[prompt_mode]["heading"])
    prompt = template.replace("<PARENT_UID>", row.uid).replace("<CHILD_UID>", row.uid).replace("<TITLE>", row.title)
    return (
        f"{prompt}\n\n"
        "Ledger binding:\n"
        f"- ledger_uid: {row.uid}\n"
        f"- title: {row.title}\n"
        f"- pis_path: {pis_rel}\n"
        f"- current_ledger_status: {row.status}\n"
    )


def _queue_child_prompt_meta(*, enabled: bool = True, run_after_minutes: int = 5) -> Dict[str, Any]:
    return {
        "queue_child_implementation_jobs": {
            "enabled": bool(enabled),
            "run_after_minutes": int(run_after_minutes),
            "child_mode": "implementation",
        }
    }


def _default_spec(row: PisChoice, *, prompt_mode: str, queue_child_jobs: bool = True, child_delay_minutes: int = 5) -> Dict[str, Any]:
    model = _scheduler_default_model()
    stage_runtime = _runtime_spec_from_model(model, _scheduler_default_fallback_models())
    mode_meta = PROMPT_MODES[prompt_mode]
    job_kind = "build_verify_loop" if prompt_mode == "decompose_parent" else "single_turn"
    spec = {
        "job_id": _job_id_from_pis(row.uid, row.title),
        "job_kind": job_kind,
        "enabled": True,
        "role": mode_meta["default_role"],
        "binding": {"type": "ledger_uid", "id": row.uid},
        "display_title": f"{row.uid.split('-')[-1].lower()} {row.title[:80]}",
        "prompt": _default_prompt(row, prompt_mode=prompt_mode),
        "context_refs": [str(row.pis_path.relative_to(REPO_ROOT)).replace("\\", "/")],
        "expected_output": dict(mode_meta["expected_output"]),
        "schedule": {"mode": "once", "run_at": _default_run_at(), "timezone": DEFAULT_TIMEZONE},
        "policy": {"max_runtime_seconds": DEFAULT_RUNTIME_SECONDS},
        "runtime": dict(stage_runtime),
        "required_context_bundle": "lab_core_v1",
        "_prompt_mode": prompt_mode,
    }
    if job_kind == "build_verify_loop":
        stage_defaults = _decompose_runtime_defaults() if prompt_mode == "decompose_parent" else {}
        spec.pop("role", None)
        spec["loop"] = {
            "builder_role": "builder",
            "verifier_role": "verifier",
            "max_iterations": 3,
            "pass_marker": "VERDICT: PASS",
            "builder_runtime": dict(stage_defaults.get("builder") or stage_runtime),
            "verifier_runtime": dict(stage_defaults.get("verifier") or stage_runtime),
        }
        if stage_defaults.get("runtime"):
            spec["runtime"] = dict(stage_defaults["runtime"])
        spec["prompt_meta"] = _queue_child_prompt_meta(enabled=queue_child_jobs, run_after_minutes=child_delay_minutes)
    return spec


def _build_spec_from_answers(row: PisChoice, *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cur = dict(existing or _default_spec(row, prompt_mode="implementation"))
    runtime = dict(cur.get("runtime") or {})
    schedule = dict(cur.get("schedule") or {})
    policy = dict(cur.get("policy") or {})
    prompt_mode = str(cur.get("_prompt_mode") or "implementation").strip()
    if prompt_mode not in PROMPT_MODES:
        prompt_mode = "implementation"
    default_prompt = _default_prompt(row, prompt_mode=prompt_mode)
    default_expected_output = dict(PROMPT_MODES[prompt_mode]["expected_output"])
    role_choices = _available_role_choices()
    builder_role_choices = _loop_builder_role_choices()
    verifier_role_choices = _loop_verifier_role_choices()
    model_choices = _available_model_choices(str(runtime.get("model") or _scheduler_default_model()))
    prompt_meta = dict(cur.get("prompt_meta") or {})
    queue_children_meta = dict(prompt_meta.get("queue_child_implementation_jobs") or {})
    loop = dict(cur.get("loop") or {})
    builder_runtime = dict(loop.get("builder_runtime") or {})
    verifier_runtime = dict(loop.get("verifier_runtime") or {})
    current_executor = str(runtime.get("executor_agent_tech") or "codex").strip().lower() or "codex"
    print()
    print(f"Selected PIS: {row.uid}")
    print(f"Title: {row.title}")
    print(f"Status: {row.status}")
    print()
    job_id = _prompt("job_id", default=str(cur.get("job_id") or ""), validator=_validate_job_id, required=True)
    enabled = _prompt_bool("enabled", default=bool(cur.get("enabled", True)))
    role = ""
    builder_role = ""
    verifier_role = ""
    max_iterations = 0
    pass_marker = ""
    queue_child_jobs = False
    queue_delay_minutes = 5
    default_job_kind = str(cur.get("job_kind") or ("build_verify_loop" if prompt_mode == "decompose_parent" else "single_turn"))
    if prompt_mode == "implementation":
        chosen_execution = _prompt_menu("implementation execution", choices=["single_turn", "build_verify_loop"], default_index=0 if default_job_kind != "build_verify_loop" else 1)
        default_job_kind = chosen_execution
    if default_job_kind == "single_turn":
        role_default = str(cur.get("role") or PROMPT_MODES[prompt_mode]["default_role"])
        role = _prompt_menu(
            f"role ({_inline_choice_hint(role_choices)})",
            choices=role_choices,
            default_index=_choice_default_index(role_choices, role_default),
        )
    else:
        builder_role_default = str(loop.get("builder_role") or "builder")
        builder_role = _prompt_menu(
            f"builder role ({_inline_choice_hint(builder_role_choices)})",
            choices=builder_role_choices,
            default_index=_choice_default_index(builder_role_choices, builder_role_default),
        )
        verifier_role_default = str(loop.get("verifier_role") or "verifier")
        verifier_role = _prompt_menu(
            f"verifier role ({_inline_choice_hint(verifier_role_choices)})",
            choices=verifier_role_choices,
            default_index=_choice_default_index(verifier_role_choices, verifier_role_default),
        )
        max_iterations = int(
            _prompt(
                "max iterations",
                default=str(loop.get("max_iterations") or 3),
                required=True,
            )
        )
        pass_marker = _prompt("pass marker", default=str(loop.get("pass_marker") or "VERDICT: PASS"), required=True)
        if prompt_mode == "decompose_parent":
            queue_child_jobs = _prompt_bool(
                "queue implementation jobs for accepted child PISes right after verifier PASS",
                default=bool(queue_children_meta.get("enabled", True)),
            )
            if queue_child_jobs:
                queue_delay_minutes = int(
                    _prompt(
                        "child job delay minutes after acceptance",
                        default=str(queue_children_meta.get("run_after_minutes") or 5),
                        required=True,
                    )
                )
    run_at = _prompt(
        "run_at UTC (YYYY-MM-DDTHH:MM:SSZ)",
        default=str(schedule.get("run_at") or _default_run_at()),
        validator=_validate_once_utc,
        required=True,
    )
    timezone_value = _prompt(
        "timezone (IANA timezone, examples: UTC, Europe/Vienna, America/New_York)",
        default=str(schedule.get("timezone") or DEFAULT_TIMEZONE),
        validator=_validate_timezone,
        required=True,
    )
    executor_agent_tech = _prompt_menu(
        f"job executor ({_inline_choice_hint(list(EXECUTOR_CHOICES), prefix='available')})",
        choices=list(EXECUTOR_CHOICES),
        default_index=_choice_default_index(list(EXECUTOR_CHOICES), current_executor),
    )
    _warn_if_pipeline_executor(executor_agent_tech=executor_agent_tech, job_kind=default_job_kind, pipeline_executor_mode=_default_pipeline_executor_mode(executor_agent_tech=executor_agent_tech, job_kind=default_job_kind, current=str(runtime.get("pipeline_executor_mode") or builder_runtime.get("pipeline_executor_mode") or "")))
    model = (
        _prompt_menu(
            f"{_model_prompt_label(executor_agent_tech=executor_agent_tech)} ({_inline_choice_hint(model_choices, prefix='available')})",            choices=model_choices,
            default_index=_choice_default_index(model_choices, str(runtime.get("model") or _scheduler_default_model())),
        )
        if default_job_kind == "single_turn"
        else ""
    )
    builder_model = model or str(builder_runtime.get("model") or runtime.get("model") or _scheduler_default_model())
    verifier_model = model or str(verifier_runtime.get("model") or runtime.get("model") or _scheduler_default_model())
    if default_job_kind != "single_turn":
        builder_model = _prompt_menu(
            f"{_model_prompt_label(executor_agent_tech=executor_agent_tech, stage='builder')} ({_inline_choice_hint(model_choices, prefix='available')})",
            choices=model_choices,
            default_index=_choice_default_index(model_choices, str(builder_runtime.get("model") or runtime.get("model") or _scheduler_default_model())),
        )
        verifier_model = _prompt_menu(
            f"{_model_prompt_label(executor_agent_tech=executor_agent_tech, stage='verifier')} ({_inline_choice_hint(model_choices, prefix='available')})",
            choices=model_choices,
            default_index=_choice_default_index(model_choices, str(verifier_runtime.get("model") or builder_model)),
        )
    execution_mode_default = _default_execution_mode(
        executor_agent_tech=executor_agent_tech,
        model=model or builder_model,
        current=str(runtime.get("execution_mode") or builder_runtime.get("execution_mode") or ""),
    )
    execution_mode = _prompt_menu(
        f"execution mode ({_inline_choice_hint(list(EXECUTION_MODE_CHOICES), prefix='available')})",
        choices=list(EXECUTION_MODE_CHOICES),
        default_index=_choice_default_index(list(EXECUTION_MODE_CHOICES), execution_mode_default),
    )
    _warn_if_execution_mode(execution_mode=execution_mode, executor_agent_tech=executor_agent_tech)
    pipeline_executor_mode = _default_pipeline_executor_mode(
        executor_agent_tech=executor_agent_tech,
        job_kind=default_job_kind,
        current=str(runtime.get("pipeline_executor_mode") or builder_runtime.get("pipeline_executor_mode") or ""),
    )
    max_runtime_seconds = _validate_runtime_seconds(
        _prompt(
            "max runtime seconds",            default=str(policy.get("max_runtime_seconds") or DEFAULT_RUNTIME_SECONDS),
            required=True,
        )
    )
    prompt_default = str(cur.get("prompt") or "")
    if prompt_default == _default_prompt(row, prompt_mode=str(cur.get("_prompt_mode") or "implementation")):
        prompt_default = default_prompt
    if not prompt_default:
        prompt_default = default_prompt
    prompt_text = _prompt_multiline("prompt", default=prompt_default)
    display_title = _prompt(
        "display title",
        default=str(cur.get("display_title") or f"{row.uid.split('-')[-1].lower()} {row.title[:80]}"),
        required=False,
    )
    spec = dict(cur)
    spec.update(
        {
            "job_id": job_id,
            "job_kind": default_job_kind,
            "enabled": enabled,
            "binding": {"type": "ledger_uid", "id": row.uid},
            "display_title": display_title,
            "prompt": prompt_text,
            "context_refs": [str(row.pis_path.relative_to(REPO_ROOT)).replace("\\", "/")],
            "expected_output": default_expected_output,
            "schedule": {"mode": "once", "run_at": run_at, "timezone": timezone_value},
            "policy": {"max_runtime_seconds": max_runtime_seconds},
            "runtime": _runtime_spec_from_model(model or builder_model, list(runtime.get("fallback_models") or [])),
            "required_context_bundle": "lab_core_v1",
            "_prompt_mode": prompt_mode,
        }
    )
    spec["runtime"]["executor_agent_tech"] = executor_agent_tech
    spec["runtime"]["advisory_agent_tech"] = spec["runtime"]["agent_tech"]
    spec["runtime"]["advisory_model"] = spec["runtime"]["model"]
    if pipeline_executor_mode:
        spec["runtime"]["pipeline_executor_mode"] = pipeline_executor_mode
    else:
        spec["runtime"].pop("pipeline_executor_mode", None)
    spec["runtime"]["execution_mode"] = execution_mode
    if default_job_kind == "single_turn":
        spec["job_kind"] = "single_turn"
        spec["role"] = role
        spec.pop("loop", None)
        spec.pop("prompt_meta", None)
    else:
        spec["job_kind"] = "build_verify_loop"
        spec.pop("role", None)
        spec["loop"] = {
            "builder_role": builder_role,
            "verifier_role": verifier_role,
            "max_iterations": int(max_iterations),
            "pass_marker": pass_marker,
            "builder_runtime": _runtime_spec_from_model(builder_model, list(builder_runtime.get("fallback_models") or [])),
            "verifier_runtime": _runtime_spec_from_model(verifier_model, list(verifier_runtime.get("fallback_models") or [])),
        }
        spec["loop"]["builder_runtime"]["executor_agent_tech"] = executor_agent_tech
        spec["loop"]["verifier_runtime"]["executor_agent_tech"] = executor_agent_tech
        spec["loop"]["builder_runtime"]["advisory_agent_tech"] = spec["loop"]["builder_runtime"]["agent_tech"]
        spec["loop"]["builder_runtime"]["advisory_model"] = spec["loop"]["builder_runtime"]["model"]
        spec["loop"]["verifier_runtime"]["advisory_agent_tech"] = spec["loop"]["verifier_runtime"]["agent_tech"]
        spec["loop"]["verifier_runtime"]["advisory_model"] = spec["loop"]["verifier_runtime"]["model"]
        if pipeline_executor_mode:
            spec["loop"]["builder_runtime"]["pipeline_executor_mode"] = pipeline_executor_mode
            spec["loop"]["verifier_runtime"]["pipeline_executor_mode"] = pipeline_executor_mode
        else:
            spec["loop"]["builder_runtime"].pop("pipeline_executor_mode", None)
            spec["loop"]["verifier_runtime"].pop("pipeline_executor_mode", None)
        spec["loop"]["builder_runtime"]["execution_mode"] = execution_mode
        spec["loop"]["verifier_runtime"]["execution_mode"] = execution_mode
        if prompt_mode == "decompose_parent":            spec["prompt_meta"] = {
                "queue_child_implementation_jobs": {
                    "enabled": bool(queue_child_jobs),
                    "run_after_minutes": int(queue_delay_minutes),
                    "child_mode": "implementation",
                }
            }
        else:
            spec.pop("prompt_meta", None)
    return spec


def _print_summary(spec: Dict[str, Any]) -> None:
    print()
    print("Planned scheduled job:")
    print(json.dumps(sched.job_summary(spec), indent=2, ensure_ascii=False))
    print()


def _print_default_config(spec: Dict[str, Any]) -> None:
    print()
    print("Default scheduled job config:")
    print(json.dumps(spec, indent=2, ensure_ascii=False))
    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a scheduled job from recent ledger PIS items.")
    parser.add_argument("--pis", default="", help="Optional PIS UID to preselect.")
    parser.add_argument("--template-id", default="", help="Instantiate a stored scheduled-job template instead of a custom job.")
    parser.add_argument("--limit", type=int, default=20, help="How many recent PIS items to show (default: 20).")
    parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
    args = parser.parse_args(argv)
    try:
        sched_backend.require_legacy_for_mutation("create scheduled job", backend=args.backend or None)
        _require_tty()
        template_id = str(args.template_id or "").strip()
        if template_id:
            choice = _select_pis(limit=max(1, int(args.limit)), preselected_uid=str(args.pis or "").strip())
            spec = _default_spec_from_template(choice, template_id=template_id)
        else:
            prompt_mode_choices = ["implementation", "decompose_parent"]
            prompt_mode = _prompt_menu("Job type", choices=prompt_mode_choices, default_index=0)
            implementation_job_kind = "single_turn"
            if prompt_mode == "implementation":
                implementation_job_kind = _prompt_menu("Implementation execution", choices=["single_turn", "build_verify_loop"], default_index=0)
            choice = _select_pis(limit=max(1, int(args.limit)), preselected_uid=str(args.pis or "").strip())
            queue_child_jobs = True
            child_delay_minutes = 5
            if prompt_mode == "decompose_parent":
                queue_child_jobs = _prompt_bool("Queue implementation jobs for accepted child PISes right after verifier PASS", default=True)
                if queue_child_jobs:
                    child_delay_minutes = int(_prompt("Child job delay minutes after acceptance", default="5", required=True))
            spec = _default_spec(choice, prompt_mode=prompt_mode, queue_child_jobs=queue_child_jobs, child_delay_minutes=child_delay_minutes)
            if prompt_mode == "implementation" and implementation_job_kind == "build_verify_loop":
                spec["job_kind"] = "build_verify_loop"
                spec.pop("role", None)
                spec["loop"] = {
                    "builder_role": "builder",
                    "verifier_role": "verifier",
                    "max_iterations": 3,
                    "pass_marker": "VERDICT: PASS",
                    "builder_runtime": dict(spec["runtime"]),
                    "verifier_runtime": dict(spec["runtime"]),
                }
        _print_default_config(spec)
        if _prompt_bool("Use this default config without modification", default=True):
            pass
        else:
            spec = _build_spec_from_answers(choice, existing=spec)
        canonical = sched.validate_job_spec(spec)
        _print_summary(canonical)
        if not _prompt_bool("Save this job", default=True):
            raise UserAbort("save canceled")
        saved = sched.save_job(canonical)
        print(json.dumps(sched.job_summary(saved), indent=2, ensure_ascii=False))
        return 0
    except UserAbort as exc:
        print(f"Canceled: {exc}", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
