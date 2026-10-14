from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

from infra.root_tools import load_tool_module as _load_root_tool_module

dumps_canonical = _load_root_tool_module("lib_canonical_json", "tools/lib/canonical_json.py").dumps_canonical

from .knowledge_assets import _load_minimal_yaml, resolve_knowledge_asset_paths

DEFAULT_ENV_PREFIX = "PIPELINE_"
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PIPELINE_CODE_ROOT = Path(__file__).resolve().parents[2]


@dataclasses.dataclass
class StructuredError:
    code: str
    message: str
    details: Dict[str, Any]
    retryable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details or {}),
            "retryable": bool(self.retryable),
        }


class PipelineError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        self.retryable = bool(retryable)
        self.exit_code = int(exit_code)

    def to_structured_error(self) -> StructuredError:
        return StructuredError(
            code=self.code,
            message=self.message,
            details=self.details,
            retryable=self.retryable,
        )


@dataclasses.dataclass
class SubprocessResult:
    args: List[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "args": list(self.args),
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
        }


@dataclasses.dataclass(frozen=True)
class ResolvedStageIO:
    stage_key: str
    input_path: Path
    output_path: Path
    log_path: Path


@dataclasses.dataclass(frozen=True)
class StagePathSpec:
    input_name: str
    output_name: str
    log_name: str


STAGE_FILE_SPECS: Dict[str, StagePathSpec] = {
    "script_1_job_intake": StagePathSpec("input/00_raw_request.json", "01_job_contract.json", "01_job_intake.exec.json"),
    "script_2_task_classifier": StagePathSpec("01_job_contract.json", "02_task_profile.json", "02_task_classifier.exec.json"),
    "script_3_search_plan": StagePathSpec("02_task_profile.json", "03_search_plan.json", "03_search_plan.exec.json"),
    "script_4_repo_retriever": StagePathSpec("03_search_plan.json", "04_retrieval.json", "04_repo_retriever.exec.json"),
    "script_5_context_ranker": StagePathSpec("04_retrieval.json", "05_context_ranked.json", "05_context_ranker.exec.json"),
    "script_6_context_bundle": StagePathSpec("05_context_ranked.json", "06_context_bundle.json", "06_context_bundle.exec.json"),
    "script_7_patch_plan": StagePathSpec("06_context_bundle.json", "07_patch_plan.json", "07_patch_plan.exec.json"),
    "script_8_patch_generate": StagePathSpec("07_patch_plan.json", "08_patch_artifact.json", "08_patch_generate.exec.json"),
    "script_9_artifact_validate": StagePathSpec("08_patch_artifact.json", "09_validated_artifact.json", "09_artifact_validate.exec.json"),
    "script_10_patch_apply": StagePathSpec("09_validated_artifact.json", "10_apply_result.json", "10_patch_apply.exec.json"),
    "script_11_verify_static": StagePathSpec("10_apply_result.json", "11_static_verification.json", "11_verify_static.exec.json"),
    "script_12_verify_runtime": StagePathSpec("11_static_verification.json", "12_runtime_verification.json", "12_verify_runtime.exec.json"),
    "script_13_contract_verify": StagePathSpec("12_runtime_verification.json", "13_contract_verification.json", "13_contract_verify.exec.json"),
    "script_14_repair_loop": StagePathSpec("13_contract_verification.json", "14_repair_decision.json", "14_repair_loop.exec.json"),
    "script_15_finalize_job": StagePathSpec("14_repair_decision.json", "15_finalization.json", "15_finalize_job.exec.json"),
    "script_16_job_report": StagePathSpec("15_finalization.json", "16_job_report.json", "16_job_report.exec.json"),
}


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def sha256_payload(payload: Any) -> str:
    return sha256_text(dumps_canonical(payload))


def sha256_file(path: str | os.PathLike[str], chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: str | os.PathLike[str]) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise PipelineError("input_missing", f"JSON file not found: {p}", details={"path": str(p)}, exit_code=3)
    raw = p.read_text(encoding="utf-8")
    if CONTROL_CHARS_RE.search(raw):
        raise PipelineError("unsafe_control_chars", f"Control characters detected in JSON file: {p}", details={"path": str(p)}, exit_code=11)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipelineError(
            "invalid_json",
            f"Invalid JSON in {p}: {exc}",
            details={"path": str(p), "lineno": exc.lineno, "colno": exc.colno},
            exit_code=3,
        ) from exc
    if not isinstance(data, dict):
        raise PipelineError("invalid_json_root", f"Top-level JSON object must be a dict: {p}", details={"path": str(p)}, exit_code=3)
    return data


def save_json_atomic(path: str | os.PathLike[str], payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = dumps_canonical(payload) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(target.parent)) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)


def save_text_atomic(path: str | os.PathLike[str], text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(target.parent)) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)


def ensure_directory(path: str | os.PathLike[str]) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge_dicts(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _merge_dicts(out[key], value)
        else:
            out[key] = value
    return out


def _coerce_env_value(raw: str) -> Any:
    txt = str(raw).strip()
    lowered = txt.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", txt):
        return int(txt)
    if re.fullmatch(r"-?\d+\.\d+", txt):
        return float(txt)
    if lowered in {"[]", "{}"}:
        return json.loads(lowered)
    return txt


def _env_to_nested_dict(env: Mapping[str, str], prefix: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith(prefix):
            continue
        if not str(value or "").strip():
            # Treat blank env overrides as absent so they do not clobber defaults
            # with invalid empty-string config values.
            continue
        suffix = key[len(prefix):].strip("_")
        if not suffix:
            continue
        parts = [p.lower() for p in suffix.split("__") if p]
        cursor: MutableMapping[str, Any] = out
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, MutableMapping):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = _coerce_env_value(value)
    return out


def _stringify_env_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def nested_config_to_env(config: Mapping[str, Any], *, prefix: str = DEFAULT_ENV_PREFIX) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def visit(node: Mapping[str, Any], parts: List[str]) -> None:
        for key, value in node.items():
            key_txt = str(key or "").strip()
            if not key_txt:
                continue
            next_parts = parts + [key_txt.upper()]
            if isinstance(value, Mapping):
                visit(value, next_parts)
                continue
            env_key = prefix + "__".join(next_parts)
            out[env_key] = _stringify_env_value(value)

    visit(config, [])
    return out


def _parse_simple_yaml(raw: str) -> Dict[str, Any]:
    try:
        data = _load_minimal_yaml(raw)
    except ValueError as exc:
        raise PipelineError("invalid_config", "Unsupported YAML syntax in config", details={"error": str(exc)}, exit_code=3) from exc
    if not isinstance(data, dict):
        raise PipelineError("invalid_config_root", "Config root must be a mapping", details={"type": type(data).__name__}, exit_code=3)
    return data


def _load_yaml_or_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(raw) or {}
        except Exception:
            data = _parse_simple_yaml(raw)
    if not isinstance(data, dict):
        raise PipelineError("invalid_config_root", f"Config root must be a mapping: {path}", details={"path": str(path)}, exit_code=3)
    return data


def resolve_pipeline_config_root(repo_root: str | os.PathLike[str]) -> Path:
    target_root = Path(repo_root).resolve()
    target_defaults = target_root / "config" / "pipeline.defaults.yaml"
    if target_defaults.exists():
        return target_root
    return PIPELINE_CODE_ROOT


def _validate_positive_int(section: Mapping[str, Any], key: str) -> None:
    value = section.get(key)
    if not isinstance(value, int) or value <= 0:
        raise PipelineError("invalid_config_value", f"Config value must be a positive integer: {key}", details={"key": key, "value": value}, exit_code=3)


def _validate_non_negative_number(section: Mapping[str, Any], key: str) -> None:
    value = section.get(key)
    if value is None:
        return
    if not isinstance(value, (int, float)) or float(value) < 0:
        raise PipelineError("invalid_config_value", f"Config value must be a non-negative number: {key}", details={"key": key, "value": value}, exit_code=3)


def validate_pipeline_config(config: Mapping[str, Any]) -> None:
    pipeline = config.get("pipeline", {})
    timeouts = config.get("timeouts", {})
    limits = config.get("limits", {})
    tools = config.get("tools", {})
    artifacts = config.get("artifacts", {})
    orchestrator = config.get("orchestrator", {})
    policy = config.get("policy", {})
    models = config.get("models", {})
    logging_cfg = config.get("logging", {})
    reporting = config.get("reporting", {})
    user_budgets = config.get("user_budgets", {})
    repair_loop = config.get("repair_loop", {})
    failure_signature = config.get("failure_signature", {})
    progress_cfg = config.get("progress", {})
    mutation = config.get("mutation", {})
    branching = config.get("branching", {})
    retention = config.get("retention", {})
    strategy_selection = config.get("strategy_selection", {})
    cost_budget = config.get("cost_budget", {})
    deterministic_v2 = config.get("deterministic_v2", {})
    knowledge_assets = config.get("knowledge_assets", {})
    for section_name, section in {
        "pipeline": pipeline,
        "timeouts": timeouts,
        "limits": limits,
        "tools": tools,
        "artifacts": artifacts,
        "orchestrator": orchestrator,
        "policy": policy,
        "models": models,
        "logging": logging_cfg,
        "reporting": reporting,
        "user_budgets": user_budgets,
        "repair_loop": repair_loop,
        "failure_signature": failure_signature,
        "progress": progress_cfg,
        "mutation": mutation,
        "branching": branching,
        "retention": retention,
        "strategy_selection": strategy_selection,
        "cost_budget": cost_budget,
        "deterministic_v2": deterministic_v2,
        "knowledge_assets": knowledge_assets,
    }.items():
        if not isinstance(section, Mapping):
            raise PipelineError("invalid_config", f"{section_name} section must be a mapping", exit_code=3)
    for key in ["subprocess_default_seconds", "search_seconds", "patch_plan_seconds", "model_seconds"]:
        _validate_positive_int(timeouts, key)
    for key in [
        "max_file_candidates",
        "max_selected_files",
        "max_snippets",
        "max_total_context_chars",
        "max_patch_artifact_chars",
        "max_patch_artifact_files",
        "max_bundle_files",
        "max_bundle_snippets",
        "max_snippet_chars",
        "max_primary_targets",
        "max_test_snippets",
        "min_file_score",
        "retry_limit",
    ]:
        _validate_positive_int(limits, key)
    margin = limits.get("score_margin_keep_secondary")
    if not isinstance(margin, (int, float)) or float(margin) < 0:
        raise PipelineError("invalid_config_value", "limits.score_margin_keep_secondary must be a non-negative number", details={"key": "score_margin_keep_secondary", "value": margin}, exit_code=3)
    for key in ["tool_version", "contract_version", "schema_version", "jobs_root", "workspace_root"]:
        if not str(pipeline.get(key) or "").strip():
            raise PipelineError("missing_config_key", f"pipeline.{key} is required", details={"key": f"pipeline.{key}"}, exit_code=3)
    for key in ["python_path", "rg_path", "git_path", "ast_grep_path", "pytest_path"]:
        if not str(tools.get(key) or "").strip():
            raise PipelineError("missing_config_key", f"tools.{key} is required", details={"key": f"tools.{key}"}, exit_code=3)
    if orchestrator.get("branching_mode") not in {"explicit_retry_branches"}:
        raise PipelineError("invalid_config_value", "orchestrator.branching_mode must be explicit_retry_branches", details={"value": orchestrator.get("branching_mode")}, exit_code=3)
    _validate_non_negative_number(orchestrator, "low_confidence_threshold")
    for key in ["immutable", "atomic_json_writes", "include_version_metadata", "persist_raw_model_output"]:
        if not isinstance(artifacts.get(key), bool):
            raise PipelineError("invalid_config_value", f"artifacts.{key} must be boolean", details={"value": artifacts.get(key)}, exit_code=3)
    for key in [
        "allow_non_git",
        "allow_zip_artifacts",
        "allow_full_file_replacements",
        "follow_repo_external_symlinks",
        "commit_to_protected_branches",
        "require_isolated_workspace",
    ]:
        if not isinstance(policy.get(key), bool):
            raise PipelineError("invalid_config_value", f"policy.{key} must be boolean", details={"value": policy.get(key)}, exit_code=3)
    for key in ["redact_env_values", "redact_provider_tokens", "include_command_lines", "include_workspace_path", "emit_timing_metrics"]:
        if not isinstance(logging_cfg.get(key), bool):
            raise PipelineError("invalid_config_value", f"logging.{key} must be boolean", details={"value": logging_cfg.get(key)}, exit_code=3)
    model_response_review = reporting.get("model_response_review", {}) if isinstance(reporting, Mapping) else {}
    if model_response_review:
        if not isinstance(model_response_review, Mapping):
            raise PipelineError("invalid_config", "reporting.model_response_review must be a mapping", exit_code=3)
        for key in ["enabled", "include_in_quick_read"]:
            if key in model_response_review and not isinstance(model_response_review.get(key), bool):
                raise PipelineError("invalid_config_value", f"reporting.model_response_review.{key} must be boolean", details={"value": model_response_review.get(key)}, exit_code=3)
        for key in ["max_preview_chars", "preview_head_chars", "preview_tail_chars"]:
            if key in model_response_review:
                _validate_positive_int(model_response_review, key)
    if not str(models.get("default_local_provider") or "").strip() or not str(models.get("default_remote_provider") or "").strip():
        raise PipelineError("missing_config_key", "models.default_local_provider and models.default_remote_provider are required", exit_code=3)
    escalation = models.get("escalation_policy", {})
    if not isinstance(escalation, Mapping):
        raise PipelineError("invalid_config", "models.escalation_policy must be a mapping", exit_code=3)
    if not isinstance(escalation.get("allow_remote_escalation"), bool) or not isinstance(escalation.get("require_explicit_stage_support"), bool):
        raise PipelineError("invalid_config_value", "models.escalation_policy booleans are invalid", exit_code=3)
    for key in ["require_confidence_score", "require_competing_hypotheses", "allow_ambiguous_failure_classification", "prefer_same_branch_repair_for_low_scope_failures", "prefer_new_branch_for_scope_or_strategy_changes"]:
        if not isinstance(repair_loop.get(key), bool):
            raise PipelineError("invalid_config_value", f"repair_loop.{key} must be boolean", details={"value": repair_loop.get(key)}, exit_code=3)
    _validate_positive_int(repair_loop, "max_competing_hypotheses")

    for key in ["enabled", "deterministic_first", "allow_external_llm_stages_1_7", "stage_foundation_fields"]:
        if key in deterministic_v2 and not isinstance(deterministic_v2.get(key), bool):
            raise PipelineError("invalid_config_value", f"deterministic_v2.{key} must be boolean", details={"value": deterministic_v2.get(key)}, exit_code=3)
    for subsection_name in ["failure_taxonomy", "invariants", "search_plan", "retrieval_scoring", "semantic_gate", "patch_contract"]:
        subsection = deterministic_v2.get(subsection_name, {})
        if subsection is None:
            continue
        if not isinstance(subsection, Mapping):
            raise PipelineError("invalid_config", f"deterministic_v2.{subsection_name} must be a mapping", exit_code=3)
    retrieval_scoring = deterministic_v2.get("retrieval_scoring", {}) if isinstance(deterministic_v2, Mapping) else {}
    if isinstance(retrieval_scoring, Mapping):
        for key in ["filename_only_match_penalty", "superficial_match_penalty", "minimum_coverage_score"]:
            _validate_non_negative_number(retrieval_scoring, key)
    semantic_gate = deterministic_v2.get("semantic_gate", {}) if isinstance(deterministic_v2, Mapping) else {}
    if isinstance(semantic_gate, Mapping):
        for key in ["allow_local_semantic_retrieval", "require_explicit_enablement", "local_only"]:
            if key in semantic_gate and not isinstance(semantic_gate.get(key), bool):
                raise PipelineError("invalid_config_value", f"deterministic_v2.semantic_gate.{key} must be boolean", details={"value": semantic_gate.get(key)}, exit_code=3)
    if knowledge_assets and not str(knowledge_assets.get("base_dir") or "").strip():
        raise PipelineError("missing_config_key", "knowledge_assets.base_dir is required when knowledge_assets is configured", exit_code=3)
    knowledge_files = knowledge_assets.get("files", {}) if isinstance(knowledge_assets, Mapping) else {}
    if knowledge_files and not isinstance(knowledge_files, Mapping):
        raise PipelineError("invalid_config", "knowledge_assets.files must be a mapping", exit_code=3)
    for key in ["max_wall_clock_minutes", "max_model_tokens_total", "max_attempts_total", "max_stagnant_attempts"]:
        _validate_positive_int(user_budgets, key)
    _validate_non_negative_number(user_budgets, "min_progress_delta")
    weights = failure_signature.get("weights", {}) if isinstance(failure_signature, Mapping) else {}
    if not isinstance(weights, Mapping):
        raise PipelineError("invalid_config", "failure_signature.weights must be a mapping", exit_code=3)
    for key in ["error_type", "file_overlap", "test_overlap", "token_overlap", "message", "similarity_threshold"]:
        target = weights if key != "similarity_threshold" else failure_signature
        _validate_non_negative_number(target, key)
    if str(progress_cfg.get("baseline_mode") or "") not in {"first_attempt", "max_seen"}:
        raise PipelineError("invalid_config_value", "progress.baseline_mode must be first_attempt or max_seen", details={"value": progress_cfg.get("baseline_mode")}, exit_code=3)
    _validate_non_negative_number(progress_cfg, "float_tolerance")
    _validate_positive_int(mutation, "min_attempts_per_level")
    if "escalation_cooldown" in mutation and (not isinstance(mutation.get("escalation_cooldown"), int) or int(mutation.get("escalation_cooldown")) < 0):
        raise PipelineError("invalid_config_value", "mutation.escalation_cooldown must be a non-negative integer", details={"value": mutation.get("escalation_cooldown")}, exit_code=3)
    if "allow_backoff_on_improvement" in mutation and not isinstance(mutation.get("allow_backoff_on_improvement"), bool):
        raise PipelineError("invalid_config_value", "mutation.allow_backoff_on_improvement must be boolean", details={"value": mutation.get("allow_backoff_on_improvement")}, exit_code=3)
    for key in ["max_active_branches", "max_total_branches"]:
        _validate_positive_int(branching, key)
    _validate_positive_int(retention, "max_recent_attempts")
    if "keep_milestones" in retention and not isinstance(retention.get("keep_milestones"), bool):
        raise PipelineError("invalid_config_value", "retention.keep_milestones must be boolean", details={"value": retention.get("keep_milestones")}, exit_code=3)
    order = list(strategy_selection.get("order") or [])
    if order and any(str(item) not in {"local_fix", "plan_revision", "context_expansion", "task_reinterpretation"} for item in order):
        raise PipelineError("invalid_config_value", "strategy_selection.order contains an unsupported strategy", details={"value": order}, exit_code=3)
    for field in ["rules", "fallback"]:
        value = strategy_selection.get(field, [])
        if value and not isinstance(value, list):
            raise PipelineError("invalid_config", f"strategy_selection.{field} must be a list", exit_code=3)
    for key in ["max_tokens_total", "max_model_calls", "max_expensive_stage_calls"]:
        _validate_positive_int(cost_budget, key)


def load_pipeline_config(
    *,
    repo_root: str | os.PathLike[str],
    stage_key: Optional[str] = None,
    cli_overrides: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    env_prefix: str = DEFAULT_ENV_PREFIX,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    config_root = resolve_pipeline_config_root(root)
    config_dir = config_root / "config"
    defaults = _load_yaml_or_json(config_dir / "pipeline.defaults.yaml")
    local_override = _load_yaml_or_json(config_dir / "pipeline.local.override.yaml")
    overlay_paths_raw = str((env or os.environ).get(f"{env_prefix}CONFIG_OVERLAY_FILES") or (env or os.environ).get(f"{env_prefix}CONFIG_OVERLAY_FILE") or "").strip()
    overlay_paths: List[Path] = []
    if overlay_paths_raw:
        for raw_part in overlay_paths_raw.split(os.pathsep):
            item = str(raw_part or "").strip()
            if not item:
                continue
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = (root / candidate).resolve()
            overlay_paths.append(candidate)
    env_overlay = _env_to_nested_dict(env or os.environ, env_prefix)
    cli_overlay = dict(cli_overrides or {})
    merged = _merge_dicts(defaults, local_override)
    for overlay_path in overlay_paths:
        merged = _merge_dicts(merged, _load_yaml_or_json(overlay_path))
    merged = _merge_dicts(merged, env_overlay)
    merged = _merge_dicts(merged, cli_overlay)
    validate_pipeline_config(merged)
    pipeline = dict(merged.get("pipeline", {}))
    jobs_root = Path(root / str(pipeline.get("jobs_root", "jobs"))).resolve()
    workspace_root = Path(root / str(pipeline.get("workspace_root", ".pipeline_workspaces"))).resolve()
    merged.setdefault("_derived", {})
    merged["_derived"]["repo_root"] = str(root)
    merged["_derived"]["config_root"] = str(config_root)
    merged["_derived"]["jobs_root"] = str(jobs_root)
    merged["_derived"]["workspace_root"] = str(workspace_root)
    merged["_derived"]["knowledge_assets"] = resolve_knowledge_asset_paths(config_root, merged)
    merged["_derived"]["knowledge_assets_root"] = str(merged["_derived"]["knowledge_assets"].get("base_dir") or "")
    merged["_derived"]["config_overlay_files"] = [str(path) for path in overlay_paths]
    stages = merged.get("stages", {})
    if not isinstance(stages, Mapping):
        raise PipelineError("invalid_config", "stages section must be a mapping", exit_code=3)
    merged["_stage"] = dict(stages.get(stage_key, {})) if stage_key else {}
    return merged


def normalize_repo_relative_path(repo_root: str | os.PathLike[str], value: str) -> str:
    root = Path(repo_root).resolve()
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise PipelineError("invalid_path", "Empty path is not allowed", exit_code=6)
    path = Path(raw)
    if path.is_absolute():
        raise PipelineError("absolute_path_forbidden", f"Absolute path forbidden: {raw}", exit_code=6)
    resolved = (root / path).resolve()
    try:
        rel = resolved.relative_to(root).as_posix()
    except Exception as exc:
        raise PipelineError("path_traversal", f"Path escapes repo root: {raw}", details={"path": raw, "repo_root": str(root)}, exit_code=6) from exc
    return rel


def _matches_policy(rel: str, prefixes: Sequence[str]) -> bool:
    return any(rel == prefix or rel.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


def enforce_path_policy(
    repo_root: str | os.PathLike[str],
    candidate_paths: Iterable[str],
    *,
    allowed_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
) -> None:
    normalized_allowed = [normalize_repo_relative_path(repo_root, p) for p in list(allowed_paths or [])]
    normalized_forbidden = [normalize_repo_relative_path(repo_root, p) for p in list(forbidden_paths or [])]
    for candidate in candidate_paths:
        rel = normalize_repo_relative_path(repo_root, candidate)
        if normalized_forbidden and _matches_policy(rel, normalized_forbidden):
            raise PipelineError("forbidden_path", f"Path violates forbidden policy: {rel}", details={"path": rel}, exit_code=6)
        if normalized_allowed and not _matches_policy(rel, normalized_allowed):
            raise PipelineError("path_outside_allowlist", f"Path violates allowlist policy: {rel}", details={"path": rel}, exit_code=6)


def is_path_allowed(
    repo_root: str | os.PathLike[str],
    candidate_path: str,
    *,
    allowed_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
) -> bool:
    try:
        enforce_path_policy(repo_root, [candidate_path], allowed_paths=allowed_paths, forbidden_paths=forbidden_paths)
        return True
    except PipelineError:
        return False


def artifact_metadata(config: Mapping[str, Any], *, stage_key: str, source_commit: str = "") -> Dict[str, Any]:
    pipeline = config.get("pipeline", {})
    return {
        "schema_version": str(pipeline.get("schema_version")),
        "contract_version": str(pipeline.get("contract_version")),
        "stage_version": f"{stage_key}.v1",
        "tool_version": str(pipeline.get("tool_version")),
        "source_commit": str(source_commit or ""),
    }


def build_envelope(
    *,
    stage: str,
    job_id: str,
    run_id: str,
    input_payload: Mapping[str, Any],
    result: Mapping[str, Any],
    warnings: Optional[Iterable[str]] = None,
    errors: Optional[Iterable[StructuredError]] = None,
    tool_version: str,
    started_at_iso: Optional[str] = None,
    finished_at_iso: Optional[str] = None,
) -> Dict[str, Any]:
    started = started_at_iso or utc_now_iso()
    finished = finished_at_iso or utc_now_iso()
    started_dt = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
    finished_dt = dt.datetime.fromisoformat(finished.replace("Z", "+00:00"))
    duration_ms = max(0, int((finished_dt - started_dt).total_seconds() * 1000))
    error_list = [e.to_dict() if isinstance(e, StructuredError) else dict(e) for e in list(errors or [])]
    return {
        "stage": str(stage),
        "status": "failure" if error_list else "success",
        "job_id": str(job_id),
        "run_id": str(run_id),
        "created_at": finished,
        "duration_ms": duration_ms,
        "tool_version": str(tool_version),
        "input_digest": sha256_payload(input_payload),
        "result": dict(result),
        "warnings": [str(w) for w in list(warnings or [])],
        "errors": error_list,
    }


def run_subprocess(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: int,
    check: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    process_handle_callback: Optional[Callable[[Any], None]] = None,
) -> SubprocessResult:
    started = dt.datetime.now(dt.timezone.utc)
    def _notify_process_handle(value: Any) -> None:
        if process_handle_callback is None:
            return
        try:
            process_handle_callback(value)
        except Exception:
            pass

    def _terminate_process_tree(proc: subprocess.Popen[str], *, force: bool) -> None:
        try:
            if proc.poll() is not None:
                return
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif force:
                proc.kill()
            else:
                proc.terminate()
        except ProcessLookupError:
            return
        except Exception:
            try:
                if proc.poll() is None:
                    if force:
                        proc.kill()
                    else:
                        proc.terminate()
            except Exception:
                pass

    proc = subprocess.Popen(
        list(args),
        cwd=str(cwd),
        env=dict(env or os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _notify_process_handle(proc)
    poll_interval = 1.0
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=poll_interval)
                finished = dt.datetime.now(dt.timezone.utc)
                result = SubprocessResult(
                    args=[str(x) for x in args],
                    cwd=str(Path(cwd).resolve()),
                    returncode=int(proc.returncode or 0),
                    stdout=out or "",
                    stderr=err or "",
                    timed_out=False,
                    duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
                )
                break
            except subprocess.TimeoutExpired:
                now = dt.datetime.now(dt.timezone.utc)
                duration_ms = max(0, int((now - started).total_seconds() * 1000))
                if progress_callback is not None:
                    try:
                        progress_callback({"elapsed_ms": duration_ms})
                    except Exception:
                        pass
                if duration_ms >= int(timeout_seconds) * 1000:
                    _terminate_process_tree(proc, force=False)
                    try:
                        out, err = proc.communicate(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        _terminate_process_tree(proc, force=True)
                        out, err = proc.communicate()
                    finished = dt.datetime.now(dt.timezone.utc)
                    result = SubprocessResult(
                        args=[str(x) for x in args],
                        cwd=str(Path(cwd).resolve()),
                        returncode=8,
                        stdout=out or "",
                        stderr=err or "",
                        timed_out=True,
                        duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
                    )
                    raise PipelineError("timeout", f"Command timed out after {timeout_seconds}s", details=result.to_dict(), exit_code=8)
    finally:
        _notify_process_handle(None)
    if check and result.returncode != 0:
        raise PipelineError("subprocess_failed", f"Command failed with exit code {result.returncode}", details=result.to_dict(), exit_code=max(1, int(result.returncode)))
    return result


def get_repo_git_info(repo_root: str | os.PathLike[str], *, timeout_seconds: int = 20) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        return {"is_git_repo": False, "branch": None, "head_commit": None}
    branch = run_subprocess(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, timeout_seconds=timeout_seconds)
    commit = run_subprocess(["git", "rev-parse", "HEAD"], cwd=root, timeout_seconds=timeout_seconds)
    return {"is_git_repo": branch.returncode == 0 and commit.returncode == 0, "branch": branch.stdout.strip() or None, "head_commit": commit.stdout.strip() or None}


def make_job_id(now: Optional[dt.datetime] = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    return f"JOB-{current.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"


def make_run_id(now: Optional[dt.datetime] = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    return f"RUN-{current.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"


def resolve_stage_io(job_dir: str | os.PathLike[str], run_id: str, stage_key: str) -> ResolvedStageIO:
    base = Path(job_dir).resolve()
    run_dir = base / "runs" / str(run_id)
    logs_dir = run_dir / "logs"
    spec = STAGE_FILE_SPECS.get(stage_key)
    if spec is None:
        raise PipelineError("unknown_stage", f"Unknown stage: {stage_key}", details={"stage_key": stage_key}, exit_code=2)
    input_path = (base / spec.input_name).resolve() if spec.input_name.startswith("input/") else (run_dir / spec.input_name).resolve()
    output_path = run_dir / spec.output_name
    log_path = logs_dir / spec.log_name
    return ResolvedStageIO(stage_key=stage_key, input_path=input_path, output_path=output_path, log_path=log_path)


def emit_pipeline_error(error: Exception) -> int:
    if isinstance(error, PipelineError):
        sys.stderr.write(dumps_canonical({"error": error.to_structured_error().to_dict()}) + "\n")
        return error.exit_code
    fallback = PipelineError("unhandled_internal_error", str(error), exit_code=1)
    sys.stderr.write(dumps_canonical({"error": fallback.to_structured_error().to_dict()}) + "\n")
    return 1
